"""Connect hub (docs/v2.md §5) over real WebSockets. All sockets share one TestClient (one event loop),
as they share the server's loop in production."""
import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from homebeat import auth
from tests.conftest import make_user, track_ids

URL = "/api/v1/connect"


@pytest.fixture
def tc(app, conn):
    app.state.ctx.hub.grace_s = 0.3
    app.state.ctx.hub.persist_every_s = 0.05
    with TestClient(app) as c:
        yield c


def token(conn, username="alice", kind="android", name=None):
    u = make_user(conn, username, admin=username == "alice")
    did, tok = auth.create_device(conn, u.id, name or f"{username}-{kind}", kind)
    return did, tok


def drain(ws) -> list[dict]:
    """Everything queued for this socket so far (ping/pong as a barrier)."""
    ws.send_json({"type": "ping"})
    out = []
    while True:
        m = ws.receive_json()
        if m["type"] == "pong":
            return out
        out.append(m)


def until(ws, typ: str) -> dict:
    while True:
        m = ws.receive_json()
        if m["type"] == typ:
            return m


def until_devices(ws, pred) -> list[dict]:
    while True:
        devs = until(ws, "devices")["devices"]
        if pred(devs):
            return devs


def hello(ws, name="dev", kind="android", can_play=True) -> dict:
    ws.send_json({"type": "hello", "deviceName": name, "kind": kind, "canPlay": can_play, "appVersion": "1"})
    return until(ws, "welcome")


def test_auth_variants(tc, conn):
    with pytest.raises(WebSocketDisconnect) as e:
        with tc.websocket_connect(URL) as ws:
            ws.receive_json()
    assert e.value.code == 4401
    did, tok = token(conn)
    with tc.websocket_connect(f"{URL}?token={tok}") as ws:  # browsers
        assert hello(ws)["deviceId"] == did
    tc.cookies.set("nm_session", tok)  # web player cookie
    with tc.websocket_connect(URL) as ws:
        assert hello(ws)["deviceId"] == did
    tc.cookies.clear()
    with pytest.raises(WebSocketDisconnect) as e:
        with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {tok}"}) as ws:
            ws.send_json({"type": "ping"})  # not hello
            ws.receive_json()
    assert e.value.code == 4400


def test_transfer_commands_state_and_release(tc, conn):
    phone, ptok = token(conn, kind="android")
    web, wtok = token(conn, kind="web")
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {ptok}"}) as p, \
         tc.websocket_connect(URL, headers={"Authorization": f"Bearer {wtok}"}) as w:
        welcome = hello(p, "Pixel")
        assert welcome["state"]["activeDeviceId"] is None and welcome["state"]["rev"] == 0
        hello(w, "Firefox", "web")
        devs = until_devices(p, lambda ds: len(ds) == 2)
        assert {d["name"] for d in devs} == {"Pixel", "Firefox"} and not any(d["isActive"] for d in devs)

        # Phone starts playing locally: transfer to itself.
        p.send_json({"type": "transfer", "toDeviceId": phone, "play": True})
        take = until(p, "takeover")["state"]
        assert take["activeDeviceId"] == phone and take["playing"] is True
        st = until(w, "state")["state"]
        assert st["activeDeviceId"] == phone and st["rev"] == take["rev"]
        assert [d["isActive"] for d in until(w, "devices")["devices"] if d["id"] == phone] == [True]

        # Active device reports state; everyone gets it with a server rev.
        p.send_json({"type": "state", "state": {"queueId": "q1", "index": 2, "trackId": "t9", "positionMs": 61234,
                                                 "playing": True, "volume": 0.8, "repeat": "all", "rev": 999}})
        s1 = until(w, "state")["state"]
        assert (s1["index"], s1["positionMs"], s1["volume"], s1["repeat"]) == (2, 61234, 0.8, "all")
        assert s1["rev"] == take["rev"] + 1  # client rev ignored

        # A remote's command goes to the active device...
        w.send_json({"type": "command", "action": "seek", "args": {"positionMs": 1000}})
        cmd = until(p, "command")
        assert cmd == {"type": "command", "action": "seek", "args": {"positionMs": 1000}, "fromDeviceId": web}
        # ...and only the active device may report state.
        w.send_json({"type": "state", "state": {"playing": False}})
        assert until(w, "error")["detail"] == "only the active device reports state"
        w.send_json({"type": "command", "action": "selfDestruct"})
        assert "unknown action" in until(w, "error")["detail"]

        # Transfer to the web player: the phone is released.
        w.send_json({"type": "transfer", "toDeviceId": web, "play": True})
        assert until(p, "release") == {"type": "release"}
        t2 = until(w, "takeover")["state"]
        assert t2["activeDeviceId"] == web and t2["positionMs"] >= 61234 and t2["queueId"] == "q1"


