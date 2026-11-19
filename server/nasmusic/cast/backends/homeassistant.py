"""Home Assistant media_player entities (Sonos, Squeezebox, MusicCast, Bluesound, HEOS, anything HA can drive)
through HA's REST API with a long-lived access token from config.toml.

HA hands the signed media URL to the underlying integration; the speaker fetches it from our cast port.
State is polled (GET /api/states/<entity>), with media_position extrapolated from media_position_updated_at.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from nasmusic.cast.base import Media, Renderer, Status

log = logging.getLogger(__name__)


def pick_format(media: Media) -> tuple[str, str]:
    if (
        media.mime in ("audio/flac", "audio/mpeg", "audio/mp4", "audio/aac")
        and (media.codec or "").upper() != "ALAC"
    ):
        return "original", media.mime
    return "mp3", "audio/mpeg"


class HomeAssistant:
    """Thin REST client shared by all entities of one HA instance."""

    def __init__(self, url: str, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self.client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            transport=transport,
            timeout=10.0,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def states(self) -> list[dict]:
        r = await self.client.get("/api/states")
        r.raise_for_status()
        return r.json()

    async def state(self, entity_id: str) -> dict:
        r = await self.client.get(f"/api/states/{entity_id}")
        r.raise_for_status()
        return r.json()

    async def call(self, service: str, entity_id: str, **data) -> None:
        r = await self.client.post(
            f"/api/services/media_player/{service}", json={"entity_id": entity_id, **data}
        )
        r.raise_for_status()


def _position(attrs: dict, playing: bool) -> float:
    pos = float(attrs.get("media_position") or 0)
    at = attrs.get("media_position_updated_at")
    if playing and at:
        try:
            pos += max(0.0, (datetime.now(UTC) - datetime.fromisoformat(str(at))).total_seconds())
        except ValueError:
            pass
    return pos


class HaRenderer(Renderer):
    kind = "homeassistant"

    def __init__(self, entity_id: str, name: str, ha: HomeAssistant):
        super().__init__(entity_id, name)
        self.ha, self.entity_id = ha, entity_id
        self._url: str | None = None
        self._was_playing = False

    async def load(self, media: Media, position_s: float, play: bool) -> None:
        fmt, mime = pick_format(media)
        self._url = media.url(fmt)
        self._was_playing = False
        extra = {
            "title": media.title,
            "metadata": {
                "metadataType": 3,
                "title": media.title,
                "artist": media.artist,
                "albumName": media.album,
            },
        }
        if media.art_url:
            extra["thumb"] = media.art_url
        await self.ha.call(
            "play_media", self.entity_id, media_content_id=self._url, media_content_type="music", extra=extra
        )
        if position_s > 1:
            try:
                await self.ha.call("media_seek", self.entity_id, seek_position=round(position_s, 1))
            except httpx.HTTPError:
                pass  # not every integration can seek
        if not play:
            await self.ha.call("media_pause", self.entity_id)

    async def play(self) -> None:
        await self.ha.call("media_play", self.entity_id)

    async def pause(self) -> None:
        await self.ha.call("media_pause", self.entity_id)

    async def stop(self) -> None:
        self._url = None
        await self.ha.call("media_stop", self.entity_id)

    async def seek(self, position_s: float) -> None:
        await self.ha.call("media_seek", self.entity_id, seek_position=round(max(0.0, position_s), 1))

    async def set_volume(self, volume: float) -> None:
        await self.ha.call("volume_set", self.entity_id, volume_level=round(max(0.0, min(1.0, volume)), 3))

    async def status(self) -> Status:
        try:
            st = await self.ha.state(self.entity_id)
        except httpx.HTTPError as e:
            return Status("unavailable", error=str(e))
        state, attrs = st.get("state"), st.get("attributes") or {}
        vol = attrs.get("volume_level")
        ours = self._url is not None and attrs.get("media_content_id") in (None, self._url)
        if state == "playing" and ours:
            self._was_playing = True
            return Status("playing", _position(attrs, True), vol)
        if state == "buffering":
            return Status("buffering", _position(attrs, False), vol)
        if state == "paused" and ours:
            return Status("paused", _position(attrs, False), vol)
        if state in ("unavailable", "unknown"):
            return Status("unavailable", 0.0, vol)
        if (
            self._url is not None
            and self._was_playing
            and (state in ("idle", "on", "off", "standby") or not ours)
        ):
            # It stopped on its own after playing ours, or something else replaced it: move on / let go.
            self._url, self._was_playing = None, False
            return Status("finished" if state != "off" and ours else "idle", _position(attrs, False), vol)
        return Status("idle", _position(attrs, False), vol)

    async def close(self) -> None:
        pass


_instances: dict[tuple[str, str], HomeAssistant] = {}
_cache: dict[str, HaRenderer] = {}


async def discover(cfg, transport: httpx.AsyncBaseTransport | None = None) -> list[Renderer]:
    if not (cfg.homeassistant_url and cfg.homeassistant_token):
        return []
    key = (cfg.homeassistant_url, cfg.homeassistant_token)
    ha = _instances.get(key)
    if ha is None or transport is not None:
        ha = _instances[key] = HomeAssistant(cfg.homeassistant_url, cfg.homeassistant_token, transport)
        _cache.clear()
    try:
        states = await ha.states()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Home Assistant at %s: %s", cfg.homeassistant_url, e)
        return []
    wanted = set(cfg.homeassistant_entities)
    out: list[Renderer] = []
    for s in states:
        eid = str(s.get("entity_id", ""))
        if not eid.startswith("media_player.") or (wanted and eid not in wanted):
            continue
        if not wanted and s.get("state") == "unavailable":
            continue
        name = (s.get("attributes") or {}).get("friendly_name") or eid
        r = _cache.get(eid)
        if r is None:
            r = _cache[eid] = HaRenderer(eid, name, ha)
        r.name = name
        out.append(r)
    return out
