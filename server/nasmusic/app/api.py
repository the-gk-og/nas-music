"""Library, audio and listening endpoints under /api/v1 (api/openapi.yaml). Everything is per user:
starred/rating, favourites, mixes, events, suggestions, lyrics edits and the manifest are the caller's."""
from __future__ import annotations

import logging
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from nasmusic.app import ai_adapter, mixes, suggestions
from nasmusic.app import events as events_mod
from nasmusic.app.context import AppContext, ctx, db, device
from nasmusic.auth import Device
from nasmusic.core import ai_config, now_iso
from nasmusic.db import family
from nasmusic.library import art, lrclib, lyrics, overrides, queries
from nasmusic.stream import clamp_bitrate, media_type
from nasmusic.stream.transcode import TranscodeError
from nasmusic.sync import manifest as build_manifest
from nasmusic.sync import smart_candidates

log = logging.getLogger(__name__)

public = APIRouter()
router = APIRouter(dependencies=[Depends(device)])

Conn = sqlite3.Connection
Limit = Query(200, ge=1, le=1000)
Offset = Query(0, ge=0)


def _404(what: str = "not found"):
    raise HTTPException(404, what)


@public.get("/health")
def health(c: AppContext = Depends(ctx)):
    return {"status": "ok", "serverId": c.server_id, "version": c.version}


# ---------- library ----------


@router.get("/library/artists")
def list_artists(offset: int = Offset, limit: int = Limit, me: Device = Depends(device), conn: Conn = Depends(db)):
    return queries.artists(conn, offset, limit, uid=me.user_id)


