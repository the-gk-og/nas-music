"""Admin tag editing: write/revert overrides, re-apply them to the library rows, and keep an audit log.

Edits never touch the music files. After changing `tag_overrides` the affected tracks are re-resolved from
`tracks.file_tags` (+ overrides) exactly as the scanner would, so a later rescan gives the same result."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from pathlib import PurePosixPath

from nasmusic.config import Config
from nasmusic.core import now_iso
from nasmusic.library import art, overrides, scanner, tags
from nasmusic.library.tags import TrackTags

log = logging.getLogger(__name__)

MAX_TRACKS = 5000


class EditError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _j(v) -> str:
    return json.dumps(v, ensure_ascii=False)


def _file_tags(cfg: Config, conn: sqlite3.Connection, row: sqlite3.Row) -> TrackTags:
    """What the file says. Uses tracks.file_tags; tracks not re-read since v5 are read once (read-only)."""
    if row["file_tags"]:
        return overrides.tags_from_json(row["file_tags"])
    try:
        t, mf = tags.read(cfg.library_root / row["path"])
        if mf is None:
            raise ValueError("unrecognised audio file")
    except Exception as e:  # unreadable/missing file: fall back to the current row (best effort)
        log.warning("cannot read tags of %s for tag edit: %s", row["path"], e)
        t = _tags_from_row(conn, row)
        conn.execute("UPDATE tracks SET file_tags = ? WHERE id = ?", (overrides.file_tags_json(t), row["id"]))
        return t
    conn.execute("UPDATE tracks SET file_tags = ? WHERE id = ?", (overrides.file_tags_json(t), row["id"]))
    return t


def _tags_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> TrackTags:
    ar = conn.execute("SELECT name, mbid FROM artists WHERE id = ?", (row["artist_id"],)).fetchone()
    al = conn.execute("SELECT title, mbid FROM albums WHERE id = ?", (row["album_id"],)).fetchone()
    return TrackTags(title=row["title"], artist=ar["name"] if ar else None, albumartist=row["album_artist"],
                     album=al["title"] if al else None, track_no=row["track_no"], disc_no=row["disc_no"],
                     year=row["year"], genre=row["genre"], mb_track=row["mbid"],
                     mb_artist=ar["mbid"] if ar else None, mb_releasegroup=al["mbid"] if al else None)


def _rows(conn: sqlite3.Connection, track_ids: list[str]) -> list[sqlite3.Row]:
    ids = list(dict.fromkeys(track_ids))
    if not ids:
        raise EditError(422, "no tracks")
    if len(ids) > MAX_TRACKS:
        raise EditError(422, f"at most {MAX_TRACKS} tracks per edit")
    rows: list[sqlite3.Row] = []
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows += conn.execute(f"SELECT * FROM tracks WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
    if len(rows) != len(ids):
        raise EditError(404, "unknown track id")
    return rows


def _effective(file_t: TrackTags, ov: dict, field: str):
    return ov[field] if field in ov else overrides.file_value(file_t, field)


def _reapply(cfg: Config, conn: sqlite3.Connection, row: sqlite3.Row, file_t: TrackTags, ov: dict,
             now: str) -> tuple[str, str]:
    t = overrides.apply(file_t, {k: v for k, v in ov.items() if overrides.FIELDS.get(k)})
    ta_id, al_id = scanner.upsert_names(conn, row["path"], t, now)
    conn.execute(
        "UPDATE tracks SET album_id = ?, artist_id = ?, title = ?, track_no = ?, disc_no = ?, year = ?, genre = ?, "
        "album_artist = ?, mbid = ? WHERE id = ?",
        (al_id, ta_id, t.title or PurePosixPath(row["path"]).stem, t.track_no, t.disc_no, t.year, t.genre,
         t.albumartist, t.mb_track, row["id"]))
    return row["album_id"], al_id


def refresh_albums(cfg: Config, conn: sqlite3.Connection, album_ids: set[str]) -> None:
    """After tracks moved between albums: drop orphans, recompute years, and fix the art of `album_ids`."""
    conn.execute("DELETE FROM albums WHERE id NOT IN (SELECT DISTINCT album_id FROM tracks)")
    conn.execute("DELETE FROM artists WHERE id NOT IN (SELECT DISTINCT artist_id FROM albums) "
                 "AND id NOT IN (SELECT DISTINCT artist_id FROM tracks)")
    covers = overrides.album_covers(conn)
    for al_id in album_ids:
        conn.execute("UPDATE albums SET year = (SELECT MIN(year) FROM tracks WHERE album_id = ? AND missing = 0) "
                     "WHERE id = ?", (al_id, al_id))
        cur = conn.execute("SELECT art_path FROM albums WHERE id = ?", (al_id,)).fetchone()
        if cur is None:
            continue
        first = conn.execute("SELECT path FROM tracks WHERE album_id = ? AND missing = 0 "
                             "ORDER BY disc_no, track_no, path LIMIT 1", (al_id,)).fetchone()
        if first is None:
            continue
        want = covers.get(al_id) or _folder_art(cfg, PurePosixPath(first["path"]).parent.as_posix())
        if want != cur["art_path"]:
            conn.execute("UPDATE albums SET art_path = ? WHERE id = ?", (want, al_id))
            art.forget_resized(cfg.data_dir, al_id)
        if not want and not art.embedded_state_known(cfg.data_dir, al_id):
            art.store_embedded(cfg.data_dir, al_id, scanner._embedded_from_first_track(cfg, conn, al_id))


def _folder_art(cfg: Config, rel_dir: str) -> str | None:
    rel_dir = "" if rel_dir == "." else rel_dir
    images: dict[str, list[str]] = {}
    dirs = [rel_dir]
    if rel_dir:
        parent = PurePosixPath(rel_dir).parent.as_posix()
        dirs.append("" if parent == "." else parent)
    for d in dirs:
        try:
            images[d] = [n for n in os.listdir(cfg.library_root / d)
                         if os.path.splitext(n)[1].lower() in art.IMAGE_EXTS]
        except OSError:
            images[d] = []
    return scanner._folder_art(rel_dir, images)


def _clean_change(set_: dict | None, revert: list[str] | None) -> tuple[dict, list[str]]:
    set_ = dict(set_ or {})
    revert = list(revert or [])
    for f in list(set_):
        set_[f] = overrides.clean_value(f, set_[f])
        if f in ("lyrics", "cover") and set_[f] is None:
            del set_[f]
            revert.append(f)
    if "*" in revert:
        revert = list(overrides.FIELDS)
    for f in revert:
        if f not in overrides.FIELDS:
            raise EditError(422, f"unknown field {f!r}")
    return set_, revert


def edit(cfg: Config, conn: sqlite3.Connection, user_id: str, track_ids: list[str], set_: dict | None = None,
         revert: list[str] | None = None) -> dict:
    """Set fields (`set_`: {db_field: value}, value None = clear the tag) and/or revert fields to the file's
    tags (`revert`: db field names, or ["*"] for all) on many tracks in one transaction. For `lyrics` and
    `cover`, None means revert. Returns {"batchId", "changed"} (number of audit rows)."""
    set_, revert = _clean_change(set_, revert)
    if not set_ and not revert:
        raise EditError(422, "nothing to change")
    return edit_each(cfg, conn, user_id, {tid: set_ for tid in track_ids}, revert)


def edit_each(cfg: Config, conn: sqlite3.Connection, user_id: str, changes: dict[str, dict],
              revert: list[str] | None = None, batch: str | None = None, *, in_transaction: bool = False) -> dict:
    """Like edit(), but with different values per track ({track_id: {db_field: value}}), all in one audit
    batch. Used by automatic tag fixing to accept a whole album at once. With `in_transaction` the caller
    owns the (BEGIN IMMEDIATE) transaction."""
    cleaned = {tid: _clean_change(v, revert) for tid, v in changes.items()}
    batch = batch or uuid.uuid4().hex
    now = now_iso()
    changed = 0
    touched: set[str] = set()
    # BEGIN IMMEDIATE serialises with the scanner, which re-loads overrides inside its own write transaction.
    if not in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        for row in _rows(conn, list(cleaned)):
            set_, rev = cleaned[row["id"]]
            file_t = _file_tags(cfg, conn, row)
            ov = overrides.load(conn, row["id"])
            new = dict(ov)
            for f in rev:
                new.pop(f, None)
            new.update(set_)
            if new == ov:
                continue
            for f in sorted(set(ov) | set(new)):
                if ov.get(f, ...) == new.get(f, ...):
                    continue
                old_v, new_v = _effective(file_t, ov, f), _effective(file_t, new, f)
                if f in new:
                    conn.execute(
                        "INSERT INTO tag_overrides (track_id, field, value, updated_by, updated_at) "
                        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(track_id, field) DO UPDATE SET value = excluded.value, "
                        "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
                        (row["id"], f, _j(new[f]), user_id, now))
                else:
                    conn.execute("DELETE FROM tag_overrides WHERE track_id = ? AND field = ?", (row["id"], f))
                conn.execute(
                    "INSERT INTO tag_edits (batch_id, track_id, user_id, at, action, field, old_value, new_value) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (batch, row["id"], user_id, now, "set" if f in new else "revert", f, _j(old_v), _j(new_v)))
                changed += 1
            old_al, new_al = _reapply(cfg, conn, row, file_t, new, now)
            touched.update((old_al, new_al))
        if touched:
            refresh_albums(cfg, conn, touched)
        if not in_transaction:
            conn.execute("COMMIT")
    except BaseException:
        if not in_transaction:
            conn.execute("ROLLBACK")
        raise
    return {"batchId": batch, "changed": changed}


def album_track_ids(conn: sqlite3.Connection, album_id: str) -> list[str]:
    ids = [r[0] for r in conn.execute("SELECT id FROM tracks WHERE album_id = ? ORDER BY disc_no, track_no, path",
                                      (album_id,))]
    if not ids:
        raise EditError(404, "no such album")
    return ids


def _api_tags(get) -> dict:
    return {overrides.API_NAMES[f]: get(f) for f in overrides.TAG_FIELDS}


def detail(cfg: Config, conn: sqlite3.Connection, track_id: str) -> dict:
    """File tags, overrides and effective tags of one track (camelCase field names)."""
    rows = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchall()
    if not rows:
        raise EditError(404, "no such track")
    row = rows[0]
    if row["file_tags"]:
        file_t = overrides.tags_from_json(row["file_tags"])
    else:  # read-only peek; written back on the next edit or scan
        try:
            file_t, _ = tags.read(cfg.library_root / row["path"])
        except Exception:
            file_t = _tags_from_row(conn, row)
    ov = overrides.load(conn, track_id)
    meta = {r["field"]: r for r in conn.execute(
        "SELECT o.field, o.updated_at, u.username FROM tag_overrides o LEFT JOIN users u ON u.id = o.updated_by "
        "WHERE o.track_id = ?", (track_id,))}
    return {
        "trackId": track_id, "path": row["path"], "albumId": row["album_id"],
        "file": _api_tags(lambda f: overrides.file_value(file_t, f)),
        "effective": _api_tags(lambda f: _effective(file_t, ov, f)),
        "overridden": sorted(overrides.API_NAMES[f] for f in ov),
        "overrides": [{"field": overrides.API_NAMES[f], "value": None if f == "cover" else ov[f],
                       "updatedAt": meta[f]["updated_at"], "updatedBy": meta[f]["username"]} for f in sorted(ov)],
        "lyrics": ov.get("lyrics"),
        "hasCoverOverride": "cover" in ov,
    }


def history(conn: sqlite3.Connection, track_id: str | None = None, album_id: str | None = None,
            limit: int = 100, offset: int = 0) -> list[dict]:
    where, params = [], []
    if track_id:
        where.append("e.track_id = ?")
        params.append(track_id)
    if album_id:
        where.append("t.album_id = ?")
        params.append(album_id)
    q = ("SELECT e.*, u.username, t.title AS track_title, t.path FROM tag_edits e "
         "JOIN tracks t ON t.id = e.track_id LEFT JOIN users u ON u.id = e.user_id"
         + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY e.id DESC LIMIT ? OFFSET ?")

    def val(raw, field):
        v = json.loads(raw) if raw is not None else None
        if field == "lyrics" and isinstance(v, str) and len(v) > 200:
            return v[:200] + "…"
        return v

    return [{"id": r["id"], "batchId": r["batch_id"], "trackId": r["track_id"], "trackTitle": r["track_title"],
             "path": r["path"], "at": r["at"], "user": r["username"], "action": r["action"],
             "field": overrides.API_NAMES.get(r["field"], r["field"]),
             "oldValue": val(r["old_value"], r["field"]), "newValue": val(r["new_value"], r["field"])}
            for r in conn.execute(q, (*params, limit, offset))]
