"""Casting: signed media URLs for speakers, the speaker list, and the registry's database hooks.

- GET /cast/media/{trackId}?f&exp&sig and /cast/art/{albumId}?exp&sig: no session auth; the HMAC signature
  (homebeat.cast.signing) is the credential. Also served alone on the plain-HTTP `cast_port` (cast_app()).
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

from homebeat.app.context import AppContext, admin_device, ctx, db, device
from homebeat.auth import Device
from homebeat.cast import CastHooks, Media, SpeakerRegistry, signing
from homebeat.config import Config
from homebeat.core import open_db
from homebeat.library import art
from homebeat.stream import media_type
from homebeat.stream.transcode import Mp3Cache, TranscodeError

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


def spoken_media_for(conn: sqlite3.Connection, cfg: Config, key: bytes, uid: str, kind: str, ident: str,
                     position_s: float = 0.0, base: str | None = None):
    """A podcast episode, the audiobook part holding `position_s`, or a live station, as a speaker's Media
    (signed URLs, family mode respected), or None."""
    from homebeat.cast import SpokenMedia
    from homebeat.spoken import audiobooks, podcasts, stations

    base = base or cfg.cast_base_url
    if kind == "episode":
        r = podcasts.episode_row(conn, uid, ident)
        if r is None:
            return None
        ttl = _ttl(cfg, r["duration_s"] or 4 * 3600)
        m = Media(track_id=f"episode:{r['id']}", title=r["title"], artist=r["ptitle"] or "", album=r["ptitle"] or "",
                  duration_s=float(r["duration_s"] or 0), mime=r["enclosure_type"] or "audio/mpeg",
                  art_url=_signed(base, key, "spokenart", "podcast", r["podcast_id"], ttl),
                  url_for=lambda fmt: _signed(base, key, "spoken", "episode", r["id"], ttl))
        return SpokenMedia(m)
    if kind == "book":
        b = conn.execute("SELECT * FROM audiobooks WHERE id = ? AND missing = 0", (ident,)).fetchone()
        if b is None:
            return None
        parts = conn.execute("SELECT * FROM audiobook_files WHERE book_id = ? ORDER BY idx", (ident,)).fetchall()
        if not parts:
            return None
        i = 0
        for j, f in enumerate(parts):
            if float(f["start_s"] or 0) <= position_s:
                i = j
        f = parts[i]
        ttl = _ttl(cfg, f["duration_s"] or 0)
        m = Media(track_id=f"book:{b['id']}:{f['idx']}", title=b["title"], artist=b["author"] or "",
                  album=b["title"], duration_s=float(f["duration_s"] or 0), mime=audiobooks.media_type(f["path"]),
                  art_url=_signed(base, key, "spokenart", "book", b["id"], ttl),
                  url_for=lambda fmt: _signed(base, key, "spoken", "book", f"{b['id']}.{f['idx']}", ttl))
        end = float(parts[i + 1]["start_s"]) if i + 1 < len(parts) else None
        return SpokenMedia(m, start_s=float(f["start_s"] or 0), end_s=end)
    if kind == "station":
        got = stations.get(conn, uid, ident)
        if got is None:
            return None
        st = got[0]
        ttl = signing.MAX_TTL_S
        mime = {"AAC": "audio/aac", "AAC+": "audio/aac", "OGG": "audio/ogg", "FLAC": "audio/flac"}.get(
            st["codec"] or "", "audio/mpeg")
        m = Media(track_id=f"station:{st['id']}", title=st["name"], artist="Internet radio", album="",
                  duration_s=0.0, mime=mime,
                  art_url=_signed(base, key, "spokenart", "station", st["id"], ttl) if st["favicon"] else None,
                  url_for=lambda fmt: _signed(base, key, "spoken", "station", st["id"], ttl))
        return SpokenMedia(m, live=True)
    return None


def make_registry(c: AppContext, **kw) -> SpeakerRegistry:
    cfg = c.cfg
    key = cast_key(c)

    def queue_track(uid: str, qid: str, index: int) -> str | None:
        from homebeat.app import collections as coll

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

    def spoken(uid: str, kind: str, ident: str, position_s: float):
        conn = open_db(cfg)
        try:
            return spoken_media_for(conn, cfg, key, uid, kind, ident, position_s)
        finally:
            conn.close()

    hooks = CastHooks(
        spoken=spoken,
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
    from homebeat.library import queries

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
    from homebeat.db import family

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


# ---------- podcasts / audiobooks / internet radio (homebeat.spoken) ----------

SPOKEN_KINDS = ("episode", "book", "station")


@media_router.get("/cast/spoken/{kind}/{ident}")
async def cast_spoken(kind: Literal["episode", "book", "station"], ident: str, request: Request,
                      exp: int = Query(0), sig: str = Query("", max_length=64), c: AppContext = Depends(ctx),
                      conn: sqlite3.Connection = Depends(db)):
    """Signed media for speakers: an episode, an audiobook part (`<bookId>.<part>`) or a live station."""
    from homebeat.app import spoken_api

    if not signing.verify(cast_key(c), "spoken", f"{kind}:{ident}", "", exp, sig):
        _forbidden()
    if kind == "episode":
        row = conn.execute("SELECT * FROM podcast_episodes WHERE id = ?", (ident,)).fetchone()
        if row is None:
            raise HTTPException(404, "not found")
        return await spoken_api.serve_episode(c, row, request.headers.get("range"))
    if kind == "book":
        bid, _, part = ident.partition(".")
        if not part.isdigit():
            raise HTTPException(404, "not found")
        return spoken_api.serve_book_part(c.cfg, conn, bid, int(part))
    row = conn.execute("SELECT stream_url FROM web_stations WHERE id = ?", (ident,)).fetchone()
    if row is None:
        raise HTTPException(404, "not found")
    return await spoken_api.serve_station(c, ident, row["stream_url"])


@media_router.get("/cast/spoken-art/{kind}/{ident}")
def cast_spoken_art(kind: Literal["podcast", "book", "station"], ident: str, exp: int = Query(0),
                    sig: str = Query("", max_length=64), c: AppContext = Depends(ctx),
                    conn: sqlite3.Connection = Depends(db)):
    from homebeat.app import spoken_api

    if not signing.verify(cast_key(c), "spokenart", f"{kind}:{ident}", "", exp, sig):
        _forbidden()
    return spoken_api.cover_file(c, conn, kind, ident, 512)


def _signed(base: str, key: bytes, kind: str, path_kind: str, ident: str, ttl: float) -> str:
    from urllib.parse import quote, urlencode

    exp = signing.expiry(ttl)
    q = urlencode({"exp": exp, "sig": signing.sign(key, kind, f"{path_kind}:{ident}", "", exp)})
    route = "spoken" if kind == "spoken" else "spoken-art"
    return f"{base.rstrip('/')}/api/v1/cast/{route}/{path_kind}/{quote(ident, safe='.')}?{q}"


class SpokenSignBody(BaseModel):
    kind: Literal["episode", "book", "station"]
    id: str = Field(min_length=1, max_length=64)
    part: int | None = Field(default=None, ge=0)   # books: which part (default: every part)


@router.post("/cast/sign-spoken")
def sign_spoken(body: SpokenSignBody, request: Request, c: AppContext = Depends(ctx),
                conn: sqlite3.Connection = Depends(db), me: Device = Depends(device)):
    """Signed URLs for casting an episode, audiobook parts or a live station (family mode respected)."""
    from homebeat.spoken import audiobooks, podcasts, stations

    key = cast_key(c)
    base = sign_base(request, c.cfg)
    items = []
    if body.kind == "episode":
        r = podcasts.episode_row(conn, me.user_id, body.id)
        if r is None:
            raise HTTPException(404, "not found")
        ttl = _ttl(c.cfg, r["duration_s"] or 4 * 3600)
        items.append({"url": _signed(base, key, "spoken", "episode", r["id"], ttl),
                      "artUrl": _signed(base, key, "spokenart", "podcast", r["podcast_id"], ttl),
                      "contentType": r["enclosure_type"] or "audio/mpeg", "title": r["title"],
                      "subtitle": r["ptitle"], "durationS": r["duration_s"], "live": False,
                      "expiresAt": signing.expiry(ttl)})
    elif body.kind == "book":
        b = conn.execute("SELECT * FROM audiobooks WHERE id = ? AND missing = 0", (body.id,)).fetchone()
        if b is None:
            raise HTTPException(404, "not found")
        for f in conn.execute("SELECT * FROM audiobook_files WHERE book_id = ? ORDER BY idx", (body.id,)):
            if body.part is not None and f["idx"] != body.part:
                continue
            ttl = _ttl(c.cfg, f["duration_s"])
            items.append({"url": _signed(base, key, "spoken", "book", f"{b['id']}.{f['idx']}", ttl),
                          "artUrl": _signed(base, key, "spokenart", "book", b["id"], ttl),
                          "contentType": audiobooks.media_type(f["path"]), "title": b["title"],
                          "subtitle": b["author"], "durationS": f["duration_s"], "startS": f["start_s"],
                          "part": f["idx"], "live": False, "expiresAt": signing.expiry(ttl)})
    else:
        got = stations.get(conn, me.user_id, body.id)
        if got is None:
            raise HTTPException(404, "not found")
        st = got[0]
        ttl = signing.MAX_TTL_S
        items.append({"url": _signed(base, key, "spoken", "station", st["id"], ttl),
                      "artUrl": _signed(base, key, "spokenart", "station", st["id"], ttl) if st["favicon"] else None,
                      "contentType": {"AAC": "audio/aac", "AAC+": "audio/aac", "OGG": "audio/ogg",
                                      "FLAC": "audio/flac"}.get(st["codec"] or "", "audio/mpeg"),
                      "title": st["name"], "subtitle": "Internet radio", "durationS": None, "live": True,
                      "expiresAt": signing.expiry(ttl)})
    return {"items": items}


def cast_app(context: AppContext) -> FastAPI:
    """The app on the plain-HTTP cast port: signed media routes only, nothing else reachable."""
    app = FastAPI(title="Homebeat cast", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = context
    app.include_router(media_router, prefix="/api/v1")
    return app
