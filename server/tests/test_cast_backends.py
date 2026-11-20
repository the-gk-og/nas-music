"""Speaker backends against mocked devices: pychromecast-shaped fakes, a fake DmrDevice, Home Assistant
via httpx.MockTransport, and a fake snapserver (JSON-RPC + TCP stream sink) on localhost."""

import asyncio
import json
import types
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from nasmusic.cast.backends import chromecast, dlna, homeassistant, snapcast
from nasmusic.cast.base import Media


def media(mime="audio/flac", codec="FLAC", path=None, duration=200.0) -> Media:
    return Media(
        track_id="t1",
        title="Song",
        artist="Artist",
        album="Album",
        duration_s=duration,
        url_for=lambda fmt: f"http://nas:8090/api/v1/cast/media/t1?f={fmt}&exp=1&sig=s",
        art_url="http://nas:8090/api/v1/cast/art/a1?exp=1&sig=s",
        mime=mime,
        codec=codec,
        path=path,
    )


# ---------- Chromecast ----------


class FakeMC:
    def __init__(self):
        self.calls = []
        self.status = types.SimpleNamespace(
            player_state="UNKNOWN", idle_reason=None, adjusted_current_time=0.0
        )

    answer: tuple | None = (True, {})  # what the device says to LOAD; None = never answers

    def play_media(self, url, ctype, **kw):
        self.calls.append(("play_media", url, ctype, kw))
        if self.answer is None:
            return
        if self.answer[0]:
            self.status.player_state = "PLAYING" if kw.get("autoplay") else "PAUSED"
        if kw.get("callback_function"):
            kw["callback_function"](*self.answer)

    def block_until_active(self, timeout=None):
        pass

    def play(self):
        self.calls.append(("play",))

    def pause(self):
        self.calls.append(("pause",))

    def stop(self):
        self.calls.append(("stop",))

    def seek(self, pos):
        self.calls.append(("seek", pos))


class FakeCast:
    def __init__(self):
        self.media_controller = FakeMC()
        self.status = types.SimpleNamespace(volume_level=0.25)
        self.waited = False
        self.volume = None

    def wait(self, timeout=None):
        self.waited = True

    def set_volume(self, v):
        self.volume = v

    def disconnect(self, timeout=None):
        pass


def test_chromecast_load_status_and_finish():
    cast = FakeCast()
    r = chromecast.ChromecastRenderer("uuid-1", "Living room", cast)
    assert r.device_id == "spk:chromecast:uuid-1"

    async def run():
        assert (await r.status()).state == "idle"
        await r.load(media(), 12.5, True)
        assert cast.waited
        _, url, ctype, kw = cast.media_controller.calls[0]
        assert url.endswith("f=original&exp=1&sig=s") and ctype == "audio/flac"
        assert kw["current_time"] == 12.5 and kw["autoplay"] is True and kw["stream_type"] == "BUFFERED"
        assert kw["metadata"]["artist"] == "Artist" and kw["metadata"]["images"][0]["url"].startswith(
            "http://nas"
        )
        cast.media_controller.status.adjusted_current_time = 13.0
        st = await r.status()
        assert (st.state, st.position_s, st.volume) == ("playing", 13.0, 0.25)
        await r.set_volume(1.7)
        assert cast.volume == 1.0
        await r.seek(30)
        await r.pause()
        cast.media_controller.status.player_state = "IDLE"
        cast.media_controller.status.idle_reason = "FINISHED"
        assert (await r.status()).state == "finished"
        assert (await r.status()).state == "idle"  # reported once
        cast.media_controller.status.idle_reason = "ERROR"
        assert (await r.status()).error

    asyncio.run(run())


class DeadCast(FakeCast):
    """The Nest Hub case: discovered, but its cast port doesn't answer."""

    def __init__(self):
        super().__init__()
        self.disconnected = False

    def wait(self, timeout=None):
        raise TimeoutError("wait")

    def disconnect(self, timeout=None):
        self.disconnected = True


