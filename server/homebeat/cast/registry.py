"""SpeakerRegistry: the speakers this server can cast to, found by each backend's `discover(cfg)`.

Discovery runs on the Hub's event loop: once when the first Connect socket opens, then every
`refresh_s` seconds, and on demand (POST /cast/speakers/refresh). A speaker that drops out of discovery is
kept while it's playing for someone (mDNS/SSDP answers are flaky); otherwise it disappears from the list.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable

from homebeat.cast.base import Renderer
from homebeat.cast.session import CastHooks, SpeakerSession

log = logging.getLogger(__name__)

Discoverer = Callable[[object], Awaitable[list[Renderer]]]


def default_discoverers() -> list[Discoverer]:
    from homebeat.cast.backends import chromecast, dlna, homeassistant, snapcast

    return [chromecast.discover, dlna.discover, homeassistant.discover, snapcast.discover]


class SpeakerRegistry:
    def __init__(
        self,
        cfg,
        hooks: CastHooks | None = None,
        discoverers: list[Discoverer] | None = None,
        refresh_s: float = 300.0,
        poll_s: float = 1.0,
    ):
        self.cfg = cfg
        self.hooks = hooks
        self.discoverers = discoverers if discoverers is not None else default_discoverers()
        self.refresh_s, self.poll_s = refresh_s, poll_s
        self.sessions: dict[str, SpeakerSession] = {}
        self.on_change: Callable[[], Awaitable[None]] | None = None
        self.last_refresh: float | None = None
        self.errors: dict[str, str] = {}
        self._task: asyncio.Task | None = None
        self._refreshing: asyncio.Lock | None = None

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.cfg, "cast_enabled", True))

    def start(self) -> None:
        """Begin background discovery (idempotent; needs a running loop)."""
        if not self.enabled or (self._task is not None and not self._task.done()):
            return
        self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        for s in list(self.sessions.values()):
            with contextlib.suppress(Exception):
                await s.r.close()

    async def _loop(self) -> None:
        while True:
            try:
                await self.refresh()
            except Exception:  # noqa: BLE001
                log.exception("speaker discovery failed")
            await asyncio.sleep(self.refresh_s)

    async def refresh(self) -> list[dict]:
        if self._refreshing is None:
            self._refreshing = asyncio.Lock()
        async with self._refreshing:
            results = await asyncio.gather(*(d(self.cfg) for d in self.discoverers), return_exceptions=True)
            found: dict[str, Renderer] = {}
            self.errors = {}
            for d, res in zip(self.discoverers, results, strict=True):
                name = getattr(d, "__module__", "?").rsplit(".", 1)[-1]
                if isinstance(res, BaseException):
                    self.errors[name] = str(res)
                    log.warning("speaker discovery (%s): %s", name, res)
                    continue
                for r in res:
                    found.setdefault(r.device_id, r)
            before = {k: s.r.name for k, s in self.sessions.items()}
            for did, r in found.items():
                s = self.sessions.get(did)
                if s is None:
                    self.sessions[did] = SpeakerSession(r, self._hooks(), poll_s=self.poll_s)
                else:
                    s.r.name = r.name
            for did in list(self.sessions):
                if did not in found and self.sessions[did].owner is None:
                    del self.sessions[did]
            self.last_refresh = asyncio.get_running_loop().time()
            if before != {k: s.r.name for k, s in self.sessions.items()} and self.on_change is not None:
                await self.on_change()
            return self.devices()

    def _hooks(self) -> CastHooks:
        if self.hooks is None:
            raise RuntimeError("SpeakerRegistry.hooks not set")
        return self.hooks

    def get(self, device_id: str | None) -> SpeakerSession | None:
        return self.sessions.get(device_id) if device_id else None

    def devices(self) -> list[dict]:
        return [
            {**s.r.info(), "kind": "speaker", "canPlay": True, "online": True, "busy": s.owner is not None}
            for s in self.sessions.values()
        ]
