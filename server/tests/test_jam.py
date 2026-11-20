"""Jam (listen together): host controls, guest permissions, token expiry, kick/end, rate limits, queue
integration and the guest WebSocket. One TestClient carries every caller (headers per request), so HTTP
handlers and sockets share one event loop as in production."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nasmusic import auth
from nasmusic.jam import clean_name
from tests.conftest import make_user, track_ids

API = "/api/v1"


@pytest.fixture
def tc(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def ids(conn):
    return track_ids(conn)


def bearer(conn, username="alice", admin=True):
    u = make_user(conn, username, admin)
    _, tok = auth.create_device(conn, u.id, f"{username}-phone", "android")
    return {"Authorization": f"Bearer {tok}"}, u.id


@pytest.fixture
def host(conn):
    return bearer(conn, "alice")[0]


def start(tc, host, **settings):
    r = tc.post(f"{API}/jams", json=settings, headers=host)
    assert r.status_code == 200, r.text
    return r.json()


def join(tc, code, name="Sam", **kw):
    r = tc.post(f"{API}/jam/join/{code}", json={"name": name}, **kw)
    assert r.status_code == 200, r.text
    body = r.json()
    return {"X-Jam-Token": body["token"]}, body


def add(tc, g, tid, play_next=False):
    return tc.post(f"{API}/jam/requests", json={"trackId": tid, "playNext": play_next}, headers=g)


def queue_titles(tc, host, qid):
    return [t["title"] for t in tc.get(f"{API}/queues/{qid}", headers=host).json()["tracks"]]


# ---------- starting and joining ----------


def test_start_join_and_guest_view(tc, host, ids):
    j = start(tc, host)
    jam = j["jam"]
    assert jam["joinUrl"] == f"https://music.example.com/jam/{jam['joinCode']}"
    assert jam["lanJoinUrl"] == f"https://192.168.1.50:8443/jam/{jam['joinCode']}"
    assert j["me"]["isHost"] and j["upcoming"] == [] and j["nowPlaying"] is None
    # Starting again returns the same Jam; /jams/current finds it.
    assert start(tc, host)["jam"]["id"] == jam["id"]
    assert tc.get(f"{API}/jams/current", headers=host).json()["jam"]["id"] == jam["id"]

    pv = tc.get(f"{API}/jam/join/{jam['joinCode']}").json()
    assert pv["hostName"] == "alice" and pv["guestCount"] == 0 and pv["signedInAs"] is None
    g, body = join(tc, jam["joinCode"], "  Sam\u202e  the\x00 guest ")
    assert body["token"].startswith("jg_")
    assert body["me"]["name"] == "Sam the guest" and not body["me"]["isHost"]
    assert "joinCode" not in body["jam"] and "queueId" not in body["jam"]
    assert tc.get(f"{API}/jam/me", headers=g).json()["guests"][0]["name"] == "Sam the guest"
    qr = tc.get(f"{API}/jams/{jam['id']}/qr", headers=host).json()
    assert qr["qr"].startswith("data:image/png;base64,") and qr["lanQr"]


def test_guest_token_is_not_a_session(tc, host, ids):
    jam = start(tc, host)["jam"]
    g, _ = join(tc, jam["joinCode"])
    tok = g["X-Jam-Token"]
    tid = ids["Song 1"]
    for path in ("/library/albums", f"/stream/{tid}", "/queues", "/me", f"/jams/{jam['id']}"):
        assert tc.get(f"{API}{path}", headers={"Authorization": f"Bearer {tok}"}).status_code == 401, path
    assert tc.get(f"{API}/stream/{tid}?token={tok}").status_code == 401
    # Guests can't use host endpoints even with their own token header.
    assert tc.delete(f"{API}/jams/{jam['id']}", headers=g).status_code == 401
    assert tc.get(f"{API}/jam/me").status_code == 401
    assert tc.get(f"{API}/jam/me", headers={"X-Jam-Token": "jg_nope"}).status_code == 401


def test_names_and_bad_codes(tc, host):
    assert clean_name("<b>Hi</b>") == "<b>Hi</b>"   # kept as text; clients never render it as HTML
    assert clean_name("a" * 100) == "a" * 32
    jam = start(tc, host)["jam"]
    assert tc.post(f"{API}/jam/join/{jam['joinCode']}", json={"name": " \u200b "}).status_code == 400
    assert tc.get(f"{API}/jam/join/nope-nope-nope").status_code == 404
    assert tc.post(f"{API}/jam/join/nope-nope-nope", json={"name": "x"}).status_code == 404


def test_other_users_cannot_manage(tc, host, conn):
    jam = start(tc, host)["jam"]
    bob, _ = bearer(conn, "bob", admin=False)
    assert tc.get(f"{API}/jams/{jam['id']}", headers=bob).status_code == 404
    assert tc.delete(f"{API}/jams/{jam['id']}", headers=bob).status_code == 404
    assert tc.patch(f"{API}/jams/{jam['id']}", json={"approval": True}, headers=bob).status_code == 404
    assert tc.get(f"{API}/jams/current", headers=bob).status_code == 204


def test_member_joins_as_themselves(tc, host, conn):
    jam = start(tc, host)["jam"]
    bob, _ = bearer(conn, "bob", admin=False)
    assert tc.get(f"{API}/jam/join/{jam['joinCode']}", headers=bob).json()["signedInAs"] == "bob"
    r = tc.post(f"{API}/jam/join/{jam['joinCode']}", json={"asMember": True}, headers=bob)
    assert r.status_code == 200 and r.json()["me"]["name"] == "bob" and r.json()["me"]["isMember"]
    assert tc.post(f"{API}/jam/join/{jam['joinCode']}", json={"asMember": True}).status_code == 401
    assert tc.post(f"{API}/jam/join/{jam['joinCode']}", json={"asMember": True}, headers=host).status_code == 409


# ---------- queue integration ----------


def test_guest_adds_go_to_the_host_queue_with_attribution(tc, host, ids, conn):
    j = start(tc, host)
    jam, qid = j["jam"], j["jam"]["queueId"]
    sam, _ = join(tc, jam["joinCode"], "Sam")
    kim, _ = join(tc, jam["joinCode"], "Kim")
    assert add(tc, sam, ids["Song 1"]).json()["queued"] is True
    assert add(tc, kim, ids["Opening"]).status_code == 200
    tc.post(f"{API}/jams/{jam['id']}/queue", json={"trackId": ids["Closing"]}, headers=host)
    assert queue_titles(tc, host, qid) == ["Song 1", "Opening", "Closing"]
    snap = tc.get(f"{API}/jam/me", headers=sam).json()
    assert snap["nowPlaying"]["track"]["title"] == "Song 1"
    assert snap["nowPlaying"]["addedBy"]["name"] == "Sam"
    assert [(e["track"]["title"], e["addedBy"]["name"], e["addedBy"]["isHost"]) for e in snap["upcoming"]] == [
        ("Opening", "Kim", False), ("Closing", "alice", True)]
    assert set(snap["upcoming"][0]["track"]) == {"id", "title", "artist", "album", "albumId", "durationS"}
    # Already coming up -> 409; not in the library -> 404.
    assert add(tc, sam, ids["Opening"]).status_code == 409
    assert add(tc, sam, "nope").status_code == 404


def test_jam_follows_the_queue_the_host_plays(tc, host, ids):
    q = tc.post(f"{API}/queues", json={"name": "Album", "trackIds": [ids["Song 1"], ids["Song 2"]]},
                headers=host).json()
    tc.patch(f"{API}/queues/{q['id']}", json={"currentIndex": 0}, headers=host)
    j = start(tc, host)
    assert j["jam"]["queueId"] != q["id"]   # nothing playing yet -> its own "Jam" queue
    # The host starts playing the album (Connect state): guest adds now land there.
    hub, uid = tc.app.state.ctx.hub, _uid(tc, host)

    async def play():
        u = await hub._user(uid)
        u.state.update({"queueId": q["id"], "index": 0, "trackId": ids["Song 1"], "playing": True})

    tc.portal.call(play)
    sam, _ = join(tc, j["jam"]["joinCode"])
    assert add(tc, sam, ids["Opening"]).status_code == 200
    assert queue_titles(tc, host, q["id"]) == ["Song 1", "Song 2", "Opening"]
    snap = tc.get(f"{API}/jam/me", headers=sam).json()
    assert snap["nowPlaying"]["track"]["title"] == "Song 1" and snap["nowPlaying"]["playing"]
    assert [e["track"]["title"] for e in snap["upcoming"]] == ["Song 2", "Opening"]


def _uid(tc, host):
    return tc.get(f"{API}/me", headers=host).json()["id"]


def test_play_next_permission(tc, host, ids):
    j = start(tc, host)
    jam, qid = j["jam"], j["jam"]["queueId"]
    sam, _ = join(tc, jam["joinCode"])
    add(tc, sam, ids["Song 1"])
    add(tc, sam, ids["Song 2"])
    assert add(tc, sam, ids["Opening"], play_next=True).status_code == 403
    tc.patch(f"{API}/jams/{jam['id']}", json={"allowPlayNext": True}, headers=host)
    assert add(tc, sam, ids["Opening"], play_next=True).status_code == 200
    assert queue_titles(tc, host, qid) == ["Song 1", "Opening", "Song 2"]


def test_approval_flow(tc, host, ids):
    j = start(tc, host, approval=True)
    jam, qid = j["jam"], j["jam"]["queueId"]
    sam, _ = join(tc, jam["joinCode"], "Sam")
    kim, _ = join(tc, jam["joinCode"], "Kim")
    r1 = add(tc, sam, ids["Song 1"]).json()
    r2 = add(tc, kim, ids["Song 2"]).json()
    assert not r1["queued"] and r1["request"]["status"] == "pending"
    assert add(tc, kim, ids["Song 1"]).status_code == 409   # someone already asked
    assert queue_titles(tc, host, qid) == []
    # Guests see only their own pending requests; the host sees all.
    assert [p["track"]["title"] for p in tc.get(f"{API}/jam/me", headers=sam).json()["pending"]] == ["Song 1"]
    hs = tc.get(f"{API}/jams/{jam['id']}", headers=host).json()
    assert [(p["track"]["title"], p["by"]["name"]) for p in hs["pending"]] == [("Song 1", "Sam"), ("Song 2", "Kim")]
    rid1, rid2 = r1["request"]["id"], r2["request"]["id"]
    assert tc.post(f"{API}/jams/{jam['id']}/requests/{rid1}", json={"approve": True}, headers=host).status_code == 200
    assert tc.post(f"{API}/jams/{jam['id']}/requests/{rid2}", json={"approve": False}, headers=host).status_code == 200
    assert tc.post(f"{API}/jams/{jam['id']}/requests/{rid2}", json={"approve": True}, headers=host).status_code == 409
    assert queue_titles(tc, host, qid) == ["Song 1"]
    snap = tc.get(f"{API}/jam/me", headers=sam).json()
    assert snap["nowPlaying"]["addedBy"]["name"] == "Sam" and snap["pending"] == []
    # A guest withdraws their own pending request (not someone else's).
    r3 = add(tc, kim, ids["Opening"]).json()["request"]["id"]
    assert tc.delete(f"{API}/jam/requests/{r3}", headers=sam).status_code == 404
    assert tc.delete(f"{API}/jam/requests/{r3}", headers=kim).status_code == 204


def test_max_per_guest(tc, host, ids):
    j = start(tc, host, maxPerGuest=2)
    jam = j["jam"]
    sam, _ = join(tc, jam["joinCode"])
    assert add(tc, sam, ids["Song 1"]).status_code == 200   # becomes the current track: doesn't count
    assert add(tc, sam, ids["Song 2"]).status_code == 200
    assert add(tc, sam, ids["Opening"]).status_code == 200
    me = tc.get(f"{API}/jam/me", headers=sam).json()["me"]
    assert me["waiting"] == 2 and me["remaining"] == 0
    r = add(tc, sam, ids["Closing"])
    assert r.status_code == 403 and "2 songs" in r.json()["detail"]
    # The host lifts the cap.
    tc.patch(f"{API}/jams/{jam['id']}", json={"maxPerGuest": None}, headers=host)
    assert add(tc, sam, ids["Closing"]).status_code == 200


def test_host_removes_entry_and_attribution_stays_right(tc, host, ids):
    j = start(tc, host)
    jam, qid = j["jam"], j["jam"]["queueId"]
    sam, _ = join(tc, jam["joinCode"], "Sam")
    kim, _ = join(tc, jam["joinCode"], "Kim")
    add(tc, kim, ids["Song 1"])
    add(tc, sam, ids["Song 2"])
    assert tc.delete(f"{API}/jams/{jam['id']}/queue/1?trackId={ids['Song 1']}", headers=host).status_code == 409
    assert tc.delete(f"{API}/jams/{jam['id']}/queue/1?trackId={ids['Song 2']}", headers=host).status_code == 200
    assert queue_titles(tc, host, qid) == ["Song 1"]
    add(tc, kim, ids["Song 2"])
    snap = tc.get(f"{API}/jams/{jam['id']}", headers=host).json()
    assert [(e["track"]["title"], e["addedBy"]["name"]) for e in snap["upcoming"]] == [("Song 2", "Kim")]


def test_votes_and_search(tc, host, ids):
    jam = start(tc, host)["jam"]
    sam, _ = join(tc, jam["joinCode"], "Sam")
    kim, _ = join(tc, jam["joinCode"], "Kim")
    add(tc, sam, ids["Song 1"])
    add(tc, sam, ids["Song 2"])
    s2 = ids["Song 2"]
    assert tc.post(f"{API}/jam/votes", json={"trackId": s2, "value": 1}, headers=sam).status_code == 204
    assert tc.post(f"{API}/jam/votes", json={"trackId": s2, "value": -1}, headers=kim).status_code == 204
    assert tc.post(f"{API}/jam/votes", json={"trackId": s2, "value": 5}, headers=kim).status_code == 422
    v = tc.get(f"{API}/jam/me", headers=sam).json()["upcoming"][0]["votes"]
    assert v == {"up": 1, "down": 1, "mine": 1}
    tc.post(f"{API}/jam/votes", json={"trackId": s2, "value": 0}, headers=sam)
    assert tc.get(f"{API}/jam/me", headers=sam).json()["upcoming"][0]["votes"] == {"up": 0, "down": 1, "mine": 0}
    res = tc.get(f"{API}/jam/search?q=song", headers=sam).json()["tracks"]
    assert {t["title"] for t in res} == {"Song 1", "Song 2"}
    assert all("starred" not in t and "rating" not in t and "flacBytes" not in t for t in res)
    assert tc.get(f"{API}/jam/search?q=song").status_code == 401


# ---------- kick, end, links, expiry ----------


def test_kick(tc, host, ids):
    jam = start(tc, host, approval=True)["jam"]
    sam, body = join(tc, jam["joinCode"], "Sam")
    add(tc, sam, ids["Song 1"])
    gid = body["me"]["guestId"]
    with tc.websocket_connect(f"{API}/jam/ws?token={sam['X-Jam-Token']}") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        r = tc.delete(f"{API}/jams/{jam['id']}/guests/{gid}", headers=host)
        assert r.status_code == 200 and r.json()["guests"] == [] and r.json()["pending"] == []
        msgs = []
        with pytest.raises(WebSocketDisconnect) as e:
            while True:
                msgs.append(ws.receive_json()["type"])
        assert e.value.code == 4403 and "kicked" in msgs
    assert tc.get(f"{API}/jam/me", headers=sam).status_code == 401
    assert add(tc, sam, ids["Song 2"]).status_code == 401
    with pytest.raises(WebSocketDisconnect) as e:
        with tc.websocket_connect(f"{API}/jam/ws?token={sam['X-Jam-Token']}") as ws:
            ws.receive_json()
    assert e.value.code == 4401


def test_end_kills_every_token(tc, host, ids, conn):
    jam = start(tc, host)["jam"]
    sam, _ = join(tc, jam["joinCode"], "Sam")
    kim, _ = join(tc, jam["joinCode"], "Kim")
    with tc.websocket_connect(f"{API}/jam/ws?token={sam['X-Jam-Token']}") as ws:
        ws.receive_json()
        assert tc.delete(f"{API}/jams/{jam['id']}", headers=host).status_code == 204
        with pytest.raises(WebSocketDisconnect) as e:
            while True:
                ws.receive_json()
        assert e.value.code == 4410
    for g in (sam, kim):
        assert tc.get(f"{API}/jam/me", headers=g).status_code == 401
    assert tc.get(f"{API}/jam/join/{jam['joinCode']}").status_code == 404
    assert tc.get(f"{API}/jams/{jam['id']}", headers=host).status_code == 410
    assert tc.get(f"{API}/jams/current", headers=host).status_code == 204
    assert start(tc, host)["jam"]["id"] != jam["id"]   # a fresh one


def test_regenerate_and_close_link(tc, host):
    jam = start(tc, host)["jam"]
    sam, _ = join(tc, jam["joinCode"])
    new = tc.post(f"{API}/jams/{jam['id']}/link", json={"ttlMinutes": 30}, headers=host).json()["jam"]
    assert new["joinCode"] != jam["joinCode"]
    assert tc.get(f"{API}/jam/join/{jam['joinCode']}").status_code == 404
    assert tc.get(f"{API}/jam/join/{new['joinCode']}").status_code == 200
    assert tc.get(f"{API}/jam/me", headers=sam).status_code == 200   # guests already in stay
    closed = tc.post(f"{API}/jams/{jam['id']}/link", json={"close": True}, headers=host).json()["jam"]
    assert closed["joinCode"] is None and closed["joinUrl"] is None
    assert tc.get(f"{API}/jam/join/{new['joinCode']}").status_code == 404


def test_link_token_and_jam_expiry(tc, host, conn):
    jam = start(tc, host)["jam"]
    sam, _ = join(tc, jam["joinCode"], "Sam")
    kim, body = join(tc, jam["joinCode"], "Kim")
    past = "2000-01-01T00:00:00.000Z"
    conn.execute("UPDATE jams SET join_expires_at = ? WHERE id = ?", (past, jam["id"]))
    r = tc.post(f"{API}/jam/join/{jam['joinCode']}", json={"name": "Late"})
    assert r.status_code == 410 and "expired" in r.json()["detail"]
    conn.execute("UPDATE jam_guests SET expires_at = ? WHERE id = ?", (past, body["me"]["guestId"]))
    assert tc.get(f"{API}/jam/me", headers=kim).status_code == 401
    assert tc.get(f"{API}/jam/me", headers=sam).status_code == 200
    conn.execute("UPDATE jams SET expires_at = ? WHERE id = ?", (past, jam["id"]))
    assert tc.get(f"{API}/jam/me", headers=sam).status_code == 401
    assert conn.execute("SELECT ended_at FROM jams WHERE id = ?", (jam["id"],)).fetchone()[0] is not None


# ---------- rate limits ----------


def test_rate_limits(tc, host, ids):
    jam = start(tc, host)["jam"]
    sam, _ = join(tc, jam["joinCode"])
    codes = [tc.get(f"{API}/jam/search?q=song", headers=sam).status_code for _ in range(21)]
    assert codes[:20] == [200] * 20 and codes[20] == 429
    reacts = [tc.post(f"{API}/jam/reactions", json={"emoji": "🔥"}, headers=sam).status_code for _ in range(11)]
    assert reacts[:10] == [204] * 10 and reacts[10] == 429
    assert tc.post(f"{API}/jam/reactions", json={"emoji": "<script>"}, headers=sam).status_code == 422
    # Joining is limited per client IP (one join already happened above).
    joins = [tc.post(f"{API}/jam/join/{jam['joinCode']}", json={"name": f"g{i}"}).status_code for i in range(10)]
    assert joins[:9] == [200] * 9 and joins[9] == 429


def test_adds_rate_limited(tc, host, ids, app):
    jam = start(tc, host, maxPerGuest=None)["jam"]
    sam, body = join(tc, jam["joinCode"])
    for _ in range(10):
        app.state.ctx.jam_hub.limits.add.fail(body["me"]["guestId"])
    assert add(tc, sam, ids["Song 1"]).status_code == 429


# ---------- live updates ----------


def test_websocket_live_updates_and_reactions(tc, host, ids):
    jam = start(tc, host)["jam"]
    sam, _ = join(tc, jam["joinCode"], "Sam")
    kim, _ = join(tc, jam["joinCode"], "Kim")
    with tc.websocket_connect(f"{API}/jam/ws?token={sam['X-Jam-Token']}") as ws, \
            tc.websocket_connect(f"{API}/jam/ws?jam={jam['id']}", headers=host) as hws:
        first = ws.receive_json()
        assert first["type"] == "snapshot" and first["me"]["name"] == "Sam"
        assert hws.receive_json()["me"]["isHost"]
        add(tc, kim, ids["Song 1"])
        add(tc, kim, ids["Song 2"])

        def until(sock, pred):
            while True:
                m = sock.receive_json()
                if pred(m):
                    return m

        snap = until(ws, lambda m: m["type"] == "snapshot" and len(m["upcoming"]) == 1)
        assert snap["upcoming"][0]["addedBy"]["name"] == "Kim"
        until(hws, lambda m: m["type"] == "snapshot" and len(m["upcoming"]) == 1)
        tc.post(f"{API}/jam/reactions", json={"emoji": "🔥"}, headers=kim)
        r = until(ws, lambda m: m["type"] == "reaction")
        assert r == {"type": "reaction", "emoji": "🔥", "name": "Kim"}
        ws.send_json({"type": "ping"})
        until(ws, lambda m: m["type"] == "pong")
    with pytest.raises(WebSocketDisconnect) as e:
        with tc.websocket_connect(f"{API}/jam/ws?jam={jam['id']}") as ws:
            ws.receive_json()
    assert e.value.code == 4401


def test_host_family_mode_applies_to_guests(tc, host, ids, conn):
    """With the host's family mode on, guests can't find or add explicit songs."""
    conn.execute("INSERT INTO track_explicit (track_id, status, source, updated_at) VALUES (?, 'explicit', 'admin', "
                 "'2026-01-01T00:00:00Z')", (ids["Song 1"],))
    assert tc.put(f"{API}/me/family", json={"enabled": True}, headers=host).status_code == 200
    jam = start(tc, host)["jam"]
    g, _ = join(tc, jam["joinCode"])
    found = {t["title"] for t in tc.get(f"{API}/jam/search", params={"q": "Song"}, headers=g).json()["tracks"]}
    assert "Song 1" not in found and "Song 2" in found
    assert add(tc, g, ids["Song 1"]).status_code == 404
    assert add(tc, g, ids["Song 2"]).status_code == 200
