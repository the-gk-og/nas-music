"""Casting: signed URLs, the speaker registry as Connect devices, and a fake speaker driven end to end."""

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from homebeat.app.cast_api import cast_app, cast_key
from homebeat.cast import Renderer, Status, signing
from homebeat.cast import session as session_mod
from tests.conftest import track_ids
from tests.test_connect import URL, drain, hello, token, until, until_devices


# ---------- signing ----------


def test_sign_verify_scope_and_expiry():
    key = signing.derive_key(b"x" * 32)
    assert key != signing.derive_key(b"y" * 32)
    exp = int(time.time()) + 60
    sig = signing.sign(key, "media", "t1", "original", exp)
    assert signing.verify(key, "media", "t1", "original", exp, sig)
    assert signing.verify(key, "media", "t1", "original", str(exp), sig)
    assert not signing.verify(key, "media", "t2", "original", exp, sig)  # another track
    assert not signing.verify(key, "media", "t1", "mp3", exp, sig)  # another format
    assert not signing.verify(key, "art", "t1", "original", exp, sig)  # another kind
    assert not signing.verify(key, "media", "t1", "original", exp + 1, sig)  # extended expiry
    assert not signing.verify(signing.derive_key(b"y" * 32), "media", "t1", "original", exp, sig)
    assert not signing.verify(key, "media", "t1", "original", exp, sig, now=exp + 1)  # expired
    assert not signing.verify(key, "media", "t1", "original", "soon", sig)
    assert not signing.verify(key, "media", "t1", "original", exp, None)
    # TTL is capped at a day.
    assert signing.expiry(10**9, now=1000) == 1000 + signing.MAX_TTL_S


def test_media_url_shape_has_no_session_token():
    url = signing.media_url("http://nas:8090/", b"k" * 32, "abc", "opus", 100, now=1000)
    assert url.startswith("http://nas:8090/api/v1/cast/media/abc?f=opus&exp=1100&sig=")
    assert "token" not in url


# ---------- signed media endpoints ----------


def _signed(app, path_url: str) -> str:
    return path_url.split("://", 1)[1].split("/", 1)[1]  # strip scheme+host -> "api/v1/..."


def test_signed_media_and_art(app, conn, anon):
    c = app.state.ctx
    key = cast_key(c)
    tid = track_ids(conn)["Song 1"]
    url = "/" + _signed(app, signing.media_url("http://x", key, tid, "original", 60))
    r = anon.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "audio/flac"
    assert r.headers["contentfeatures.dlna.org"].startswith("DLNA.ORG_OP=01")
    r = anon.get(url, headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.content) == 10
    # Wrong format / tampered / missing signature / expired.
    assert anon.get(url.replace("f=original", "f=opus")).status_code == 403
    assert anon.get(url[:-2] + "AA").status_code == 403
    assert anon.get(f"/api/v1/cast/media/{tid}").status_code == 403
    exp = int(time.time()) - 1
    sig = signing.sign(key, "media", tid, "original", exp)
    old = f"/api/v1/cast/media/{tid}?f=original&exp={exp}&sig={sig}"
    assert anon.get(old).status_code == 403
    # A signature for one track doesn't open another.
    other = track_ids(conn)["Song 2"]
    assert anon.get(url.replace(tid, other)).status_code == 403
    # Transcoded variants.
    mp3 = anon.get("/" + _signed(app, signing.media_url("http://x", key, tid, "mp3", 60)))
    assert (
        mp3.status_code == 200 and mp3.headers["content-type"] == "audio/mpeg" and mp3.content[:3] == b"ID3"
    )
    opus = anon.get("/" + _signed(app, signing.media_url("http://x", key, tid, "opus", 60)))
    assert opus.status_code == 200 and opus.content[:4] == b"OggS"
    # Art.
    album = conn.execute("SELECT album_id FROM tracks WHERE id = ?", (tid,)).fetchone()[0]
    a = anon.get("/" + _signed(app, signing.art_url("http://x", key, album, 60)))
    assert a.status_code == 200 and a.headers["content-type"] == "image/jpeg"
    assert anon.get(f"/api/v1/cast/art/{album}?exp=9999999999&sig=nope").status_code == 403


def test_cast_port_app_serves_only_signed_media(app, conn):
    c = app.state.ctx
    tid = track_ids(conn)["Song 1"]
    with TestClient(cast_app(c)) as cc:
        url = "/" + _signed(app, signing.media_url("http://x", cast_key(c), tid, "original", 60))
        assert cc.get(url).status_code == 200
        assert cc.get("/api/v1/health").status_code == 404
        assert cc.get(f"/api/v1/stream/{tid}").status_code == 404
        assert cc.get("/admin").status_code == 404


