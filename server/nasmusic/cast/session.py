"""SpeakerSession: plays a user's Connect queue on one Renderer.

A phone or browser is a Connect device that plays the queue itself. A speaker can't, so the server does it
on the speaker's behalf: on `takeover` it loads the current track (signed URL), then polls the renderer,
advances through the queue when a track finishes, executes the commands remotes send, and reports
PlaybackState back to the Hub exactly as an active phone would.

All operations on one session run one at a time (asyncio.Lock, FIFO), in tasks, so the Hub never waits on
the network.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nasmusic.cast.base import Media, Renderer, Status

log = logging.getLogger(__name__)

REPEAT = ("off", "all", "one")
PREV_RESTART_S = 3.0
LOAD_GRACE_S = 8.0  # speakers report idle for a moment after a load; don't read that as "stopped"
UNAVAILABLE_LIMIT = 5  # consecutive failed polls (~5 s) before the speaker counts as dropped
CONNECT_TIMEOUT_S = 10.0  # connect + launch the receiver + load the first track, in total
OP_TIMEOUT_S = 10.0  # any one later operation (command, poll)
STATUS_TIMEOUT_S = 4.0


@dataclass
class CastHooks:
    """What a session needs from the rest of the server. `media`, `queue_track`, `queue_len` are blocking
    (DB) and run in a thread; `report`, `lost`, `error` are coroutines on the Hub's loop."""

    media: Callable[[str], Media | None]
    queue_track: Callable[[str, str, int], str | None]
    queue_len: Callable[[str, str], int]
    report: Callable[[str, str, dict], Awaitable[bool]]  # (uid, device_id, partial state) -> still active?
    lost: Callable[[str, str], Awaitable[None]]  # (uid, device_id): another user took the speaker
    error: Callable[[str, str], Awaitable[None]]  # (uid, message)
    # (uid, device_id, message, positionMs): couldn't connect / dropped. The session has already let go;
    # the Hub hands playback back to the device that picked the speaker.
    failed: Callable[[str, str, str, int], Awaitable[None]] | None = None


def _now_iso() -> str:
    from nasmusic.connect import _now_iso as iso

    return iso()