def test_play_from_remote_with_no_active_device(tc, conn):
    _, tok = token(conn)
    _, wtok = token(conn, kind="watch", name="watch")
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {wtok}"}) as watch, \
         tc.websocket_connect(URL, headers={"Authorization": f"Bearer {tok}"}) as ws:
        hello(watch, "Watch", "watch", can_play=False)
        hello(ws)
        watch.send_json({"type": "command", "action": "pause"})
        assert until(watch, "error")["detail"] == "no active device"
        watch.send_json({"type": "command", "action": "play"})  # can't play itself
        assert until(watch, "error")["detail"] == "no active device"
        ws.send_json({"type": "command", "action": "play"})
        assert until(ws, "takeover")["state"]["playing"] is True
        watch.send_json({"type": "transfer", "toDeviceId": "nope"})
        assert "offline" in until(watch, "error")["detail"]


def test_play_tracks_creates_queue(tc, conn, app):
    ids = list(track_ids(conn).values())
    did, tok = token(conn)
    _, rtok = token(conn, kind="web")
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {tok}"}) as player, \
         tc.websocket_connect(URL, headers={"Authorization": f"Bearer {rtok}"}) as remote:
        hello(player)
        hello(remote, kind="web", can_play=False)
        # No active device yet: the requester can't play, so it's refused...
        remote.send_json({"type": "command", "action": "playTracks", "args": {"trackIds": ids[:3], "index": 1}})
        qid = until(remote, "queues")["queueId"]
        assert until(remote, "error")["detail"] == "no active device"
        # ...once the player is active, playTracks becomes playQueue on it.
        player.send_json({"type": "transfer", "play": False})
        until(player, "takeover")
        remote.send_json({"type": "command", "action": "playTracks",
                          "args": {"trackIds": ids[:2], "index": 1, "queueName": "Album: Second"}})
        cmd = until(player, "command")
        assert cmd["action"] == "playQueue" and cmd["args"]["index"] == 1
        q = tc.get(f"/api/v1/queues/{cmd['args']['queueId']}", headers={"Authorization": f"Bearer {tok}"}).json()
        assert q["name"] == "Album: Second" and [t["id"] for t in q["tracks"]] == ids[:2]
        assert cmd["args"]["queueId"] != qid
        # Same queue name again replaces that queue instead of using another slot.
        remote.send_json({"type": "command", "action": "playTracks",
                          "args": {"trackIds": ids[2:4], "index": 0, "queueName": "Album: Second"}})
        assert until(player, "command")["args"]["queueId"] == cmd["args"]["queueId"]


def test_play_queue_takeover_fills_track(tc, conn):
    ids = list(track_ids(conn).values())
    _, tok = token(conn)
    h = {"Authorization": f"Bearer {tok}"}
    q = tc.post("/api/v1/queues", json={"name": "Q", "trackIds": ids[:3]}, headers=h).json()
    with tc.websocket_connect(URL, headers=h) as ws:
        hello(ws)
        ws.send_json({"type": "command", "action": "playQueue", "args": {"queueId": q["id"], "index": 2}})
        st = until(ws, "takeover")["state"]
        assert (st["queueId"], st["index"], st["trackId"], st["playing"]) == (q["id"], 2, ids[2], True)