@router.get("/library/artists/{artist_id}")
def get_artist(artist_id: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return queries.artist(conn, artist_id, c.data_dir, uid=me.user_id) or _404()


@router.get("/library/albums")
def list_albums(artistId: str | None = None, sort: Literal["name", "year", "added"] = "name",
                offset: int = Offset, limit: int = Limit, me: Device = Depends(device), conn: Conn = Depends(db),
                c: AppContext = Depends(ctx)):
    return queries.albums(conn, c.data_dir, artistId, sort, offset, limit, uid=me.user_id)


@router.get("/library/albums/{album_id}")
def get_album(album_id: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return queries.album(conn, me.user_id, album_id, c.data_dir) or _404()


@router.get("/library/tracks")
def all_tracks(sort: Literal["title", "added", "year"] = "title", offset: int = Offset, limit: int = Limit,
               me: Device = Depends(device), conn: Conn = Depends(db)):
    return queries.all_tracks(conn, me.user_id, sort, offset, limit)


@router.get("/library/tracks/{track_id}")
def get_track(track_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    return queries.track(conn, me.user_id, track_id) or _404()


@router.get("/library/tracks/{track_id}/info")
def track_info(track_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    return queries.track_info(conn, me.user_id, track_id) or _404()


@router.get("/library/folders")
def folders(path: str = Query("", max_length=4096), me: Device = Depends(device), conn: Conn = Depends(db)):
    return queries.folders(conn, me.user_id, path) or _404("no such folder")


@router.get("/library/genres")
def genres(me: Device = Depends(device), conn: Conn = Depends(db)):
    return queries.genres(conn, me.user_id)


@router.get("/library/genres/{genre:path}/tracks")
def genre_tracks(genre: str, offset: int = Offset, limit: int = Limit, me: Device = Depends(device),
                 conn: Conn = Depends(db)):
    return queries.genre_tracks(conn, me.user_id, genre, offset, limit)


@router.get("/library/search")
def search(q: str = Query(..., max_length=200), limit: int = Query(50, ge=1, le=500), me: Device = Depends(device),
           conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return queries.search(conn, me.user_id, q, limit, c.data_dir)


@router.get("/lists/{kind}")
def smart_list(kind: Literal["favourites", "recently_added", "recently_played", "most_played", "not_played"],
               offset: int = Offset, limit: int = Limit, me: Device = Depends(device), conn: Conn = Depends(db)):
    return queries.smart_list(conn, me.user_id, kind, offset, limit)


# ---------- audio + art ----------


def family_guard(conn: Conn, uid: str, track_ids: list[str]) -> None:
    """A user whose family mode an admin has LOCKED cannot stream (or cast) tracks it hides: 403. Unlocked
    family mode only hides them (the user could turn it off anyway)."""
    p = family.policy(conn, uid)
    if p.private_others and family.hidden_among(conn, family.Policy(viewer=uid, private_others=True), track_ids):
        _404()  # someone else's "only me" upload
    if p.locked and family.hidden_among(conn, p, track_ids):
        raise HTTPException(403, "blocked by family mode")


@router.get("/stream/{track_id}")
def stream(track_id: str, format: Literal["opus", "flac"] = "opus", bitrate: int = Query(160, ge=1, le=1000),
           me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    row = queries.track_file(conn, track_id)
    if row is None:
        _404()
    family_guard(conn, me.user_id, [track_id])
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
def favourites(me: Device = Depends(device), conn: Conn = Depends(db)):
    return queries.favourites(conn, me.user_id)


@router.put("/favourites/{track_id}", status_code=204)
def put_favourite(track_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    if not events_mod.star(conn, me.user_id, track_id):
        _404()
    return Response(status_code=204)


@router.delete("/favourites/{track_id}", status_code=204)
def delete_favourite(track_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    if not events_mod.unstar(conn, me.user_id, track_id):
        _404()
    return Response(status_code=204)


# ---------- mixes ----------


@router.get("/mixes/today")
def mixes_today(me: Device = Depends(device), conn: Conn = Depends(db)):
    return mixes.today(conn, me.user_id)


@router.get("/mixes/{mix_id}")
def get_mix(mix_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    return mixes.get(conn, me.user_id, mix_id) or _404()


@router.post("/mixes/more-like/{track_id}")
def more_like(track_id: str, minutes: int = Query(40, ge=10, le=240), me: Device = Depends(device),
              conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    if queries.track(conn, me.user_id, track_id) is None:
        _404()
    try:
        mix_id = ai_adapter.more_like(conn, ai_config(c.cfg), me.user_id, track_id, minutes)
    except NotImplementedError as e:
        raise HTTPException(503, "recommender not available yet") from e
    except KeyError as e:  # the AI's signal for "unknown or not analysed yet"
        raise HTTPException(404, "track not analysed yet") from e
    except Exception as e:
        log.exception("more_like failed for %s", track_id)
        raise HTTPException(500, "could not build mix") from e
    mix = mixes.get(conn, me.user_id, mix_id)
    if mix is None:
        log.error("more_like returned %r but the user has no such mix", mix_id)
        raise HTTPException(500, "could not build mix")
    return mix


@router.get("/tracks/{track_id}/why")
def why(track_id: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    if queries.track(conn, me.user_id, track_id) is None:
        _404()
    try:
        reason, similar_ids = ai_adapter.explain(conn, ai_config(c.cfg), me.user_id, track_id)
    except NotImplementedError as e:
        raise HTTPException(503, "recommender not available yet") from e
    except Exception as e:
        log.exception("explain failed for %s", track_id)
        raise HTTPException(500, "could not explain track") from e
    similar = queries.tracks_by_ids(conn, me.user_id, list(similar_ids)[:3])
    return {"reason": reason, "similarTo": [similar[i] for i in similar_ids[:3] if i in similar]}


# ---------- lyrics ----------


def _lyrics_options(conn: Conn, c: AppContext, uid: str, track_id: str) -> dict[str, lyrics.Lyrics]:
    row = queries.track_file(conn, track_id)
    if row is None:
        _404()
    opts: dict[str, lyrics.Lyrics] = {}
    ov = conn.execute("SELECT raw FROM lyrics_overrides WHERE user_id = ? AND track_id = ?",
                      (uid, track_id)).fetchone()
    if ov:
        parsed = lyrics.from_raw(ov[0], "user")
        if parsed:
            opts["user"] = parsed
    shared = overrides.lyrics_override(conn, track_id)  # an admin's edit, for everyone
    if shared:
        parsed = lyrics.from_raw(shared, "library")
        if parsed:
            opts["library"] = parsed
    src = c.cfg.library_root / row["path"]
    if src.is_file():
        opts.update(lyrics.read_all(src))
    if not any(k in opts for k in ("library", "embedded", "lrc_file")) and c.cfg.lrclib_enabled and c.lrclib is not None:
        cache = lrclib.fetch(conn, c.lrclib, track_id)  # cached; asks lrclib.net at most once per 30 days
        if cache is not None and cache["status"] == "found" and cache["raw"]:
            parsed = lyrics.from_raw(cache["raw"], "lrclib")
            if parsed:
                opts["lrclib"] = parsed
    return opts


def _lyrics_response(track_id: str, chosen: lyrics.Lyrics, opts: dict) -> dict:
    return {**chosen.as_api(track_id), "sources": list(opts), "canEdit": True}


@router.get("/tracks/{track_id}/lyrics")
def track_lyrics(track_id: str, source: Literal["user", "library", "embedded", "lrc_file", "lrclib"] | None = None,
                 me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    opts = _lyrics_options(conn, c, me.user_id, track_id)
    if source is not None:
        chosen = opts.get(source)
    elif "user" in opts:
        chosen = opts["user"]
    elif "library" in opts:
        chosen = opts["library"]
    else:
        files = [o for k, o in opts.items() if k != "user"]
        synced = [o for o in files if o.synced]
        chosen = (synced or files or [None])[0]
    if chosen is None:
        _404("no lyrics")
    return _lyrics_response(track_id, chosen, opts)


class LyricsBody(BaseModel):
    raw: str = Field(max_length=200_000)


@router.put("/tracks/{track_id}/lyrics")
def put_lyrics(track_id: str, body: LyricsBody, me: Device = Depends(device), conn: Conn = Depends(db),
               c: AppContext = Depends(ctx)):
    if queries.track_file(conn, track_id) is None:
        _404()
    parsed = lyrics.from_raw(body.raw, "user")
    if parsed is None:
        raise HTTPException(422, "lyrics are empty")
    conn.execute("INSERT INTO lyrics_overrides (user_id, track_id, raw, updated_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(user_id, track_id) DO UPDATE SET raw = excluded.raw, updated_at = excluded.updated_at",
                 (me.user_id, track_id, body.raw, now_iso()))
    return _lyrics_response(track_id, parsed, _lyrics_options(conn, c, me.user_id, track_id))


@router.delete("/tracks/{track_id}/lyrics", status_code=204)
def delete_lyrics(track_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    conn.execute("DELETE FROM lyrics_overrides WHERE user_id = ? AND track_id = ?", (me.user_id, track_id))
    return Response(status_code=204)


# ---------- sync + events ----------


@router.get("/sync/manifest")
def sync_manifest(favouritesFormat: Literal["opus", "flac"] = "opus", me: Device = Depends(device),
                  conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return build_manifest(conn, me.user_id, favouritesFormat, opus=c.opus)


@router.get("/sync/smart")
def sync_smart(limit: int = Query(1500, ge=1, le=5000), me: Device = Depends(device),
               conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    """Smart-download candidates: favourites, this week's mixes, recent plays, Release Radar albums in the
    library, "because you played" and the recommender's likely picks, each with the AI's likelihood."""
    aicfg = ai_config(c.cfg)
    likely = ai_adapter.likely_next(conn, aicfg, me.user_id)
    try:
        foryou = [t for s in ai_adapter.because_you_played(conn, aicfg, me.user_id, 3) for t in s["track_ids"]]
    except Exception:
        log.exception("smart downloads: because_you_played failed")
        foryou = []
    try:
        radar = [a["albumId"] for a in (ai_adapter.release_radar(conn, aicfg, me.user_id) or {}).get("recentlyAdded", [])
                 if isinstance(a, dict) and a.get("albumId")]
    except Exception:
        log.exception("smart downloads: release radar failed")
        radar = []
    out = smart_candidates(conn, me.user_id, likely, foryou, radar, limit=limit)
    tracks = queries.tracks_by_ids(conn, me.user_id, [i["trackId"] for i in out["items"]])
    for it in out["items"]:
        it["track"] = tracks.get(it["trackId"])
    return out


@router.post("/events")
def post_events(batch: events_mod.EventBatch, me: Device = Depends(device), conn: Conn = Depends(db),
                c: AppContext = Depends(ctx)):
    return events_mod.ingest(conn, batch.events, me.id, me.user_id, weather=c.weather)


# ---------- suggestions ----------


@router.get("/suggestions")
def list_suggestions(me: Device = Depends(device), conn: Conn = Depends(db)):
    return suggestions.list_all(conn, me.user_id)


class SuggestionAnswer(BaseModel):
    status: Literal["want", "not_interested", "have"]


@router.post("/suggestions/{suggestion_id}", status_code=204)
def post_suggestion(suggestion_id: str, body: SuggestionAnswer, me: Device = Depends(device),
                    conn: Conn = Depends(db)):
    if not suggestions.answer(conn, me.user_id, suggestion_id, body.status):
        _404()
    return Response(status_code=204)


@router.get("/suggestions/{suggestion_id}/preview")
async def suggestion_preview(suggestion_id: str, request: Request, me: Device = Depends(device),
                             conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return await suggestions.preview(c, conn, me.user_id, suggestion_id, request.headers.get("range"))


@router.get("/suggestions/{suggestion_id}/cover")
def suggestion_cover(suggestion_id: str, size: int = Query(512, ge=16, le=4096), me: Device = Depends(device),
                     conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    out = suggestions.cover(c, conn, me.user_id, suggestion_id, size)
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})


# ---------- AutoEq headphone profiles (fetched on demand, cached; see nasmusic.app.autoeq) ----------


def _autoeq(c: AppContext):
    from nasmusic.app.autoeq import AutoEq

    if c.autoeq is None:
        c.autoeq = AutoEq(c.data_dir / "autoeq", c.http_transport)
    return c.autoeq


@router.get("/eq/autoeq")
def autoeq_search(q: str = Query("", max_length=200), limit: int = Query(50, ge=1, le=200),
                  c: AppContext = Depends(ctx)):
    from nasmusic.app.autoeq import AutoEqError, search

    try:
        entries = _autoeq(c).entries()
    except AutoEqError as e:
        raise HTTPException(502, f"AutoEq index unavailable: {e}") from e
    return {"total": len(entries), "results": [e.as_dict() for e in search(entries, q, limit)]}


@router.get("/eq/autoeq/profile")
def autoeq_profile(id: str = Query(..., min_length=1, max_length=500), c: AppContext = Depends(ctx)):
    from nasmusic.app.autoeq import AutoEqError

    try:
        return _autoeq(c).profile(id)
    except KeyError as e:
        raise HTTPException(404, "no such AutoEq profile") from e
    except AutoEqError as e:
        raise HTTPException(502, str(e)) from e
