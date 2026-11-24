"""Playlists, including collaborative ones (schema v5).

The owner (playlists.user_id) can invite other users as editors (add, remove, reorder tracks) or viewers
(read-only); only the owner renames, deletes and manages members; a member can leave.

Ordering is conflict-safe: every entry has an id and a fractional `position` (REAL). Inserting between two
entries takes the midpoint, so an edit never renumbers other entries, and the entry-based operations
(insert after entry X, move entry X after entry Y, remove entry X) keep their meaning however other people
edited the list meanwhile. Positions are renormalised (1, 2, 3, ...) only when a gap gets too small, under
the same write transaction. The old index-based operations still work (they are translated to entries).

Every change bumps playlists.rev, appends to playlist_activity, and is pushed over Connect to everyone
with access as {"type": "playlist", "playlistId", "rev"} (see collections_api)."""
from __future__ import annotations

import json
import sqlite3
import uuid

from nasmusic.app.collections import BadRequest, Conflict, Forbidden, NotFound, _check_tracks, _tracks, _tx
from nasmusic.core import now_iso

ROLES = ("editor", "viewer")
MIN_GAP = 1e-9
MAX_MEMBERS = 50


# ---------- access ----------


def _role(conn: sqlite3.Connection, uid: str, pid: str) -> tuple[sqlite3.Row, str]:
    """(playlist row, 'owner' | 'editor' | 'viewer'); NotFound when the user has no access at all."""
    row = conn.execute("SELECT * FROM playlists WHERE id = ?", (pid,)).fetchone()
    if row is None:
        raise NotFound("no such playlist")
    if row["user_id"] == uid:
        return row, "owner"
    m = conn.execute("SELECT role FROM playlist_members WHERE playlist_id = ? AND user_id = ?", (pid, uid)).fetchone()
    if m is None:
        raise NotFound("no such playlist")
    return row, m[0]


def _need(conn: sqlite3.Connection, uid: str, pid: str, level: str) -> tuple[sqlite3.Row, str]:
    row, role = _role(conn, uid, pid)
    if level == "edit" and role == "viewer":
        raise Forbidden("you can only view this playlist")
    if level == "owner" and role != "owner":
        raise Forbidden("only the playlist's owner can do that")
    return row, role


def can_view(conn: sqlite3.Connection, uid: str, pid: str) -> bool:
    try:
        _role(conn, uid, pid)
        return True
    except NotFound:
        return False


def audience(conn: sqlite3.Connection, pid: str) -> set[str]:
    """Everyone with access (owner + members): who gets live updates."""
    out = {r[0] for r in conn.execute("SELECT user_id FROM playlist_members WHERE playlist_id = ?", (pid,))}
    row = conn.execute("SELECT user_id FROM playlists WHERE id = ?", (pid,)).fetchone()
    if row:
        out.add(row[0])
    return out


def _log(conn: sqlite3.Connection, pid: str, uid: str | None, action: str, track_id: str | None = None,
         **detail) -> None:
    conn.execute("INSERT INTO playlist_activity (playlist_id, user_id, at, action, track_id, detail) "
                 "VALUES (?, ?, ?, ?, ?, ?)",
                 (pid, uid, now_iso(), action, track_id, json.dumps(detail, ensure_ascii=False) if detail else None))


def _touch(conn: sqlite3.Connection, pid: str) -> None:
    conn.execute("UPDATE playlists SET updated_at = ?, rev = rev + 1 WHERE id = ?", (now_iso(), pid))


# ---------- reading ----------


def _entries(conn: sqlite3.Connection, pid: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT pt.id, pt.track_id, pt.position, pt.added_at, pt.added_by, u.username "
        "FROM playlist_tracks pt LEFT JOIN users u ON u.id = pt.added_by "
        "WHERE pt.playlist_id = ? ORDER BY pt.position, pt.id", (pid,)).fetchall()


