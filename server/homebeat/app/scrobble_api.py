"""v7 endpoints: /me/scrobble/* (ListenBrainz + Last.fm), /me/notifications, /library/new."""
from __future__ import annotations

import logging
import sqlite3
import threading
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from homebeat.app import notify, scrobble
from homebeat.app.context import AppContext, ctx, db, device
from homebeat.auth import Device
from homebeat.core import get_setting, now_iso, open_db, set_setting

log = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(device)])
Conn = sqlite3.Connection
Service = Literal["listenbrainz", "lastfm"]
_imports: set[str] = set()
_imports_lock = threading.Lock()


def _transport(c: AppContext):
    return c.http_transport if c.http_transport is not None else (c.scrobbler.transport if c.scrobbler else None)


def _client(c: AppContext) -> httpx.Client:
    return scrobble._client(_transport(c))


def _import_key(uid: str) -> str:
    return f"scrobble.import.{uid}"


def _status(conn: Conn, c: AppContext, uid: str) -> dict:
    out = scrobble.status(conn, c.cfg, uid)
    out["import"] = get_setting(conn, _import_key(uid)) or None
    out["importedEvents"] = scrobble.imported_count(conn, uid)
    return out


def _http(e: scrobble.ScrobbleError, what: str):
    if e.kind == "auth":
        raise HTTPException(400, f"{what}: {e}")
    raise HTTPException(502, f"{what}: {e}")


# ---------- scrobbling ----------