def test_chromecast_unreachable_or_refusing_fails_fast(monkeypatch):
    monkeypatch.setattr(chromecast, "LOAD_TIMEOUT_S", 0.1)

    async def run():
        made = []

        def factory():
            made.append(DeadCast() if len(made) == 0 else FakeCast())
            return made[-1]

        r = chromecast.ChromecastRenderer("u", "Bedroom display", factory=factory)
        assert (await r.status()).state == "idle"  # listing it doesn't connect
        assert made == []
        with pytest.raises(chromecast.CastError, match="no answer"):
            await r.load(media(), 0, True)
        await r.reset()  # the session resets after a failure: the dead connection is dropped...
        assert made[0].disconnected and r.cast is None
        await r.load(media(), 0, True)  # ...and the next attempt builds a new one
        assert len(made) == 2 and (await r.status()).state == "playing"

        # Connected, but the receiver never answers LOAD / refuses it.
        cast = FakeCast()
        cast.media_controller.answer = None
        r2 = chromecast.ChromecastRenderer("u2", "Kitchen", cast)
        with pytest.raises(chromecast.CastError, match="didn't answer"):
            await r2.load(media(), 0, True)
        cast.media_controller.answer = (False, {"type": "LOAD_FAILED"})
        with pytest.raises(chromecast.CastError, match="refused"):
            await r2.load(media(), 0, True)

        # The socket drops mid-playback: status says unavailable (the session counts those and gives up).
        cast.media_controller.answer = (True, {})
        await r2.load(media(), 0, True)
        cast.socket_client = types.SimpleNamespace(is_connected=False)
        assert (await r2.status()).state == "unavailable"
        with pytest.raises(chromecast.CastError):
            await r2.play()

    asyncio.run(run())


def test_chromecast_format_choice():
    assert chromecast.pick_format(media()) == ("original", "audio/flac")
    assert chromecast.pick_format(media("audio/mpeg", "MP3")) == ("original", "audio/mpeg")
    assert chromecast.pick_format(media("audio/mp4", "ALAC")) == ("opus", "audio/ogg")
    assert chromecast.pick_format(media("audio/x-ms-wma", None)) == ("opus", "audio/ogg")


def test_chromecast_discovery_off(cfg):
    off = replace(cfg, cast_discovery=False, chromecast_hosts=[])
    assert asyncio.run(chromecast.discover(off)) == []


# ---------- DLNA ----------


class FakeDmr:
    def __init__(self):
        self.calls = []
        self.transport_state = types.SimpleNamespace(value="STOPPED")
        self.media_position = 0
        self.volume_level = 0.5
        self.has_seek_rel_time = True
        self.has_volume_level = True
        self.can_stop = True

    async def construct_play_media_metadata(self, **kw):
        self.calls.append(("meta", kw["override_mime_type"], kw["meta_data"]["artist"]))
        return "<DIDL-Lite/>"

    async def async_stop(self):
        self.calls.append(("stop",))
        self.transport_state.value = "STOPPED"

    async def async_set_transport_uri(self, url, title, meta):
        self.calls.append(("uri", url, title, meta))

    async def async_wait_for_can_play(self, t):
        pass

    async def async_play(self):
        self.calls.append(("play",))
        self.transport_state.value = "PLAYING"

    async def async_pause(self):
        self.calls.append(("pause",))
        self.transport_state.value = "PAUSED_PLAYBACK"

    async def async_seek_rel_time(self, t):
        self.calls.append(("seek", t))

    async def async_set_volume_level(self, v):
        self.calls.append(("volume", v))

    async def async_update(self, do_ping=True):
        pass


def test_dlna_format_from_protocol_info():
    flac_ok = ["http-get:*:audio/x-flac:*", "http-get:*:audio/mpeg:*"]
    assert dlna.pick_format(media(), flac_ok) == ("original", "audio/flac")
    assert dlna.pick_format(media(), ["http-get:*:audio/mpeg:*"]) == ("mp3", "audio/mpeg")
    assert dlna.pick_format(media("audio/mp4", "AAC"), None) == ("mp3", "audio/mpeg")
    assert dlna.pick_format(media(), None) == ("original", "audio/flac")
    assert dlna.pick_format(media("audio/mp4", "ALAC"), ["http-get:*:audio/mp4:*"]) == ("mp3", "audio/mpeg")