def test_sign_endpoint(client, conn, anon, app):
    ids = list(track_ids(conn).values())
    r = client.post("/api/v1/cast/sign", json={"trackIds": ids[:2] + ["nope"], "format": "mp3"})
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["trackId"] for i in items] == ids[:2]
    assert items[0]["url"].startswith("http://192.168.1.50:8090/api/v1/cast/media/")
    assert items[0]["contentType"] == "audio/mpeg" and items[0]["expiresAt"] > time.time()
    assert anon.get("/" + _signed(app, items[0]["url"])).status_code == 200
    assert anon.post("/api/v1/cast/sign", json={"trackIds": ids[:1]}).status_code == 401


def test_cast_base_url_config(cfg):
    from dataclasses import replace

    assert cfg.cast_base_url == "http://192.168.1.50:8090"
    assert replace(cfg, cast_media_url="http://music.lan:9000/").cast_base_url == "http://music.lan:9000"


# ---------- a fake speaker through Connect ----------


class FakeSpeaker(Renderer):
    kind = "fake"

    def __init__(self, uid="one", name="Kitchen"):
        super().__init__(uid, name)
        self.calls: list[tuple] = []
        self.state = "idle"
        self.pos = 0.0
        self.volume = 0.4
        self.media = None

    async def load(self, media, position_s, play):
        self.calls.append(("load", media.track_id, round(position_s, 1), play))
        self.media, self.pos, self.state = media, position_s, "playing" if play else "paused"

    async def play(self):
        self.calls.append(("play",))
        self.state = "playing"

    async def pause(self):
        self.calls.append(("pause",))
        self.state = "paused"

    async def stop(self):
        self.calls.append(("stop",))
        self.state = "idle"

    async def seek(self, position_s):
        self.calls.append(("seek", position_s))
        self.pos = position_s

    async def set_volume(self, volume):
        self.calls.append(("volume", volume))
        self.volume = volume

    async def status(self):
        return Status(self.state, self.pos, self.volume)


def wait_for(pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.02)
    raise AssertionError("timed out")


@pytest.fixture
def speaker(app):
    spk = FakeSpeaker()
    reg = app.state.ctx.hub.speakers
    reg.poll_s = 0.05

    async def disc(cfg):
        return [spk]

    reg.discoverers = [disc]
    return spk


@pytest.fixture
def tc(app):
    app.state.ctx.hub.persist_every_s = 0.05
    with TestClient(app) as c:
        yield c


def test_speaker_is_a_connect_device_and_plays_the_queue(tc, conn, client, speaker):
    ids = [r[0] for r in conn.execute("SELECT id FROM tracks ORDER BY path")][:3]
    q = client.post("/api/v1/queues", json={"name": "Q", "trackIds": ids, "startIndex": 0}).json()
    phone, ptok = token(conn, kind="android")
    sid = speaker.device_id
    assert sid == "spk:fake:one"
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {ptok}"}) as p:
        hello(p, "Pixel")
        devs = until_devices(p, lambda ds: any(d["id"] == sid for d in ds))
        spk = next(d for d in devs if d["id"] == sid)
        assert spk == {
            "id": sid,
            "name": "Kitchen",
            "kind": "speaker",
            "canPlay": True,
            "online": True,
            "isActive": False,
            "castType": "fake",
        }
        assert client.get("/api/v1/cast/speakers").json()["speakers"][0]["id"] == sid

        # Phone plays the queue at index 1, 30 s in, then hands it to the speaker.
        p.send_json({"type": "transfer", "toDeviceId": phone, "play": True})
        until(p, "takeover")
        p.send_json(
            {
                "type": "state",
                "state": {
                    "queueId": q["id"],
                    "index": 1,
                    "trackId": ids[1],
                    "positionMs": 30000,
                    "playing": True,
                },
            }
        )
        until(p, "state")
        p.send_json({"type": "transfer", "toDeviceId": sid})
        assert until(p, "release") == {"type": "release"}
        wait_for(lambda: speaker.calls and speaker.calls[0][0] == "load")
        kind, tid, pos, play = speaker.calls[0]
        assert (tid, play) == (ids[1], True) and 30 <= pos < 32
        assert speaker.media.url("original").startswith("http://192.168.1.50:8090/api/v1/cast/media/")
        st = _state_until(p, lambda s: s["activeDeviceId"] == sid and s.get("volume") == 0.4)
        assert st["playing"] is True and st["trackId"] == ids[1]

        # Remote commands go to the speaker.
        p.send_json({"type": "command", "action": "pause"})
        _state_until(p, lambda s: s["playing"] is False)
        p.send_json({"type": "command", "action": "seek", "args": {"positionMs": 5000}})
        p.send_json({"type": "command", "action": "setVolume", "args": {"volume": 0.7}})
        p.send_json({"type": "command", "action": "play"})
        _state_until(p, lambda s: s["playing"] is True and s["volume"] == 0.7)
        assert [c[0] for c in speaker.calls[1:]] == ["pause", "seek", "volume", "play"]

        # The track ends on the speaker: it moves on to the next one by itself.
        speaker.state = "finished"
        st = _state_until(p, lambda s: s["index"] == 2)
        assert st["trackId"] == ids[2] and speaker.calls[-1][:2] == ("load", ids[2])
        # The end of the queue (repeat off) stops.
        speaker.state = "finished"
        st = _state_until(p, lambda s: s["playing"] is False)
        assert speaker.calls[-1] == ("stop",)
        # prev at the start of a track goes back one.
        p.send_json({"type": "command", "action": "prev"})
        _state_until(p, lambda s: s["index"] == 1)

        # Back to the phone: the speaker stops, the phone takes over at the speaker's position.
        speaker.pos = 42.0
        speaker.state = "playing"
        time.sleep(0.2)
        p.send_json({"type": "transfer", "toDeviceId": phone, "play": True})
        t = until(p, "takeover")["state"]
        assert t["activeDeviceId"] == phone and t["queueId"] == q["id"] and t["index"] == 1
        wait_for(lambda: speaker.calls[-1] == ("stop",))


