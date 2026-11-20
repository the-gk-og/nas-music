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


# ---------- v3 -> v4 (LRCLIB lyrics cache, bug reports): additive only ----------

V4_TABLES = ("lyrics_cache", "bug_reports")


def migrate_v3_to_v4(conn: sqlite3.Connection, schema_sql: str, make_backup: bool = True) -> bool:
    """New tables only. Returns True if it migrated."""
    if current_version(conn) != 3:
        return False
    if make_backup:
        dest = backup(conn, "v3-backup")
        if dest:
            log.warning("schema v3 -> v4: backed up the database to %s", dest)
    stmts = statements(schema_sql)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if current_version(conn) != 3:
            conn.execute("ROLLBACK")
            return False
        for t in V4_TABLES:
            if not _table_exists(conn, t):
                conn.execute(_create_table(stmts, t))
        _create_indexes(conn, stmts)
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (4)")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    log.warning("schema v3 -> v4 done")
    return True


# ---------- v4 -> v5 (admin tag overrides, collaborative playlists, Release Radar, Blend, ReplayGain) ----------

V5_TABLES = ("tag_overrides", "tag_edits", "playlist_members", "playlist_activity",
             "release_radar", "blends", "blend_members", "blend_tracks")
# Volume levelling: nullable; the next scan reads gain tags and the loudness job measures the rest.
V5_TRACK_COLUMNS = (("rg_track_gain", "REAL"), ("rg_track_peak", "REAL"), ("rg_album_gain", "REAL"),
                    ("rg_album_peak", "REAL"), ("rg_source", "TEXT"), ("loudness_lufs", "REAL"))


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _rebuild_playlist_tracks(conn: sqlite3.Connection, stmts: list[str]) -> None:
    """v4 playlist_tracks (PK playlist_id+position, dense INTEGER positions) -> v5 (entry id PK, fractional
    REAL position, added_by). Order is kept; positions become 1.0, 2.0, ...; added_by = the playlist owner."""
    if "id" in _columns(conn, "playlist_tracks"):
        return  # already v5-shaped (a v1 database upgraded by this code base creates it that way)
    conn.execute(_create_table(stmts, "playlist_tracks", "playlist_tracks__v5"))
    rows = conn.execute(
        "SELECT pt.playlist_id, pt.position, pt.track_id, pt.added_at, p.user_id FROM playlist_tracks pt "
        "JOIN playlists p ON p.id = pt.playlist_id ORDER BY pt.playlist_id, pt.position").fetchall()
    conn.executemany(
        "INSERT INTO playlist_tracks__v5 (id, playlist_id, position, track_id, added_at, added_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(uuid.uuid4().hex, r[0], float(r[1] + 1), r[2], r[3], r[4]) for r in rows])
    conn.execute("DROP TABLE playlist_tracks")
    conn.execute("ALTER TABLE playlist_tracks__v5 RENAME TO playlist_tracks")


def migrate_v4_to_v5(conn: sqlite3.Connection, schema_sql: str, make_backup: bool = True) -> bool:
    """New tables, two nullable/defaulted columns, and a rebuild of playlist_tracks (data kept, in one
    transaction with foreign keys off). Returns True if it migrated."""
    if current_version(conn) != 4:
        return False
    if make_backup:
        dest = backup(conn, "v4-backup")
        if dest:
            log.warning("schema v4 -> v5: backed up the database to %s", dest)
    stmts = statements(schema_sql)
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if current_version(conn) != 4:
                conn.execute("ROLLBACK")
                return False
            for t in V5_TABLES:
                if not _table_exists(conn, t):
                    conn.execute(_create_table(stmts, t))
            if "file_tags" not in _columns(conn, "tracks"):
                conn.execute("ALTER TABLE tracks ADD COLUMN file_tags TEXT")
            have = _columns(conn, "tracks")
            for col, typ in V5_TRACK_COLUMNS:
                if col not in have:
                    conn.execute(f"ALTER TABLE tracks ADD COLUMN {col} {typ}")
            if "rev" not in _columns(conn, "playlists"):
                conn.execute("ALTER TABLE playlists ADD COLUMN rev INTEGER NOT NULL DEFAULT 0")
            _rebuild_playlist_tracks(conn, stmts)
            _create_indexes(conn, stmts)
            bad = conn.execute("PRAGMA foreign_key_check(playlist_tracks)").fetchall()
            if bad:
                log.warning("schema v5: %d playlist entries point at missing rows (kept)", len(bad))
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version (version) VALUES (5)")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    log.warning("schema v4 -> v5 done")
    return True


# ---------- v5 -> v6 (explicit flags, family mode, automatic tag fixing, duplicates, Jam, AI radio) ----------

V6_TABLES = ("track_explicit", "family_mode",
             "fingerprints", "acoustid_lookups", "mb_releases", "autotag_suggestions", "duplicate_hidden",
             "jams", "jam_guests", "jam_requests", "jam_votes",
             "radio_stations", "radio_feedback")


def _rebuild_tag_overrides(conn: sqlite3.Connection, stmts: list[str]) -> None:
    """Widen the tag_overrides.field CHECK ('explicit' and the MusicBrainz id fields). Rows are copied unchanged."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tag_overrides'").fetchone()
    if sql is None or ("'explicit'" in sql[0] and "mb_track" in sql[0]):
        return
    conn.execute(_create_table(stmts, "tag_overrides", "tag_overrides__v6"))
    conn.execute("INSERT INTO tag_overrides__v6 (track_id, field, value, updated_by, updated_at) "
                 "SELECT track_id, field, value, updated_by, updated_at FROM tag_overrides")
    conn.execute("DROP TABLE tag_overrides")
    conn.execute("ALTER TABLE tag_overrides__v6 RENAME TO tag_overrides")


def migrate_v5_to_v6(conn: sqlite3.Connection, schema_sql: str, make_backup: bool = True) -> bool:
    """New tables and a rebuild of tag_overrides (data kept, one transaction, foreign keys off). The explicit
    job fills track_explicit afterwards. AI radio adds two nullable radio_sessions columns. Returns True if it migrated."""
    if current_version(conn) != 5:
        return False
    if make_backup:
        dest = backup(conn, "v5-backup")
        if dest:
            log.warning("schema v5 -> v6: backed up the database to %s", dest)
    stmts = statements(schema_sql)
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if current_version(conn) != 5:
                conn.execute("ROLLBACK")
                return False
            if _table_exists(conn, "radio_sessions"):
                have = _columns(conn, "radio_sessions")
                for col in ("station_key", "params"):
                    if col not in have:
                        conn.execute(f"ALTER TABLE radio_sessions ADD COLUMN {col} TEXT")
            for t in V6_TABLES:
                if not _table_exists(conn, t):
                    conn.execute(_create_table(stmts, t))
            _rebuild_tag_overrides(conn, stmts)
            _create_indexes(conn, stmts)
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version (version) VALUES (6)")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    log.warning("schema v5 -> v6 done")
    return True


# ---------- v6 -> v7: uploads (additive only) ----------

V7_TABLES = ("upload_access", "uploads", "upload_sessions")


def migrate_v6_to_v7(conn: sqlite3.Connection, schema_sql: str, make_backup: bool = True) -> bool:
    """New upload tables. Returns True if it migrated."""
    if current_version(conn) != 6:
        return False
    if make_backup:
        dest = backup(conn, "v6-backup")
        if dest:
            log.warning("schema v6 -> v7: backed up the database to %s", dest)
    stmts = statements(schema_sql)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if current_version(conn) != 6:
            conn.execute("ROLLBACK")
            return False
        for t in V7_TABLES:
            if not _table_exists(conn, t):
                conn.execute(_create_table(stmts, t))
        _create_indexes(conn, stmts)
        conn.execute("DELETE FROM schema_version")
        conn.execute("INSERT INTO schema_version (version) VALUES (7)")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    log.warning("schema v6 -> v7 done")
    return True
