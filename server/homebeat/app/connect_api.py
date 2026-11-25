"""WebSocket /api/v1/connect (docs/v2.md §5) and the Hub's database callbacks."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, WebSocket

from homebeat import auth
from homebeat.app import collections as coll
from homebeat.app.context import SESSION_COOKIE, AppContext, origin_allowed
from homebeat.config import Config
from homebeat.connect import Hub, state_from_json
from homebeat.core import now_iso, open_db

router = APIRouter()


def make_hub(cfg: Config, **kw) -> Hub:
    def load(uid: str) -> dict | None:
        conn = open_db(cfg)
        try:
            row = conn.execute("SELECT state FROM playback_state WHERE user_id = ?", (uid,)).fetchone()
            return state_from_json(row[0]) if row else None
        finally:
            conn.close()

    def save(uid: str, state: dict) -> None:
        conn = open_db(cfg)
        try:
            conn.execute("INSERT INTO playback_state (user_id, state, updated_at) VALUES (?, ?, ?) "
                         "ON CONFLICT(user_id) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at",
                         (uid, json.dumps(state), now_iso()))
            media = state.get("media") if isinstance(state.get("media"), dict) else None
            if state.get("queueId") and not media:
                conn.execute("UPDATE queues SET current_index = ?, position_ms = ?, updated_at = ? "
                             "WHERE id = ? AND user_id = ?",
                             (int(state.get("index") or 0), int(state.get("positionMs") or 0), now_iso(),
                              state["queueId"], uid))
            elif media and media.get("kind") in ("episode", "book"):
                # Resume point on the server too (a speaker can't write /spoken/progress itself; clients also do).
                _spoken_progress(conn, uid, state, media)
        finally:
            conn.close()

    def queue_resume(uid: str, qid: str) -> tuple[int, int] | None:
        conn = open_db(cfg)
        try:
            r = conn.execute("SELECT current_index, position_ms FROM queues WHERE id = ? AND user_id = ?",
                             (qid, uid)).fetchone()
            return (r[0], r[1]) if r else None
        finally:
            conn.close()

    def make_queue(uid: str, track_ids: list[str], name: str, index: int) -> str:
        conn = open_db(cfg)
        try:
            return coll.create_queue(conn, uid, name, track_ids, index, replace_same_name=True)["id"]
        finally:
            conn.close()

    def queue_track(uid: str, qid: str, index: int) -> str | None:
        conn = open_db(cfg)
        try:
            return coll.queue_track_at(conn, uid, qid, index)
        finally:
            conn.close()

    kw.setdefault("queue_resume", queue_resume)
    return Hub(load, save, make_queue, queue_track, **kw)


def _spoken_progress(conn, uid: str, state: dict, media: dict) -> None:
    from homebeat.spoken import progress as prog

    kind, ident = media["kind"], media["id"]
    table, col = ("podcast_episodes", "id") if kind == "episode" else ("audiobooks", "id")
    if conn.execute(f"SELECT 1 FROM {table} WHERE {col} = ?", (ident,)).fetchone() is None:
        return
    pos_s = round(int(state.get("positionMs") or 0) / 1000, 1)
    if pos_s <= 0:
        return
    prog.put(conn, uid, state.get("activeDeviceId"), kind, ident, position_s=pos_s,
             updated_at=state.get("positionAt"))


def _token(ws: WebSocket) -> str | None:
    header = ws.headers.get("authorization", "")
    scheme, _, tok = header.partition(" ")
    if scheme.lower() == "bearer" and tok.strip():
        return tok.strip()
    return ws.query_params.get("token") or ws.cookies.get(SESSION_COOKIE) or None


@router.websocket("/connect")
async def connect(ws: WebSocket):
    c: AppContext = ws.app.state.ctx
    token = _token(ws)
    if token and token == ws.cookies.get(SESSION_COOKIE) and not origin_allowed(ws.headers, c.cfg):
        token = None  # the cookie on a handshake from another site's page (cross-site WebSocket hijacking)

    def resolve():
        conn = open_db(c.cfg)
        try:
            return auth.device_for_token(conn, token)
        finally:
            conn.close()

    dev = await asyncio.to_thread(resolve) if token else None
    await ws.accept()
    if dev is None:
        await ws.close(code=4401, reason="invalid or missing token")
        return
    await c.hub.handle(ws, dev)
