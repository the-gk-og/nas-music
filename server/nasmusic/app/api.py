"""HTTP API under /api/v1 — implements api/openapi.yaml."""
from __future__ import annotations

import logging
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from nasmusic import auth
from nasmusic.ai import api as ai
from nasmusic.app import events as events_mod
from nasmusic.app import mixes, suggestions
from nasmusic.app.context import AppContext, ctx, db, device
from nasmusic.core import ai_config
from nasmusic.library import art, lyrics, queries
from nasmusic.stream import clamp_bitrate, media_type
from nasmusic.stream.transcode import TranscodeError
from nasmusic.sync import manifest as build_manifest

log = logging.getLogger(__name__)

public = APIRouter()
router = APIRouter(dependencies=[Depends(device)])

Conn = sqlite3.Connection


def _404(what: str = "not found"):
    raise HTTPException(404, what)


# ---------- public ----------


@public.get("/health")
def health(c: AppContext = Depends(ctx)):
    return {"status": "ok", "serverId": c.server_id, "version": c.version}


class PairRequest(BaseModel):
    pairCode: str = Field(min_length=1, max_length=64)
    deviceName: str = Field(min_length=1, max_length=200)


@public.post("/pair")
def pair(body: PairRequest, conn: Conn = Depends(db)):
    res = auth.pair_device(conn, body.pairCode, body.deviceName)
    if res is None:
        raise HTTPException(403, "pair code invalid or expired")
    device_id, token = res
    return {"deviceId": device_id, "deviceToken": token}


# ---------- library ----------


@router.get("/library/artists")
def list_artists(offset: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000), conn: Conn = Depends(db)):
    return queries.artists(conn, offset, limit)


@router.get("/library/artists/{artist_id}")
def get_artist(artist_id: str, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return queries.artist(conn, artist_id, c.data_dir) or _404()


@router.get("/library/albums")
def list_albums(
    artistId: str | None = None,
    sort: Literal["name", "year", "added"] = "name",
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    conn: Conn = Depends(db),
    c: AppContext = Depends(ctx),
):
    return queries.albums(conn, c.data_dir, artistId, sort, offset, limit)


@router.get("/library/albums/{album_id}")
def get_album(album_id: str, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return queries.album(conn, album_id, c.data_dir) or _404()


@router.get("/library/tracks/{track_id}")
def get_track(track_id: str, conn: Conn = Depends(db)):
    return queries.track(conn, track_id) or _404()


@router.get("/library/search")
def search(q: str = Query(..., max_length=200), limit: int = Query(50, ge=1, le=500), conn: Conn = Depends(db),
           c: AppContext = Depends(ctx)):
    return queries.search(conn, q, limit, c.data_dir)


# ---------- audio + art ----------


@router.get("/stream/{track_id}")
def stream(
    track_id: str,
    format: Literal["opus", "flac"] = "opus",
    bitrate: int = Query(160, ge=1, le=1000),
    conn: Conn = Depends(db),
    c: AppContext = Depends(ctx),
):
    row = queries.track_file(conn, track_id)
    if row is None:
        _404()
    src = c.cfg.library_root / row["path"]
    if not src.is_file():
        _404("file missing")
    if format == "flac":
        # "flac" means the original file; non-FLAC sources are served as-is with their own type.
        return FileResponse(src, media_type=media_type(row["path"]),
                            headers={"Cache-Control": "private, max-age=86400"})
    kbps = clamp_bitrate(bitrate)
    try:
        out = c.opus.get(src, track_id, kbps, row["mtime"])
    except TranscodeError as e:
        log.error("transcode failed for %s: %s", track_id, e)
        raise HTTPException(500, "transcode failed") from e
    return FileResponse(out, media_type="audio/ogg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/art/{album_id}")
def album_art(album_id: str, size: int = Query(512, ge=16, le=4096), conn: Conn = Depends(db),
              c: AppContext = Depends(ctx)):
    row = queries.album_art_info(conn, album_id)
    if row is None:
        _404()
    out = art.resized(c.data_dir, c.cfg.library_root, album_id, row["art_path"], size)
    if out is None:
        _404("no art")
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})


# ---------- favourites ----------


@router.get("/favourites")
def favourites(conn: Conn = Depends(db)):
    return queries.favourites(conn)


