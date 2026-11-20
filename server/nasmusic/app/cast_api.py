"""Casting: signed media URLs for speakers, the speaker list, and the registry's database hooks.

- GET /cast/media/{trackId}?f&exp&sig and /cast/art/{albumId}?exp&sig: no session auth; the HMAC signature
  (nasmusic.cast.signing) is the credential. Also served alone on the plain-HTTP `cast_port` (cast_app()).
- GET /cast/speakers, POST /cast/speakers/refresh: what the registry found (refresh: admin).
- POST /cast/sign: signed URLs for clients that cast natively (Android `full` flavour's Google Cast button).
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from nasmusic.app.context import AppContext, admin_device, ctx, db, device
from nasmusic.auth import Device
from nasmusic.cast import CastHooks, Media, SpeakerRegistry
from nasmusic.cast import signing
from nasmusic.config import Config
from nasmusic.core import open_db
from nasmusic.library import art
from nasmusic.stream import media_type
from nasmusic.stream.transcode import Mp3Cache, TranscodeError

log = logging.getLogger(__name__)

media_router = APIRouter()  # signed, no session
router = APIRouter(dependencies=[Depends(device)])

OPUS_KBPS = 192
MP3_KBPS = 320
FORMAT_TYPES = {"opus": "audio/ogg", "mp3": "audio/mpeg"}


def cast_key(c: AppContext) -> bytes:
    return signing.derive_key(c.web_secret)


def _ttl(cfg: Config, duration_s: float) -> float:
    return float(cfg.cast_url_ttl_s) + max(0.0, float(duration_s or 0))


def media_for(cfg: Config, key: bytes, track_id: str, base: str | None = None) -> Media | None:
    """Everything a speaker backend needs about a track, with signed URL builders (blocking: DB)."""
    base = base or cfg.cast_base_url
    conn = open_db(cfg)
    try:
        r = conn.execute(
            "SELECT t.id, t.title, t.path, t.duration_s, t.codec, t.album_id, ar.name AS artist, "
            "al.title AS album, al.art_path FROM tracks t JOIN artists ar ON ar.id = t.artist_id "
            "JOIN albums al ON al.id = t.album_id "
            "WHERE t.id = ? AND t.missing = 0",
            (track_id,),
        ).fetchone()
    finally:
        conn.close()
    if r is None:
        return None
    ttl = _ttl(cfg, r["duration_s"])
    art_url = signing.art_url(base, key, r["album_id"], ttl)
    return Media(
        track_id=r["id"],
        title=r["title"],
        artist=r["artist"],
        album=r["album"],
        duration_s=float(r["duration_s"] or 0),
        art_url=art_url,
        mime=media_type(r["path"]),
        codec=r["codec"],
        path=cfg.library_root / r["path"],
        url_for=lambda fmt: signing.media_url(base, key, r["id"], fmt, ttl),
    )


def make_registry(c: AppContext, **kw) -> SpeakerRegistry:
    cfg = c.cfg
    key = cast_key(c)

    def queue_track(uid: str, qid: str, index: int) -> str | None:
        from nasmusic.app import collections as coll

        conn = open_db(cfg)
        try:
            return coll.queue_track_at(conn, uid, qid, index)
        finally:
            conn.close()

    def queue_len(uid: str, qid: str) -> int:
        conn = open_db(cfg)
        try:
            return conn.execute(
                "SELECT COUNT(*) FROM queue_tracks q JOIN queues u ON u.id = q.queue_id "
                "WHERE q.queue_id = ? AND u.user_id = ?",
                (qid, uid),
            ).fetchone()[0]
        finally:
            conn.close()

    async def _unbound(*a):  # replaced by Hub.attach_speakers
        return False

    hooks = CastHooks(
        media=lambda tid: media_for(cfg, key, tid),
        queue_track=queue_track,
        queue_len=queue_len,
        report=_unbound,
        lost=_unbound,
        error=_unbound,
    )
    return SpeakerRegistry(cfg, hooks, **kw)


# ---------- signed media (speakers) ----------


def _forbidden():
    raise HTTPException(403, "invalid or expired signature")


@media_router.get("/cast/media/{track_id}")
def cast_media(
    track_id: str,
    f: Literal["original", "opus", "mp3"] = "original",
    exp: int = Query(0),
    sig: str = Query("", max_length=64),
    c: AppContext = Depends(ctx),
    conn: sqlite3.Connection = Depends(db),
):
    if not signing.verify(cast_key(c), "media", track_id, f, exp, sig):
        _forbidden()
    row = conn.execute(
        "SELECT id, path, mtime FROM tracks WHERE id = ? AND missing = 0", (track_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(404, "not found")
    src = c.cfg.library_root / row["path"]
    if not src.is_file():
        raise HTTPException(404, "file missing")
    headers = {
        "Cache-Control": "private, max-age=3600",
        "transferMode.dlna.org": "Streaming",
        "contentFeatures.dlna.org": "DLNA.ORG_OP=01;DLNA.ORG_CI=0",
    }
    if f == "original":
        return FileResponse(src, media_type=media_type(row["path"]), headers=headers)
    try:
        if f == "opus":
            out = c.opus.get(src, track_id, OPUS_KBPS, row["mtime"])
        else:
            out = _mp3(c).get(src, track_id, MP3_KBPS, row["mtime"])
    except TranscodeError as e:
        log.error("cast transcode failed for %s: %s", track_id, e)
        raise HTTPException(500, "transcode failed") from e
    headers["contentFeatures.dlna.org"] = "DLNA.ORG_OP=01;DLNA.ORG_CI=1"
    return FileResponse(out, media_type=FORMAT_TYPES[f], headers=headers)


_mp3_caches: dict[str, Mp3Cache] = {}


def _mp3(c: AppContext) -> Mp3Cache:
    k = str(c.cfg.data_dir)
    if k not in _mp3_caches:
        _mp3_caches[k] = Mp3Cache(c.cfg.data_dir / "mp3", 2 * 1024**3)
    return _mp3_caches[k]


@media_router.get("/cast/art/{album_id}")
def cast_art(
    album_id: str,
    exp: int = Query(0),
    sig: str = Query("", max_length=64),
    c: AppContext = Depends(ctx),
    conn: sqlite3.Connection = Depends(db),
):
    if not signing.verify(cast_key(c), "art", album_id, "", exp, sig):
        _forbidden()
    from nasmusic.library import queries

    row = queries.album_art_info(conn, album_id)
    if row is None:
        raise HTTPException(404, "not found")
    out = art.resized(c.data_dir, c.cfg.library_root, album_id, row["art_path"], 512)
    if out is None:
        raise HTTPException(404, "no art")
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


# ---------- speakers ----------


def _registry(c: AppContext) -> SpeakerRegistry:
    reg = getattr(c.hub, "speakers", None)
    if reg is None or not reg.enabled:
        raise HTTPException(404, "casting is disabled on this server")
    return reg


@router.get("/cast/speakers")
async def list_speakers(c: AppContext = Depends(ctx)):
    reg = _registry(c)
    reg.start()
    return {"speakers": reg.devices(), "mediaBaseUrl": c.cfg.cast_base_url, "errors": reg.errors}


@router.post("/cast/speakers/refresh", dependencies=[Depends(admin_device)])
async def refresh_speakers(c: AppContext = Depends(ctx)):
    reg = _registry(c)
    devices = await reg.refresh()
    return {"speakers": devices, "mediaBaseUrl": c.cfg.cast_base_url, "errors": reg.errors}


class SignBody(BaseModel):
    trackIds: list[str] = Field(min_length=1, max_length=500)
    format: Literal["original", "opus", "mp3"] = "original"


def sign_base(request: Request, cfg: Config) -> str:
    """Where the speaker should fetch from. A phone that reached us through the public address (the internal
    HTTP port, behind the relay/tunnel) may be casting to a speaker on another network, which can't reach the
    LAN-only cast port — so hand out public links. On the LAN, use the fast direct cast port."""
    server = request.scope.get("server") or (None, None)
    if cfg.tunnel_url and server[1] == cfg.http_port:
        return cfg.tunnel_url.rstrip("/")
    return cfg.cast_base_url


@router.post("/cast/sign")
def sign_urls(body: SignBody, request: Request, c: AppContext = Depends(ctx),
              conn: sqlite3.Connection = Depends(db), me: Device = Depends(device)):
    """Signed media + art URLs for tracks, for a client that casts by itself (the Google Cast button).
    Family mode: tracks the user may not see get no link."""
    from nasmusic.db import family

    key = cast_key(c)
    hidden = family.hidden_among(conn, me.user_id, body.trackIds)
    base = sign_base(request, c.cfg)
    marks = ",".join("?" * len(body.trackIds))
    rows = {
        r["id"]: r
        for r in conn.execute(
            f"SELECT id, path, duration_s, album_id FROM tracks WHERE missing = 0 AND id IN ({marks})",
            body.trackIds,
        )
    }
    out = []
    for tid in body.trackIds:
        r = rows.get(tid)
        if r is None or tid in hidden:
            continue
        ttl = _ttl(c.cfg, r["duration_s"])
        ctype = media_type(r["path"]) if body.format == "original" else FORMAT_TYPES[body.format]
        out.append(
            {
                "trackId": tid,
                "url": signing.media_url(base, key, tid, body.format, ttl),
                "artUrl": signing.art_url(base, key, r["album_id"], ttl),
                "contentType": ctype,
                "expiresAt": signing.expiry(ttl),
            }
        )
    return {"items": out}


def cast_app(context: AppContext) -> FastAPI:
    """The app on the plain-HTTP cast port: signed media routes only, nothing else reachable."""
    app = FastAPI(title="nas-music cast", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = context
    app.include_router(media_router, prefix="/api/v1")
    return app
