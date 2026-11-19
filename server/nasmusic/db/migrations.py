"""Schema migrations. v1 -> v2 (docs/v2.md §2): single-user -> multi-user.

All v1 per-user data (favourites, ratings, thumbs, events, devices, mixes, taste snapshots,
suggestions) is assigned to the admin user. The migration:
- is idempotent: it only runs when schema_version is 1, re-checked inside the write transaction;
- copies the database file first (`<db>.v1-backup-<UTC timestamp>`) using SQLite's online backup;
- runs in ONE transaction, so a failure leaves the v1 database untouched;
- rebuilds changed tables with SQLite's documented procedure (create new, copy, drop old, rename),
  with foreign keys off, then runs `PRAGMA foreign_key_check`.
"""
from __future__ import annotations

import logging
import posixpath
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

# Tables whose shape changes in v2 (rebuilt); everything else in v2 is new or unchanged.
REBUILT = ("tracks", "devices", "pair_codes", "events", "mixes", "taste_snapshots", "suggestions")
NEW = ("users", "invites", "user_settings", "user_tracks", "playlists", "playlist_tracks", "queues",
       "queue_tracks", "lyrics_overrides", "playback_state")


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def statements(sql: str) -> list[str]:
    """Split a schema script into complete statements (comments stripped)."""
    out, buf = [], []
    for line in sql.splitlines():
        line = line.split("--", 1)[0].rstrip()
        if not line.strip():
            continue
        buf.append(line)
        stmt = "\n".join(buf)
        if sqlite3.complete_statement(stmt):
            out.append(stmt.strip())
            buf = []
    return out


def _create_table(stmts: list[str], table: str, as_name: str | None = None) -> str:
    pat = re.compile(rf"^CREATE TABLE IF NOT EXISTS {table}\s*\(", re.IGNORECASE)
    for s in stmts:
        if pat.match(s):
            return pat.sub(f"CREATE TABLE {as_name or table} (", s, count=1)
    raise KeyError(table)


def current_version(conn: sqlite3.Connection) -> int | None:
    has = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_version'").fetchone()
    if not has:
        return None
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] if row and row[0] is not None else None


def _db_file(conn: sqlite3.Connection) -> Path | None:
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main" and row[2]:
            return Path(row[2])
    return None


