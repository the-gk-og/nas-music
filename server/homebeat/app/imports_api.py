"""Imports from other services and M3U export (logic in homebeat.app.imports).

GET    /me/import                      - imported listens per source, the Last.fm import job, availability
POST   /me/import/spotify              - raw body: my_spotify_data.zip, a history/Playlist JSON, or Exportify CSV
POST   /me/import/lastfm               - {username?, maxScrobbles?}: Last.fm history by user name (background)
POST   /me/import/lastfm/csv           - raw body: a Last.fm scrobbles CSV
POST   /me/import/m3u                  - raw body: an .m3u/.m3u8 -> new playlist
DELETE /me/import/history/{source}     - remove every listen imported from listenbrainz | lastfm | spotify
GET    /playlists/{pid}/m3u            - the playlist as .m3u8
"""
from __future__ import annotations

import logging
import sqlite3
import tempfile
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from homebeat.app import collections as coll
from homebeat.app import imports, scrobble
from homebeat.app.context import AppContext, ctx, db, device
from homebeat.app.scrobble_api import _transport
from homebeat.auth import Device
from homebeat.core import get_setting, now_iso, open_db, set_setting

log = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(device)])
Conn = sqlite3.Connection
Source = Literal["listenbrainz", "lastfm", "spotify"]
MAX_SPOTIFY = 512 * 1024 * 1024    # matches LARGE_BODY_PATHS in homebeat.app
MAX_SMALL = 20 * 1024 * 1024
_running: set[str] = set()
_lock = threading.Lock()


def _job_key(uid: str) -> str:
    return f"import.lastfm.{uid}"


def _status(conn: Conn, c: AppContext, uid: str) -> dict:
    a = scrobble.account(conn, uid, "lastfm")
    return {
        "imported": imports.imported_by_source(conn, uid),
        "lastfm": get_setting(conn, _job_key(uid)) or None,
        "lastfmAvailable": bool(c.cfg.lastfm_api_key),
        "lastfmUsername": (a["username"] if a else None) or None,
    }


async def _body(request: Request, limit: int):
    """The raw request body, spooled to a temp file (kept in memory up to 16 MiB)."""
    f = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    n = 0
    async for part in request.stream():
        n += len(part)
        if n > limit:
            f.close()
            raise HTTPException(413, f"file larger than {limit // (1024 * 1024)} MB")
        f.write(part)
    if n == 0:
        f.close()
        raise HTTPException(422, "empty file")
    f.seek(0)
    return f


def _run(fn, *a, **k):
    try:
        return fn(*a, **k)
    except imports.BadImport as e:
        raise HTTPException(e.status, e.detail) from e
    except coll.NotFound as e:
        raise HTTPException(404, str(e)) from e


@router.get("/me/import")
def get_imports(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _status(conn, c, me.user_id)


@router.post("/me/import/spotify")
async def import_spotify(request: Request, name: str = Query("", max_length=300),
                         addMissing: bool = Query(True), missingMinPlays: int = Query(0, ge=0, le=100_000),
                         me: Device = Depends(device), conn: Conn = Depends(db)):
    """One file per request; re-sending a file is harmless (history is idempotent, an identical playlist is
    not created twice)."""
    f = await _body(request, MAX_SPOTIFY)
    try:
        return await run_in_threadpool(_run, imports.import_spotify, conn, me.user_id, name or "upload", f,
                                       add_missing=addMissing, missing_min_plays=missingMinPlays)
    finally:
        f.close()


@router.post("/me/import/lastfm/csv")
async def import_lastfm_csv(request: Request, missingMinPlays: int = Query(0, ge=0, le=100_000),
                            me: Device = Depends(device), conn: Conn = Depends(db)):
    f = await _body(request, MAX_SMALL)
    try:
        data = f.read()
    finally:
        f.close()
    return await run_in_threadpool(_run, imports.import_lastfm_csv, conn, me.user_id, data,
                                   add_missing_min_plays=missingMinPlays)


class LastfmBody(BaseModel):
    username: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.\-]+$")
    maxScrobbles: int = Field(default=imports.LASTFM_MAX, ge=1, le=imports.LASTFM_MAX)