def test_disconnect_grace_pauses_and_reconnect_resumes(tc, conn, app):
    phone, ptok = token(conn)
    _, wtok = token(conn, kind="web")
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {wtok}"}) as w:
        hello(w, kind="web")
        with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {ptok}"}) as p:
            hello(p)
            p.send_json({"type": "transfer", "play": True})
            until(p, "takeover")
            until(w, "state")
        devs = until_devices(w, lambda ds: any(not d["online"] for d in ds))  # phone gone, still active
        assert [(d["online"], d["isActive"]) for d in devs if d["id"] == phone] == [(False, True)]
        st = until(w, "state")["state"]  # after the grace period
        assert st["playing"] is False and st["activeDeviceId"] == phone
        # Reconnect: still the active device, no new pause.
        with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {ptok}"}) as p:
            assert hello(p)["state"]["activeDeviceId"] == phone


def test_quick_reconnect_keeps_playing(tc, conn, app):
    _, ptok = token(conn)
    h = {"Authorization": f"Bearer {ptok}"}
    with tc.websocket_connect(URL, headers=h) as p:
        hello(p)
        p.send_json({"type": "transfer", "play": True})
        until(p, "takeover")
    with tc.websocket_connect(URL, headers=h) as p:  # within 0.3 s
        assert hello(p)["state"]["playing"] is True
        time.sleep(0.5)
        assert all(m.get("state", {}).get("playing", True) for m in drain(p) if m["type"] == "state")


def test_newer_socket_replaces_older(tc, conn):
    _, tok = token(conn)
    h = {"Authorization": f"Bearer {tok}"}
    with tc.websocket_connect(URL, headers=h) as old:
        hello(old)
        with tc.websocket_connect(URL, headers=h) as new:
            hello(new)
            with pytest.raises(WebSocketDisconnect) as e:
                while True:
                    old.receive_json()
            assert e.value.code == 4000
            assert [d["online"] for d in until_devices(new, lambda ds: True)] == [True]


def test_users_are_isolated_and_queue_changes_notify(tc, conn):
    ids = list(track_ids(conn).values())
    _, atok = token(conn, "alice")
    _, btok = token(conn, "bob")
    ha = {"Authorization": f"Bearer {atok}"}
    with tc.websocket_connect(URL, headers=ha) as a, \
         tc.websocket_connect(URL, headers={"Authorization": f"Bearer {btok}"}) as b:
        hello(a)
        hello(b)
        drain(a)
        drain(b)
        a.send_json({"type": "transfer", "play": True})
        until(a, "takeover")
        q = tc.post("/api/v1/queues", json={"name": "Q", "trackIds": ids[:2]}, headers=ha).json()
        assert until(a, "queues")["queueId"] == q["id"]
        tc.post(f"/api/v1/queues/{q['id']}/tracks", json={"trackIds": [ids[3]]}, headers=ha)
        assert until(a, "queues")["queueId"] == q["id"]
        later = drain(b)
        assert later == []  # bob saw none of alice's devices, state or queues


def test_revoked_device_is_disconnected(tc, conn):
    did, tok = token(conn)
    _, other = token(conn)
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {tok}"}) as ws:
        hello(ws)
        assert tc.delete(f"/api/v1/me/devices/{did}", headers={"Authorization": f"Bearer {other}"}).status_code == 204
        with pytest.raises(WebSocketDisconnect) as e:
            while True:
                ws.receive_json()
        assert e.value.code == 4401


def test_state_persisted_and_restored(tc, conn, app):
    _, tok = token(conn)
    uid = conn.execute("SELECT id FROM users WHERE username = 'alice'").fetchone()[0]
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {tok}"}) as ws:
        hello(ws)
        ws.send_json({"type": "transfer", "play": True})
        until(ws, "takeover")
        ws.send_json({"type": "state", "state": {"trackId": "tX", "positionMs": 4242, "volume": 0.5}})
        until(ws, "state")
        time.sleep(0.2)
    row = conn.execute("SELECT state FROM playback_state WHERE user_id = ?", (uid,)).fetchone()
    saved = json.loads(row[0])
    assert saved["trackId"] == "tX" and saved["volume"] == 0.5
    # A fresh hub (server restart) loads it, not playing.
    from homebeat.app.connect_api import make_hub

    app.state.ctx.hub = make_hub(app.state.ctx.cfg)
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {tok}"}) as ws:
        st = hello(ws)["state"]
        assert st["trackId"] == "tX" and st["volume"] == 0.5 and st["playing"] is False


