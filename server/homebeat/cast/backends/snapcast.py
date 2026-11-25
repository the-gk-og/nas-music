"""Snapcast multi-room (snapserver) via its JSON-RPC control API (TCP, port 1705) and a TCP stream source.

The server decodes the track itself with ffmpeg (real-time paced, `-re`) to raw PCM and writes it to a
snapserver source configured as

    [stream]
    source = tcp://0.0.0.0:4953?name=homebeat&mode=server&sampleformat=48000:16:2

Each Snapcast *group* is offered as a speaker; playing on it points that group at the `homebeat` source.
There is one PCM feed per server, so two groups playing at once hear the same thing (group the rooms in
Snapcast for multi-room). Pause/seek restart ffmpeg at the position.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging

from homebeat.cast.base import Media, Renderer, Status

log = logging.getLogger(__name__)

RATE, CHANNELS, SAMPLE_BYTES = 48000, 2, 2
BYTES_PER_S = RATE * CHANNELS * SAMPLE_BYTES
CHUNK = 4096


class SnapControl:
    """Minimal JSON-RPC client: one request per connection (snapserver closes nothing, so we do)."""

    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self._ids = itertools.count(1)

    async def call(self, method: str, params: dict | None = None, timeout: float = 5.0) -> dict:
        rid = next(self._ids)
        reader, writer = await asyncio.wait_for(asyncio.open_connection(self.host, self.port), timeout)
        try:
            req = {"id": rid, "jsonrpc": "2.0", "method": method}
            if params is not None:
                req["params"] = params
            writer.write((json.dumps(req) + "\r\n").encode())
            await writer.drain()
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout)
                if not line:
                    raise ConnectionError("snapserver closed the connection")
                msg = json.loads(line)
                if isinstance(msg, dict) and msg.get("id") == rid:  # skip notifications
                    if "error" in msg:
                        raise RuntimeError(f"snapserver {method}: {msg['error']}")
                    return msg.get("result") or {}
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


class Feeder:
    """Plays one file into the snapserver TCP source in real time."""

    def __init__(self, host: str, port: int, ffmpeg: str = "ffmpeg"):
        self.host, self.port, self.ffmpeg = host, port, ffmpeg
        self.task: asyncio.Task | None = None
        self.media: Media | None = None
        self.start_s = 0.0
        self.sent = 0
        self.finished = False
        self.error: str | None = None
        self.owner: SnapcastRenderer | None = None

    @property
    def position_s(self) -> float:
        return self.start_s + self.sent / BYTES_PER_S

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    async def start(self, media: Media, position_s: float) -> None:
        await self.halt()
        self.media, self.start_s, self.sent, self.finished, self.error = (
            media,
            max(0.0, position_s),
            0,
            False,
            None,
        )
        self.task = asyncio.create_task(self._run(media, self.start_s))

    async def halt(self) -> None:
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.task
        self.task = None

    async def _run(self, media: Media, pos: float) -> None:
        src = str(media.path) if media.path else media.url("original")
        cmd = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-ss",
            f"{pos:.3f}",
            "-i",
            src,
            "-map",
            "0:a:0",
            "-vn",
            "-f",
            "s16le",
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(RATE),
            "-ac",
            str(CHANNELS),
            "pipe:1",
        ]
        proc = None
        writer = None
        try:
            _, writer = await asyncio.open_connection(self.host, self.port)
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            assert proc.stdout is not None
            while True:
                chunk = await proc.stdout.read(CHUNK)
                if not chunk:
                    break
                writer.write(chunk)
                await writer.drain()
                self.sent += len(chunk)
            rc = await proc.wait()
            if rc == 0:
                self.finished = True
            else:
                err = (await proc.stderr.read()).decode(errors="replace")[-300:] if proc.stderr else ""
                self.error = f"ffmpeg exited {rc}: {err}"
        except (OSError, ConnectionError) as e:
            self.error = f"snapserver stream port: {e}"
            log.warning("Snapcast feed: %s", e)
        finally:
            if proc is not None and proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()


class SnapcastRenderer(Renderer):
    kind = "snapcast"

    def __init__(self, group_id: str, name: str, ctl: SnapControl, feeder: Feeder, stream_id: str):
        super().__init__(group_id, name)
        self.ctl, self.feeder, self.stream_id = ctl, feeder, stream_id
        self.group_id = group_id
        self._paused_at: float | None = None
        self._volume: float | None = None

    async def _claim(self) -> None:
        prev = self.feeder.owner
        if prev is not None and prev is not self:
            prev._paused_at = None  # it no longer owns the feed; its status() goes idle
        self.feeder.owner = self
        await self.ctl.call("Group.SetStream", {"id": self.group_id, "stream_id": self.stream_id})

    async def load(self, media: Media, position_s: float, play: bool) -> None:
        await self._claim()
        if play:
            self._paused_at = None
            await self.feeder.start(media, position_s)
        else:
            await self.feeder.halt()
            self.feeder.media, self.feeder.finished = media, False
            self._paused_at = position_s

    async def play(self) -> None:
        if self.feeder.owner is self and self._paused_at is not None and self.feeder.media is not None:
            pos, self._paused_at = self._paused_at, None
            await self.feeder.start(self.feeder.media, pos)

    async def pause(self) -> None:
        if self.feeder.owner is self and self._paused_at is None:
            self._paused_at = self.feeder.position_s
            await self.feeder.halt()

    async def stop(self) -> None:
        if self.feeder.owner is self:
            await self.feeder.halt()
            self.feeder.owner = None
        self._paused_at = None

    async def seek(self, position_s: float) -> None:
        if self.feeder.owner is not self or self.feeder.media is None:
            return
        if self._paused_at is not None:
            self._paused_at = position_s
        else:
            await self.feeder.start(self.feeder.media, position_s)

    async def _clients(self) -> list[dict]:
        st = await self.ctl.call("Server.GetStatus")
        for g in st.get("server", {}).get("groups", []):
            if g.get("id") == self.group_id:
                return g.get("clients", [])
        return []

    async def set_volume(self, volume: float) -> None:
        pct = int(round(max(0.0, min(1.0, volume)) * 100))
        for c in await self._clients():
            muted = bool(c.get("config", {}).get("volume", {}).get("muted", False))
            await self.ctl.call(
                "Client.SetVolume", {"id": c["id"], "volume": {"percent": pct, "muted": muted}}
            )
        self._volume = pct / 100

    async def status(self) -> Status:
        f = self.feeder
        if f.owner is not self:
            return Status("idle", 0.0, self._volume)
        if self._paused_at is not None:
            return Status("paused", self._paused_at, self._volume)
        if f.running:
            return Status("playing", f.position_s, self._volume)
        if f.finished:
            f.finished = False
            return Status("finished", f.position_s, self._volume)
        if f.error:
            err, f.error = f.error, None
            return Status("idle", f.position_s, self._volume, error=err)
        return Status("idle", f.position_s, self._volume)

    async def close(self) -> None:
        await self.stop()


_state: dict[str, object] = {}


async def discover(cfg) -> list[Renderer]:
    if not cfg.snapcast_host:
        return []
    key = (cfg.snapcast_host, cfg.snapcast_control_port, cfg.snapcast_stream_port, cfg.snapcast_stream_id)
    if _state.get("key") != key:
        _state.clear()
        _state.update(
            key=key,
            ctl=SnapControl(cfg.snapcast_host, cfg.snapcast_control_port),
            feeder=Feeder(cfg.snapcast_host, cfg.snapcast_stream_port),
            cache={},
        )
    ctl: SnapControl = _state["ctl"]  # type: ignore[assignment]
    feeder: Feeder = _state["feeder"]  # type: ignore[assignment]
    cache: dict[str, SnapcastRenderer] = _state["cache"]  # type: ignore[assignment]
    try:
        st = await ctl.call("Server.GetStatus")
    except (OSError, ConnectionError, RuntimeError, ValueError, asyncio.TimeoutError) as e:
        log.warning("Snapcast at %s:%s: %s", cfg.snapcast_host, cfg.snapcast_control_port, e)
        return []
    server = st.get("server", {})
    if not any(s.get("id") == cfg.snapcast_stream_id for s in server.get("streams", [])):
        log.warning(
            "snapserver has no stream named %r; add a tcp source (see deploy/README.md)",
            cfg.snapcast_stream_id,
        )
    out: list[Renderer] = []
    for g in server.get("groups", []):
        clients = g.get("clients", [])
        if not any(c.get("connected") for c in clients):
            continue
        names = [
            c.get("config", {}).get("name") or c.get("host", {}).get("name") or c.get("id") for c in clients
        ]
        name = g.get("name") or " + ".join(n for n in names if n) or "Snapcast"
        r = cache.get(g["id"])
        if r is None:
            r = cache[g["id"]] = SnapcastRenderer(g["id"], name, ctl, feeder, cfg.snapcast_stream_id)
        r.name = f"{name} (Snapcast)" if not name.endswith("(Snapcast)") else name
        out.append(r)
    return out
