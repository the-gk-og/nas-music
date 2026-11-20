"""In-app notifications (v7): "new music added", one batched notification per user per library scan.

After each scan, tracks whose `added_at` is newer than the server-wide watermark (settings `newmusic.watermark`)
are the batch. Every enabled user with `notify.newMusic` = true gets one notification (family mode: only tracks
they may see), stored in `notifications` (the bell list) and pushed over Connect as
{"type": "notification", "notification": {...}}. Android shows a system notification from its periodic
WorkManager check (GET /me/notifications?since=...) and on reconnect; there is no Google push.
The first run on a database only sets the watermark, so an existing library never becomes "12,000 new songs".
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from nasmusic.core import get_setting, iso, now_iso, set_setting
from nasmusic.db import family

log = logging.getLogger(__name__)

WATERMARK = "newmusic.watermark"
SETTING = "notify.newMusic"
KEEP_PER_USER = 100
KEEP_DAYS = 90
MAX_IDS = 500


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _names(names: list[str], limit: int = 3) -> str:
    names = list(dict.fromkeys(n for n in names if n))
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) <= limit:
        return ", ".join(names[:-1]) + " and " + names[-1]
    return ", ".join(names[:limit]) + f" and {len(names) - limit} more"


def describe(tracks: list[sqlite3.Row | dict], new_album_ids: set[str]) -> tuple[str, str, dict]:
    """(title, body, data) for a batch of new tracks (rows with id, album_id, artist, album_artist_name)."""
    albums: dict[str, str] = {}
    loose_artists: list[str] = []
    loose = 0
    for t in tracks:
        if t["album_id"] in new_album_ids:
            albums.setdefault(t["album_id"], t["album_artist_name"] or t["artist"])
        else:
            loose += 1
            loose_artists.append(t["artist"])
    title = f"{_plural(len(tracks), 'new song')}"
    parts = []
    if albums:
        parts.append(f"{_plural(len(albums), 'new album')} from {_names(list(albums.values()))}")
    if loose:
        if albums:
            parts.append(f"plus {_plural(loose, 'song')} from {_names(loose_artists)}")
        else:
            parts.append(f"From {_names(loose_artists)}")
    body = ", ".join(parts)
    artists = list(dict.fromkeys([*albums.values(), *loose_artists]))
    data = {"tracks": len(tracks), "albums": len(albums), "artists": artists[:20],
            "albumIds": list(albums)[:MAX_IDS], "trackIds": [t["id"] for t in tracks][:MAX_IDS]}
    return title, body, data


def to_dict(r: sqlite3.Row) -> dict:
    try:
        data = json.loads(r["data"] or "{}")
    except ValueError:
        data = {}
    return {"id": r["id"], "kind": r["kind"], "title": r["title"], "body": r["body"], "data": data,
            "createdAt": r["created_at"], "read": r["read_at"] is not None}


def _wants(conn: sqlite3.Connection, uid: str) -> bool:
    r = conn.execute("SELECT value FROM user_settings WHERE user_id = ? AND key = ?", (uid, SETTING)).fetchone()
    return bool(r) and r[0].strip() == "true"


def new_music(conn: sqlite3.Connection, hub=None, now: datetime | None = None) -> dict:
    """Run after a scan. Returns {"tracks": n, "notified": users} (or {"initialised": True})."""
    now = now or datetime.now(UTC)
    wm = get_setting(conn, WATERMARK)
    latest = conn.execute("SELECT MAX(added_at) FROM tracks").fetchone()[0]
    if wm is None:
        set_setting(conn, WATERMARK, latest or iso(now))
        return {"initialised": True}
    rows = conn.execute(
        "SELECT t.id, t.album_id, t.added_at, ar.name AS artist, aa.name AS album_artist_name, "
        "al.added_at AS album_added "
        "FROM tracks t JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id "
        "JOIN artists aa ON aa.id = al.artist_id WHERE t.added_at > ? AND t.missing = 0 "
        "ORDER BY aa.sort_name COLLATE NOCASE, al.title COLLATE NOCASE, t.disc_no, t.track_no, t.path",
        (wm,)).fetchall()
    if not rows:
        return {"tracks": 0, "notified": 0}
    set_setting(conn, WATERMARK, max(r["added_at"] for r in rows))
    new_albums = {r["album_id"] for r in rows if r["album_added"] and r["album_added"] > wm}
    notified = 0
    cutoff = iso(now - timedelta(days=KEEP_DAYS))
    for u in conn.execute("SELECT id FROM users WHERE disabled = 0").fetchall():
        uid = u["id"]
        if not _wants(conn, uid):
            continue
        visible = set(family.filter_ids(conn, uid, [r["id"] for r in rows]))
        mine = [r for r in rows if r["id"] in visible]
        if not mine:
            continue
        title, body, data = describe(mine, new_albums)
        nid = uuid.uuid4().hex
        conn.execute("INSERT INTO notifications (id, user_id, kind, title, body, data, created_at) "
                     "VALUES (?, ?, 'new_music', ?, ?, ?, ?)", (nid, uid, title, body, json.dumps(data), iso(now)))
        conn.execute("DELETE FROM notifications WHERE user_id = ? AND (created_at < ? OR id NOT IN ("
                     "SELECT id FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT ?))",
                     (uid, cutoff, uid, KEEP_PER_USER))
        notified += 1
        if hub is not None:
            row = conn.execute("SELECT * FROM notifications WHERE id = ?", (nid,)).fetchone()
            try:
                hub.notify_user(uid, {"type": "notification", "notification": to_dict(row)})
            except Exception:
                log.exception("notification push failed")
    return {"tracks": len(rows), "notified": notified}


def listing(conn: sqlite3.Connection, uid: str, limit: int = 50, since: str | None = None,
            unread_only: bool = False) -> dict:
    q, params = "SELECT * FROM notifications WHERE user_id = ?", [uid]
    if since:
        q += " AND created_at > ?"
        params.append(since)
    if unread_only:
        q += " AND read_at IS NULL"
    rows = conn.execute(q + " ORDER BY created_at DESC, id LIMIT ?", (*params, limit)).fetchall()
    unread = conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL",
                          (uid,)).fetchone()[0]
    return {"items": [to_dict(r) for r in rows], "unread": unread}


def mark_read(conn: sqlite3.Connection, uid: str, ids: list[str] | None) -> int:
    q = "UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL"
    params: list = [now_iso(), uid]
    if ids is not None:
        if not ids:
            return 0
        q += f" AND id IN ({','.join('?' * len(ids))})"
        params += ids
    return conn.execute(q, params).rowcount


def new_this_week(conn: sqlite3.Connection, uid: str, data_dir, days: int = 7, limit: int = 200,
                  now: datetime | None = None) -> dict:
    """Albums first added in the last `days`, then songs added to albums that already existed."""
    from nasmusic.library import queries

    since = iso((now or datetime.now(UTC)) - timedelta(days=days))
    f = queries.fam(conn, uid)
    albums = [queries.album_dict(r, data_dir) | {"addedAt": r["added_at"]} for r in conn.execute(
        queries.album_select(f) + " WHERE al.added_at >= ? ORDER BY al.added_at DESC, al.title COLLATE NOCASE "
        "LIMIT ?", (since, limit))]
    album_ids = {a["id"] for a in albums}
    ids = [r[0] for r in conn.execute(
        "SELECT t.id FROM tracks t WHERE t.added_at >= ? AND t.missing = 0 ORDER BY t.added_at DESC, t.path LIMIT ?",
        (since, limit * 5))]
    by_id = queries.tracks_by_ids(conn, uid, ids)
    tracks = [by_id[i] for i in ids if i in by_id and by_id[i]["albumId"] not in album_ids][:limit]
    return {"since": since, "days": days, "albums": albums, "tracks": tracks}
