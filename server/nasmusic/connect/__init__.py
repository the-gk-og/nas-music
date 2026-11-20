"""Connect: Spotify-Connect-style live device control (docs/v2.md §5).

One in-memory `Hub` per server process holds, per user: the PlaybackState (with a server-assigned `rev`),
the connected devices, and which device is active. Only the active device plays audio; the others are
remotes whose commands are routed to it. State is persisted to `playback_state` (throttled, and
immediately on transfers / pauses) and reloaded on first use after a restart.

Concurrency: everything runs on the event loop that serves the WebSockets. Other threads (sync API
endpoints) use the thread-safe `notify_queue` / `disconnect_device` / `online_device_ids`.
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

from nasmusic.auth import Device

log = logging.getLogger(__name__)

STATE_FIELDS = {
    "queueId": (str, type(None)), "index": (int,), "trackId": (str, type(None)), "positionMs": (int, float),
    "positionAt": (str,), "playing": (bool,), "volume": (int, float), "shuffle": (bool,), "repeat": (str,),  # noqa: E501
    "speed": (int, float),
    # The active phone is playing through a Google Cast session it started: the speaker's name, else None.
    "castTo": (str, type(None)),
}
REPEAT = ("off", "all", "one")
ACTIONS = ("play", "pause", "next", "prev", "seek", "setVolume", "setShuffle", "setRepeat", "playQueue",
           "playTracks", "setSpeed")
MAX_MESSAGE = 256 * 1024
HELLO_TIMEOUT_S = 15.0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def default_state() -> dict:
    return {"activeDeviceId": None, "queueId": None, "index": 0, "trackId": None, "positionMs": 0,
            "positionAt": _now_iso(), "playing": False, "volume": 1.0, "shuffle": False, "repeat": "off",
            "speed": 1.0, "castTo": None, "rev": 0}


def position_now(state: dict) -> int:
    """positionMs extrapolated to now while playing."""
    pos = int(state.get("positionMs") or 0)
    if not state.get("playing"):
        return pos
    try:
        at = datetime.fromisoformat(str(state.get("positionAt")).replace("Z", "+00:00"))
    except ValueError:
        return pos
    elapsed = (datetime.now(UTC) - at).total_seconds() * 1000 * float(state.get("speed") or 1.0)
    return max(0, int(pos + max(0.0, elapsed)))


@dataclass
class Conn:
    ws: WebSocket
    device_id: str
    name: str
    kind: str
    can_play: bool = True
    app_version: str | None = None

    async def send(self, msg: dict) -> bool:
        if self.ws.application_state != WebSocketState.CONNECTED:
            return False
        try:
            await self.ws.send_text(json.dumps(msg))
            return True
        except (RuntimeError, WebSocketDisconnect, OSError):
            return False


class SpeakerConn:
    """A server-side speaker posing as a Connect device: messages the Hub would send to an active phone go
    to its SpeakerSession instead (which runs them in its own task)."""

    kind = "speaker"
    can_play = True
    ws = None

    def __init__(self, uid: str, session):
        self.uid, self.session = uid, session
        self.device_id = session.device_id
        self.name = session.r.name

    async def send(self, msg: dict) -> bool:
        t = msg.get("type")
        if t == "takeover":
            self.session.spawn(self.session.takeover(self.uid, msg.get("state") or {}))
        elif t == "release":
            self.session.spawn(self.session.release(self.uid))
        elif t == "command":
            self.session.spawn(self.session.command(self.uid, str(msg.get("action")), msg.get("args") or {}))
        return True


@dataclass
class UserHub:
    state: dict = field(default_factory=default_state)
    conns: dict[str, Conn] = field(default_factory=dict)
    known: dict[str, dict] = field(default_factory=dict)   # device_id -> last hello info (for the active one)
    release_task: asyncio.Task | None = None
    flush_task: asyncio.Task | None = None
    dirty: bool = False
    loaded: bool = False


class Hub:
    def __init__(self, load: Callable[[str], dict | None], save: Callable[[str, dict], None],
                 make_queue: Callable[[str, list[str], str, int], str] | None = None,
                 queue_track: Callable[[str, str, int], str | None] | None = None,
                 grace_s: float = 15.0, persist_every_s: float = 3.0):
        """`load(uid)` / `save(uid, state)` persist PlaybackState; `make_queue(uid, track_ids, name, index)`
        creates-or-replaces a named queue and returns its id; `queue_track(uid, queue_id, index)` returns the
        track id at that index. The callables are blocking (run in a worker thread)."""
        self._load, self._save, self._make_queue, self._queue_track = load, save, make_queue, queue_track
        self.grace_s, self.persist_every_s = grace_s, persist_every_s
        self.users: dict[str, UserHub] = {}
        self.loop: asyncio.AbstractEventLoop | None = None
        self.speakers = None   # nasmusic.cast.SpeakerRegistry (attach_speakers)

    def attach_speakers(self, registry) -> None:
        """Offer the registry's speakers as Connect devices to every user (see nasmusic.cast)."""
        from nasmusic.cast import CastHooks

        self.speakers = registry
        hooks = registry.hooks
        registry.hooks = CastHooks(
            media=hooks.media, queue_track=hooks.queue_track, queue_len=hooks.queue_len,
            report=self.speaker_report, lost=self.speaker_lost, error=self.speaker_error)
        registry.on_change = self._broadcast_devices_all

    # ---------- thread-safe API for sync code ----------

    def _call_soon(self, coro_fn, *args) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            return
        try:
            asyncio.run_coroutine_threadsafe(coro_fn(*args), loop)
        except RuntimeError:
            pass

    def notify_queue(self, uid: str, queue_id: str) -> None:
        """A queue changed (REST API): tell all the user's devices to refetch it."""
        self._call_soon(self._broadcast, uid, {"type": "queues", "queueId": queue_id})

    def notify_playlist(self, uids, playlist_id: str, rev: int | None, deleted: bool = False) -> None:
        """A playlist changed (REST API): tell every device of everyone with access (owner + members) to
        refetch it. `rev` lets a client skip a refetch it already has; `deleted` = gone or access removed."""
        msg = {"type": "playlist", "playlistId": playlist_id, "rev": rev, "deleted": deleted}
        for uid in set(uids):
            self._call_soon(self._broadcast, uid, msg)

    def notify_family(self, uid: str, policy: dict) -> None:
        """Family mode changed (the user or an admin): devices refetch the library / hide tracks."""
        self._call_soon(self._broadcast, uid, {"type": "family", "family": policy})

    def disconnect_device(self, device_id: str) -> None:
        """A device was signed out / revoked: close its socket."""
        self._call_soon(self._close_device, device_id)

    def online_device_ids(self, uid: str) -> set[str]:
        u = self.users.get(uid)
        return set(list(u.conns)) if u else set()

    def devices_for(self, uid: str) -> list[dict]:
        """The user's Connect devices (online ones + an offline active one), for the Home "devices" shelf."""
        u = self.users.get(uid)
        return self._devices(u) if u else []

    def snapshot(self, uid: str) -> dict | None:
        u = self.users.get(uid)
        return dict(u.state) if u else None

    # ---------- internals ----------

    async def _user(self, uid: str) -> UserHub:
        u = self.users.get(uid)
        if u is None:
            u = self.users[uid] = UserHub()
        if not u.loaded:
            u.loaded = True
            try:
                stored = await asyncio.to_thread(self._load, uid)
            except Exception:
                log.exception("loading playback state for %s failed", uid)
                stored = None
            if stored:
                st = default_state()
                st.update({k: v for k, v in stored.items() if k in st})
                st["playing"] = False  # nothing is playing right after a server restart
                u.state = st
        return u

    def _devices(self, u: UserHub) -> list[dict]:
        active = u.state.get("activeDeviceId")
        out = [{"id": c.device_id, "name": c.name, "kind": c.kind, "canPlay": c.can_play, "online": True,
                "isActive": c.device_id == active} for c in u.conns.values()]
        speakers = self.speakers.devices() if self.speakers is not None else []
        for d in speakers:
            out.append({"id": d["id"], "name": d["name"], "kind": "speaker", "canPlay": True, "online": True,
                        "isActive": d["id"] == active, "castType": d.get("castType")})
        if (active and active not in u.conns and active in u.known
                and not any(d["id"] == active for d in speakers)):
            k = u.known[active]
            out.append({"id": active, "name": k["name"], "kind": k["kind"], "canPlay": k["canPlay"],
                        "online": False, "isActive": True})
        return out

    def _target(self, uid: str, u: UserHub, device_id: str | None):
        """The Conn (phone/browser socket) or SpeakerConn for a device id, or None if it's not reachable."""
        if not device_id:
            return None
        c = u.conns.get(device_id)
        if c is not None:
            return c
        sess = self.speakers.get(device_id) if self.speakers is not None else None
        return SpeakerConn(uid, sess) if sess is not None else None

    async def _broadcast_devices_all(self) -> None:
        for uid, u in list(self.users.items()):
            await self._broadcast_devices(uid, u)

    # ---------- speaker callbacks (nasmusic.cast.SpeakerSession) ----------

    async def speaker_report(self, uid: str, device_id: str, partial: dict) -> bool:
        """A speaker playing for `uid` reports its state, like an active phone's `state` message.
        Returns False if it's no longer the active device (it should stop)."""
        u = await self._user(uid)
        if u.state.get("activeDeviceId") != device_id:
            return False
        was = u.state.get("playing")
        for k, types in STATE_FIELDS.items():
            v = partial.get(k, ...)
            if v is ... or (isinstance(v, bool) and bool not in types):
                continue
            if isinstance(v, types):
                u.state[k] = v
        await self._changed(uid, u, force_persist=bool(was) and not u.state.get("playing"))
        return True

    async def speaker_lost(self, uid: str, device_id: str) -> None:
        """Another user took this speaker over: `uid` has no active device any more."""
        u = await self._user(uid)
        if u.state.get("activeDeviceId") != device_id:
            return
        self._pause_in_place(u.state)
        u.state["activeDeviceId"] = None
        u.state["castTo"] = None
        await self._changed(uid, u, force_persist=True)
        await self._broadcast(uid, {"type": "error", "source": "speaker",
                                    "detail": "someone else started playing on that speaker"})
        await self._broadcast_devices(uid, u)

    async def speaker_error(self, uid: str, detail: str) -> None:
        await self._broadcast(uid, {"type": "error", "source": "speaker", "detail": detail})

    async def _broadcast(self, uid: str, msg: dict, exclude: str | None = None) -> None:
        u = self.users.get(uid)
        if not u:
            return
        for c in list(u.conns.values()):
            if c.device_id != exclude:
                await c.send(msg)

    async def _broadcast_devices(self, uid: str, u: UserHub) -> None:
        await self._broadcast(uid, {"type": "devices", "devices": self._devices(u)})

    async def _changed(self, uid: str, u: UserHub, force_persist: bool = False) -> None:
        """Bump rev, broadcast the state, persist (throttled unless forced)."""
        u.state["rev"] = int(u.state.get("rev", 0)) + 1
        await self._broadcast(uid, {"type": "state", "state": dict(u.state)})
        u.dirty = True
        if force_persist:
            await self._flush(uid, u)
        elif u.flush_task is None or u.flush_task.done():
            u.flush_task = asyncio.create_task(self._flush_later(uid, u))

    async def _flush_later(self, uid: str, u: UserHub) -> None:
        await asyncio.sleep(self.persist_every_s)
        await self._flush(uid, u)

    async def _flush(self, uid: str, u: UserHub) -> None:
        if not u.dirty:
            return
        u.dirty = False
        try:
            await asyncio.to_thread(self._save, uid, dict(u.state))
        except Exception:
            log.exception("saving playback state for %s failed", uid)

    async def _close_device(self, device_id: str) -> None:
        for u in self.users.values():
            c = u.conns.get(device_id)
            if c:
                with contextlib.suppress(Exception):
                    await c.ws.close(code=4401, reason="signed out")

    def _pause_in_place(self, state: dict) -> None:
        state["positionMs"] = position_now(state)
        state["positionAt"] = _now_iso()
        state["playing"] = False

    async def _release_after_grace(self, uid: str, u: UserHub, device_id: str) -> None:
        try:
            await asyncio.sleep(self.grace_s)
        except asyncio.CancelledError:
            return
        if device_id in u.conns or u.state.get("activeDeviceId") != device_id:
            return
        if u.state.get("playing"):
            # Keep activeDeviceId so a reconnect resumes control; any device may transfer to itself.
            self._pause_in_place(u.state)
            await self._changed(uid, u, force_persist=True)
        await self._broadcast_devices(uid, u)

    # ---------- the socket ----------

    async def handle(self, ws: WebSocket, dev: Device) -> None:
        """Serve one accepted socket until it closes."""
        self.loop = asyncio.get_running_loop()
        if self.speakers is not None:
            self.speakers.start()
        uid = dev.user_id
        try:
            first = await asyncio.wait_for(ws.receive_text(), HELLO_TIMEOUT_S)
            hello = json.loads(first)
            if not isinstance(hello, dict) or hello.get("type") != "hello":
                raise ValueError("first message must be hello")
        except (asyncio.TimeoutError, ValueError, WebSocketDisconnect, RuntimeError):
            with contextlib.suppress(Exception):
                await ws.close(code=4400, reason="expected hello")
            return
        kind = hello.get("kind") if hello.get("kind") in ("android", "web", "desktop", "watch", "other") else dev.kind
        conn = Conn(ws=ws, device_id=dev.id, name=str(hello.get("deviceName") or dev.name)[:100], kind=kind,
                    can_play=bool(hello.get("canPlay", True)), app_version=hello.get("appVersion"))
        u = await self._user(uid)
        old = u.conns.get(dev.id)
        u.conns[dev.id] = conn
        u.known[dev.id] = {"name": conn.name, "kind": conn.kind, "canPlay": conn.can_play}
        if old is not None:
            with contextlib.suppress(Exception):
                await old.ws.close(code=4000, reason="replaced by a newer connection")
        if u.release_task and u.state.get("activeDeviceId") == dev.id:
            u.release_task.cancel()
            u.release_task = None
        await conn.send({"type": "welcome", "deviceId": dev.id, "devices": self._devices(u),
                         "state": dict(u.state)})
        await self._broadcast_devices(uid, u)
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > MAX_MESSAGE:
                    await conn.send({"type": "error", "detail": "message too large"})
                    continue
                try:
                    msg = json.loads(raw)
                    if not isinstance(msg, dict):
                        raise ValueError
                except ValueError:
                    await conn.send({"type": "error", "detail": "invalid JSON"})
                    continue
                await self._dispatch(uid, u, conn, msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            if u.conns.get(dev.id) is conn:
                del u.conns[dev.id]
                if u.state.get("activeDeviceId") == dev.id:
                    u.release_task = asyncio.create_task(self._release_after_grace(uid, u, dev.id))
                await self._broadcast_devices(uid, u)

    async def _dispatch(self, uid: str, u: UserHub, conn: Conn, msg: dict) -> None:
        t = msg.get("type")
        if t == "ping":
            await conn.send({"type": "pong"})
        elif t == "hello":
            conn.can_play = bool(msg.get("canPlay", conn.can_play))
            conn.name = str(msg.get("deviceName") or conn.name)[:100]
            await self._broadcast_devices(uid, u)
        elif t == "state":
            await self._on_state(uid, u, conn, msg)
        elif t == "command":
            await self._on_command(uid, u, conn, msg)
        elif t == "transfer":
            await self._on_transfer(uid, u, conn, msg)
        else:
            await conn.send({"type": "error", "detail": f"unknown message type {t!r}"})

    async def _on_state(self, uid: str, u: UserHub, conn: Conn, msg: dict) -> None:
        if u.state.get("activeDeviceId") != conn.device_id:
            await conn.send({"type": "error", "detail": "only the active device reports state"})
            return
        incoming = msg.get("state") if isinstance(msg.get("state"), dict) else msg
        was_playing = u.state.get("playing")
        for k, types in STATE_FIELDS.items():
            v = incoming.get(k, ...)
            if v is ... or (isinstance(v, bool) and bool not in types):
                continue
            if isinstance(v, types):
                u.state[k] = v
        if u.state.get("repeat") not in REPEAT:
            u.state["repeat"] = "off"
        if "castTo" not in incoming:  # clients that don't know the field (or stopped casting) aren't casting
            u.state["castTo"] = None
        if "positionAt" not in incoming:
            u.state["positionAt"] = _now_iso()
        u.state["positionMs"] = int(u.state.get("positionMs") or 0)
        await self._changed(uid, u, force_persist=bool(was_playing) and not u.state.get("playing"))

    async def _make_active(self, uid: str, u: UserHub, target: Conn, play: bool | None,
                           extra: dict | None = None) -> None:
        prev = u.state.get("activeDeviceId")
        if u.state.get("playing"):
            u.state["positionMs"] = position_now(u.state)
        u.state["positionAt"] = _now_iso()
        if extra:
            u.state.update(extra)
        if play is not None:
            u.state["playing"] = bool(play)
        u.state["activeDeviceId"] = target.device_id
        u.state["castTo"] = None
        if u.release_task:
            u.release_task.cancel()
            u.release_task = None
        if prev and prev != target.device_id:
            old = self._target(uid, u, prev)
            if old is not None:
                await old.send({"type": "release"})
        await self._changed(uid, u, force_persist=True)
        await target.send({"type": "takeover", "state": dict(u.state)})
        await self._broadcast_devices(uid, u)

    async def _on_transfer(self, uid: str, u: UserHub, conn: Conn, msg: dict) -> None:
        to = msg.get("toDeviceId") or conn.device_id
        target = self._target(uid, u, to)
        if target is None:
            await conn.send({"type": "error", "detail": "that device is offline"})
            return
        if not target.can_play:
            await conn.send({"type": "error", "detail": "that device can't play audio"})
            return
        play = msg.get("play")
        await self._make_active(uid, u, target, None if play is None else bool(play))

    async def _play_extra(self, uid: str, action: str, args: dict) -> dict | None:
        if action != "playQueue":
            return None
        idx = int(args.get("index") or 0)
        track = None
        if self._queue_track is not None:
            track = await asyncio.to_thread(self._queue_track, uid, str(args.get("queueId")), idx)
        return {"queueId": args.get("queueId"), "index": idx, "trackId": track, "positionMs": 0}

    async def _on_command(self, uid: str, u: UserHub, conn: Conn, msg: dict) -> None:
        action, args = msg.get("action"), msg.get("args") if isinstance(msg.get("args"), dict) else {}
        if action not in ACTIONS:
            await conn.send({"type": "error", "detail": f"unknown action {action!r}"})
            return
        if action == "playTracks":
            ids = [i for i in args.get("trackIds", []) if isinstance(i, str)]
            if not ids or self._make_queue is None:
                await conn.send({"type": "error", "detail": "playTracks needs trackIds"})
                return
            index = int(args.get("index") or 0)
            name = str(args.get("queueName") or "Now playing")[:200]
            try:
                qid = await asyncio.to_thread(self._make_queue, uid, ids, name, index)
            except Exception as e:
                await conn.send({"type": "error", "detail": f"could not create queue: {e}"})
                return
            await self._broadcast(uid, {"type": "queues", "queueId": qid})
            action, args = "playQueue", {"queueId": qid, "index": index}

        active_id = u.state.get("activeDeviceId")
        target_id = msg.get("targetDeviceId") or active_id
        target = self._target(uid, u, target_id)
        if isinstance(target, SpeakerConn) and target_id != active_id:
            # Playing straight onto a speaker that isn't active yet: make it active with that queue.
            if action in ("play", "playQueue"):
                await self._make_active(uid, u, target, True, await self._play_extra(uid, action, args))
            else:
                await conn.send({"type": "error", "detail": "that speaker isn't playing for you"})
            return
        if target is None:
            # No reachable player: a play from a device that can play makes it the active device.
            if action in ("play", "playQueue") and conn.can_play:
                await self._make_active(uid, u, conn, True, await self._play_extra(uid, action, args))
                return
            await conn.send({"type": "error",
                             "detail": "the active device is offline" if active_id else "no active device"})
            return
        await target.send({"type": "command", "action": action, "args": args, "fromDeviceId": conn.device_id})


def state_from_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    return v if isinstance(v, dict) else None

