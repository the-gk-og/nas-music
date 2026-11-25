"""Live Jam updates over WebSocket `/api/v1/jam/ws` (docs/jam.md). Separate from Connect: a guest's socket
only ever hears about its own Jam, authenticated by its guest token. The host's apps may listen too (with
their normal session token and `?jam=<id>`), e.g. to see requests waiting for approval.

Server -> client messages:
  {"type": "snapshot", ...}    full per-viewer view (homebeat.jam.snapshot), on connect and after any change
  {"type": "playback", "nowPlaying": {...}, "serverTime": ...}   position/pause changes of the same track
  {"type": "reaction", "emoji": "🔥", "name": "Sam"}
  {"type": "kicked"} then close 4403;  {"type": "ended"} then close 4410;  expired token: close 4401
Client -> server: {"type": "ping"} -> {"type": "pong"}. Anything else is ignored.

Everything runs on the server's event loop; sync code calls the thread-safe `changed`, `kick`, `end`,
`react` and `on_host_event` (a Connect hub listener).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

log = logging.getLogger(__name__)

MAX_MESSAGE = 4096
MAX_SOCKETS_PER_JAM = 200
SWEEP_S = 30.0
DEBOUNCE_S = 0.1


@dataclass(eq=False)
class Sock:
    ws: WebSocket
    jam_id: str
    host_uid: str
    guest_id: str | None          # None = the host
    expires_at: str               # ISO; the socket is closed (4401) after this

    async def send(self, msg: dict) -> bool:
        if self.ws.application_state != WebSocketState.CONNECTED:
            return False
        try:
            await self.ws.send_text(json.dumps(msg, ensure_ascii=False))
            return True
        except (RuntimeError, WebSocketDisconnect, OSError):
            return False

    async def close(self, code: int, reason: str) -> None:
        with contextlib.suppress(Exception):
            await self.ws.close(code=code, reason=reason)


@dataclass
class _Jam:
    socks: set[Sock] = field(default_factory=set)
    last_track: tuple | None = None


class JamHub:
    def __init__(self, view: Callable[[str, str | None], dict | None]):
        """`view(jam_id, guest_id)` -> the snapshot for that viewer (guest_id None = host), or None if the
        Jam/guest is no longer live. Blocking (run in a worker thread)."""
        self._view = view
        self.jams: dict[str, _Jam] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self._pending: set[str] = set()
        self._sweeper: asyncio.Task | None = None

    # ---------- thread-safe API ----------

    def _soon(self, fn, *args) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(fn, *args)
        except RuntimeError:
            pass

    def changed(self, jam_id: str) -> None:
        """Something in the Jam changed: push fresh snapshots (debounced)."""
        self._soon(self._schedule, jam_id)

    def kick(self, jam_id: str, guest_id: str) -> None:
        self._soon(self._spawn, self._kick, jam_id, guest_id)

    def end(self, jam_id: str) -> None:
        self._soon(self._spawn, self._end, jam_id)

    def react(self, jam_id: str, emoji: str, name: str) -> None:
        self._soon(self._spawn, self._broadcast, jam_id, {"type": "reaction", "emoji": emoji, "name": name})

    def on_host_event(self, uid: str, kind: str, payload) -> None:
        """Connect listener: the host's playback state or a queue changed."""
        self._soon(self._host_event, uid, kind, payload)

    # ---------- loop side ----------

    def _spawn(self, fn, *args) -> None:
        asyncio.ensure_future(fn(*args))

    def _host_event(self, uid: str, kind: str, payload) -> None:
        for jid, j in list(self.jams.items()):
            if not j.socks or next(iter(j.socks)).host_uid != uid:
                continue
            if kind == "state" and isinstance(payload, dict):
                key = (payload.get("queueId"), payload.get("index"), payload.get("trackId"))
                if key == j.last_track:
                    asyncio.ensure_future(self._playback(jid, payload))
                    continue
                j.last_track = key
            self._schedule(jid)

    def _schedule(self, jam_id: str) -> None:
        if jam_id in self._pending or jam_id not in self.jams:
            return
        self._pending.add(jam_id)
        asyncio.get_running_loop().call_later(DEBOUNCE_S, lambda: asyncio.ensure_future(self._refresh(jam_id)))

    async def _refresh(self, jam_id: str) -> None:
        self._pending.discard(jam_id)
        j = self.jams.get(jam_id)
        if not j:
            return
        for s in list(j.socks):
            try:
                snap = await asyncio.to_thread(self._view, jam_id, s.guest_id)
            except Exception:
                log.exception("jam snapshot failed")
                continue
            if snap is None:
                await s.close(4401, "no longer in this Jam")
                continue
            await s.send({"type": "snapshot", **snap})

    async def _playback(self, jam_id: str, state: dict) -> None:
        now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        msg = {"type": "playback", "serverTime": now, "nowPlaying": {
            "positionMs": int(state.get("positionMs") or 0), "positionAt": state.get("positionAt") or now,
            "playing": bool(state.get("playing")), "speed": float(state.get("speed") or 1.0)}}
        await self._broadcast(jam_id, msg)

    async def _broadcast(self, jam_id: str, msg: dict) -> None:
        j = self.jams.get(jam_id)
        for s in list(j.socks if j else ()):
            await s.send(msg)

    async def _kick(self, jam_id: str, guest_id: str) -> None:
        j = self.jams.get(jam_id)
        for s in [s for s in (j.socks if j else ()) if s.guest_id == guest_id]:
            await s.send({"type": "kicked"})
            await s.close(4403, "removed by the host")
        self._schedule(jam_id)

    async def _end(self, jam_id: str) -> None:
        j = self.jams.pop(jam_id, None)
        for s in list(j.socks if j else ()):
            await s.send({"type": "ended"})
            await s.close(4410, "the Jam has ended")

    async def _sweep(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_S)
            now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            for j in list(self.jams.values()):
                for s in [s for s in j.socks if s.expires_at <= now]:
                    await s.close(4401, "expired")

    # ---------- the socket ----------

    async def handle(self, sock: Sock) -> None:
        """Serve one accepted, authenticated socket until it closes."""
        self.loop = asyncio.get_running_loop()
        if self._sweeper is None or self._sweeper.done():
            self._sweeper = asyncio.create_task(self._sweep())
        j = self.jams.setdefault(sock.jam_id, _Jam())
        if len(j.socks) >= MAX_SOCKETS_PER_JAM:
            await sock.close(4429, "too many connections")
            return
        j.socks.add(sock)
        try:
            snap = await asyncio.to_thread(self._view, sock.jam_id, sock.guest_id)
            if snap is None:
                await sock.close(4401, "no longer in this Jam")
                return
            await sock.send({"type": "snapshot", **snap})
            while True:
                raw = await sock.ws.receive_text()
                if len(raw) > MAX_MESSAGE:
                    continue
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "ping":
                    await sock.send({"type": "pong"})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            j.socks.discard(sock)
            if not j.socks and self.jams.get(sock.jam_id) is j:
                del self.jams[sock.jam_id]