@router.get("/me/scrobble")
def get_scrobble(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _status(conn, c, me.user_id)


class ScrobbleSettings(BaseModel):
    enabled: bool | None = None
    nowPlaying: bool | None = None


@router.put("/me/scrobble/{service}")
def put_scrobble(service: Service, body: ScrobbleSettings, me: Device = Depends(device), conn: Conn = Depends(db),
                 c: AppContext = Depends(ctx)):
    a = scrobble.account(conn, me.user_id, service)
    if not a or not a["token"]:
        raise HTTPException(409, f"{service} is not connected")
    cols = {}
    if body.enabled is not None:
        cols["enabled"] = int(body.enabled)
    if body.nowPlaying is not None:
        cols["now_playing"] = int(body.nowPlaying)
    if cols:
        scrobble._upsert(conn, me.user_id, service, **cols)
    return _status(conn, c, me.user_id)


@router.delete("/me/scrobble/{service}")
def delete_scrobble(service: Service, me: Device = Depends(device), conn: Conn = Depends(db),
                    c: AppContext = Depends(ctx)):
    scrobble.disconnect(conn, me.user_id, service)
    return _status(conn, c, me.user_id)


class LbConnect(BaseModel):
    token: str = Field(min_length=8, max_length=200)


@router.post("/me/scrobble/listenbrainz/connect")
def connect_listenbrainz(body: LbConnect, me: Device = Depends(device), conn: Conn = Depends(db),
                         c: AppContext = Depends(ctx)):
    token = body.token.strip()
    try:
        with _client(c) as client:
            name = scrobble.lb_validate(client, token)
    except scrobble.ScrobbleError as e:
        _http(e, "ListenBrainz")
    scrobble.connect_account(conn, me.user_id, "listenbrainz", token, name)
    if c.scrobbler is not None:
        c.scrobbler.kick()   # anything queued while it needed a reconnect
    return _status(conn, c, me.user_id)


@router.post("/me/scrobble/lastfm/connect")
def connect_lastfm(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Step 1: a request token; the user opens `authUrl`, approves, then calls /complete."""
    if not scrobble.lastfm_available(c.cfg):
        raise HTTPException(503, "Last.fm is not set up on this server (lastfm_api_key / lastfm_api_secret)")
    try:
        with _client(c) as client:
            token, url = scrobble.lastfm_start(client, c.cfg)
    except scrobble.ScrobbleError as e:
        _http(e, "Last.fm")
    scrobble._upsert(conn, me.user_id, "lastfm", pending_token=token, pending_at=now_iso())
    return {"authUrl": url}


@router.post("/me/scrobble/lastfm/complete")
def complete_lastfm(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Step 2: exchange the approved token for a session. 409 while the user hasn't approved it yet."""
    if not scrobble.lastfm_available(c.cfg):
        raise HTTPException(503, "Last.fm is not set up on this server")
    a = scrobble.account(conn, me.user_id, "lastfm")
    if not a or not a["pending_token"]:
        raise HTTPException(409, "start with POST /me/scrobble/lastfm/connect")
    try:
        with _client(c) as client:
            sk, name = scrobble.lastfm_session(client, c.cfg, a["pending_token"])
    except scrobble.ScrobbleError as e:
        if e.code == 14:
            raise HTTPException(409, "not approved on last.fm yet") from e
        if e.code in (4, 15):
            scrobble._upsert(conn, me.user_id, "lastfm", pending_token=None, pending_at=None)
            raise HTTPException(410, "the Last.fm request expired; connect again") from e
        _http(e, "Last.fm")
    scrobble.connect_account(conn, me.user_id, "lastfm", sk, name)
    if c.scrobbler is not None:
        c.scrobbler.kick()
    return _status(conn, c, me.user_id)


@router.post("/me/scrobble/retry")
def retry_scrobbles(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Send failed scrobbles again (and anything due) now."""
    scrobble.retry_failed(conn, me.user_id)
    conn.execute("UPDATE scrobble_queue SET next_attempt_at = ? WHERE user_id = ? AND status = 'pending'",
                 (now_iso(), me.user_id))
    if c.scrobbler is not None:
        c.scrobbler.kick()
    return _status(conn, c, me.user_id)


class ImportBody(BaseModel):
    maxListens: int = Field(default=scrobble.IMPORT_MAX, ge=1, le=scrobble.IMPORT_MAX)


def _run_import(c: AppContext, uid: str, max_listens: int) -> None:
    conn = open_db(c.cfg)
    started = now_iso()
    try:
        def progress(res):
            set_setting(conn, _import_key(uid), {"state": "running", "startedAt": started, **res})

        res = scrobble.import_listenbrainz(conn, uid, _transport(c), max_listens, progress)
        set_setting(conn, _import_key(uid), {"state": "done", "startedAt": started, "finishedAt": now_iso(), **res})
    except Exception as e:
        log.warning("ListenBrainz import failed: %s", e)
        prev = get_setting(conn, _import_key(uid)) or {}
        set_setting(conn, _import_key(uid), {**prev, "state": "error", "startedAt": started,
                                             "finishedAt": now_iso(), "error": str(e)[:300]})
    finally:
        conn.close()
        with _imports_lock:
            _imports.discard(uid)


@router.post("/me/scrobble/listenbrainz/import", status_code=202)
def import_history(body: ImportBody | None = None, wait: bool = Query(False), me: Device = Depends(device),
                   conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Import ListenBrainz listen history (tracks in this library only) as recommender signals. Runs in the
    background; progress in GET /me/scrobble `import`. `wait=true` runs it inline (tests, small histories)."""
    a = scrobble.account(conn, me.user_id, "listenbrainz")
    if not a or not a["token"]:
        raise HTTPException(409, "connect ListenBrainz first")
    with _imports_lock:
        if me.user_id in _imports:
            raise HTTPException(409, "an import is already running")
        _imports.add(me.user_id)
    set_setting(conn, _import_key(me.user_id), {"state": "running", "startedAt": now_iso()})
    n = (body or ImportBody()).maxListens
    if wait:
        _run_import(c, me.user_id, n)
    else:
        threading.Thread(target=_run_import, args=(c, me.user_id, n), name="lb-import", daemon=True).start()
    return _status(conn, c, me.user_id)


@router.delete("/me/scrobble/listenbrainz/import")
def remove_import(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Remove every imported listen (and the play counts they added)."""
    with _imports_lock:
        if me.user_id in _imports:
            raise HTTPException(409, "an import is running")
    removed = scrobble.remove_imported(conn, me.user_id)
    conn.execute("DELETE FROM settings WHERE key = ?", (_import_key(me.user_id),))
    return {"removed": removed, **_status(conn, c, me.user_id)}


# ---------- notifications ----------


@router.get("/me/notifications")
def get_notifications(limit: int = Query(50, ge=1, le=200), since: str | None = Query(None, max_length=40),
                      unread: bool = Query(False), me: Device = Depends(device), conn: Conn = Depends(db)):
    return notify.listing(conn, me.user_id, limit, since, unread)


class ReadBody(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=500)


@router.post("/me/notifications/read")
def read_notifications(body: ReadBody, me: Device = Depends(device), conn: Conn = Depends(db)):
    """Mark notifications read: `ids`, or all when omitted."""
    n = notify.mark_read(conn, me.user_id, body.ids)
    return {"marked": n, "unread": notify.listing(conn, me.user_id, 1)["unread"]}


@router.get("/library/new")
def library_new(days: int = Query(7, ge=1, le=90), limit: int = Query(100, ge=1, le=500),
                me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """"New this week": albums added in the last `days`, then songs added to existing albums."""
    return notify.new_this_week(conn, me.user_id, c.data_dir, days, limit)
