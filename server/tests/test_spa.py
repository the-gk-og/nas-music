"""Web player serving at / (history fallback), /join/<code>, admin pages under /admin, CORS for Tauri."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nasmusic.app import create_app
from nasmusic.web import spa


@pytest.fixture
def web_dist(tmp_path):
    d = tmp_path / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<!doctype html><title>player</title><div id=app></div>")
    (d / "assets" / "app-1234.js").write_text("console.log('hi')")
    (d / "favicon.svg").write_text("<svg/>")
    (tmp_path / "secret.txt").write_text("nope")
    return d


@pytest.fixture
def player(cfg, scanned, web_dist):
    with TestClient(create_app(replace(cfg, web_root=web_dist))) as c:
        yield c


def test_spa_routes_and_fallback(player):
    for path in ("/", "/albums/123", "/queues", "/join/abc123"):
        r = player.get(path)
        assert r.status_code == 200 and "player" in r.text, path
        assert r.headers["cache-control"] == "no-cache"
        assert "connect-src 'self' ws: wss:" in r.headers["content-security-policy"]
    a = player.get("/assets/app-1234.js")
    assert a.status_code == 200 and "immutable" in a.headers["cache-control"]
    assert "javascript" in a.headers["content-type"]
    assert player.get("/favicon.svg").status_code == 200
    assert player.get("/assets/missing-999.js").status_code == 404  # not index.html
    assert player.get("/..%2fsecret.txt").status_code == 404
    assert player.get("/api/v1/nope").status_code == 404
    assert player.get("/api/v1/nope").headers["content-type"].startswith("application/json")
    r = player.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/login"
    assert "default-src 'none'" in player.get("/admin/login").headers["content-security-policy"]


def test_placeholder_and_join_form_without_web_build(cfg, scanned, conn, monkeypatch):
    monkeypatch.setattr(spa, "DOCKER_WEB", cfg.data_dir / "none")
    monkeypatch.setattr(spa, "REPO_WEB", cfg.data_dir / "none")
    from nasmusic.auth import users
    from tests.conftest import make_user

    admin = make_user(conn, "alice", admin=True)
    code = users.create_invite(conn, admin.id)["code"]
    with TestClient(create_app(cfg)) as c:
        assert "web player isn't installed" in c.get("/").text
        form = c.get(f"/join/{code}")
        assert "Create account" in form.text and f'action="/join/{code}"' in form.text
        bad = c.post(f"/join/{code}", data={"username": "x", "password": "longenough"})
        assert "3-32 characters" in bad.text
        ok = c.post(f"/join/{code}", data={"username": "frank", "password": "longenough"})
        assert "Welcome, frank" in ok.text
        again = c.post(f"/join/{code}", data={"username": "frank2", "password": "longenough"})
        assert "used or expired" in again.text
        r = c.post("/api/v1/auth/login", json={"username": "frank", "password": "longenough",
                                               "deviceName": "d", "deviceKind": "web"})
        assert r.status_code == 200


def test_cors_for_tauri_bearer_only(client):
    pre = client.options("/api/v1/library/artists", headers={
        "Origin": "tauri://localhost", "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization"})
    assert pre.status_code == 200 and pre.headers["access-control-allow-origin"] == "tauri://localhost"
    assert "access-control-allow-credentials" not in pre.headers
    r = client.get("/api/v1/library/artists", headers={"Origin": "http://tauri.localhost"})
    assert r.headers["access-control-allow-origin"] == "http://tauri.localhost"
    assert "content-range" in r.headers["access-control-expose-headers"].lower()
    evil = client.get("/api/v1/library/artists", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in evil.headers


def test_admin_login_as_user(cfg, scanned, conn):
    from tests.conftest import make_user

    make_user(conn, "alice", admin=True)
    make_user(conn, "bob")
    with TestClient(create_app(cfg)) as c:
        r = c.post("/admin/login", data={"username": "bob", "password": "password123"})
        assert "for admins" in r.text
        r = c.post("/admin/login", data={"username": "alice", "password": "password123"}, follow_redirects=False)
        assert r.status_code == 303 and "nm_session=" in r.headers["set-cookie"]
        page = c.get("/admin/users").text
        assert "alice" in page and "bob" in page and "Create invite" in page
        r = c.post("/admin/invites", data={"note": "for Gina", "days": "3"}, follow_redirects=False)
        code = r.headers["location"].split("invite=")[1]
        assert f"/join/{code}" in c.get(f"/admin/users?invite={code}").text
        bob_id = conn.execute("SELECT id FROM users WHERE username = 'bob'").fetchone()[0]
        c.post(f"/admin/users/{bob_id}", data={"action": "disable"})
        assert conn.execute("SELECT disabled FROM users WHERE id = ?", (bob_id,)).fetchone()[0] == 1
        alice_id = conn.execute("SELECT id FROM users WHERE username = 'alice'").fetchone()[0]
        admin_id = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]
        c.post(f"/admin/users/{admin_id}", data={"action": "remove_admin"})
        r = c.post(f"/admin/users/{alice_id}", data={"action": "remove_admin"}, follow_redirects=False)
        assert "last%20admin" in r.headers["location"]
        # The session cookie is a normal device token: the player API accepts it too.
        assert c.get("/api/v1/me").json()["username"] == "alice"
        c.post("/admin/logout")
        assert c.get("/admin", follow_redirects=False).status_code == 303