def _state_until(ws, pred):
    while True:
        m = until(ws, "state")["state"]
        if pred(m):
            return m


def test_play_tracks_straight_onto_a_speaker(tc, conn, speaker):
    ids = list(track_ids(conn).values())
    _, tok = token(conn, kind="web")
    sid = speaker.device_id
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {tok}"}) as w:
        hello(w, "Browser", "web")
        until_devices(w, lambda ds: any(d["id"] == sid for d in ds))
        w.send_json({"type": "command", "action": "pause", "targetDeviceId": sid})
        assert "isn't playing for you" in until(w, "error")["detail"]
        w.send_json(
            {
                "type": "command",
                "action": "playTracks",
                "targetDeviceId": sid,
                "args": {"trackIds": ids[:2], "index": 1, "queueName": "x"},
            }
        )
        st = _state_until(w, lambda s: s["activeDeviceId"] == sid)
        assert st["trackId"] == ids[1] and st["playing"] is False and st["connecting"] is True
        st = _state_until(w, lambda s: s["playing"])
        assert st["connecting"] is False and st["activeDeviceId"] == sid
        wait_for(lambda: speaker.calls and speaker.calls[0][:2] == ("load", ids[1]))


def test_a_second_user_takes_the_speaker(tc, conn, speaker):
    ids = list(track_ids(conn).values())
    _, atok = token(conn, "alice")
    _, btok = token(conn, "bob")
    sid = speaker.device_id
    with (
        tc.websocket_connect(URL, headers={"Authorization": f"Bearer {atok}"}) as a,
        tc.websocket_connect(URL, headers={"Authorization": f"Bearer {btok}"}) as b,
    ):
        hello(a)
        hello(b)
        until_devices(a, lambda ds: any(d["id"] == sid for d in ds))
        until_devices(b, lambda ds: any(d["id"] == sid for d in ds))
        a.send_json(
            {"type": "command", "action": "playTracks", "targetDeviceId": sid, "args": {"trackIds": ids[:1]}}
        )
        _state_until(a, lambda s: s["activeDeviceId"] == sid and s["playing"])
        b.send_json(
            {"type": "command", "action": "playTracks", "targetDeviceId": sid, "args": {"trackIds": ids[1:2]}}
        )
        _state_until(b, lambda s: s["activeDeviceId"] == sid and s["playing"])
        lost = _state_until(a, lambda s: s["activeDeviceId"] is None)
        assert lost["playing"] is False
        err = until(a, "error")
        assert "someone else" in err["detail"] and err["source"] == "speaker"
        # Alice's commands no longer reach the speaker. (Wait for Bob's load to land first; it's async.)
        import time
        deadline = time.monotonic() + 5
        while not any(c[0] == "load" and c[1] == ids[1] for c in speaker.calls) and time.monotonic() < deadline:
            time.sleep(0.02)
        n = len(speaker.calls)
        a.send_json({"type": "command", "action": "pause", "targetDeviceId": sid})
        until(a, "error")
        assert len(speaker.calls) == n