def test_dlna_load_seek_and_track_end():
    d = FakeDmr()
    r = dlna.DlnaRenderer("abc", "Receiver", d, ["http-get:*:audio/mpeg:*"])

    async def run():
        await r.load(media(), 42.0, True)
        names = [c[0] for c in d.calls]
        assert names == ["meta", "stop", "uri", "play", "seek"]
        assert d.calls[0][1] == "audio/mpeg" and "f=mp3" in d.calls[2][1]
        assert d.calls[4][1] == timedelta(seconds=42)
        d.media_position = 43
        st = await r.status()
        assert (st.state, st.position_s, st.volume) == ("playing", 43.0, 0.5)
        d.transport_state.value = "STOPPED"
        assert (await r.status()).state == "finished"
        assert (await r.status()).state == "idle"
        # Loading paused at a position: play, seek, pause.
        d.calls.clear()
        await r.load(media(), 10.0, False)
        assert [c[0] for c in d.calls][-3:] == ["play", "seek", "pause"]
        assert (await r.status()).state == "paused"
        await r.set_volume(-1)
        assert d.calls[-1] == ("volume", 0.0)

    asyncio.run(run())


# ---------- Home Assistant ----------


class FakeHA:
    def __init__(self):
        self.calls = []
        self.states = {
            "media_player.kitchen": {
                "entity_id": "media_player.kitchen",
                "state": "idle",
                "attributes": {"friendly_name": "Kitchen Sonos", "volume_level": 0.3},
            },
            "media_player.tv": {"entity_id": "media_player.tv", "state": "unavailable", "attributes": {}},
            "light.lamp": {"entity_id": "light.lamp", "state": "on", "attributes": {}},
        }

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer tok"
        if req.method == "GET" and req.url.path == "/api/states":
            return httpx.Response(200, json=list(self.states.values()))
        if req.method == "GET" and req.url.path.startswith("/api/states/"):
            return httpx.Response(200, json=self.states[req.url.path.rsplit("/", 1)[1]])
        if req.method == "POST" and req.url.path.startswith("/api/services/media_player/"):
            body = json.loads(req.content)
            svc = req.url.path.rsplit("/", 1)[1]
            self.calls.append((svc, body))
            st = self.states[body["entity_id"]]
            if svc == "play_media":
                st["state"] = "playing"
                st["attributes"].update(
                    media_content_id=body["media_content_id"],
                    media_position=0,
                    media_position_updated_at=datetime.now(UTC).isoformat(),
                )
            elif svc == "media_pause":
                st["state"] = "paused"
            return httpx.Response(200, json=[])
        return httpx.Response(404)


def test_homeassistant_discover_and_control(cfg):
    fake = FakeHA()
    c = replace(cfg, homeassistant_url="http://ha:8123", homeassistant_token="tok")

    async def run():
        found = await homeassistant.discover(c, transport=httpx.MockTransport(fake))
        assert [(r.device_id, r.name) for r in found] == [
            ("spk:homeassistant:media_player.kitchen", "Kitchen Sonos")
        ]
        only = replace(c, homeassistant_entities=["media_player.tv"])
        assert [r.uid for r in await homeassistant.discover(only, transport=httpx.MockTransport(fake))] == [
            "media_player.tv"
        ]
        r = found[0]
        r.ha = homeassistant.HomeAssistant("http://ha:8123", "tok", httpx.MockTransport(fake))
        await r.load(media(), 20.0, True)
        svc, body = fake.calls[0]
        assert svc == "play_media" and body["media_content_type"] == "music"
        assert (
            body["media_content_id"].endswith("f=original&exp=1&sig=s") and body["extra"]["title"] == "Song"
        )
        assert fake.calls[1] == ("media_seek", {"entity_id": "media_player.kitchen", "seek_position": 20.0})
        attrs = fake.states["media_player.kitchen"]["attributes"]
        attrs["media_position"] = 20
        attrs["media_position_updated_at"] = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        st = await r.status()
        assert st.state == "playing" and 24.5 < st.position_s < 26 and st.volume == 0.3
        await r.set_volume(0.55)
        assert fake.calls[-1][1]["volume_level"] == 0.55
        await r.pause()
        assert (await r.status()).state == "paused"
        fake.states["media_player.kitchen"]["state"] = "idle"
        assert (await r.status()).state == "finished"
        # Something else played on it after us: we let go instead of advancing.
        await r.load(media(), 0, True)
        await r.status()
        attrs["media_content_id"] = "spotify:track:x"
        assert (await r.status()).state == "idle"

    asyncio.run(run())


def test_homeassistant_unconfigured_or_down(cfg):
    assert asyncio.run(homeassistant.discover(cfg)) == []

    def down(req):
        raise httpx.ConnectError("refused")

    c = replace(cfg, homeassistant_url="http://ha:8123", homeassistant_token="tok")
    assert asyncio.run(homeassistant.discover(c, transport=httpx.MockTransport(down))) == []


