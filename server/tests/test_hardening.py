"""Security hardening: CSRF origin check for cookie sessions, request body cap, SSRF on redirects/DNS,
family PIN lockout, cross-site WebSocket refusal."""
from __future__ import annotations

import socket

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from homebeat import auth
from homebeat.app import MAX_BODY_BYTES
from homebeat.app.context import origin_allowed
from homebeat.spoken import net
from tests.conftest import make_user, user_client

API = "/api/v1"


def _cookie_client(app, conn, username="carol", admin=False):
    from fastapi.testclient import TestClient

    u = make_user(conn, username, admin)
    _, token = auth.create_device(conn, u.id, "browser", "web")
    c = TestClient(app, base_url="https://nas.local:8443")
    c.cookies.set("nm_session", token)
    return c


def test_origin_allowed_rules(cfg):
    h = {"host": "nas.local:8443"}
    assert origin_allowed(h, cfg)                                              # no Origin: not a browser CSRF
    assert origin_allowed({**h, "origin": "https://nas.local:8443"}, cfg)
    assert not origin_allowed({**h, "origin": "https://evil.example"}, cfg)
    assert not origin_allowed({**h, "origin": "null"}, cfg)
    assert origin_allowed({"host": "music.example.org", "origin": "https://music.example.org"}, cfg)  # :443
    assert origin_allowed({"host": "internal:8080", "x-forwarded-host": "music.example.org",
                           "origin": "https://music.example.org"}, cfg)
    cfg.tunnel_url = "https://music.example.org"
    assert origin_allowed({"host": "homebeat:8080", "origin": "https://music.example.org"}, cfg)


def test_cookie_session_csrf_refused_from_other_sites(app, conn):
    with _cookie_client(app, conn) as c:
        assert c.get(f"{API}/me").status_code == 200
        # a page on another site posting with the victim's cookie
        r = c.post(f"{API}/me/pair-code", headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        r = c.post(f"{API}/auth/logout", headers={"Origin": "https://evil.example"})
        assert r.status_code == 403
        # the player's own pages, and clients that send no Origin, still work
        assert c.post(f"{API}/me/pair-code", headers={"Origin": "https://nas.local:8443"}).status_code == 200
        assert c.post(f"{API}/me/pair-code").status_code == 200
        # reads are never blocked (SameSite already keeps the cookie off cross-site subrequests)
        assert c.get(f"{API}/me", headers={"Origin": "https://evil.example"}).status_code == 200


def test_bearer_requests_skip_the_origin_check(app, conn):
    with user_client(app, conn, "dave", admin=False) as c:
        c.cookies.set("nm_session", "stale")
        r = c.post(f"{API}/me/pair-code", headers={"Origin": "tauri://localhost"})
        assert r.status_code == 200


def test_admin_form_posts_need_same_origin(app, conn):
    with _cookie_client(app, conn, "erin", admin=True) as c:
        r = c.post("/admin/invites", data={"note": "x"}, headers={"Origin": "https://evil.example"},
                   follow_redirects=False)
        assert r.status_code == 403


def test_oversized_bodies_refused_before_parsing(anon):
    r = anon.post(f"{API}/auth/login", content=b"x" * 16,
                  headers={"Content-Length": str(MAX_BODY_BYTES + 1), "Content-Type": "application/json"})
    assert r.status_code == 413

    def chunks():  # no Content-Length: counted as it streams
        for _ in range(MAX_BODY_BYTES // (1024 * 1024) + 2):
            yield b" " * (1024 * 1024)

    r = anon.post(f"{API}/auth/login", content=chunks(), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_family_pin_lockout_is_fifteen_minutes(app, conn, client, scanned):
    kid = user_client(app, conn, "kid2", admin=False)
    with kid:
        client.put(f"{API}/admin/users/{kid.user_id}/family", json={"locked": True, "pin": "1357"})
        codes = [kid.put(f"{API}/me/family", json={"enabled": False, "pin": "0000"}).status_code for _ in range(6)]
        assert codes[:5] == [403] * 5 and codes[5] == 429
        assert app.state.ctx.pin_limiter.window == 900
        # the login limiter is separate: a PIN lockout doesn't lock the household out of signing in
        assert not app.state.ctx.login_limiter.blocked("testclient")


def test_redirect_to_local_address_is_refused():
    def handler(request):
        if request.url.host == "feeds.example.com":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8080/api/v1/admin/backups"})
        return httpx.Response(200, text="secret")

    with net.client(httpx.MockTransport(handler)) as c:
        with pytest.raises(httpx.ConnectError):
            c.get("https://feeds.example.com/feed.xml")
        with pytest.raises(httpx.ConnectError):
            c.get("http://[::ffff:127.0.0.1]/")


@pytest.mark.anyio
async def test_async_redirect_to_local_address_is_refused():
    def handler(request):
        return httpx.Response(302, headers={"Location": "http://localhost/"})

    async with net.async_client(httpx.MockTransport(handler)) as c:
        with pytest.raises(httpx.ConnectError):
            await c.get("https://cdn.example.com/ep.mp3")


def test_dns_answers_pointing_home_are_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    req = httpx.Request("GET", "https://rebind.example.com/")
    with pytest.raises(httpx.ConnectError):
        net._check_request(req, resolve=True)
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))])
    net._check_request(req, resolve=True)


