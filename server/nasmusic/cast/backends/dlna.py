"""DLNA / UPnP AV MediaRenderers (network streamers, AV receivers, many smart TVs, BubbleUPnP Server,
gmrender, Kodi, ...) via async-upnp-client (Apache-2.0).

No event subscription (that needs the renderer to reach a callback server in our container); state is polled
with GetTransportInfo / GetPositionInfo, which every renderer supports.

Discovery is SSDP multicast: needs host networking in Docker. `dlna_renderers` in config.toml lists device
description URLs to use directly instead.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from nasmusic.cast.base import Media, Renderer, Status

log = logging.getLogger(__name__)

DMR_TYPES = (
    "urn:schemas-upnp-org:device:MediaRenderer:1",
    "urn:schemas-upnp-org:device:MediaRenderer:2",
    "urn:schemas-upnp-org:device:MediaRenderer:3",
)


def pick_format(media: Media, sink_protocol_info: list[str] | None) -> tuple[str, str]:
    """The original file if the renderer says it takes that mime type, else MP3 (every renderer plays MP3).
    With no protocol info at all, only send FLAC/MP3 as-is."""
    mimes = set()
    for p in sink_protocol_info or []:
        parts = p.split(":")
        if len(parts) >= 3:
            mimes.add(parts[2].lower())
    m = media.mime.lower()
    aliases = {
        "audio/flac": {"audio/flac", "audio/x-flac"},
        "audio/mpeg": {"audio/mpeg", "audio/mp3"},
        "audio/mp4": {"audio/mp4", "audio/x-m4a", "audio/aac"},
        "audio/ogg": {"audio/ogg", "application/ogg"},
    }
    ok = aliases.get(m, {m})
    if (mimes and ok & mimes) or (not mimes and m in ("audio/flac", "audio/mpeg")):
        if (media.codec or "").upper() != "ALAC":
            return "original", m
    return "mp3", "audio/mpeg"


class DlnaRenderer(Renderer):
    kind = "dlna"

    def __init__(self, uid: str, name: str, dmr: Any, sink: list[str] | None = None):
        super().__init__(uid, name)
        self.dmr = dmr  # async_upnp_client.profiles.dlna.DmrDevice (or a test double)
        self.sink = sink  # CM/GetProtocolInfo sink entries ("http-get:*:audio/flac:*", ...)
        self._loaded = False
        self._was_playing = False
        self._duration = 0.0

    async def load(self, media: Media, position_s: float, play: bool) -> None:
        fmt, mime = pick_format(media, self.sink)
        url = media.url(fmt)
        meta = await self._metadata(media, url, mime)
        if getattr(self.dmr, "can_stop", True):
            try:
                await self.dmr.async_stop()  # some renderers refuse SetAVTransportURI while playing
            except Exception:  # noqa: BLE001
                pass
        await self.dmr.async_set_transport_uri(url, media.title, meta)
        self._loaded, self._was_playing, self._duration = True, False, media.duration_s
        if play or position_s > 1:
            await self._wait_can_play()
            await self.dmr.async_play()
            if position_s > 1:
                await self._seek(position_s)
            if not play:
                await self.dmr.async_pause()

    async def _metadata(self, media: Media, url: str, mime: str) -> str | None:
        try:
            return await self.dmr.construct_play_media_metadata(
                media_url=url,
                media_title=media.title,
                override_mime_type=mime,
                override_dlna_features="*",
                override_upnp_class="object.item.audioItem.musicTrack",
                meta_data={
                    "title": media.title,
                    "artist": media.artist,
                    "album": media.album,
                    "album_art_uri": media.art_url or "",
                },
            )
        except Exception as e:  # noqa: BLE001 - metadata is optional
            log.debug("DIDL metadata failed: %s", e)
            return None

    async def _wait_can_play(self) -> None:
        wait = getattr(self.dmr, "async_wait_for_can_play", None)
        if wait is not None:
            try:
                await wait(5)
            except Exception:  # noqa: BLE001
                pass

    async def _seek(self, position_s: float) -> None:
        t = timedelta(seconds=int(max(0.0, position_s)))
        if getattr(self.dmr, "has_seek_rel_time", True):
            await self.dmr.async_seek_rel_time(t)
        else:
            await self.dmr.async_seek_abs_time(t)

    async def play(self) -> None:
        await self.dmr.async_play()

    async def pause(self) -> None:
        await self.dmr.async_pause()

    async def stop(self) -> None:
        self._loaded = False
        await self.dmr.async_stop()

    async def seek(self, position_s: float) -> None:
        await self._seek(position_s)

    async def set_volume(self, volume: float) -> None:
        if getattr(self.dmr, "has_volume_level", True):
            await self.dmr.async_set_volume_level(max(0.0, min(1.0, volume)))

    async def status(self) -> Status:
        try:
            await self.dmr.async_update(do_ping=True)
        except Exception as e:  # noqa: BLE001
            return Status("unavailable", error=str(e))
        ts = getattr(self.dmr.transport_state, "value", self.dmr.transport_state)
        pos = float(self.dmr.media_position or 0)
        vol = self.dmr.volume_level
        if ts == "PLAYING":
            self._was_playing = True
            return Status("playing", pos, vol)
        if ts == "TRANSITIONING":
            return Status("buffering", pos, vol)
        if ts in ("PAUSED_PLAYBACK", "PAUSED_RECORDING"):
            return Status("paused", pos, vol)
        if ts in ("STOPPED", "NO_MEDIA_PRESENT") and self._loaded and self._was_playing:
            # Stopped by itself after playing: the track ended (a user stop on the renderer looks the same).
            self._loaded = self._was_playing = False
            return Status("finished", pos, vol)
        return Status("idle", pos, vol)


async def _make(requester, url: str) -> DlnaRenderer | None:
    from async_upnp_client.client_factory import UpnpFactory
    from async_upnp_client.profiles.dlna import DmrDevice

    try:
        dev = await UpnpFactory(requester, non_strict=True).async_create_device(url)
    except Exception as e:  # noqa: BLE001
        log.warning("DLNA renderer %s: %s", url, e)
        return None
    if not DmrDevice.is_profile_device(dev):
        return None
    dmr = DmrDevice(dev, None)
    sink = None
    try:
        sink = (await dmr.async_get_protocol_info()).get("sink") or None
    except Exception:  # noqa: BLE001 - optional action
        pass
    return DlnaRenderer(dev.udn.removeprefix("uuid:"), dev.friendly_name or url, dmr, sink)


_requester = None
_cache: dict[str, DlnaRenderer] = {}


async def discover(cfg, timeout_s: float = 4.0) -> list[Renderer]:
    global _requester
    try:
        from async_upnp_client.aiohttp import AiohttpRequester
        from async_upnp_client.search import async_search
    except ImportError:  # pragma: no cover
        return []
    if _requester is None:
        _requester = AiohttpRequester(timeout=5)
    urls: set[str] = set(cfg.dlna_renderers)
    if cfg.cast_discovery:
        found: set[str] = set()

        async def on_response(headers) -> None:
            st = headers.get("st") or headers.get("nt") or ""
            if st in DMR_TYPES and headers.get("location"):
                found.add(headers["location"])

        try:
            for st in DMR_TYPES[:1]:  # MediaRenderer:1 matches newer versions too (they must answer :1)
                await async_search(on_response, timeout=int(timeout_s), search_target=st)
        except Exception as e:  # noqa: BLE001 - no multicast route etc.
            log.warning("SSDP discovery failed: %s", e)
        urls |= found
    out: list[Renderer] = []
    for url in sorted(urls):
        r = _cache.get(url)
        if r is None:
            r = await _make(_requester, url)
            if r is None:
                continue
            _cache[url] = r
        out.append(r)
    return out