class SpeakerSession:
    def __init__(
        self, renderer: Renderer, hooks: CastHooks, poll_s: float = 1.0, report_every_s: float = 5.0
    ):
        self.r = renderer
        self.hooks = hooks
        self.poll_s, self.report_every_s = poll_s, report_every_s
        self.owner: str | None = None
        self.queue_id: str | None = None
        self.index = 0
        self.track_id: str | None = None
        self.media: Media | None = None
        self.playing = False
        self.position_s = 0.0
        self.volume: float | None = None
        self.shuffle = False
        self.repeat = "off"
        self._lock = asyncio.Lock()
        self._poll: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._last_report = 0.0
        self._bad = 0
        self._loaded_at = 0.0
        self.connecting = False
        self._connect_task: asyncio.Task | None = None

    @property
    def device_id(self) -> str:
        return self.r.device_id

    # ---------- entry points (called by the Hub; return immediately) ----------

    def spawn(self, coro, timeout_s: float | None = OP_TIMEOUT_S) -> asyncio.Task:
        is_takeover = getattr(coro, "__name__", "") == "takeover"
        if is_takeover:
            timeout_s = None  # bounded inside (CONNECT_TIMEOUT_S), with its own failure handling

        async def run():
            async with self._lock:
                try:
                    if timeout_s is None:
                        await coro
                    else:
                        try:
                            await asyncio.wait_for(coro, timeout_s)
                        except TimeoutError:
                            raise RuntimeError("not responding") from None
                except Exception as e:  # noqa: BLE001 - a speaker failing must not kill the Hub
                    log.warning("%s: %s", self.r.name, e, exc_info=log.isEnabledFor(logging.DEBUG))
                    if self.owner:
                        await self.hooks.error(self.owner, f"{self.r.name}: {e}")
                        self.playing = False
                        await self._report(force=True)

        t = asyncio.create_task(run())
        if is_takeover:
            self._connect_task = t
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def idle(self) -> None:
        """Wait for queued operations (tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def takeover(self, uid: str, state: dict) -> None:
        """Connect, load the current track, start reporting. Bounded by CONNECT_TIMEOUT_S: on failure the
        session lets go and `hooks.failed` hands playback back (the speaker never stays "active" silently)."""
        if self.owner and self.owner != uid:
            await self.hooks.lost(self.owner, self.device_id)
        self.owner = uid
        self.queue_id = state.get("queueId")
        self.index = int(state.get("index") or 0)
        self.track_id = state.get("trackId")
        self.shuffle = bool(state.get("shuffle"))
        self.repeat = state.get("repeat") if state.get("repeat") in REPEAT else "off"
        self.position_s = float(state.get("positionMs") or 0) / 1000
        self.media = None
        self._bad = 0
        self.connecting = True
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_S):
                if not self.track_id and self.queue_id:
                    self.track_id = await asyncio.to_thread(self.hooks.queue_track, uid, self.queue_id, self.index)
                if self.track_id:
                    await self._load(self.position_s, bool(state.get("playing")))
                else:
                    self.playing = False
                st = await self._status()
        except TimeoutError:
            await self._fail(f"Couldn't connect to {self.r.name} (no answer)", reset=True)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("%s: connect/load failed: %s", self.r.name, e)
            await self._fail(f"Couldn't connect to {self.r.name}", reset=True)
            return
        finally:
            self.connecting = False
        if st.volume is not None:
            self.volume = st.volume
        await self._report(force=True)
        self._ensure_poll()

    def cancel_connect(self) -> bool:
        """Abort a takeover that is still connecting (the user cancelled / picked another device)."""
        t = self._connect_task
        if t is not None and not t.done() and self.connecting:
            t.cancel()
            return True
        return False

    async def release(self, uid: str, cancelled: bool = False) -> None:
        if self.owner != uid:
            return
        self.owner = None
        self.playing = False
        self.connecting = False
        self._stop_poll()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.r.stop(), STATUS_TIMEOUT_S)
        if cancelled:  # a load may still be in flight in a worker thread: drop the connection under it
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.r.reset(), STATUS_TIMEOUT_S)

    async def _fail(self, message: str, reset: bool = False) -> None:
        """Give the speaker up after it failed to connect or dropped, and tell the Hub."""
        uid = self.owner
        self.owner = None
        self.playing = False
        self.connecting = False
        self.media = None
        self._stop_poll()
        log.warning("%s: %s", self.r.name, message)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.r.stop(), 2.0)
        if reset:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.r.reset(), 2.0)
        if uid is None:
            return
        if self.hooks.failed is not None:
            await self.hooks.failed(uid, self.device_id, message, int(self.position_s * 1000))
        else:
            await self.hooks.error(uid, message)

    async def command(self, uid: str, action: str, args: dict) -> None:
        if self.owner != uid:
            return
        if action == "play":
            if self.media is None or self.media.track_id != self.track_id:
                await self._load(self.position_s, True)
            else:
                await self.r.play()
                self.playing = True
        elif action == "pause":
            st = await self._status()
            await self.r.pause()
            self.position_s = st.position_s or self.position_s
            self.playing = False
        elif action == "seek":
            self.position_s = max(0.0, float(args.get("positionMs") or 0) / 1000)
            await self.r.seek(self.position_s)
        elif action == "setVolume":
            v = max(0.0, min(1.0, float(args.get("volume", self.volume or 1.0))))
            await self.r.set_volume(v)
            self.volume = v
        elif action == "next":
            await self._advance(+1, self.playing, user=True)
        elif action == "prev":
            st = await self._status()
            if st.position_s > PREV_RESTART_S or self.index == 0:
                self.position_s = 0.0
                await self.r.seek(0)
            else:
                await self._goto(self.index - 1, self.playing)
        elif action == "setShuffle":
            self.shuffle = bool(args.get("on", args.get("shuffle")))
        elif action == "setRepeat":
            mode = args.get("mode", args.get("repeat"))
            self.repeat = mode if mode in REPEAT else "off"
        elif action == "playQueue":
            self.queue_id = str(args.get("queueId") or self.queue_id or "")
            await self._goto(int(args.get("index") or 0), True)
        else:  # setSpeed etc.: not supported on speakers
            return
        self._ensure_poll()
        await self._report(force=True)

    # ---------- internals ----------

    async def _status(self) -> Status:
        try:
            return await asyncio.wait_for(self.r.status(), STATUS_TIMEOUT_S)
        except TimeoutError:
            return Status("unavailable", self.position_s, error="no answer")
        except Exception as e:  # noqa: BLE001
            return Status("unavailable", self.position_s, error=str(e))

    async def _load(self, position_s: float, play: bool) -> None:
        if not self.track_id:
            self.playing = False
            return
        media = await asyncio.to_thread(self.hooks.media, self.track_id)
        if media is None:
            raise RuntimeError("track not found")
        await self.r.load(media, position_s, play)
        self.media, self.position_s, self.playing = media, position_s, play
        self._loaded_at = time.monotonic()

    async def _goto(self, index: int, play: bool) -> None:
        if not self.queue_id or self.owner is None:
            return
        tid = await asyncio.to_thread(self.hooks.queue_track, self.owner, self.queue_id, index)
        if tid is None:
            return
        self.index, self.track_id = index, tid
        await self._load(0.0, play)

    async def _advance(self, step: int, play: bool, user: bool = False) -> None:
        """Next track (user=True: the next button, repeat-one doesn't apply)."""
        if not self.queue_id or self.owner is None:
            self.playing = False
            return
        n = await asyncio.to_thread(self.hooks.queue_len, self.owner, self.queue_id)
        if not user and self.repeat == "one":
            await self._goto(self.index, play)
            return
        if self.shuffle and n > 1:
            nxt = random.choice([i for i in range(n) if i != self.index])
        else:
            nxt = self.index + step
        if nxt >= n:
            if self.repeat == "all" and n:
                nxt = 0
            else:  # end of the queue
                self.playing = False
                self.position_s = 0.0
                with contextlib.suppress(Exception):
                    await self.r.stop()
                self.media = None
                return
        await self._goto(nxt, play)

    async def _report(self, force: bool = False) -> None:
        if self.owner is None:
            return
        now = time.monotonic()
        if not force and now - self._last_report < self.report_every_s:
            return
        self._last_report = now
        partial = {
            "queueId": self.queue_id,
            "index": self.index,
            "trackId": self.track_id,
            "positionMs": int(self.position_s * 1000),
            "positionAt": _now_iso(),
            "playing": self.playing,
            "shuffle": self.shuffle,
            "repeat": self.repeat,
            "speed": 1.0,
        }
        if self.volume is not None:
            partial["volume"] = self.volume
        still = await self.hooks.report(self.owner, self.device_id, partial)
        if not still:  # the Hub moved on without telling us (e.g. a restart of the state)
            self.owner = None
            self._stop_poll()

    def _ensure_poll(self) -> None:
        if self.owner and (self._poll is None or self._poll.done()):
            self._poll = asyncio.create_task(self._poll_loop())

    def _stop_poll(self) -> None:
        t, self._poll = self._poll, None
        if t is not None and t is not asyncio.current_task():
            t.cancel()

    async def _poll_loop(self) -> None:
        try:
            while self.owner is not None:
                await asyncio.sleep(self.poll_s)
                async with self._lock:
                    if self.owner is None:
                        return
                    await self._tick()
        except asyncio.CancelledError:
            pass

    async def _tick(self) -> None:
        st = await self._status()
        changed = False
        if st.volume is not None and (self.volume is None or abs(st.volume - self.volume) > 0.005):
            self.volume, changed = st.volume, True
        if st.state == "unavailable":
            self._bad += 1
            if self._bad >= UNAVAILABLE_LIMIT:
                await self._fail(f"Lost connection to {self.r.name}", reset=True)
            return
        self._bad = 0
        if st.error and self.owner:
            await self.hooks.error(self.owner, f"{self.r.name}: {st.error}")
        if st.state == "finished":
            try:
                await self._advance(+1, True)
            except Exception as e:  # noqa: BLE001
                self.playing = False
                if self.owner:
                    await self.hooks.error(self.owner, f"{self.r.name}: {e}")
            await self._report(force=True)
            return
        if st.state in ("playing", "paused", "buffering"):
            self.position_s = st.position_s
        playing = st.state in ("playing", "buffering")
        if st.state in ("playing", "paused") and playing != self.playing:
            # Paused/resumed on the speaker itself (its own buttons, the Google Home app, HA).
            self.playing, changed = playing, True
        elif (
            st.state == "idle"
            and self.playing
            and self.media is not None
            and time.monotonic() - self._loaded_at > LOAD_GRACE_S
        ):
            # Stopped from elsewhere (another app cast to it): let go of play state.
            self.playing, changed = False, True
        await self._report(force=changed)
