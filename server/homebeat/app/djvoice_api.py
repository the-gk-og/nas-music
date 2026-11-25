"""Radio DJ voice (0.9): spoken intros before songs in AI radio and mixes.

The client asks for the intro of an upcoming song (POST /dj/intro) a little before it starts, then plays the
clip (/stream/dj/{clipId}) over the song's opening with the music ducked. The server decides whether the DJ
talks at that spot (user settings `dj.voice`, `dj.every`) and what it says (homebeat.djvoice.script), and
synthesises it once (homebeat.djvoice.tts)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from homebeat.app.context import AppContext, ctx, db, device
from homebeat.app.userdata import get_settings
from homebeat.auth import Device
from homebeat.djvoice import script, tts
from homebeat.library import queries

log = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(device)])
DEFAULT_EVERY = 3
STATION_SOURCES = ("radio", "mix")


def engine(c: AppContext) -> tts.Engine:
    e = getattr(c, "dj_voice", None)
    if e is None or e.cfg is not c.cfg:
        e = c.dj_voice = tts.Engine(c.cfg, transport=c.http_transport)
    return e


def user_prefs(conn, uid: str) -> tuple[bool, int]:
    s = get_settings(conn, uid)
    every = s.get("dj.every", DEFAULT_EVERY)
    if isinstance(every, bool) or not isinstance(every, int):
        every = DEFAULT_EVERY
    return s.get("dj.voice") is True, max(1, min(10, every))


@router.get("/dj/status")
def dj_status(me: Device = Depends(device), conn=Depends(db), c: AppContext = Depends(ctx)):
    e = engine(c)
    st = e.status()
    if st["state"] in ("downloading", "error") and not getattr(e, "_downloading", False):
        e.ensure_voice()
        st = e.status()
    enabled, every = user_prefs(conn, me.user_id)
    return {**st, "enabled": enabled, "every": every}


class IntroBody(BaseModel):
    trackId: str = Field(max_length=200)
    previousTrackId: str | None = Field(default=None, max_length=200)
    queueId: str | None = Field(default=None, max_length=200)
    position: int = Field(ge=0, le=100_000)
    force: bool = False


def _song(t: dict) -> script.Song:
    return script.Song(title=t["title"], artist=t["artist"], album=t.get("album"), year=t.get("year"),
                       artist_id=t.get("artistId"))


def _local_time(tz: str) -> tuple[int, int]:
    try:
        now = datetime.now(ZoneInfo(tz))
    except (ZoneInfoNotFoundError, ValueError):
        now = datetime.now(UTC)
    return now.hour, now.minute


def _want_artist_info(c: AppContext, artist_id: str) -> None:
    """No facts yet for this artist: queue the artist-page lookup so the next intro has some."""
    if not c.cfg.artist_info_enabled:
        return
    try:
        from homebeat.app.smart_api import fetcher

        fetcher(c).request(artist_id)
    except Exception:  # noqa: BLE001 - a nicety
        log.debug("artist info request failed", exc_info=True)


@router.post("/dj/intro", responses={204: {"description": "the DJ stays quiet before this song"}})
def dj_intro(body: IntroBody, me: Device = Depends(device), conn=Depends(db), c: AppContext = Depends(ctx)):
    enabled, every = user_prefs(conn, me.user_id)
    if not body.force and (not enabled or not script.speaks_at(body.position, every)):
        return Response(status_code=204)
    t = queries.track(conn, me.user_id, body.trackId)
    if t is None:
        raise HTTPException(404, "no such track")
    prev = queries.track(conn, me.user_id, body.previousTrackId) if body.previousTrackId else None
    station = None
    if body.queueId:
        q = conn.execute("SELECT name, source_type FROM queues WHERE id = ? AND user_id = ?",
                         (body.queueId, me.user_id)).fetchone()
        if q is not None and (q["source_type"] in STATION_SOURCES or q["name"].lower().endswith("radio")):
            station = q["name"]
    facts = script.facts_for(conn, t.get("artistId"), script.speakable(t["artist"]))
    if not facts.sentences and t.get("artistId"):
        _want_artist_info(c, t["artistId"])
    seed = script.seed_of(me.user_id, body.queueId, body.position, t["id"], prev["id"] if prev else None,
                          date.today().isoformat())
    text = script.write(_song(t), _song(prev) if prev else None, station=station, position=body.position,
                        facts=facts, seed=seed, local_time=_local_time(c.cfg.timezone))
    e = engine(c)
    res = e.speak(text)
    if res is None:
        st = e.status()
        raise HTTPException(503, f"the DJ voice isn't ready: {st['detail'] or st['state']}")
    cid, ms = res
    return {"clipId": cid, "text": text, "url": f"stream/dj/{cid}", "durationMs": ms, "voice": e.voice}


@router.get("/stream/dj/{clip_id}")
def dj_clip(clip_id: str, c: AppContext = Depends(ctx)):
    p = engine(c).clip_path(clip_id)
    if p is None:
        raise HTTPException(404, "no such clip")
    return FileResponse(p, media_type="audio/ogg", headers={"Cache-Control": "private, max-age=86400"})
