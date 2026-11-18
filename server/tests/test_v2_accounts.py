"""v2 accounts: login/register/logout, cookie sessions, invites, admin user management, /me, QR pairing,
and per-user isolation (user B can't see user A's anything)."""
import uuid

from fastapi.testclient import TestClient

from tests.conftest import make_user, track_ids

API = "/api/v1"


def login(c, username, password="password123", **kw):
    return c.post(f"{API}/auth/login", json={"username": username, "password": password, "deviceName": "test",
                                            "deviceKind": "web", **kw})


def test_server_info_public(anon):
    r = anon.get(f"{API}/server-info").json()
    assert r["registration"] == "invite" and r["lanUrl"] == "https://192.168.1.50:8443"
    assert r["tunnelUrl"] == "https://music.example.com" and len(r["certSha256"].split(":")) == 32
    assert r["serverId"] == anon.get(f"{API}/health").json()["serverId"]


def test_login_logout_and_cookie(anon, conn):
    make_user(conn, "carol")
    r = login(anon, "CAROL")  # usernames are case-insensitive
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["username"] == "carol" and s["isAdmin"] is False and s["deviceId"] and s["deviceToken"]
    assert "set-cookie" not in r.headers
    me = anon.get(f"{API}/me", headers={"Authorization": f"Bearer {s['deviceToken']}"}).json()
    assert me["username"] == "carol" and me["lastLogin"]
    assert conn.execute("SELECT kind FROM devices WHERE id = ?", (s["deviceId"],)).fetchone()[0] == "web"

    # Web player: the cookie alone authenticates.
    r = login(anon, "carol", cookie=True)
    assert "nm_session=" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    assert anon.get(f"{API}/me").status_code == 200
    assert anon.post(f"{API}/auth/logout").status_code == 204
    anon.cookies.clear()
    tok = r.json()["deviceToken"]
    assert anon.get(f"{API}/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_login_failures_and_rate_limit(anon, conn):
    make_user(conn, "dave")
    assert login(anon, "dave", "wrong-password").status_code == 401
    assert login(anon, "nobody").status_code == 401
    for _ in range(3):
        login(anon, "dave", "nope")
    assert login(anon, "dave").status_code == 429  # 5 failures/min/IP: even the right password waits
    anon.app.state.ctx.login_limiter.clear()
    assert login(anon, "dave").status_code == 200


def test_disabled_user(client, conn, bob):
    assert bob.get(f"{API}/me").status_code == 200
    r = client.patch(f"{API}/admin/users/{bob.user_id}", json={"disabled": True})
    assert r.status_code == 200 and r.json()["disabled"] is True and r.json()["deviceCount"] == 0
    assert bob.get(f"{API}/me").status_code == 401  # devices revoked
    with TestClient(client.app) as c:
        assert login(c, "bob").status_code == 403
    client.patch(f"{API}/admin/users/{bob.user_id}", json={"disabled": False})
    with TestClient(client.app) as c:
        assert login(c, "bob").status_code == 200


def test_invites_and_register(client, conn):
    r = client.post(f"{API}/admin/invites", json={"note": "for Eve", "expiresInDays": 3})
    assert r.status_code == 200
    inv = r.json()
    assert inv["url"] == f"https://music.example.com/join/{inv['code']}"
    listed = client.get(f"{API}/admin/invites").json()
    assert listed[0]["code"] == inv["code"] and listed[0]["createdBy"] == "alice" and listed[0]["usedBy"] is None

    with TestClient(client.app) as anon:
        body = {"inviteCode": inv["code"], "username": "eve", "password": "longenough", "deviceName": "p",
                "deviceKind": "android"}
        assert anon.post(f"{API}/auth/register", json={**body, "username": "x"}).status_code == 422
        assert anon.post(f"{API}/auth/register", json={**body, "password": "short"}).status_code == 422
        assert anon.post(f"{API}/auth/register", json={**body, "username": "ALICE"}).status_code == 409
        r = anon.post(f"{API}/auth/register", json=body)
        assert r.status_code == 200 and r.json()["username"] == "eve" and r.json()["isAdmin"] is False
        # single use
        assert anon.post(f"{API}/auth/register", json={**body, "username": "eve2"}).status_code == 403
        assert anon.post(f"{API}/auth/register", json={**body, "inviteCode": "nope"}).status_code == 403
    assert client.get(f"{API}/admin/invites").json()[0]["usedBy"] == "eve"

    inv2 = client.post(f"{API}/admin/invites", json={}).json()
    assert client.delete(f"{API}/admin/invites/{inv2['code']}").status_code == 204
    conn.execute("UPDATE invites SET expires_at = '2000-01-01T00:00:00Z' WHERE code = ?", (inv["code"],))


def test_expired_invite(client, conn):
    code = client.post(f"{API}/admin/invites", json={}).json()["code"]
    conn.execute("UPDATE invites SET expires_at = '2000-01-01T00:00:00Z' WHERE code = ?", (code,))
    with TestClient(client.app) as anon:
        r = anon.post(f"{API}/auth/register", json={"inviteCode": code, "username": "late", "password": "12345678",
                                                    "deviceName": "p", "deviceKind": "web"})
        assert r.status_code == 403


def test_admin_only(bob):
    for method, path in [("get", "/admin/invites"), ("post", "/admin/invites"), ("get", "/admin/users")]:
        assert getattr(bob, method)(f"{API}{path}").status_code == 403


def test_last_admin_protected_and_reset_password(client, bob, conn):
    # The config 'admin' user also exists; demote it so alice is the last active admin.
    admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
    assert client.patch(f"{API}/admin/users/{admin_id}", json={"isAdmin": False}).status_code == 200
    assert client.patch(f"{API}/admin/users/{client.user_id}", json={"isAdmin": False}).status_code == 409
    assert client.patch(f"{API}/admin/users/{client.user_id}", json={"disabled": True}).status_code == 409
    assert client.patch("/api/v1/admin/users/nope", json={"disabled": True}).status_code == 404
    assert client.patch(f"{API}/admin/users/{bob.user_id}", json={"isAdmin": True}).json()["isAdmin"] is True
    assert client.patch(f"{API}/admin/users/{client.user_id}", json={"isAdmin": False}).status_code == 200

    assert bob.post(f"{API}/admin/users/{client.user_id}/reset-password", json={"new": "short"}).status_code == 422
    assert bob.post(f"{API}/admin/users/{client.user_id}/reset-password",
                    json={"new": "brand-new-pw"}).status_code == 204
    assert client.get(f"{API}/me").status_code == 401  # signed out everywhere
    with TestClient(client.app) as c:
        assert login(c, "alice", "brand-new-pw").status_code == 200


def test_change_password_and_devices(client, conn):
    assert client.put(f"{API}/me/password", json={"current": "wrong", "new": "whatever123"}).status_code == 403
    assert client.put(f"{API}/me/password", json={"current": "password123", "new": "short"}).status_code == 422
    assert client.put(f"{API}/me/password", json={"current": "password123", "new": "whatever123"}).status_code == 204
    with TestClient(client.app) as c:
        other = login(c, "alice", "whatever123").json()
    devs = client.get(f"{API}/me/devices").json()
    assert {d["id"] for d in devs} == {client.device_id, other["deviceId"]}
    assert [d["current"] for d in devs if d["id"] == client.device_id] == [True]
    assert client.delete(f"{API}/me/devices/{other['deviceId']}").status_code == 204
    assert len(client.get(f"{API}/me/devices").json()) == 1


def test_pair_code_logs_in_as_that_user(client, bob, anon):
    r = bob.post(f"{API}/me/pair-code").json()
    assert r["qrPayload"]["pairCode"] == r["pairCode"] and r["qrPayload"]["lanUrl"]
    s = anon.post(f"{API}/pair", json={"pairCode": r["pairCode"], "deviceName": "Bob's phone"}).json()
    assert s["username"] == "bob" and s["userId"] == bob.user_id and s["deviceToken"]
    assert anon.post(f"{API}/pair", json={"pairCode": r["pairCode"], "deviceName": "again"}).status_code == 403


def test_users_cannot_touch_each_others_devices(client, bob):
    assert bob.delete(f"{API}/me/devices/{client.device_id}").status_code == 404
    assert client.get(f"{API}/me").status_code == 200


def test_per_user_isolation(client, bob, conn):
    """Everything about listening is per user."""
    ids = track_ids(conn)
    s1, s2 = ids["Song 1"], ids["Song 2"]
    client.put(f"{API}/favourites/{s1}")
    client.post(f"{API}/events", json={"events": [
        {"id": str(uuid.uuid4()), "type": "rate", "trackId": s2, "at": "2026-11-18T10:00:00Z", "rating": 5},
        {"id": str(uuid.uuid4()), "type": "play", "trackId": s2, "at": "2026-11-18T10:01:00Z"}]})
    pl = client.post(f"{API}/playlists", json={"name": "Alice's", "trackIds": [s1]}).json()
    q = client.post(f"{API}/queues", json={"name": "Alice q", "trackIds": [s1, s2]}).json()
    client.put(f"{API}/tracks/{s1}/lyrics", json={"raw": "alice's words"})
    client.put(f"{API}/me/settings", json={"settings": {"context.output": True}})
    uid_a = client.user_id
    conn.execute("INSERT INTO mixes (id, user_id, kind, title, for_date, created_at) "
                 "VALUES ('ma', ?, 'daily', 'Daily', '2026-11-18', '2026-11-18T02:00:00Z')", (uid_a,))
    conn.execute("INSERT INTO mix_tracks VALUES ('ma', 0, ?, 1, 'r')", (s1,))
    conn.execute("INSERT INTO suggestions (id, user_id, artist, album, reason, created_at) "
                 "VALUES ('sa', ?, 'A', 'B', 'r', 'x')", (uid_a,))

    # Alice sees her state...
    assert client.get(f"{API}/library/tracks/{s1}").json()["starred"] is True
    assert client.get(f"{API}/library/tracks/{s2}").json()["rating"] == 5
    assert [t["id"] for t in client.get(f"{API}/lists/most_played").json()] == [s2]
    # ...Bob sees none of it.
    assert bob.get(f"{API}/favourites").json() == []
    assert bob.get(f"{API}/library/tracks/{s1}").json()["starred"] is False
    assert bob.get(f"{API}/library/tracks/{s2}").json()["rating"] is None
    assert bob.get(f"{API}/lists/most_played").json() == []
    assert bob.get(f"{API}/lists/recently_played").json() == []
    assert len(bob.get(f"{API}/lists/not_played").json()) == 5
    assert bob.get(f"{API}/playlists").json() == []
    assert bob.get(f"{API}/playlists/{pl['id']}").status_code == 404
    assert bob.delete(f"{API}/playlists/{pl['id']}").status_code == 404
    assert bob.post(f"{API}/playlists/{pl['id']}/tracks", json={"trackIds": [s2]}).status_code == 404
    assert bob.get(f"{API}/queues").json() == []
    assert bob.get(f"{API}/queues/{q['id']}").status_code == 404
    assert bob.patch(f"{API}/queues/{q['id']}", json={"name": "mine"}).status_code == 404
    assert bob.get(f"{API}/mixes/today").json() == []
    assert bob.get(f"{API}/mixes/ma").status_code == 404
    assert bob.get(f"{API}/suggestions").json() == []
    assert bob.post(f"{API}/suggestions/sa", json={"status": "want"}).status_code == 404
    assert bob.get(f"{API}/suggestions/sa/cover").status_code == 404
    assert bob.get(f"{API}/sync/manifest").json()["items"] == []
    assert bob.get(f"{API}/me/settings").json() == {"settings": {}}
    assert bob.get(f"{API}/me/data").json()["events"]["count"] == 0
    assert bob.get(f"{API}/tracks/{s1}/lyrics").status_code == 404  # alice's edit isn't bob's
    info = bob.get(f"{API}/library/tracks/{s2}/info").json()
    assert info["playCount"] == 0
    # Bob's own actions don't touch Alice's.
    bob.put(f"{API}/favourites/{s2}")
    bob.delete(f"{API}/favourites/{s1}")
    assert [t["id"] for t in client.get(f"{API}/favourites").json()] == [s1]
    export = bob.get(f"{API}/me/data/export").json()
    assert export["user"]["username"] == "bob" and export["events"] == [] and export["playlists"] == []
