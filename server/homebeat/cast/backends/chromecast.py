"""Google Cast (Chromecast, Nest speakers, Cast groups, TVs) via pychromecast (MIT).

The speaker runs Google's Default Media Receiver and fetches the signed media URL itself. pychromecast is
thread-based and blocking, so every call goes through asyncio.to_thread.

Discovery is mDNS (zeroconf): it only works when the container shares the LAN's multicast domain (host
networking). `chromecast_hosts` in config.toml lists IPs to probe directly instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from typing import Any

from homebeat.cast.base import Media, Renderer, Status

log = logging.getLogger(__name__)

# Default Media Receiver: FLAC, MP3, AAC/MP4, Ogg (Vorbis/Opus), WAV. Anything else (ALAC, WMA, ...) -> Opus.
NATIVE_MIMES = {"audio/flac", "audio/mpeg", "audio/mp4", "audio/aac", "audio/ogg", "audio/wav"}
NATIVE_CODECS = {None, "FLAC", "MP3", "AAC", "OPUS", "VORBIS", "PCM"}
CONNECT_TIMEOUT_S = 5.0  # TCP + TLS + first status from the device
LOAD_TIMEOUT_S = 8.0  # launch the Default Media Receiver + LOAD answered
# The session bounds connect + load together (cast.session.CONNECT_TIMEOUT_S); these keep worker threads short.


class CastError(RuntimeError):
    pass


def pick_format(media: Media) -> tuple[str, str]:
    """(format, content type) the receiver will play."""
    codec = (getattr(media, "codec", None) or "").upper() or None
    if media.mime in NATIVE_MIMES and codec in NATIVE_CODECS:
        return "original", media.mime
    return "opus", "audio/ogg"


class ChromecastRenderer(Renderer):
    kind = "chromecast"

    def __init__(self, uid: str, name: str, cast: Any = None, factory: Any = None):
        super().__init__(uid, name)
        # pychromecast.Chromecast (or a test double with the same surface). `factory()` makes a fresh one:
        # after a failure the old one is dropped (its socket thread would retry forever) and rebuilt.
        self.cast = cast
        self.factory = factory
        self._ready = False
        self._loaded = False

    def _connected(self) -> bool:
        sc = getattr(self.cast, "socket_client", None)
        return True if sc is None else bool(getattr(sc, "is_connected", True))

    async def _ensure(self) -> None:
        if self._ready and not self._connected():
            raise CastError("not connected")
        if not self._ready:
            if self.cast is None:
                if self.factory is None:
                    raise CastError("no connection")
                self.cast = await asyncio.to_thread(self.factory)
            try:
                await asyncio.to_thread(self.cast.wait, CONNECT_TIMEOUT_S)
            except Exception as e:  # pychromecast.RequestTimeout etc.
                raise CastError(f"no answer from {self.name}") from e
            self._ready = True

    async def reset(self) -> None:
        """Stop the socket thread and forget the connection; the next call reconnects from scratch."""
        cast, ready = self.cast, self._ready
        self._ready = self._loaded = False
        if self.factory is not None:
            self.cast = None
        if cast is not None and (ready or self.factory is not None):
            with contextlib.suppress(Exception):  # join(0) raises while the thread winds down
                await asyncio.to_thread(cast.disconnect, 0)

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
            # play_media() only queues the request (launch receiver, then LOAD); its callback says whether
            # the device accepted it. Without waiting on that a dead/unresponsive speaker looks "loaded".
            done = threading.Event()
            result: dict = {}

            def cb(ok: bool, response=None):
                result["ok"], result["response"] = ok, response
                done.set()

            self._mc.play_media(
                media.url(fmt),
                ctype,
                title=media.title,
                thumb=media.art_url,
                current_time=max(0.0, position_s),
                autoplay=play,
                stream_type="BUFFERED",
                metadata=meta,
                callback_function=cb,
            )
            if not done.wait(LOAD_TIMEOUT_S):
                raise CastError(f"{self.name} didn't answer the load request")
            if not result.get("ok"):
                resp = result.get("response") or {}
                reason = resp.get("reason") or resp.get("type") if isinstance(resp, dict) else None
                raise CastError(f"{self.name} refused the track" + (f" ({reason})" if reason else ""))

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
        if not self._connected():
            return Status("unavailable", error="disconnected")
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
        if self._ready and self.cast is not None:
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
        self.renderers_by_id: dict[str, ChromecastRenderer] = {}

    def renderers(self) -> list[Renderer]:
        import pychromecast

        out = []
        for uuid, info in list(self.browser.devices.items()):
            key = str(uuid)
            name = info.friendly_name or info.host
            r = self.renderers_by_id.get(key)
            if r is None:
                # Connect lazily, on first use: building a Chromecast starts a socket thread that retries
                # forever, so unreachable speakers must not get one just for being listed. Cast groups
                # (cast_type "group", port != 8009) work the same way through their elected leader.
                def factory(info=info):
                    return pychromecast.get_chromecast_from_cast_info(
                        info, self.zconf, tries=2, timeout=CONNECT_TIMEOUT_S, retry_wait=2
                    )

                r = self.renderers_by_id[key] = ChromecastRenderer(key, name, factory=factory)
            r.name = name
            out.append(r)
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