def backup(conn: sqlite3.Connection, suffix: str) -> Path | None:
    src = _db_file(conn)
    if src is None:
        return None
    dest = src.with_name(f"{src.name}.{suffix}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
    out = sqlite3.connect(dest)
    try:
        conn.backup(out)
    finally:
        out.close()
    return dest


def migrate_v1_to_v2(conn: sqlite3.Connection, schema_sql: str, admin_username: str = "admin",
                     admin_password_hash: str | None = None, make_backup: bool = True) -> bool:
    """Upgrade a v1 database in place. Returns True if it migrated, False if there was nothing to do."""
    if current_version(conn) != 1:
        return False
    if make_backup:
        dest = backup(conn, "v1-backup")
        if dest:
            log.warning("schema v1 -> v2: backed up the database to %s", dest)
    stmts = statements(schema_sql)
    conn.execute("PRAGMA foreign_keys = OFF")  # must be outside the transaction
    conn.execute("PRAGMA legacy_alter_table = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if current_version(conn) != 1:  # someone else migrated meanwhile
                conn.execute("ROLLBACK")
                return False
            admin_id = _migrate(conn, stmts, admin_username, admin_password_hash)
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version (version) VALUES (2)")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        log.warning("schema v2: %d foreign-key inconsistencies left from v1 (first: %s)",
                    len(problems), tuple(problems[0]))
    log.warning("schema v1 -> v2 done; v1 data now belongs to user %r (%s)", admin_username, admin_id)
    return True


def _migrate(conn: sqlite3.Connection, stmts: list[str], admin_username: str, admin_hash: str | None) -> str:
    now = _now()
    for t in NEW:
        conn.execute(_create_table(stmts, t))
    admin_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES (?, ?, ?, 1, ?)",
        (admin_id, admin_username, admin_hash or "!", now),
    )

    # Per-user track state, with play/skip counts reconstructed from the event history.
    conn.execute(
        """INSERT INTO user_tracks (user_id, track_id, starred_at, rating, blocked, play_count, skip_count,
                                    last_played)
           SELECT ?, t.id, t.starred_at, t.rating, t.blocked,
                  (SELECT COUNT(*) FROM events e WHERE e.track_id = t.id AND e.type = 'play'),
                  (SELECT COUNT(*) FROM events e WHERE e.track_id = t.id AND e.type = 'skip'),
                  (SELECT MAX(at) FROM events e WHERE e.track_id = t.id AND e.type = 'play')
           FROM tracks t
           WHERE t.starred_at IS NOT NULL OR t.rating IS NOT NULL OR t.blocked = 1
              OR EXISTS (SELECT 1 FROM events e WHERE e.track_id = t.id)""",
        (admin_id,),
    )

    for t in REBUILT:
        conn.execute(_create_table(stmts, t, f"{t}__v2"))
    # mtime = 0 makes the next scan re-read every file's tags to fill the new song-info columns;
    # ids, paths and added_at are kept so nothing that references a track changes.
    conn.execute(
        """INSERT INTO tracks__v2 (id, path, album_id, artist_id, title, track_no, disc_no, year, genre,
                                   duration_s, flac_bytes, mtime, mbid, added_at, missing, folder)
           SELECT id, path, album_id, artist_id, title, track_no, disc_no, year, genre, duration_s, flac_bytes,
                  0, mbid, added_at, missing, '' FROM tracks"""
    )
    folders = [(_folder(r[1]), r[0]) for r in conn.execute("SELECT id, path FROM tracks__v2")]
    conn.executemany("UPDATE tracks__v2 SET folder = ? WHERE id = ?", folders)
    conn.execute(
        """INSERT INTO devices__v2 (id, user_id, name, kind, app_version, token_hash, created_at, last_seen, revoked)
           SELECT id, ?, name, 'android', NULL, token_hash, created_at, last_seen, revoked FROM devices""",
        (admin_id,),
    )
    conn.execute(
        """INSERT INTO pair_codes__v2 (code, user_id, expires_at, used)
           SELECT code, ?, expires_at, used FROM pair_codes""", (admin_id,),
    )
    conn.execute(
        """INSERT INTO events__v2 (id, user_id, device_id, type, track_id, at, position_s, duration_s, source,
                                   mix_id, rating, offline, received_at, context)
           SELECT id, ?, device_id, type, track_id, at, position_s, duration_s, source, mix_id, rating, offline,
                  received_at, NULL FROM events""", (admin_id,),
    )
    conn.execute(
        """INSERT INTO mixes__v2 (id, user_id, kind, title, for_date, created_at, seed_track, active)
           SELECT id, ?, kind, title, for_date, created_at, seed_track, active FROM mixes""", (admin_id,),
    )
    conn.execute(
        """INSERT INTO taste_snapshots__v2 (id, user_id, created_at, model_version, payload, notes)
           SELECT id, ?, created_at, model_version, payload, notes FROM taste_snapshots""", (admin_id,),
    )
    conn.execute(
        """INSERT INTO suggestions__v2 (id, user_id, artist, album, year, artist_mbid, release_group_mbid,
                                        cover_url, preview_url, buy_links, reason, score, status, created_at,
                                        answered_at)
           SELECT id, ?, artist, album, year, artist_mbid, release_group_mbid, cover_url, preview_url, buy_links,
                  reason, score, status, created_at, answered_at FROM suggestions""", (admin_id,),
    )
    for t in REBUILT:
        conn.execute(f"DROP TABLE {t}")
        conn.execute(f"ALTER TABLE {t}__v2 RENAME TO {t}")
    _create_indexes(conn, stmts)
    return admin_id


_INDEX_TABLE = re.compile(r"\bON\s+(\w+)\s*\(", re.IGNORECASE)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone() is not None


def _create_indexes(conn: sqlite3.Connection, stmts: list[str]) -> None:
    """Create the schema's indexes for tables that exist at this migration step."""
    for s in stmts:
        if s.upper().startswith("CREATE INDEX"):
            m = _INDEX_TABLE.search(s)
            if m and _table_exists(conn, m.group(1)):
                conn.execute(s)


# ---------- v2 -> v3 (docs/v3-ux.md phase 1): additive only ----------

V3_TABLES = ("pins", "toget", "catalog_items", "radio_sessions")  # radio_sessions may pre-exist (AI creates it)
V3_QUEUE_COLUMNS = (("source_type", "TEXT"), ("source_id", "TEXT"), ("radio_seed", "TEXT"), ("radio_id", "TEXT"))


def migrate_v2_to_v3(conn: sqlite3.Connection, schema_sql: str, make_backup: bool = True) -> bool:
    """New tables + nullable queue columns. No data is rewritten. Returns True if it migrated."""
    if current_version(conn) != 2:
        return False
    if make_backup:
        dest = backup(conn, "v2-backup")
        if dest:
            log.warning("schema v2 -> v3: backed up the database to %s", dest)
    stmts = statements(schema_sql)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if current_version(conn) != 2:
            conn.execute("ROLLBACK")
            return False
        for t in V3_TABLES:
            if not _table_exists(conn, t):
                conn.execute(_create_table(stmts, t))
        have = {r[1] for r in conn.execute("PRAGMA table_info(queues)")}
        for col, typ in V3_QUEUE_COLUMNS:
            if col not in have:
                conn.execute(f"ALTER TABLE queues ADD COLUMN {col} {typ}")
        _create_indexes(conn, stmts)
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (3)")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    log.warning("schema v2 -> v3 done")
    return True


def _folder(path: str) -> str:
    d = posixpath.dirname(path)
    return "" if d in ("", ".") else d
