import base64
import io
import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from nasmusic import auth
from nasmusic.app import create_app
from nasmusic.app.scheduler import Jobs
from nasmusic.core import get_setting


def _admin_id(conn):
    return conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()[0]


@pytest.fixture
def web(cfg, scanned):
    cfg = replace(cfg, admin_password_hash=auth.hash_password("hunter22"))
    app = create_app(cfg, jobs=Jobs(cfg))
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def login(c, pw="hunter22"):
    return c.post("/admin/login", data={"password": pw}, follow_redirects=False)


def test_login_required_and_login(web):
    r = web.get("/admin", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin/login"
    assert web.get("/admin/pair", follow_redirects=False).status_code == 303
    r = login(web, "wrong")
    assert r.status_code == 200 and "Wrong username or password" in r.text
    r = login(web)
    assert r.status_code == 303
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Secure" in cookie
    r = web.get("/admin")
    assert r.status_code == 200 and "Dashboard" in r.text and ">5</b><span>tracks" in r.text
    assert r.headers["x-frame-options"] == "DENY"
    web.post("/admin/logout")
    web.cookies.clear()
    assert web.get("/admin", follow_redirects=False).status_code == 303


def test_forged_cookie_rejected(web):
    web.cookies.set("nm_session", "forged-token")
    assert web.get("/admin", follow_redirects=False).status_code == 303


def test_login_rate_limited(web):
    for _ in range(5):
        login(web, "nope")
    r = login(web)  # even the right password is refused for a while
    assert "Too many attempts" in r.text


def test_no_password_configured(anon):
    r = anon.get("/admin/login")
    assert "No admin password is set" in r.text
    assert anon.post("/admin/login", data={"password": "x"}).status_code == 200
    assert anon.get("/admin", follow_redirects=False).status_code == 303


def test_pair_page_qr_payload(web, conn):
    login(web)
    r = web.get("/admin/pair")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    code = r.text.split('class="code mono">')[1].split("<")[0]
    assert len(code) == 8
    # Decode the QR image and check the payload matches docs/pairing.md.
    png = base64.b64decode(r.text.split("data:image/png;base64,")[1].split('"')[0])
    from PIL import Image

    Image.open(io.BytesIO(png)).verify()
    from nasmusic.auth import qr

    ctx = web.app.state.ctx
    p = qr.payload(ctx.cfg, ctx.server_id, ctx.cert_sha256, code)
    assert set(p) == {"v", "name", "serverId", "lanUrl", "tunnelUrl", "certSha256", "pairCode",
                      "cfAccessClientId", "cfAccessClientSecret"}
    assert p["lanUrl"] == "https://192.168.1.50:8443" and p["cfAccessClientSecret"] == "csecret"
    assert json.loads(qr.payload_json(p)) == p
    # The code shown actually pairs.
    assert web.post("/api/v1/pair", json={"pairCode": code, "deviceName": "Phone"}).status_code == 200


def test_devices_revoke_from_dashboard(web, conn):
    login(web)
    code, _ = auth.create_pair_code(conn, _admin_id(conn))
    token = web.post("/api/v1/pair", json={"pairCode": code, "deviceName": "My Pixel"}).json()
    assert "My Pixel" in web.get("/admin").text
    r = web.post(f"/admin/devices/{token['deviceId']}/revoke", follow_redirects=False)
    assert r.status_code == 303
    h = {"Authorization": f"Bearer {token['deviceToken']}"}
    assert web.get("/api/v1/favourites", headers=h).status_code == 401
    assert "revoked" in web.get("/admin").text


def test_suggestions_page(web, conn):
    conn.execute("INSERT INTO suggestions (id, user_id, artist, album, reason, created_at) VALUES "
                 "('s1', (SELECT id FROM users WHERE username = 'admin'), 'Artist <b>', 'Album', 'Because', '2026-11-15T03:00:00Z')")
    login(web)
    r = web.get("/admin/suggestions")
    assert "Artist &lt;b&gt;" in r.text  # escaped
    r = web.post("/admin/suggestions/s1", data={"status": "not_interested"}, follow_redirects=False)
    assert r.status_code == 303
    assert conn.execute("SELECT status FROM suggestions").fetchone()[0] == "not_interested"


def test_settings_and_jobs(web, conn):
    login(web)
    r = web.get("/admin/settings")
    assert r.status_code == 200 and "csecret" not in r.text and "Europe/London" in r.text
    jobs: Jobs = web.app.state.ctx.jobs
    jobs.chain = False  # no background analysis after the scan: nightly must see nothing analysed
    rec = jobs.run("scan")
    assert rec["ok"] and rec["result"]["unchanged"] == 5
    assert get_setting(conn, "job.scan")["ok"] is True
    rec = jobs.run("nightly")
    assert rec["ok"] and all(ids == [] for ids in rec["result"]["mixIds"].values())  # {user or "*": [ids]}
    assert "Nightly mixes" in web.get("/admin").text
    assert web.post("/admin/jobs/bogus", follow_redirects=False).status_code == 404


def test_job_failure_recorded(cfg, conn, monkeypatch):
    from nasmusic.ai import api as ai

    def boom(*a, **k):
        raise RuntimeError("model missing")

    monkeypatch.setattr(ai, "run_weekly_discovery", boom)
    rec = Jobs(cfg).run("discovery")
    assert rec["ok"] is False and "model missing" in rec["error"]
    assert get_setting(conn, "job.discovery")["error"] == rec["error"]


def test_scheduler_jobs_registered(cfg):
    jobs = Jobs(cfg)
    sched = jobs.start(initial_scan=False)
    try:
        ids = {j.id: j for j in sched.get_jobs()}
        assert set(ids) == {"scan", "nightly", "discovery", "release_radar", "cleanup", "autotag",
                            "smart", "artist_info", "podcasts", "backup"}
        assert "hour='4'" in str(ids["backup"].trigger) and "minute='15'" in str(ids["backup"].trigger)
        assert "day_of_week='fri'" in str(ids["release_radar"].trigger)
        assert str(ids["nightly"].trigger).startswith("cron[") and "hour='2'" in str(ids["nightly"].trigger)
        assert "day_of_week='sun'" in str(ids["discovery"].trigger)
    finally:
        jobs.shutdown()


def test_progress_prefers_ai_status_key(conn):
    import json

    from nasmusic.web import _analysis_progress

    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('ai.status.model_version', ?)",
        (json.dumps({"requested": "effnet-discogs-1", "analysing": "basic-1",
                     "fallback_reason": "Essentia is not importable"}),),
    )
    p = _analysis_progress(conn, "effnet-discogs-1")
    assert p["model"] == "basic-1"
    assert p["fallback_reason"] == "Essentia is not importable"