def _run_lastfm(c: AppContext, uid: str, username: str, max_scrobbles: int) -> None:
    conn = open_db(c.cfg)
    started = now_iso()
    try:
        def progress(res):
            set_setting(conn, _job_key(uid), {"state": "running", "startedAt": started, **res})

        res = imports.import_lastfm(conn, uid, c.cfg, username, _transport(c), max_scrobbles, progress)
        set_setting(conn, _job_key(uid), {"state": "done", "startedAt": started, "finishedAt": now_iso(), **res})
    except Exception as e:
        log.warning("Last.fm import failed: %s", e)
        prev = get_setting(conn, _job_key(uid)) or {}
        set_setting(conn, _job_key(uid), {**prev, "state": "error", "startedAt": started, "username": username,
                                          "finishedAt": now_iso(), "error": str(e)[:300]})
    finally:
        conn.close()
        with _lock:
            _running.discard(uid)


@router.post("/me/import/lastfm", status_code=202)
def import_lastfm(body: LastfmBody | None = None, wait: bool = Query(False), me: Device = Depends(device),
                  conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Import Last.fm scrobbles (tracks in this library only) by user name; defaults to the connected
    account. Runs in the background; progress in GET /me/import `lastfm`."""
    if not c.cfg.lastfm_api_key:
        raise HTTPException(503, "Last.fm is not set up on this server; import a CSV of your scrobbles instead")
    body = body or LastfmBody()
    a = scrobble.account(conn, me.user_id, "lastfm")
    username = body.username or (a["username"] if a else None)
    if not username:
        raise HTTPException(422, "give your Last.fm user name (or connect Last.fm first)")
    with _lock:
        if me.user_id in _running:
            raise HTTPException(409, "a Last.fm import is already running")
        _running.add(me.user_id)
    set_setting(conn, _job_key(me.user_id), {"state": "running", "startedAt": now_iso(), "username": username})
    if wait:
        _run_lastfm(c, me.user_id, username, body.maxScrobbles)
    else:
        threading.Thread(target=_run_lastfm, args=(c, me.user_id, username, body.maxScrobbles),
                         name="lastfm-import", daemon=True).start()
    return _status(conn, c, me.user_id)


@router.delete("/me/import/history/{source}")
def remove_history(source: Source, me: Device = Depends(device), conn: Conn = Depends(db),
                   c: AppContext = Depends(ctx)):
    """Remove every listen imported from `source` (and the play counts it added)."""
    if source == "lastfm":
        with _lock:
            if me.user_id in _running:
                raise HTTPException(409, "a Last.fm import is running")
        conn.execute("DELETE FROM settings WHERE key = ?", (_job_key(me.user_id),))
    elif source == "listenbrainz":
        conn.execute("DELETE FROM settings WHERE key = ?", (f"scrobble.import.{me.user_id}",))
    removed = imports.remove_imported(conn, me.user_id, source)
    return {"removed": removed, **_status(conn, c, me.user_id)}


@router.post("/me/import/m3u")
async def import_m3u(request: Request, name: str = Query("", max_length=300), addMissing: bool = Query(True),
                     me: Device = Depends(device), conn: Conn = Depends(db)):
    f = await _body(request, MAX_SMALL)
    try:
        data = f.read()
    finally:
        f.close()
    return await run_in_threadpool(_run, imports.import_m3u, conn, me.user_id, data, name or None,
                                   add_missing=addMissing)


@router.get("/playlists/{pid}/m3u")
def export_m3u(pid: str, request: Request, paths: Literal["library", "absolute", "url"] = "library",
               me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    text = _run(imports.export_m3u, conn, me.user_id, pid, c.cfg, paths, str(request.base_url))
    name = conn.execute("SELECT name FROM playlists WHERE id = ?", (pid,)).fetchone()[0]
    safe = "".join(ch if ch.isalnum() or ch in " -_()" else "_" for ch in name).strip() or "playlist"
    from urllib.parse import quote

    return Response(text, media_type="audio/x-mpegurl; charset=utf-8", headers={
        "Content-Disposition": f"attachment; filename=\"{safe.encode('ascii', 'ignore').decode().strip() or 'playlist'}.m3u8\"; "
                               f"filename*=UTF-8''{quote(safe)}.m3u8"})
