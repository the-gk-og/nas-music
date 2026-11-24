"""Playlist folders (schema v10).

Each user has their own folder tree (playlist_folders, parent_id NULL = top level) and files into it any
playlist they can see, their own or one shared with them (playlist_folder_items). Filing is personal: the
owner and each member of a shared playlist keep it wherever they like. A playlist with no row is at the top
level, so a new or newly shared playlist shows there until it is filed.

Deleting a folder moves what was in it (playlists and subfolders) up to the folder's parent, so deleting a
folder never deletes a playlist."""
from __future__ import annotations

import sqlite3
import uuid

from nasmusic.app.collections import BadRequest, NotFound, _tx
from nasmusic.app.playlists import can_view
from nasmusic.core import now_iso

MAX_FOLDERS = 500
MAX_DEPTH = 8
UNSET = object()


def _row(conn: sqlite3.Connection, uid: str, fid: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM playlist_folders WHERE id = ? AND user_id = ?", (fid, uid)).fetchone()
    if row is None:
        raise NotFound("no such folder")
    return row


def _depth(conn: sqlite3.Connection, fid: str | None) -> int:
    """1 for a top-level folder, 2 for one inside it, ..."""
    d = 0
    while fid is not None and d <= MAX_DEPTH + 1:
        r = conn.execute("SELECT parent_id FROM playlist_folders WHERE id = ?", (fid,)).fetchone()
        if r is None:
            break
        d, fid = d + 1, r[0]
    return d


def _subtree_height(conn: sqlite3.Connection, fid: str) -> int:
    """1 for a folder with no subfolders."""
    kids = [r[0] for r in conn.execute("SELECT id FROM playlist_folders WHERE parent_id = ?", (fid,))]
    return 1 + max((_subtree_height(conn, k) for k in kids), default=0)


def _ancestors(conn: sqlite3.Connection, fid: str | None) -> set[str]:
    out: set[str] = set()
    while fid is not None and fid not in out:
        out.add(fid)
        r = conn.execute("SELECT parent_id FROM playlist_folders WHERE id = ?", (fid,)).fetchone()
        fid = r[0] if r else None
    return out


def _end_position(conn: sqlite3.Connection, uid: str, parent_id: str | None) -> float:
    r = conn.execute("SELECT MAX(position) FROM playlist_folders WHERE user_id = ? AND parent_id IS ?",
                     (uid, parent_id)).fetchone()
    return (r[0] or 0.0) + 1.0


def _folder_dict(r: sqlite3.Row, count: int) -> dict:
    return {"id": r["id"], "name": r["name"], "parentId": r["parent_id"], "createdAt": r["created_at"],
            "playlistCount": count}


def folder_of(conn: sqlite3.Connection, uid: str, pid: str) -> str | None:
    r = conn.execute("SELECT folder_id FROM playlist_folder_items WHERE user_id = ? AND playlist_id = ?",
                     (uid, pid)).fetchone()
    return r[0] if r else None


def list_folders(conn: sqlite3.Connection, uid: str) -> list[dict]:
    """The user's folders, siblings in their order (a parent always comes before its children)."""
    rows = conn.execute("SELECT * FROM playlist_folders WHERE user_id = ? ORDER BY position, name", (uid,)).fetchall()
    counts = {r[0]: r[1] for r in conn.execute(
        "SELECT fi.folder_id, COUNT(*) FROM playlist_folder_items fi JOIN playlists p ON p.id = fi.playlist_id "
        "WHERE fi.user_id = ? AND (p.user_id = ? OR EXISTS (SELECT 1 FROM playlist_members m "
        "WHERE m.playlist_id = p.id AND m.user_id = ?)) GROUP BY fi.folder_id", (uid, uid, uid))}
    children: dict[str | None, list[sqlite3.Row]] = {}
    for r in rows:
        children.setdefault(r["parent_id"], []).append(r)
    out: list[dict] = []

    def walk(parent: str | None) -> None:
        for r in children.get(parent, []):
            out.append(_folder_dict(r, counts.get(r["id"], 0)))
            walk(r["id"])

    walk(None)
    return out


def get_folder(conn: sqlite3.Connection, uid: str, fid: str) -> dict:
    _row(conn, uid, fid)
    return next(f for f in list_folders(conn, uid) if f["id"] == fid)


def _check_parent(conn: sqlite3.Connection, uid: str, parent_id: str | None, height: int = 1) -> None:
    if parent_id is None:
        return
    try:
        _row(conn, uid, parent_id)
    except NotFound as e:
        raise BadRequest("no such parent folder") from e
    if _depth(conn, parent_id) + height > MAX_DEPTH:
        raise BadRequest(f"folders nest at most {MAX_DEPTH} deep")


def create_folder(conn: sqlite3.Connection, uid: str, name: str, parent_id: str | None = None) -> dict:
    name = name.strip()[:200]
    if not name:
        raise BadRequest("name is required")
    fid = uuid.uuid4().hex
    with _tx(conn):
        if conn.execute("SELECT COUNT(*) FROM playlist_folders WHERE user_id = ?", (uid,)).fetchone()[0] >= MAX_FOLDERS:
            raise BadRequest(f"at most {MAX_FOLDERS} folders")
        _check_parent(conn, uid, parent_id)
        conn.execute("INSERT INTO playlist_folders (id, user_id, parent_id, name, position, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (fid, uid, parent_id, name, _end_position(conn, uid, parent_id),
                                                   now_iso()))
    return get_folder(conn, uid, fid)


