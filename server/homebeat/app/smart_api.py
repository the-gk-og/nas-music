"""v7 endpoints under /api/v1: smart playlists (/smart-playlists/*) and artist pages (/library/artists/{id}/page,
/library/artists/{id}/tracks, /library/artists/{id}/info, /art/artist/{id})."""
from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from homebeat.app import artists, smart
from homebeat.app.context import AppContext, ctx, db, device
from homebeat.auth import Device
from homebeat.core import ai_config, open_db
from homebeat.library import artistinfo

router = APIRouter(dependencies=[Depends(device)])
Conn = sqlite3.Connection


def _call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except smart.NotFound as e:
        raise HTTPException(404, str(e)) from e
    except smart.Invalid as e:
        raise HTTPException(422, str(e)) from e


# ---------- smart playlists ----------


class SmartBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    definition: dict[str, Any]
    preset: str | None = Field(default=None, max_length=64)


class SmartPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    definition: dict[str, Any] | None = None


class PreviewBody(BaseModel):
    definition: dict[str, Any]
    smartPlaylistId: str | None = Field(default=None, max_length=64)  # editing this one (self-reference check)
    sample: int = Field(default=25, ge=0, le=200)


@router.get("/smart-playlists")
def list_smart(me: Device = Depends(device), conn: Conn = Depends(db)):
    return smart.list_all(conn, me.user_id)


@router.get("/smart-playlists/fields")
def smart_fields():
    return smart.fields()


@router.post("/smart-playlists", status_code=201)
def create_smart(body: SmartBody, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _call(smart.create, conn, me.user_id, body.name, body.definition, body.preset)


@router.post("/smart-playlists/preview")
def preview_smart(body: PreviewBody, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _call(smart.preview, conn, me.user_id, body.definition, body.sample, self_id=body.smartPlaylistId)


@router.get("/smart-playlists/{pid}")
def get_smart(pid: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _call(smart.get, conn, me.user_id, pid)


@router.patch("/smart-playlists/{pid}")
def patch_smart(pid: str, body: SmartPatch, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _call(smart.update, conn, me.user_id, pid, body.name, body.definition)


@router.delete("/smart-playlists/{pid}", status_code=204)
def delete_smart(pid: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    _call(smart.delete, conn, me.user_id, pid)
    return Response(status_code=204)


@router.post("/smart-playlists/{pid}/refresh")
def refresh_smart(pid: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    row = _call(smart._row, conn, me.user_id, pid)
    smart.refresh(conn, row, force=True)
    return _call(smart.get, conn, me.user_id, pid)


# ---------- artist pages ----------


def fetcher(c: AppContext) -> artistinfo.Fetcher:
    f = getattr(c, "artist_fetcher", None)
    if f is None:
        f = c.artist_fetcher = artistinfo.Fetcher(lambda: open_db(c.cfg), c.data_dir, c.http_transport)
    return f


def _info(conn: Conn, c: AppContext, artist_id: str) -> dict:
    """The cached metadata; a due lookup is queued in the background (never waited for)."""
    row = artistinfo.cached(conn, artist_id)
    if not c.cfg.artist_info_enabled:
        return artistinfo.as_api(row)
    f = fetcher(c)
    pending = f.pending(artist_id)
    if artistinfo.is_due(row) and not pending:
        f.request(artist_id)
        pending = True
    return artistinfo.as_api(row, pending=pending)


@router.get("/library/artists/{artist_id}/page")
def artist_page(artist_id: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    if artists._visible_artist(conn, me.user_id, artist_id) is None:
        raise HTTPException(404, "not found")
    p = artists.page(conn, c.data_dir, ai_config(c.cfg), me.user_id, artist_id, info=_info(conn, c, artist_id))
    if p is None:
        raise HTTPException(404, "not found")
    return p


@router.get("/library/artists/{artist_id}/info")
def artist_info(artist_id: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    if artists._visible_artist(conn, me.user_id, artist_id) is None:
        raise HTTPException(404, "not found")
    return {**_info(conn, c, artist_id), "photoUrl": artists.photo_url(c.data_dir, artist_id)}


@router.get("/library/artists/{artist_id}/tracks")
def artist_tracks(artist_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    if artists._visible_artist(conn, me.user_id, artist_id) is None:
        raise HTTPException(404, "not found")
    return artists.tracks(conn, me.user_id, artist_id)


@router.get("/art/artist/{artist_id}")
def artist_photo(artist_id: str, size: int = Query(600, ge=16, le=4096), c: AppContext = Depends(ctx)):
    p = artistinfo.art_path(c.data_dir, artist_id)
    if not p.exists():
        raise HTTPException(404, "no photo")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})
