"""Continue listening (v8): one list of what the user is in the middle of, newest first, across devices.

Mixes the music queues left part-way (a playlist, album, mix or radio: resume at the same track and position)
with podcast episodes and audiobooks in progress (spoken_progress). Items are ShelfItems (type queue, episode or
book) with `progress` and `updatedAt`, so the Home shelf and GET /continue share them. The user can dismiss an
item; it comes back once it is played again after that (its updated_at is newer than dismissed_at).

  GET    /continue                    the list
  DELETE /continue/{type}/{id}        dismiss one item
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from nasmusic.app.context import AppContext, ctx, db, device
from nasmusic.auth import Device
from nasmusic.core import iso

router = APIRouter(dependencies=[Depends(device)])

CONTINUE_DAYS = 30
MAX_QUEUES = 10


def _dismissed(conn: sqlite3.Connection, uid: str) -> dict[tuple[str, str], str]:
    return {(r["kind"], r["item_id"]): r["dismissed_at"]
            for r in conn.execute("SELECT kind, item_id, dismissed_at FROM continue_dismissed WHERE user_id = ?",
                                  (uid,))}


def _hidden(dismissed: dict, kind: str, ident: str, updated_at: str | None) -> bool:
    at = dismissed.get((kind, ident))
    return at is not None and _ts(updated_at) <= _ts(at)


def queue_items(conn: sqlite3.Connection, data_dir, uid: str, playing_queue: str | None = None,
                dismissed: dict | None = None) -> list[dict]:
    """Music queues left part-way, newest first. A radio counts once you've listened into it; the queue that is
    playing right now (on any device) is left out — it isn't something to get back to."""
    from nasmusic.app.discover import _queue_item

    dismissed = _dismissed(conn, uid) if dismissed is None else dismissed
    since = iso(datetime.now(UTC) - timedelta(days=CONTINUE_DAYS))
    out = []
    for r in conn.execute(
        "SELECT q.id, q.current_index, q.position_ms, q.updated_at, q.radio_seed, "
        "(SELECT COUNT(*) FROM queue_tracks x WHERE x.queue_id = q.id) n "
        "FROM queues q WHERE q.user_id = ? AND q.updated_at >= ? ORDER BY q.updated_at DESC LIMIT 40", (uid, since)
    ):
        started = r["current_index"] > 0 or r["position_ms"] > 0
        if not started or r["id"] == playing_queue or _hidden(dismissed, "queue", r["id"], r["updated_at"]):
            continue
        if r["radio_seed"] is None and (r["n"] < 1 or r["current_index"] >= r["n"] - 1 and r["n"] > 1):
            continue   # finished (on the last track of a list)
        item = _queue_item(conn, data_dir, uid, r["id"], progress=True)
        if item:
            item["updatedAt"] = r["updated_at"]
            out.append(item)
        if len(out) >= MAX_QUEUES:
            break
    return out


def spoken_items(conn: sqlite3.Connection, uid: str, limit: int = 20, dismissed: dict | None = None) -> list[dict]:
    """Podcast episodes and audiobooks in progress (not played to the end), newest first."""
    from nasmusic.spoken import audiobooks, podcasts
    from nasmusic.spoken import progress as prog

    dismissed = _dismissed(conn, uid) if dismissed is None else dismissed
    rows = conn.execute("SELECT * FROM spoken_progress WHERE user_id = ? AND played = 0 AND position_s > 5 "
                        "ORDER BY updated_at DESC LIMIT ?", (uid, limit * 2)).fetchall()
    out = []
    for r in rows:
        if _hidden(dismissed, r["kind"], r["item_id"], r["updated_at"]):
            continue
        pr = prog.as_api(r)
        dur = r["duration_s"]
        progress = {"positionMs": int(r["position_s"] * 1000),
                    "durationMs": int(dur * 1000) if dur else None,
                    "fraction": round(min(1.0, r["position_s"] / dur), 4) if dur else None}
        if r["kind"] == "episode":
            e = podcasts.episode_row(conn, uid, r["item_id"])
            if e is None:
                continue
            ep = podcasts.episode_api(e, pr, {"title": e["ptitle"]})
            out.append({"type": "episode", "id": e["id"], "title": e["title"], "subtitle": e["ptitle"],
                        "artUrl": f"art/podcast/{e['podcast_id']}", "progress": progress,
                        "updatedAt": r["updated_at"], "episode": ep})
        else:
            b = conn.execute("SELECT * FROM audiobooks WHERE id = ? AND missing = 0", (r["item_id"],)).fetchone()
            if b is None:
                continue
            out.append({"type": "book", "id": b["id"], "title": b["title"], "subtitle": b["author"],
                        "artUrl": f"art/book/{b['id']}", "progress": progress, "updatedAt": r["updated_at"],
                        "book": audiobooks.book_api(b, pr)})
        if len(out) >= limit:
            break
    return out


def items(conn: sqlite3.Connection, data_dir, uid: str, playing_queue: str | None = None,
          limit: int = 20) -> list[dict]:
    """Queues and spoken items together, most recently played first."""
    dismissed = _dismissed(conn, uid)
    merged = queue_items(conn, data_dir, uid, playing_queue, dismissed) + spoken_items(conn, uid, limit, dismissed)
    merged.sort(key=lambda i: _ts(i.get("updatedAt")), reverse=True)
    return merged[:limit]


def _ts(v: str | None) -> float:
    """Timestamps come in two shapes (second and millisecond precision): compare them as times."""
    if not v:
        return 0.0
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def playing_queue(c: AppContext, uid: str) -> str | None:
    """The music queue playing right now on one of the user's devices (paused doesn't count)."""
    st = c.hub.snapshot(uid) if c.hub else None
    if not st or not st.get("playing") or st.get("media"):
        return None
    return st.get("queueId")


@router.get("/continue")
def get_continue(limit: int = Query(20, ge=1, le=50), me: Device = Depends(device),
                 conn: sqlite3.Connection = Depends(db), c: AppContext = Depends(ctx)):
    return {"items": items(conn, c.cfg.data_dir, me.user_id, playing_queue(c, me.user_id), limit)}


@router.delete("/continue/{kind}/{ident}", status_code=204)
def dismiss(kind: Literal["queue", "episode", "book"], ident: str, me: Device = Depends(device),
            conn: sqlite3.Connection = Depends(db)):
    if len(ident) > 128:
        raise HTTPException(404, "not found")
    conn.execute("INSERT INTO continue_dismissed (user_id, kind, item_id, dismissed_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(user_id, kind, item_id) DO UPDATE SET dismissed_at = excluded.dismissed_at",
                 (me.user_id, kind, ident, _now_ms()))


def _now_ms() -> str:
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