def update_folder(conn: sqlite3.Connection, uid: str, fid: str, *, name: str | None = None,
                  parent_id=UNSET, index: int | None = None) -> dict:
    """Rename, move into another folder (`parent_id`, None = top level) and/or reorder among its siblings
    (`index`, 0 = first; past the end = last)."""
    with _tx(conn):
        row = _row(conn, uid, fid)
        if name is not None:
            new = name.strip()[:200]
            if not new:
                raise BadRequest("name is required")
            conn.execute("UPDATE playlist_folders SET name = ? WHERE id = ?", (new, fid))
        parent = row["parent_id"]
        if parent_id is not UNSET and parent_id != parent:
            if parent_id is not None and fid in _ancestors(conn, parent_id):
                raise BadRequest("a folder cannot go inside itself")
            _check_parent(conn, uid, parent_id, _subtree_height(conn, fid))
            parent = parent_id
            conn.execute("UPDATE playlist_folders SET parent_id = ?, position = ? WHERE id = ?",
                         (parent, _end_position(conn, uid, parent), fid))
        if index is not None:
            sibs = [r[0] for r in conn.execute(
                "SELECT id FROM playlist_folders WHERE user_id = ? AND parent_id IS ? AND id != ? "
                "ORDER BY position, name", (uid, parent, fid))]
            sibs.insert(max(0, min(index, len(sibs))), fid)
            conn.executemany("UPDATE playlist_folders SET position = ? WHERE id = ?",
                             [(float(i + 1), s) for i, s in enumerate(sibs)])
    return get_folder(conn, uid, fid)


def delete_folder(conn: sqlite3.Connection, uid: str, fid: str) -> None:
    """Delete the folder; its playlists and subfolders move up to its parent."""
    with _tx(conn):
        row = _row(conn, uid, fid)
        parent = row["parent_id"]
        for (kid,) in conn.execute("SELECT id FROM playlist_folders WHERE parent_id = ? ORDER BY position, name",
                                   (fid,)).fetchall():
            conn.execute("UPDATE playlist_folders SET parent_id = ?, position = ? WHERE id = ?",
                         (parent, _end_position(conn, uid, parent), kid))
        if parent is None:
            conn.execute("DELETE FROM playlist_folder_items WHERE user_id = ? AND folder_id = ?", (uid, fid))
        else:
            conn.execute("UPDATE playlist_folder_items SET folder_id = ? WHERE user_id = ? AND folder_id = ?",
                         (parent, uid, fid))
        conn.execute("DELETE FROM playlist_folders WHERE id = ?", (fid,))


def set_playlist_folder(conn: sqlite3.Connection, uid: str, pid: str, folder_id: str | None) -> None:
    """File a playlist the user can see into one of their folders (None = top level)."""
    if not can_view(conn, uid, pid):
        raise NotFound("no such playlist")
    with _tx(conn):
        if folder_id is None:
            conn.execute("DELETE FROM playlist_folder_items WHERE user_id = ? AND playlist_id = ?", (uid, pid))
            return
        try:
            _row(conn, uid, folder_id)
        except NotFound as e:
            raise BadRequest("no such folder") from e
        conn.execute("INSERT INTO playlist_folder_items (user_id, playlist_id, folder_id) VALUES (?, ?, ?) "
                     "ON CONFLICT(user_id, playlist_id) DO UPDATE SET folder_id = excluded.folder_id",
                     (uid, pid, folder_id))