def test_registry_keeps_a_playing_speaker_that_vanishes(app):
    import asyncio

    from homebeat.app.cast_api import make_registry

    reg = make_registry(app.state.ctx)
    spk = FakeSpeaker()
    found = [[spk]]

    async def disc(cfg):
        return found[0]

    async def boom(cfg):
        raise OSError("no route")

    reg.discoverers = [disc, boom]

    async def run():
        assert [d["id"] for d in await reg.refresh()] == [spk.device_id]
        assert "test_cast" in reg.errors and "no route" in reg.errors["test_cast"]
        reg.sessions[spk.device_id].owner = "u1"
        found[0] = []
        assert [d["id"] for d in await reg.refresh()] == [spk.device_id]  # kept: busy
        assert reg.devices()[0]["busy"] is True
        reg.sessions[spk.device_id].owner = None
        assert await reg.refresh() == []

    asyncio.run(run())


def test_refresh_endpoint_is_admin_only(client, bob):
    assert client.post("/api/v1/cast/speakers/refresh").status_code == 200
    assert bob.post("/api/v1/cast/speakers/refresh").status_code == 403
    assert bob.get("/api/v1/cast/speakers").status_code == 200


def test_sign_base_uses_public_address_when_reached_through_the_relay(cfg):
    """Phone away from home (came in on the internal HTTP port via the relay/tunnel): public links, so a
    speaker on another network can fetch them. On the LAN port: the direct cast port."""
    from dataclasses import replace

    from starlette.requests import Request

    from homebeat.app.cast_api import sign_base

    def req(port):
        return Request({"type": "http", "server": ("homebeat", port), "headers": []})

    assert sign_base(req(cfg.http_port), cfg) == "https://music.example.com"
    assert sign_base(req(cfg.https_port), cfg) == "http://192.168.1.50:8090"
    assert sign_base(req(cfg.http_port), replace(cfg, tunnel_url=None)) == "http://192.168.1.50:8090"


def test_public_signed_link_is_served_by_the_main_app(client, conn, anon, app):
    """The relay forwards to the main app, so the signed /cast/media path must work there without a session."""
    ids = list(track_ids(conn).values())
    url = client.post("/api/v1/cast/sign", json={"trackIds": ids[:1]}).json()["items"][0]["url"]
    path = url.split("://", 1)[1].split("/", 1)[1]
    assert anon.get("/" + path).status_code == 200


# ---------- speakers that fail: hand playback back, never stay "active" silently ----------


class HangingSpeaker(FakeSpeaker):
    """Never answers the load (the unreachable Nest Hub)."""

    async def load(self, media, position_s, play):
        self.calls.append(("load", media.track_id, round(position_s, 1), play))
        await asyncio.sleep(3600)

    async def reset(self):
        self.calls.append(("reset",))


class RefusingSpeaker(FakeSpeaker):
    async def load(self, media, position_s, play):
        self.calls.append(("load", media.track_id, round(position_s, 1), play))
        raise RuntimeError("LOAD_FAILED")


def _use(app, spk):
    reg = app.state.ctx.hub.speakers
    reg.poll_s = 0.05

    async def disc(cfg):
        return [spk]

    reg.discoverers = [disc]
    return spk


def _phone_playing(p, conn, client, phone):
    ids = [r[0] for r in conn.execute("SELECT id FROM tracks ORDER BY path")][:3]
    q = client.post("/api/v1/queues", json={"name": "Q", "trackIds": ids, "startIndex": 0}).json()
    p.send_json({"type": "transfer", "toDeviceId": phone, "play": True})
    until(p, "takeover")
    p.send_json({"type": "state", "state": {"queueId": q["id"], "index": 1, "trackId": ids[1],
                                            "positionMs": 30000, "playing": True}})
    until(p, "state")
    return ids