def _playlist_ids(conn: sqlite3.Connection, pid: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position, id", (pid,))]


def _playlist_summary(conn: sqlite3.Connection, row: sqlite3.Row, uid: str | None = None) -> dict:
    s = conn.execute("SELECT COUNT(*), COALESCE(SUM(t.duration_s), 0) FROM playlist_tracks p "
                     "JOIN tracks t ON t.id = p.track_id WHERE p.playlist_id = ?", (row["id"],)).fetchone()
    owner = conn.execute("SELECT username FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    members = conn.execute("SELECT COUNT(*) FROM playlist_members WHERE playlist_id = ?", (row["id"],)).fetchone()[0]
    role = "owner"
    if uid is not None and uid != row["user_id"]:
        m = conn.execute("SELECT role FROM playlist_members WHERE playlist_id = ? AND user_id = ?",
                         (row["id"], uid)).fetchone()
        role = m[0] if m else "viewer"
    viewer = uid if uid is not None else row["user_id"]
    folder = conn.execute("SELECT folder_id FROM playlist_folder_items WHERE user_id = ? AND playlist_id = ?",
                          (viewer, row["id"])).fetchone()
    return {"id": row["id"], "name": row["name"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            "trackCount": s[0], "durationS": round(s[1], 3), "ownerId": row["user_id"],
            "owner": owner[0] if owner else None, "role": role, "rev": row["rev"], "memberCount": members,
            "collaborative": members > 0, "folderId": folder[0] if folder else None}


def list_playlists(conn: sqlite3.Connection, uid: str) -> list[dict]:
    """The user's own playlists and the ones shared with them."""
    rows = conn.execute(
        "SELECT * FROM playlists WHERE user_id = ? OR id IN (SELECT playlist_id FROM playlist_members "
        "WHERE user_id = ?) ORDER BY updated_at DESC, name", (uid, uid))
    return [_playlist_summary(conn, r, uid) for r in rows.fetchall()]


def get_playlist(conn: sqlite3.Connection, uid: str, pid: str) -> dict:
    row, _ = _role(conn, uid, pid)
    ents = _entries(conn, pid)
    by_id = {t["id"]: t for t in _tracks(conn, uid, [e["track_id"] for e in ents])}
    kept = [e for e in ents if e["track_id"] in by_id]
    return {**_playlist_summary(conn, row, uid),
            "tracks": [by_id[e["track_id"]] for e in kept],
            "entries": [{"id": e["id"], "trackId": e["track_id"], "addedAt": e["added_at"],
                         "addedById": e["added_by"], "addedBy": e["username"]} for e in kept]}


def summary_for(conn: sqlite3.Connection, uid: str, pid: str) -> dict | None:
    try:
        row, _ = _role(conn, uid, pid)
    except NotFound:
        return None
    return _playlist_summary(conn, row, uid)


# ---------- ordering ----------


def _renormalise(conn: sqlite3.Connection, pid: str) -> None:
    conn.executemany("UPDATE playlist_tracks SET position = ? WHERE id = ?",
                     [(float(i + 1), r["id"]) for i, r in enumerate(_entries(conn, pid))])


def _between(lo: float | None, hi: float | None, n: int) -> list[float] | None:
    """n increasing positions strictly between lo and hi (None = open end); None if the gap is too small."""
    if lo is None and hi is None:
        return [float(i + 1) for i in range(n)]
    if hi is None:
        return [lo + i + 1 for i in range(n)]
    if lo is None:
        return [hi - (n - i) for i in range(n)]
    step = (hi - lo) / (n + 1)
    if step < MIN_GAP:
        return None
    return [lo + step * (i + 1) for i in range(n)]


def _slot(conn: sqlite3.Connection, pid: str, after: str | None, n: int, exclude: str | None = None) -> list[float]:
    """Positions for n entries placed right after entry `after` (None = at the top)."""
    for attempt in (0, 1):
        ents = [e for e in _entries(conn, pid) if e["id"] != exclude]
        if after is None:
            lo, idx = None, 0
        else:
            idx = next((i for i, e in enumerate(ents) if e["id"] == after), None)
            if idx is None:
                raise Conflict("that entry is no longer in the playlist")
            lo, idx = ents[idx]["position"], idx + 1
        hi = ents[idx]["position"] if idx < len(ents) else None
        got = _between(lo, hi, n)
        if got is not None:
            return got
        _renormalise(conn, pid)
    raise Conflict("could not find room in the playlist")  # unreachable after renormalising


def _insert(conn: sqlite3.Connection, pid: str, uid: str, track_ids: list[str], positions: list[float]) -> None:
    now = now_iso()
    conn.executemany(
        "INSERT INTO playlist_tracks (id, playlist_id, position, track_id, added_at, added_by) VALUES (?, ?, ?, ?, ?, ?)",
        [(uuid.uuid4().hex, pid, p, t, now, uid) for t, p in zip(track_ids, positions, strict=True)])


# ---------- writing ----------


def create_playlist(conn: sqlite3.Connection, uid: str, name: str, track_ids: list[str] | None = None,
                    folder_id: str | None = None) -> dict:
    ids = list(track_ids or [])
    _check_tracks(conn, ids)
    pid = uuid.uuid4().hex
    now = now_iso()
    with _tx(conn):
        if folder_id is not None and conn.execute("SELECT 1 FROM playlist_folders WHERE id = ? AND user_id = ?",
                                                  (folder_id, uid)).fetchone() is None:
            raise BadRequest("no such folder")
        conn.execute("INSERT INTO playlists (id, user_id, name, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                     (pid, uid, name.strip()[:200] or "Playlist", now, now))
        _insert(conn, pid, uid, ids, [float(i + 1) for i in range(len(ids))])
        _log(conn, pid, uid, "create", count=len(ids))
        if folder_id is not None:  # v10
            conn.execute("INSERT INTO playlist_folder_items (user_id, playlist_id, folder_id) VALUES (?, ?, ?)",
                         (uid, pid, folder_id))
    return get_playlist(conn, uid, pid)


def rename_playlist(conn: sqlite3.Connection, uid: str, pid: str, name: str) -> dict:
    with _tx(conn):
        row, _ = _need(conn, uid, pid, "owner")
        new = name.strip()[:200]
        conn.execute("UPDATE playlists SET name = ? WHERE id = ?", (new, pid))
        _touch(conn, pid)
        _log(conn, pid, uid, "rename", **{"from": row["name"], "to": new})
    return get_playlist(conn, uid, pid)


def delete_playlist(conn: sqlite3.Connection, uid: str, pid: str) -> None:
    _need(conn, uid, pid, "owner")
    conn.execute("DELETE FROM playlists WHERE id = ?", (pid,))


def add_tracks(conn: sqlite3.Connection, uid: str, pid: str, track_ids: list[str], *, position: int | None = None,
               after_entry: str | None = None, at_top: bool = False) -> dict:
    """Insert after entry `after_entry`, at the top (`at_top`), at index `position` (legacy), or append."""
    _check_tracks(conn, track_ids)
    with _tx(conn):
        _need(conn, uid, pid, "edit")
        if after_entry is not None:
            after = after_entry
        elif at_top:
            after = None
        else:
            ents = _entries(conn, pid)
            at = len(ents) if position is None else max(0, min(position, len(ents)))
            after = ents[at - 1]["id"] if at > 0 else None
        _insert(conn, pid, uid, track_ids, _slot(conn, pid, after, len(track_ids)))
        _touch(conn, pid)
        _log(conn, pid, uid, "add", track_ids[0], count=len(track_ids))
    return get_playlist(conn, uid, pid)


def remove_entries(conn: sqlite3.Connection, uid: str, pid: str, entry_ids: list[str]) -> dict:
    """Remove entries; ones already gone are ignored (two people removing the same track is fine)."""
    with _tx(conn):
        _need(conn, uid, pid, "edit")
        gone = []
        for eid in entry_ids:
            r = conn.execute("SELECT track_id FROM playlist_tracks WHERE id = ? AND playlist_id = ?",
                             (eid, pid)).fetchone()
            if r is not None:
                conn.execute("DELETE FROM playlist_tracks WHERE id = ?", (eid,))
                gone.append(r[0])
        if gone:
            _touch(conn, pid)
            _log(conn, pid, uid, "remove", gone[0], count=len(gone))
    return get_playlist(conn, uid, pid)


def move_entry(conn: sqlite3.Connection, uid: str, pid: str, entry_id: str, after_entry: str | None) -> dict:
    """Move an entry right after `after_entry` (None = to the top). 409 if either entry is gone."""
    with _tx(conn):
        _need(conn, uid, pid, "edit")
        r = conn.execute("SELECT track_id FROM playlist_tracks WHERE id = ? AND playlist_id = ?",
                         (entry_id, pid)).fetchone()
        if r is None:
            raise Conflict("that entry is no longer in the playlist")
        if after_entry == entry_id:
            raise BadRequest("can't move an entry after itself")
        (pos,) = _slot(conn, pid, after_entry, 1, exclude=entry_id)
        conn.execute("UPDATE playlist_tracks SET position = ? WHERE id = ?", (pos, entry_id))
        _touch(conn, pid)
        _log(conn, pid, uid, "move", r[0])
    return get_playlist(conn, uid, pid)


def edit_playlist(conn: sqlite3.Connection, uid: str, pid: str, *, add: list[str] | None = None,
                  position: int | None = None, remove: int | None = None,
                  move: tuple[int, int] | None = None) -> dict:
    """Legacy index-based edits (v1-v4 clients), translated to entry operations."""
    if add is not None:
        return add_tracks(conn, uid, pid, add, position=position)
    _role(conn, uid, pid)
    ents = _entries(conn, pid)
    if remove is not None:
        if not 0 <= remove < len(ents):
            raise BadRequest("position out of range")
        return remove_entries(conn, uid, pid, [ents[remove]["id"]])
    if move is not None:
        frm, to = move
        if not (0 <= frm < len(ents)) or not (0 <= to < len(ents)):
            raise BadRequest("position out of range")
        moving = ents[frm]["id"]
        rest = [e for e in ents if e["id"] != moving]
        return move_entry(conn, uid, pid, moving, rest[to - 1]["id"] if to > 0 else None)
    return get_playlist(conn, uid, pid)


def remove_tracks_everywhere(conn: sqlite3.Connection, track_ids: list[str]) -> None:
    """Drop entries of deleted tracks from every playlist (inside the caller's transaction)."""
    for i in range(0, len(track_ids), 500):
        chunk = track_ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        pids = [r[0] for r in conn.execute(
            f"SELECT DISTINCT playlist_id FROM playlist_tracks WHERE track_id IN ({marks})", chunk)]
        conn.execute(f"DELETE FROM playlist_tracks WHERE track_id IN ({marks})", chunk)
        for pid in pids:
            _touch(conn, pid)


# ---------- members ----------


def members(conn: sqlite3.Connection, uid: str, pid: str) -> list[dict]:
    row, _ = _role(conn, uid, pid)
    owner = conn.execute("SELECT id, username FROM users WHERE id = ?", (row["user_id"],)).fetchone()
    out = [{"userId": owner["id"], "username": owner["username"], "role": "owner", "addedAt": row["created_at"]}]
    out += [{"userId": r["user_id"], "username": r["username"], "role": r["role"], "addedAt": r["added_at"]}
            for r in conn.execute(
                "SELECT m.user_id, u.username, m.role, m.added_at FROM playlist_members m JOIN users u "
                "ON u.id = m.user_id WHERE m.playlist_id = ? ORDER BY m.added_at, u.username", (pid,))]
    return out


def add_member(conn: sqlite3.Connection, uid: str, pid: str, role: str, *, user_id: str | None = None,
               username: str | None = None) -> list[dict]:
    if role not in ROLES:
        raise BadRequest("role must be editor or viewer")
    with _tx(conn):
        row, _ = _need(conn, uid, pid, "owner")
        q = ("SELECT id, username FROM users WHERE id = ? AND disabled = 0" if user_id else
             "SELECT id, username FROM users WHERE username = ? AND disabled = 0")
        u = conn.execute(q, (user_id or username or "",)).fetchone()
        if u is None:
            raise NotFound("no such user")
        if u["id"] == row["user_id"]:
            raise BadRequest("the owner is already in the playlist")
        old = conn.execute("SELECT role FROM playlist_members WHERE playlist_id = ? AND user_id = ?",
                           (pid, u["id"])).fetchone()
        if old is None and conn.execute("SELECT COUNT(*) FROM playlist_members WHERE playlist_id = ?",
                                        (pid,)).fetchone()[0] >= MAX_MEMBERS:
            raise BadRequest(f"at most {MAX_MEMBERS} members")
        if old is not None and old[0] == role:
            return members(conn, uid, pid)
        conn.execute("INSERT INTO playlist_members (playlist_id, user_id, role, added_by, added_at) VALUES (?, ?, ?, ?, ?) "
                     "ON CONFLICT(playlist_id, user_id) DO UPDATE SET role = excluded.role", (pid, u["id"], role, uid,
                                                                                             now_iso()))
        _touch(conn, pid)
        _log(conn, pid, uid, "member_role" if old else "member_add", user=u["username"], role=role)
    return members(conn, uid, pid)


def remove_member(conn: sqlite3.Connection, uid: str, pid: str, member_id: str) -> None:
    """The owner removes a member, or a member leaves (member_id == uid)."""
    with _tx(conn):
        row, role = _role(conn, uid, pid)
        if member_id == row["user_id"]:
            raise BadRequest("the owner can't leave; delete the playlist instead")
        if role != "owner" and member_id != uid:
            raise Forbidden("only the playlist's owner can do that")
        u = conn.execute("SELECT username FROM users WHERE id = ?", (member_id,)).fetchone()
        if conn.execute("DELETE FROM playlist_members WHERE playlist_id = ? AND user_id = ?",
                        (pid, member_id)).rowcount != 1:
            raise NotFound("not a member")
        # v10: their filing of it into their folders goes with their access
        conn.execute("DELETE FROM playlist_folder_items WHERE user_id = ? AND playlist_id = ?", (member_id, pid))
        _touch(conn, pid)
        _log(conn, pid, uid, "leave" if member_id == uid else "member_remove", user=u[0] if u else None)


def users_directory(conn: sqlite3.Connection, uid: str) -> list[dict]:
    """Other enabled users (to invite to a playlist)."""
    return [{"id": r[0], "username": r[1]} for r in conn.execute(
        "SELECT id, username FROM users WHERE disabled = 0 AND id != ? ORDER BY username COLLATE NOCASE", (uid,))]


def activity(conn: sqlite3.Connection, uid: str, pid: str, limit: int = 100, before: int | None = None) -> list[dict]:
    _role(conn, uid, pid)
    rows = conn.execute(
        "SELECT a.*, u.username, t.title AS track_title FROM playlist_activity a "
        "LEFT JOIN users u ON u.id = a.user_id LEFT JOIN tracks t ON t.id = a.track_id "
        "WHERE a.playlist_id = ? AND (? IS NULL OR a.id < ?) ORDER BY a.id DESC LIMIT ?",
        (pid, before, before, limit))
    return [{"id": r["id"], "at": r["at"], "userId": r["user_id"], "user": r["username"], "action": r["action"],
             "trackId": r["track_id"], "trackTitle": r["track_title"],
             "detail": json.loads(r["detail"]) if r["detail"] else {}} for r in rows]