@router.put("/favourites/{track_id}", status_code=204)
def put_favourite(track_id: str, conn: Conn = Depends(db)):
    if not events_mod.star(conn, track_id):
        _404()
    return Response(status_code=204)


@router.delete("/favourites/{track_id}", status_code=204)
def delete_favourite(track_id: str, conn: Conn = Depends(db)):
    if not events_mod.unstar(conn, track_id):
        _404()
    return Response(status_code=204)


# ---------- mixes ----------


@router.get("/mixes/today")
def mixes_today(conn: Conn = Depends(db)):
    return mixes.today(conn)


@router.get("/mixes/{mix_id}")
def get_mix(mix_id: str, conn: Conn = Depends(db)):
    return mixes.get(conn, mix_id) or _404()


@router.post("/mixes/more-like/{track_id}")
def more_like(track_id: str, minutes: int = Query(40, ge=10, le=240), conn: Conn = Depends(db),
              c: AppContext = Depends(ctx)):
    if queries.track(conn, track_id) is None:
        _404()
    try:
        mix_id = ai.more_like(conn, ai_config(c.cfg), track_id, minutes)
    except NotImplementedError as e:
        raise HTTPException(503, "recommender not available yet") from e
    except KeyError as e:  # the AI's signal for "unknown or not analysed yet"
        raise HTTPException(404, "track not analysed yet") from e
    except Exception as e:
        log.exception("more_like failed for %s", track_id)
        raise HTTPException(500, "could not build mix") from e
    mix = mixes.get(conn, mix_id)
    if mix is None:
        log.error("more_like returned %r but no such mix exists", mix_id)
        raise HTTPException(500, "could not build mix")
    return mix


@router.get("/tracks/{track_id}/why")
def why(track_id: str, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    if queries.track(conn, track_id) is None:
        _404()
    try:
        reason, similar_ids = ai.explain(conn, ai_config(c.cfg), track_id)
    except NotImplementedError as e:
        raise HTTPException(503, "recommender not available yet") from e
    except Exception as e:
        log.exception("explain failed for %s", track_id)
        raise HTTPException(500, "could not explain track") from e
    similar = queries.tracks_by_ids(conn, list(similar_ids)[:3])
    return {"reason": reason, "similarTo": [similar[i] for i in similar_ids[:3] if i in similar]}


@router.get("/tracks/{track_id}/lyrics")
def track_lyrics(track_id: str, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    row = queries.track_file(conn, track_id)
    if row is None:
        _404()
    src = c.cfg.library_root / row["path"]
    if not src.is_file():
        _404("file missing")
    found = lyrics.read(src)
    if found is None:
        _404("no lyrics")
    return found.as_api(track_id)


# ---------- sync + events ----------


@router.get("/sync/manifest")
def sync_manifest(favouritesFormat: Literal["opus", "flac"] = "opus", conn: Conn = Depends(db),
                  c: AppContext = Depends(ctx)):
    return build_manifest(conn, favouritesFormat, opus=c.opus)


@router.post("/events")
def post_events(batch: events_mod.EventBatch, conn: Conn = Depends(db), dev: auth.Device = Depends(device)):
    return events_mod.ingest(conn, batch.events, dev.id)


# ---------- suggestions ----------


@router.get("/suggestions")
def list_suggestions(conn: Conn = Depends(db)):
    return suggestions.list_all(conn)


class SuggestionAnswer(BaseModel):
    status: Literal["want", "not_interested", "have"]


@router.post("/suggestions/{suggestion_id}", status_code=204)
def post_suggestion(suggestion_id: str, body: SuggestionAnswer, conn: Conn = Depends(db)):
    if not suggestions.answer(conn, suggestion_id, body.status):
        _404()
    return Response(status_code=204)


@router.get("/suggestions/{suggestion_id}/preview")
async def suggestion_preview(suggestion_id: str, request: Request, conn: Conn = Depends(db),
                             c: AppContext = Depends(ctx)):
    return await suggestions.preview(c, conn, suggestion_id, request.headers.get("range"))


@router.get("/suggestions/{suggestion_id}/cover")
def suggestion_cover(suggestion_id: str, size: int = Query(512, ge=16, le=4096), conn: Conn = Depends(db),
                     c: AppContext = Depends(ctx)):
    out = suggestions.cover(c, conn, suggestion_id, size)
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})