@pytest.mark.parametrize("cls", [HangingSpeaker, RefusingSpeaker])
def test_speaker_that_cant_connect_hands_back_to_the_phone(tc, conn, client, app, monkeypatch, cls):
    monkeypatch.setattr(session_mod, "CONNECT_TIMEOUT_S", 0.3)
    spk = _use(app, cls())
    phone, ptok = token(conn, kind="android")
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {ptok}"}) as p:
        hello(p, "Pixel")
        until_devices(p, lambda ds: any(d["id"] == spk.device_id for d in ds))
        ids = _phone_playing(p, conn, client, phone)
        p.send_json({"type": "transfer", "toDeviceId": spk.device_id})
        until(p, "release")
        st = _state_until(p, lambda s: s["activeDeviceId"] == spk.device_id)
        assert st["connecting"] is True and st["playing"] is False  # "Connecting…", the clock doesn't run
        err = until(p, "error")
        assert err["source"] == "speaker" and err["detail"].startswith("Couldn't connect to Kitchen")
        t = until(p, "takeover")["state"]  # back on the phone, paused where it was
        assert t["activeDeviceId"] == phone and t["playing"] is False and t["connecting"] is False
        assert t["trackId"] == ids[1] and 30000 <= t["positionMs"] < 31000
        devs = until(p, "devices")["devices"]
        assert next(d for d in devs if d["id"] == spk.device_id)["isActive"] is False
        if cls is HangingSpeaker:
            wait_for(lambda: ("reset",) in spk.calls)
        reg = app.state.ctx.hub.speakers
        assert reg.get(spk.device_id).owner is None


def test_speaker_dropping_mid_playback_hands_back_at_its_position(tc, conn, client, speaker):
    phone, ptok = token(conn, kind="android")
    sid = speaker.device_id
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {ptok}"}) as p:
        hello(p, "Pixel")
        until_devices(p, lambda ds: any(d["id"] == sid for d in ds))
        ids = _phone_playing(p, conn, client, phone)
        p.send_json({"type": "transfer", "toDeviceId": sid})
        _state_until(p, lambda s: s["activeDeviceId"] == sid and s["playing"] and not s["connecting"])
        speaker.pos = 75.0
        time.sleep(0.2)
        speaker.state = "unavailable"  # e.g. powered off / Wi-Fi gone
        err = until(p, "error")
        assert err["source"] == "speaker" and "Lost connection to Kitchen" in err["detail"]
        t = until(p, "takeover")["state"]
        assert t["activeDeviceId"] == phone and t["playing"] is False
        assert t["trackId"] == ids[1] and 75000 <= t["positionMs"] < 76000


def test_cancel_while_connecting_and_stop_casting(tc, conn, client, app, monkeypatch):
    monkeypatch.setattr(session_mod, "CONNECT_TIMEOUT_S", 30)
    spk = _use(app, HangingSpeaker())
    phone, ptok = token(conn, kind="android")
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {ptok}"}) as p:
        hello(p, "Pixel")
        until_devices(p, lambda ds: any(d["id"] == spk.device_id for d in ds))
        ids = _phone_playing(p, conn, client, phone)
        p.send_json({"type": "transfer", "toDeviceId": spk.device_id})
        _state_until(p, lambda s: s["connecting"])
        wait_for(lambda: spk.calls and spk.calls[0][0] == "load")
        # Cancel = take it back to this device, paused (the same message as "Stop casting").
        p.send_json({"type": "transfer", "toDeviceId": phone, "play": False})
        t = until(p, "takeover")["state"]
        assert t["activeDeviceId"] == phone and t["playing"] is False and t["connecting"] is False
        assert t["trackId"] == ids[1] and 30000 <= t["positionMs"] < 31000
        wait_for(lambda: ("stop",) in spk.calls and ("reset",) in spk.calls)  # in-flight load abandoned
        assert app.state.ctx.hub.speakers.get(spk.device_id).owner is None
        msgs = drain(p)
        assert not any(m["type"] == "error" for m in msgs)  # cancelling isn't an error


def test_failure_with_the_picker_gone_leaves_nothing_active(tc, conn, client, app, monkeypatch):
    monkeypatch.setattr(session_mod, "CONNECT_TIMEOUT_S", 0.5)
    spk = _use(app, HangingSpeaker())
    _, wtok = token(conn, "alice", kind="web")
    _, ptok = token(conn, "alice", kind="android")
    ids = list(track_ids(conn).values())
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {ptok}"}) as p:
        hello(p, "Pixel")
        until_devices(p, lambda ds: any(d["id"] == spk.device_id for d in ds))
        with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {wtok}"}) as w:
            hello(w, "Browser", "web")
            w.send_json({"type": "command", "action": "playTracks", "targetDeviceId": spk.device_id,
                         "args": {"trackIds": ids[:2]}})
            _state_until(p, lambda s: s["connecting"])
            wait_for(lambda: spk.calls)  # (closing a TestClient socket cancels its handler mid-message)
        # The browser that picked it closed; the failure leaves nothing active rather than the phone.
        err = until(p, "error")
        assert err["source"] == "speaker"
        st = _state_until(p, lambda s: not s["connecting"])
        assert st["activeDeviceId"] is None and st["playing"] is False