def test_connect_socket_ignores_cookie_from_other_sites(app, conn):
    with _cookie_client(app, conn, "frank") as c:
        with pytest.raises(WebSocketDisconnect) as e:
            with c.websocket_connect(f"{API}/connect", headers={"Origin": "https://evil.example"}) as ws:
                ws.receive_json()
        assert e.value.code == 4401


# ---------- follow-ups: password change, idle expiry, home-network fetches, http_bind_host ----------


def test_password_change_signs_out_other_devices(app, conn):
    make_user(conn, "gina", password="password123")
    a = user_client(app, conn, "gina", admin=False)
    b = user_client(app, conn, "gina", admin=False)
    with a, b:
        assert b.get(f"{API}/me").status_code == 200
        r = a.put(f"{API}/me/password", json={"current": "password123", "new": "newpassword456"})
        assert r.status_code == 204
        assert a.get(f"{API}/me").status_code == 200
        assert b.get(f"{API}/me").status_code == 401


def test_idle_device_login_expires(conn):
    from datetime import UTC, datetime, timedelta

    from homebeat.core import iso

    u = make_user(conn, "hank")
    dev, tok = auth.create_device(conn, u.id, "old phone", "android")
    assert auth.device_for_token(conn, tok) is not None
    old = iso(datetime.now(UTC) - timedelta(days=400))
    conn.execute("UPDATE devices SET last_seen = ?, created_at = ? WHERE id = ?", (old, old, dev))
    assert auth.device_for_token(conn, tok) is None
    assert conn.execute("SELECT revoked FROM devices WHERE id = ?", (dev,)).fetchone()[0] == 1


def test_home_network_urls_need_an_admin():
    for url in ("http://192.168.1.1/", "http://10.0.0.5/feed", "http://nas.local/x", "http://router/",
                "http://100.101.102.103/"):
        with pytest.raises(net.BadUrl):
            net.check_url(url)
        with net.local_allowed():
            assert net.check_url(url) == url
    with net.local_allowed(), pytest.raises(net.BadUrl):
        net.check_url("http://127.0.0.1/")          # never, even for admins
    assert net.check_url("https://feeds.example.com/x") == "https://feeds.example.com/x"


def test_station_on_home_network_admin_only(app, conn, client, bob):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"\xff\xfb" * 100)

    app.state.ctx.http_transport = httpx.MockTransport(handler)
    body = {"streamUrl": "http://192.168.1.20:8000/live", "name": "Kitchen", "probe": False}
    assert bob.post(f"{API}/stations", json=body).status_code == 422
    r = client.post(f"{API}/stations", json=body)
    assert r.status_code == 201, r.text
    # once an admin added it, anyone may favourite it
    assert bob.post(f"{API}/stations", json=body).status_code == 201


def test_http_bind_host_setting(tmp_path):
    from homebeat import config as config_mod

    p = tmp_path / "c.toml"
    p.write_text(f'library_root = "{tmp_path}"\ndata_dir = "{tmp_path / "d"}"\nhttp_bind_host = "127.0.0.1"\n')
    assert config_mod.load(p).http_bind_host == "127.0.0.1"