def test_me_devices_shows_online(tc, conn):
    did, tok = token(conn)
    h = {"Authorization": f"Bearer {tok}"}
    with tc.websocket_connect(URL, headers=h) as ws:
        hello(ws)
        assert [d["online"] for d in tc.get("/api/v1/me/devices", headers=h).json()] == [True]
    time.sleep(0.05)
    assert [d["online"] for d in tc.get("/api/v1/me/devices", headers=h).json()] == [False]


def test_playlist_changes_push_to_all_members(tc, conn):
    ids = [r[0] for r in conn.execute("SELECT id FROM tracks ORDER BY path")]
    _, atok = token(conn, "alice")
    _, btok = token(conn, "bob")
    _, ctok = token(conn, "carol")
    ha, hb = {"Authorization": f"Bearer {atok}"}, {"Authorization": f"Bearer {btok}"}
    with tc.websocket_connect(URL, headers=ha) as a, tc.websocket_connect(URL, headers=hb) as b, \
         tc.websocket_connect(URL, headers={"Authorization": f"Bearer {ctok}"}) as c:
        for ws in (a, b, c):
            hello(ws)
            drain(ws)
        p = tc.post("/api/v1/playlists", json={"name": "Shared", "trackIds": ids[:1]}, headers=ha).json()
        tc.post(f"/api/v1/playlists/{p['id']}/members", json={"username": "bob", "role": "editor"}, headers=ha)
        m = until(b, "playlist")
        assert m["playlistId"] == p["id"] and not m["deleted"]
        tc.post(f"/api/v1/playlists/{p['id']}/tracks", json={"trackIds": ids[1:2]}, headers=hb)
        ra = [x for x in drain(a) if x["type"] == "playlist"]
        assert ra and ra[-1]["rev"] == tc.get(f"/api/v1/playlists/{p['id']}", headers=ha).json()["rev"]
        tc.delete(f"/api/v1/playlists/{p['id']}", headers=ha)
        while not (m := until(b, "playlist"))["deleted"]:
            pass
        assert m["playlistId"] == p["id"]
        assert [x for x in drain(c) if x["type"] == "playlist"] == []  # carol has no access


def test_cast_to_is_relayed_and_cleared(tc, conn):
    """A phone casting with Google Cast reports the speaker name; other devices show "casting to X"."""
    phone, ptok = token(conn, kind="android")
    web, wtok = token(conn, kind="web")
    with tc.websocket_connect(URL, headers={"Authorization": f"Bearer {ptok}"}) as p, \
         tc.websocket_connect(URL, headers={"Authorization": f"Bearer {wtok}"}) as w:
        hello(p, "Pixel")
        hello(w, "Firefox", "web")
        until_devices(p, lambda ds: len(ds) == 2)
        p.send_json({"type": "transfer", "toDeviceId": phone, "play": True})
        until(p, "takeover")
        def state_until(ws, pred):
            for _ in range(20):
                st = until(ws, "state")["state"]
                if pred(st):
                    return st
            raise AssertionError("state never matched")

        p.send_json({"type": "state", "state": {"playing": True, "castTo": "Bedroom display"}})
        state_until(w, lambda st: st["castTo"] == "Bedroom display")
        # A report without the field (stopped casting / older client) clears it.
        p.send_json({"type": "state", "state": {"playing": True, "positionMs": 5}})
        state_until(w, lambda st: st["positionMs"] == 5 and st["castTo"] is None)
        p.send_json({"type": "state", "state": {"playing": True, "castTo": "Bedroom display"}})
        state_until(w, lambda st: st["castTo"] == "Bedroom display")
        # Another device taking over isn't casting.
        w.send_json({"type": "transfer", "toDeviceId": web, "play": True})
        assert until(w, "takeover")["state"]["castTo"] is None
