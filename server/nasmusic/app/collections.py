"""Per-user Musicolet-style queues (max 20, numbered by slot); playlists live in nasmusic.app.playlists.

Queue positions are dense (0..n-1). Edits read the id list, change it in Python and rewrite the rows in one
transaction, which is simple and fast enough for lists of a few thousand tracks."""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from nasmusic.core import now_iso
from nasmusic.db import family
from nasmusic.library import queries

MAX_QUEUES = 20


class NotFound(Exception):
    pass


class BadRequest(Exception):
    pass


class Forbidden(Exception):
    pass


class Conflict(Exception):
    """The client's view is stale (an entry it referred to was moved/removed by someone else)."""


@contextmanager
def _tx(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _check_tracks(conn: sqlite3.Connection, ids: list[str]) -> None:
    uniq = list(set(ids))
    found = 0
    for i in range(0, len(uniq), 500):
        chunk = uniq[i:i + 500]
        found += conn.execute(f"SELECT COUNT(*) FROM tracks WHERE id IN ({','.join('?' * len(chunk))})",
                              chunk).fetchone()[0]
    if found != len(uniq):
        raise NotFound("unknown track id")


def _move(items: list, frm: int, to: int) -> list:
    if not (0 <= frm < len(items)) or not (0 <= to < len(items)):
        raise BadRequest("position out of range")
    items = list(items)
    items.insert(to, items.pop(frm))
    return items


def _tracks(conn: sqlite3.Connection, uid: str, ids: list[str]) -> list[dict]:
    by_id = queries.tracks_by_ids(conn, uid, ids, include_missing=True, hidden="flag")
    return [by_id[i] for i in ids if i in by_id]


def _family_keep(conn: sqlite3.Connection, uid: str, ids: list[str], index: int | None) -> tuple[list[str], int | None]:
    """Family mode: drop tracks the user may not see from a list being written to a queue, keeping `index`
    on the same track (or the next allowed one). Tracks already in a queue stay, flagged familyHidden."""
    hidden = family.hidden_among(conn, uid, ids)
    if not hidden:
        return ids, index
    kept, new_index = [], None
    for n, t in enumerate(ids):
        if t in hidden:
            continue
        if index is not None and new_index is None and n >= index:
            new_index = len(kept)
        kept.append(t)
    if index is not None and new_index is None:
        new_index = max(len(kept) - 1, 0)
    return kept, new_index


# ---------- queues ----------


def _queue_row(conn: sqlite3.Connection, uid: str, qid: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM queues WHERE id = ? AND user_id = ?", (qid, uid)).fetchone()
    if row is None:
        raise NotFound("no such queue")
    return row


def _queue_ids(conn: sqlite3.Connection, qid: str) -> list[str]:
    return [r[0] for r in conn.execute("SELECT track_id FROM queue_tracks WHERE queue_id = ? ORDER BY position",
                                       (qid,))]


def _queue_summary(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    s = conn.execute("SELECT COUNT(*), COALESCE(SUM(t.duration_s), 0) FROM queue_tracks q "
                     "JOIN tracks t ON t.id = q.track_id WHERE q.queue_id = ?", (row["id"],)).fetchone()
    radio = None
    if row["radio_seed"]:
        try:
            radio = json.loads(row["radio_seed"])
        except ValueError:
            radio = None
    return {"id": row["id"], "name": row["name"], "slot": row["slot"], "currentIndex": row["current_index"],
            "positionMs": row["position_ms"], "trackCount": s[0], "durationS": round(s[1], 3),
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            "source": {"type": row["source_type"], "id": row["source_id"]} if row["source_type"] else None,
            "radio": radio}


def list_queues(conn: sqlite3.Connection, uid: str) -> list[dict]:
    rows = conn.execute("SELECT * FROM queues WHERE user_id = ? ORDER BY slot", (uid,)).fetchall()
    return [_queue_summary(conn, r) for r in rows]


def get_queue(conn: sqlite3.Connection, uid: str, qid: str) -> dict:
    row = _queue_row(conn, uid, qid)
    return {**_queue_summary(conn, row), "tracks": _tracks(conn, uid, _queue_ids(conn, qid))}


def _write_queue(conn: sqlite3.Connection, qid: str, ids: list[str], current: int, position_ms: int | None = None
                 ) -> None:
    conn.execute("DELETE FROM queue_tracks WHERE queue_id = ?", (qid,))
    conn.executemany("INSERT INTO queue_tracks (queue_id, position, track_id) VALUES (?, ?, ?)",
                     [(qid, i, t) for i, t in enumerate(ids)])
    current = max(0, min(current, max(len(ids) - 1, 0)))
    if position_ms is None:
        conn.execute("UPDATE queues SET current_index = ?, updated_at = ? WHERE id = ?", (current, now_iso(), qid))
    else:
        conn.execute("UPDATE queues SET current_index = ?, position_ms = ?, updated_at = ? WHERE id = ?",
                     (current, position_ms, now_iso(), qid))


def create_queue(conn: sqlite3.Connection, uid: str, name: str, track_ids: list[str], start_index: int = 0,
                 protect: str | None = None, replace_same_name: bool = False,
                 source: tuple[str, str | None] | None = None, radio_seed: dict | None = None,
                 radio_id: str | None = None) -> dict:
    """New queue in the lowest free slot. With 20 already, the least recently updated queue (never `protect`,
    the one playing) is replaced and its slot reused. `replace_same_name` reuses a queue with that name."""
    _check_tracks(conn, track_ids)
    track_ids, start_index = _family_keep(conn, uid, list(track_ids), start_index)
    name = name.strip()[:200] or "Queue"
    now = now_iso()
    with _tx(conn):
        existing = conn.execute("SELECT * FROM queues WHERE user_id = ? ORDER BY slot", (uid,)).fetchall()
        same = next((r for r in existing if r["name"] == name), None) if replace_same_name else None
        if same is not None:
            qid = same["id"]
            conn.execute("UPDATE queues SET position_ms = 0 WHERE id = ?", (qid,))
        else:
            used = {r["slot"] for r in existing}
            free = [s for s in range(1, MAX_QUEUES + 1) if s not in used]
            if free:
                slot = free[0]
            else:
                victims = sorted((r for r in existing if r["id"] != protect), key=lambda r: r["updated_at"])
                slot = victims[0]["slot"]
                conn.execute("DELETE FROM queues WHERE id = ?", (victims[0]["id"],))
            qid = uuid.uuid4().hex
            conn.execute("INSERT INTO queues (id, user_id, name, slot, created_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (qid, uid, name, slot, now, now))
        _write_queue(conn, qid, track_ids, start_index, 0)
        conn.execute("UPDATE queues SET source_type = ?, source_id = ?, radio_seed = ?, radio_id = ? WHERE id = ?",
                     (source[0] if source else None, source[1] if source else None,
                      json.dumps(radio_seed) if radio_seed else None, radio_id, qid))
    return get_queue(conn, uid, qid)


def replace_queue_tracks(conn: sqlite3.Connection, uid: str, qid: str, track_ids: list[str],
                         current_index: int | None = None) -> dict:
    """Replace every track. currentIndex: given, else the current track's new position, else 0."""
    _queue_row(conn, uid, qid)
    _check_tracks(conn, track_ids)
    if current_index is not None and track_ids and not 0 <= current_index < len(track_ids):
        raise BadRequest("currentIndex out of range")
    track_ids, current_index = _family_keep(conn, uid, list(track_ids), current_index)
    with _tx(conn):
        row = _queue_row(conn, uid, qid)
        old = _queue_ids(conn, qid)
        if current_index is None:
            cur_track = old[row["current_index"]] if 0 <= row["current_index"] < len(old) else None
            current_index = track_ids.index(cur_track) if cur_track in track_ids else 0
        elif track_ids and not 0 <= current_index < len(track_ids):
            raise BadRequest("currentIndex out of range")
        _write_queue(conn, qid, track_ids, current_index)
    return get_queue(conn, uid, qid)


def queue_track_ids(conn: sqlite3.Connection, uid: str, qid: str) -> list[str]:
    _queue_row(conn, uid, qid)
    return _queue_ids(conn, qid)


def queue_row(conn: sqlite3.Connection, uid: str, qid: str) -> sqlite3.Row:
    return _queue_row(conn, uid, qid)


def patch_queue(conn: sqlite3.Connection, uid: str, qid: str, name: str | None = None,
                current_index: int | None = None, position_ms: int | None = None) -> dict:
    row = _queue_row(conn, uid, qid)
    n = conn.execute("SELECT COUNT(*) FROM queue_tracks WHERE queue_id = ?", (qid,)).fetchone()[0]
    if current_index is not None and n and not 0 <= current_index < n:
        raise BadRequest("currentIndex out of range")
    conn.execute(
        "UPDATE queues SET name = ?, current_index = ?, position_ms = ?, updated_at = ? WHERE id = ?",
        ((name.strip()[:200] or row["name"]) if name is not None else row["name"],
         row["current_index"] if current_index is None else current_index,
         row["position_ms"] if position_ms is None else position_ms, now_iso(), qid))
    return get_queue(conn, uid, qid)


def delete_queue(conn: sqlite3.Connection, uid: str, qid: str) -> None:
    if conn.execute("DELETE FROM queues WHERE id = ? AND user_id = ?", (qid, uid)).rowcount != 1:
        raise NotFound("no such queue")


def edit_queue(conn: sqlite3.Connection, uid: str, qid: str, *, add: list[str] | None = None,
               position: int | str | None = "end", remove: int | None = None,
               move: tuple[int, int] | None = None) -> dict:
    """Insert / remove / move, keeping currentIndex on the same (current) track where possible."""
    _queue_row(conn, uid, qid)
    if add:
        _check_tracks(conn, add)
        add = family.filter_ids(conn, uid, add)  # family mode (also covers Jam guest adds to a host's queue)
    with _tx(conn):
        row = _queue_row(conn, uid, qid)
        ids = _queue_ids(conn, qid)
        cur = row["current_index"]
        if add is not None:
            if position == "next":
                at = min(cur + 1, len(ids))
            elif position in (None, "end"):
                at = len(ids)
            elif isinstance(position, int):
                at = max(0, min(position, len(ids)))
            else:
                raise BadRequest('position must be an index, "next" or "end"')
            ids[at:at] = add
            if ids and at <= cur and len(ids) > len(add):
                cur += len(add)
        if remove is not None:
            if not 0 <= remove < len(ids):
                raise BadRequest("position out of range")
            ids.pop(remove)
            if remove < cur:
                cur -= 1
        if move is not None:
            frm, to = move
            ids = _move(ids, frm, to)
            if frm == cur:
                cur = to
            elif frm < cur <= to:
                cur -= 1
            elif to <= cur < frm:
                cur += 1
        _write_queue(conn, qid, ids, cur)
    return get_queue(conn, uid, qid)


def queue_to_playlist(conn: sqlite3.Connection, uid: str, qid: str, name: str) -> dict:
    _queue_row(conn, uid, qid)
    from nasmusic.app import playlists

    return playlists.create_playlist(conn, uid, name, _queue_ids(conn, qid))


def queue_track_at(conn: sqlite3.Connection, uid: str, qid: str, index: int) -> str | None:
    row = conn.execute("SELECT q.track_id FROM queue_tracks q JOIN queues u ON u.id = q.queue_id "
                       "WHERE q.queue_id = ? AND u.user_id = ? AND q.position = ?", (qid, uid, index)).fetchone()
    return row[0] if row else None
