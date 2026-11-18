"""WebSocket /api/v1/connect (docs/v2.md §5) and the Hub's database callbacks."""
from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket

from nasmusic import auth
from nasmusic.app import collections as coll
from nasmusic.app.context import SESSION_COOKIE, AppContext
from nasmusic.config import Config
from nasmusic.connect import Hub, state_from_json
from nasmusic.core import now_iso, open_db

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
            if state.get("queueId"):
                conn.execute("UPDATE queues SET current_index = ?, position_ms = ? WHERE id = ? AND user_id = ?",
                             (int(state.get("index") or 0), int(state.get("positionMs") or 0), state["queueId"], uid))
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

    return Hub(load, save, make_queue, queue_track, **kw)


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
    dev = None
    if token:
        conn = open_db(c.cfg)
        try:
            dev = auth.device_for_token(conn, token)
        finally:
            conn.close()
    await ws.accept()
    if dev is None:
        await ws.close(code=4401, reason="invalid or missing token")
        return
    await c.hub.handle(ws, dev)
