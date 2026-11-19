"""Google Cast (Chromecast, Nest speakers, Cast groups, TVs) via pychromecast (MIT).

The speaker runs Google's Default Media Receiver and fetches the signed media URL itself. pychromecast is
thread-based and blocking, so every call goes through asyncio.to_thread.

Discovery is mDNS (zeroconf): it only works when the container shares the LAN's multicast domain (host
networking). `chromecast_hosts` in config.toml lists IPs to probe directly instead.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nasmusic.cast.base import Media, Renderer, Status

log = logging.getLogger(__name__)

# Default Media Receiver: FLAC, MP3, AAC/MP4, Ogg (Vorbis/Opus), WAV. Anything else (ALAC, WMA, ...) -> Opus.
NATIVE_MIMES = {"audio/flac", "audio/mpeg", "audio/mp4", "audio/aac", "audio/ogg", "audio/wav"}
NATIVE_CODECS = {None, "FLAC", "MP3", "AAC", "OPUS", "VORBIS", "PCM"}
CONNECT_TIMEOUT_S = 10.0


def pick_format(media: Media) -> tuple[str, str]:
    """(format, content type) the receiver will play."""
    codec = (getattr(media, "codec", None) or "").upper() or None
    if media.mime in NATIVE_MIMES and codec in NATIVE_CODECS:
        return "original", media.mime
    return "opus", "audio/ogg"


class ChromecastRenderer(Renderer):
    kind = "chromecast"

    def __init__(self, uid: str, name: str, cast: Any):
        super().__init__(uid, name)
        self.cast = cast  # pychromecast.Chromecast (or a test double with the same surface)
        self._ready = False
        self._loaded = False

    async def _ensure(self) -> None:
        if not self._ready:
            await asyncio.to_thread(self.cast.wait, CONNECT_TIMEOUT_S)
            self._ready = True

    @property
    def _mc(self):
        return self.cast.media_controller

    async def load(self, media: Media, position_s: float, play: bool) -> None:
        await self._ensure()
        fmt, ctype = pick_format(media)
        meta = {"metadataType": 3, "title": media.title, "artist": media.artist, "albumName": media.album}
        if media.art_url:
            meta["images"] = [{"url": media.art_url}]

        def go():
            self._mc.play_media(
                media.url(fmt),
                ctype,
                title=media.title,
                thumb=media.art_url,
                current_time=max(0.0, position_s),
                autoplay=play,
                stream_type="BUFFERED",
                metadata=meta,
            )
            self._mc.block_until_active(timeout=CONNECT_TIMEOUT_S)

        await asyncio.to_thread(go)
        self._loaded = True

    async def play(self) -> None:
        await self._ensure()
        await asyncio.to_thread(self._mc.play)

    async def pause(self) -> None:
        await self._ensure()
        await asyncio.to_thread(self._mc.pause)

    async def stop(self) -> None:
        if not self._ready:
            return
        self._loaded = False
        await asyncio.to_thread(self._mc.stop)

    async def seek(self, position_s: float) -> None:
        await self._ensure()
        await asyncio.to_thread(self._mc.seek, max(0.0, position_s))

    async def set_volume(self, volume: float) -> None:
        await self._ensure()
        await asyncio.to_thread(self.cast.set_volume, max(0.0, min(1.0, volume)))

    async def status(self) -> Status:
        if not self._ready:
            return Status("idle")
        st = self._mc.status
        vol = getattr(getattr(self.cast, "status", None), "volume_level", None)
        ps = getattr(st, "player_state", "UNKNOWN")
        pos = float(getattr(st, "adjusted_current_time", None) or getattr(st, "current_time", 0) or 0)
        if ps == "PLAYING":
            return Status("playing", pos, vol)
        if ps == "PAUSED":
            return Status("paused", pos, vol)
        if ps == "BUFFERING":
            return Status("buffering", pos, vol)
        if ps == "IDLE" and self._loaded and getattr(st, "idle_reason", None) == "FINISHED":
            self._loaded = False
            return Status("finished", pos, vol)
        if ps == "IDLE" and getattr(st, "idle_reason", None) == "ERROR":
            return Status("idle", pos, vol, error="the speaker couldn't play this track")
        return Status("idle", pos, vol)

    async def close(self) -> None:
        if self._ready:
            await asyncio.to_thread(self.cast.disconnect, 2)


class _Browser:
    """One long-lived CastBrowser (mDNS + polling of manual hosts). Chromecast objects need its zeroconf
    instance for reconnects, so it is never torn down while the server runs."""

    def __init__(self, cfg):
        import pychromecast  # noqa: F401 - fail early if missing
        import zeroconf
        from pychromecast import discovery as d

        self.zconf = zeroconf.Zeroconf() if cfg.cast_discovery else None
        self.browser = d.CastBrowser(d.SimpleCastListener(), self.zconf, list(cfg.chromecast_hosts) or None)
        self.browser.start_discovery()
        self.casts: dict[str, Any] = {}

    def renderers(self) -> list[Renderer]:
        import pychromecast

        out = []
        for uuid, info in list(self.browser.devices.items()):
            key = str(uuid)
            c = self.casts.get(key)
            if c is None:
                try:
                    c = self.casts[key] = pychromecast.get_chromecast_from_cast_info(
                        info, self.zconf, tries=3
                    )
                except Exception as e:  # noqa: BLE001
                    log.warning("Chromecast %s: %s", info.friendly_name or info.host, e)
                    continue
            out.append(ChromecastRenderer(key, info.friendly_name or info.host, c))
        return out


_browser: _Browser | None = None


async def discover(cfg, timeout_s: float = 5.0) -> list[Renderer]:
    """Renderers seen so far. The first call starts the browser and waits `timeout_s` for answers."""
    global _browser
    if not (cfg.cast_discovery or cfg.chromecast_hosts):
        return []
    if _browser is None:
        try:
            _browser = await asyncio.to_thread(_Browser, cfg)
        except Exception as e:  # noqa: BLE001 - zeroconf can fail on odd networks
            log.warning("Chromecast discovery unavailable: %s", e)
            return []
        await asyncio.sleep(timeout_s)
    return await asyncio.to_thread(_browser.renderers)