# ---------- Snapcast ----------


class FakeSnapserver:
    def __init__(self):
        self.calls = []
        self.received = 0
        self.volumes = {}
        self.status = {
            "server": {
                "groups": [
                    {
                        "id": "g1",
                        "name": "",
                        "stream_id": "default",
                        "clients": [
                            {
                                "id": "c1",
                                "connected": True,
                                "host": {"name": "pi-kitchen"},
                                "config": {"name": "Kitchen", "volume": {"percent": 50, "muted": False}},
                            },
                            {
                                "id": "c2",
                                "connected": True,
                                "host": {"name": "pi-lounge"},
                                "config": {"name": "", "volume": {"percent": 50, "muted": True}},
                            },
                        ],
                    },
                    {
                        "id": "g2",
                        "name": "Garage",
                        "clients": [{"id": "c3", "connected": False, "config": {}}],
                    },
                ],
                "streams": [{"id": "nasmusic", "status": "idle"}],
            }
        }

    async def control(self, reader, writer):
        while line := await reader.readline():
            req = json.loads(line)
            self.calls.append((req["method"], req.get("params")))
            if req["method"] == "Client.SetVolume":
                self.volumes[req["params"]["id"]] = req["params"]["volume"]
            writer.write(
                json.dumps({"jsonrpc": "2.0", "method": "Stream.OnUpdate", "params": {}}).encode() + b"\r\n"
            )
            result = self.status if req["method"] == "Server.GetStatus" else {}
            writer.write(json.dumps({"id": req["id"], "jsonrpc": "2.0", "result": result}).encode() + b"\r\n")
            await writer.drain()
        writer.close()

    async def sink(self, reader, writer):
        while chunk := await reader.read(65536):
            self.received += len(chunk)
        writer.close()


def test_snapcast_groups_volume_and_pcm_feed(cfg, base_flac):
    srv = FakeSnapserver()

    async def run():
        ctl = await asyncio.start_server(srv.control, "127.0.0.1", 0)
        sink = await asyncio.start_server(srv.sink, "127.0.0.1", 0)
        c = replace(
            cfg,
            snapcast_host="127.0.0.1",
            snapcast_control_port=ctl.sockets[0].getsockname()[1],
            snapcast_stream_port=sink.sockets[0].getsockname()[1],
        )
        try:
            found = await snapcast.discover(c)
            assert [(r.device_id, r.name) for r in found] == [
                ("spk:snapcast:g1", "Kitchen + pi-lounge (Snapcast)")
            ]
            r = found[0]
            await r.set_volume(0.8)
            assert srv.volumes == {
                "c1": {"percent": 80, "muted": False},
                "c2": {"percent": 80, "muted": True},
            }
            assert (await r.status()).state == "idle"
            await r.load(media(path=base_flac, duration=2.0), 1.0, True)  # 1 s left of a 2 s file
            assert ("Group.SetStream", {"id": "g1", "stream_id": "nasmusic"}) in srv.calls
            await asyncio.sleep(0.3)
            st = await r.status()
            assert st.state == "playing" and st.position_s >= 1.0 and st.volume == 0.8
            await r.pause()
            paused_at = (await r.status()).position_s
            assert (await r.status()).state == "paused"
            await r.play()
            for _ in range(60):
                st = await r.status()
                if st.state != "playing":
                    break
                await asyncio.sleep(0.05)
            assert st.state == "finished", st
            # About 1 s of 48 kHz 16-bit stereo PCM arrived in total (decoded in real time).
            assert 0.8 * snapcast.BYTES_PER_S < srv.received < 1.3 * snapcast.BYTES_PER_S
            assert paused_at >= 1.0
            await r.stop()
        finally:
            ctl.close()
            sink.close()

    asyncio.run(run())


def test_snapcast_unreachable(cfg):
    c = replace(cfg, snapcast_host="127.0.0.1", snapcast_control_port=1)
    assert asyncio.run(snapcast.discover(c)) == []


@pytest.mark.parametrize("mod", [dlna])
def test_dlna_discovery_manual_url_unreachable(cfg, mod):
    c = replace(cfg, cast_discovery=False, dlna_renderers=["http://127.0.0.1:1/desc.xml"])
    assert asyncio.run(mod.discover(c)) == []
