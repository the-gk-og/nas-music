"""Admin console API (/api/v1/admin/*), in the shapes the web console's client (web/src/lib/api/admin.ts)
uses, plus bug reports."""
import json
import logging
import shutil
import uuid

import pytest
from fastapi.testclient import TestClient

from nasmusic import auth
from tests.conftest import make_flac, track_ids

API = "/api/v1"
ADMIN = f"{API}/admin"


ENDPOINTS = [("get", "/status"), ("get", "/jobs"), ("post", "/jobs/scan/run"), ("get", "/jobs/history"),
             ("get", "/library/failed"), ("post", "/library/failed/retry"), ("post", "/library/failed/x/retry"),
             ("get", "/health"), ("get", "/health/missing"), ("post", "/health/missing/fix"), ("get", "/server"),
             ("delete", "/opus-cache"), ("get", "/logs"), ("get", "/logs/stream"), ("get", "/backups"),
             ("post", "/backups"), ("get", "/backups/x/download"), ("get", "/now-playing"), ("get", "/settings"),
             ("put", "/settings"), ("get", "/reports"), ("get", "/reports/x"), ("patch", "/reports/x"),
             ("get", "/users/x/devices"), ("get", "/users/x/detail")]


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_admin_only(bob, method, path):
    kw = {"json": {}} if method in ("put", "patch") else {}
    assert getattr(bob, method)(f"{ADMIN}{path}", **kw).status_code == 403


# ---------- users ----------


def test_user_devices_detail_and_revoke(client, bob, conn):
    bob.post(f"{API}/events", json={"events": [{"id": str(uuid.uuid4()), "type": "play",
                                                 "trackId": track_ids(conn)["Song 1"], "at": "2026-11-19T01:00:00Z"}]})
    devs = client.get(f"{ADMIN}/users/{bob.user_id}/devices").json()
    assert [d["id"] for d in devs] == [bob.device_id] and devs[0]["current"] is False and "online" in devs[0]
    d = client.get(f"{ADMIN}/users/{bob.user_id}/detail").json()
    assert d["username"] == "bob" and d["eventCount"] == 1 and d["lastActivity"]
    lst = {u["username"]: u for u in client.get(f"{ADMIN}/users").json()}
    assert lst["bob"]["lastActivity"] == d["lastActivity"]
    assert client.get(f"{ADMIN}/users/nope/devices").status_code == 404
    assert client.delete(f"{ADMIN}/users/{bob.user_id}/devices/{client.device_id}").status_code == 404
    assert client.delete(f"{ADMIN}/users/{bob.user_id}/devices/{bob.device_id}").status_code == 204
    assert bob.get(f"{API}/me").status_code == 401


# ---------- status + jobs ----------


def test_status_shape(client, app):
    app.state.ctx.jobs.chain = False  # no background analysis after the scan (it would race analysis.done)
    app.state.ctx.jobs.run("scan")
    s = client.get(f"{ADMIN}/status").json()
    for k in ("version", "uptimeS", "cpuPct", "load", "memUsedBytes", "memTotalBytes", "disks", "dataBytes",
              "library", "analysis", "jobs"):
        assert k in s, k
    assert s["memTotalBytes"] > 0 and s["memUsedBytes"] > 0 and s["dataBytes"] > 0
    assert [d["name"] for d in s["disks"]] == ["Music library (read-only)", "Data volume"]
    assert all(d["usedBytes"] <= d["totalBytes"] for d in s["disks"])
    assert s["library"]["tracks"] == 5 and s["library"]["missing"] == 0 and s["library"]["lastScan"]["ok"]
    a = s["analysis"]
    assert (a["done"], a["total"], a["failed"]) == (0, 5, 0) and "etaS" in a and "modelVersion" in a
    scan = {j["name"]: j for j in s["jobs"]}["scan"]
    assert scan["state"] == "idle" and scan["ok"] is True and scan["finishedAt"] and "nextRunAt" in scan
    assert s["lyrics"]["lrclibEnabled"] is True


def test_run_job_conflict_and_history(client, app, conn):
    jobs = app.state.ctx.jobs
    jobs.run("scan", "schedule")
    jobs._locks["discovery"].acquire()  # pretend discovery is running
    try:
        r = client.post(f"{ADMIN}/jobs/discovery/run")
        assert r.status_code == 409
        st = {j["name"]: j for j in client.get(f"{ADMIN}/jobs").json()}["discovery"]
        assert st["state"] == "running" and st["running"] is True
    finally:
        jobs._locks["discovery"].release()
    r = client.post(f"{ADMIN}/jobs/cleanup/run")
    assert r.status_code == 200 and r.json()["name"] == "cleanup"
    assert client.post(f"{ADMIN}/jobs/explode/run").status_code == 422
    import time

    for _ in range(100):
        if not jobs.is_running("cleanup"):
            break
        time.sleep(0.02)
    h = client.get(f"{ADMIN}/jobs/history", params={"limit": 10}).json()
    by = {r["name"]: r for r in h}
    assert by["scan"]["trigger"] == "schedule" and by["scan"]["ok"] is True and "unchanged=5" in by["scan"]["summary"]
    assert by["cleanup"]["trigger"] == "manual" and by["cleanup"]["id"].startswith("cleanup:")
    assert h == sorted(h, key=lambda r: r["startedAt"], reverse=True)


def test_backup_job(client, app):
    rec = app.state.ctx.jobs.run("backup")
    assert rec["ok"] and rec["result"]["kind"] == "manual"
    assert any(b["id"] == rec["result"]["name"] for b in client.get(f"{ADMIN}/backups").json())


# ---------- failed files ----------


def test_failed_files_and_retry(client, conn, cfg, app, monkeypatch):
    monkeypatch.setattr(app.state.ctx.jobs, "trigger", lambda *a, **k: True)  # no real analysis in the background
    ids = list(track_ids(conn).values())
    conn.execute("INSERT INTO analysis_jobs VALUES (?, 'effnet-discogs-1', 'failed', 'decode error', 'x')", (ids[2],))
    (cfg.library_root / "broken.flac").write_bytes(b"not audio")
    app.state.ctx.jobs.run("scan")
    f = client.get(f"{ADMIN}/library/failed").json()
    stages = {x["stage"]: x for x in f}
    assert stages["analyse"]["id"] == ids[2] and stages["analyse"]["error"] == "decode error"
    assert stages["scan"]["path"] == "broken.flac" and stages["scan"]["id"].startswith("scan:")
    assert all(x["attempts"] >= 1 for x in f)
    assert client.post(f"{ADMIN}/library/failed/nope/retry").status_code == 404
    assert client.post(f"{ADMIN}/library/failed/{ids[2]}/retry").json() == {"queued": 1}
    assert [x["stage"] for x in client.get(f"{ADMIN}/library/failed").json()] == ["scan"]
    assert client.post(f"{ADMIN}/library/failed/retry").json() == {"queued": 1}


def test_analysis_eta(client, conn):
    ids = list(track_ids(conn).values())
    for tid in ids[:2]:
        conn.execute("INSERT INTO features (track_id, model_version, embedding, dims, analysed_at) "
                     "VALUES (?, 'basic-1', x'00000000', 1, '2099-01-01T00:00:00Z')", (tid,))
    a = client.get(f"{ADMIN}/status").json()["analysis"]
    assert (a["modelVersion"], a["done"], a["ratePerHour"]) == ("basic-1", 2, 2.0)
    assert a["etaS"] == 3 / 2 * 3600


# ---------- health ----------


def test_health_checks_list(client, conn, cfg, app, base_flac):
    make_flac(base_flac, cfg.library_root / "Dupes" / "a.flac", title="Song 1", artist="Artist One", album="Other")
    app.state.ctx.jobs.chain = False  # no background analysis after the scan (it would race the unanalysed count)
    app.state.ctx.jobs.run("scan")
    checks = {c["id"]: c for c in client.get(f"{ADMIN}/health").json()}
    assert list(checks) == ["missing", "duplicates", "missing_tags", "missing_art", "unanalysed", "orphan_cache",
                            "old_backups"]
    assert checks["duplicates"]["count"] == 1 and checks["duplicates"]["fix"] is None
    assert checks["missing"]["count"] == 0 and checks["missing"]["fix"] is None  # nothing to fix
    assert checks["unanalysed"]["count"] == 6 and checks["unanalysed"]["fix"]["destructive"] is False
    assert checks["missing_art"]["count"] == 2 and all(c["title"] and c["description"] for c in checks.values())
    dup = client.get(f"{ADMIN}/health/duplicates").json()[0]
    assert dup["detail"] == "2 copies" and "Dupes/a.flac" in dup["group"] and len(dup["trackIds"]) == 2
    tags = {i["path"]: i for i in client.get(f"{ADMIN}/health/missing_tags").json()}
    assert tags["Dupes/a.flac"]["detail"] == "no year / genre / trackNo"
    assert {i["title"] for i in client.get(f"{ADMIN}/health/missing_art").json()} == {"Loose", "Other"}
    assert len(client.get(f"{ADMIN}/health/unanalysed", params={"limit": 2}).json()) == 2
    assert client.get(f"{ADMIN}/health/bogus").status_code == 404
    assert client.post(f"{ADMIN}/health/duplicates/fix", json={}).status_code == 400  # library is read-only
    assert client.post(f"{ADMIN}/health/bogus/fix", json={}).status_code == 404
    r = client.post(f"{ADMIN}/health/unanalysed/fix", json={}).json()
    assert r["fixed"] == 6


def test_fix_missing_removes_from_db_with_backup(client, conn, cfg, app):
    ids = track_ids(conn)
    gone = ids["Song 2"]
    client.put(f"{API}/favourites/{gone}")
    pl = client.post(f"{API}/playlists", json={"name": "P", "trackIds": [ids["Song 1"], gone, ids["Opening"]]}).json()
    q = client.post(f"{API}/queues", json={"name": "Q", "trackIds": [ids["Song 1"], gone, ids["Opening"]],
                                           "startIndex": 2}).json()
    conn.execute("INSERT INTO mixes (id, user_id, kind, title, for_date, created_at) VALUES ('m', ?, 'daily', 'D', "
                 "'d', 'x')", (client.user_id,))
    conn.execute("INSERT INTO mix_tracks VALUES ('m', 0, ?, 1, 'a'), ('m', 1, ?, 1, 'b')", (gone, ids["Closing"]))
    shutil.move(cfg.library_root / "Artist One/First Album/02 Song 2.flac", cfg.library_root.parent / "x.flac")
    app.state.ctx.jobs.run("scan")
    items = client.get(f"{ADMIN}/health/missing").json()
    assert [i["id"] for i in items] == [gone] and items[0]["path"].endswith("02 Song 2.flac")
    checks = {c["id"]: c for c in client.get(f"{ADMIN}/health").json()}
    assert checks["missing"]["count"] == 1 and checks["missing"]["fix"]["destructive"] is True
    r = client.post(f"{ADMIN}/health/missing/fix", json={"ids": [gone]}).json()
    assert r["fixed"] == 1 and r["backup"].startswith("nasmusic-prune-")
    assert conn.execute("SELECT COUNT(*) FROM tracks WHERE id = ?", (gone,)).fetchone()[0] == 0
    assert [t["id"] for t in client.get(f"{API}/playlists/{pl['id']}").json()["tracks"]] == [ids["Song 1"],
                                                                                           ids["Opening"]]
    qq = client.get(f"{API}/queues/{q['id']}").json()
    assert qq["tracks"][qq["currentIndex"]]["id"] == ids["Opening"]
    assert [t["track"]["id"] for t in client.get(f"{API}/mixes/m").json()["tracks"]] == [ids["Closing"]]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert client.post(f"{ADMIN}/health/missing/fix", json={}).json()["fixed"] == 0


def test_orphan_cache_and_old_backups(client, conn, cfg):
    tid = track_ids(conn)["Song 1"]
    client.get(f"{API}/stream/{tid}?bitrate=64")
    opus = cfg.data_dir / "opus"
    (opus / f"{'f' * 40}_64_123.opus").write_bytes(b"x" * 10)
    (cfg.data_dir / "art" / "512").mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / "art" / "512" / "deadbeef.jpg").write_bytes(b"y" * 5)
    c = {x["id"]: x for x in client.get(f"{ADMIN}/health").json()}["orphan_cache"]
    assert (c["count"], c["bytes"]) == (2, 15)
    items = client.get(f"{ADMIN}/health/orphan_cache").json()
    first = next(i for i in items if i["detail"] == "opus")
    assert client.post(f"{ADMIN}/health/orphan_cache/fix", json={"ids": [first["id"]]}).json() == {
        "fixed": 1, "freedBytes": 10}
    assert client.post(f"{ADMIN}/health/orphan_cache/fix", json={}).json()["fixed"] == 1
    assert len(list(opus.glob("*.opus"))) == 1  # the real transcode survived
    assert client.delete(f"{ADMIN}/opus-cache").json()["files"] == 1
    for _ in range(7):
        client.post(f"{ADMIN}/backups", json={})
    old = {x["id"]: x for x in client.get(f"{ADMIN}/health").json()}["old_backups"]
    assert old["count"] == 2 and old["bytes"] > 0
    r = client.post(f"{ADMIN}/health/old_backups/fix", json={}).json()
    assert r["fixed"] == 2 and r["freedBytes"] > 0
    assert len(client.get(f"{ADMIN}/backups").json()) == 5


# ---------- server, logs ----------


def test_server_health(client):
    s = client.get(f"{ADMIN}/server").json()
    assert s["version"] and s["uptimeS"] >= 0 and s["cpu"]["cores"] >= 1
    assert s["disks"]["data"]["totalBytes"] > 0 and s["dataDirBytes"] > 0 and s["databaseBytes"] > 0
    assert s["opusCache"]["limitBytes"] == 1024**3


def test_logs_poll_filter_and_sse(client, app):
    log = logging.getLogger("nasmusic.test")
    log.setLevel(logging.INFO)
    log.warning("visible line /api/v1/stream/x?token=SECRET123 and Bearer abc.def")
    log.warning("other line")
    recs = client.get(f"{ADMIN}/logs", params={"level": "WARNING", "q": "VISIBLE"}).json()
    assert [r["message"][:12] for r in recs if r["logger"] == "nasmusic.test"] == ["visible line"]
    assert "SECRET123" not in recs[-1]["message"] and "abc.def" not in recs[-1]["message"]
    after = recs[-1]["seq"]
    assert all(r["seq"] > after for r in client.get(f"{ADMIN}/logs", params={"after": after, "level": "DEBUG"}).json())
    assert client.get(f"{ADMIN}/logs", params={"level": "CRITICAL"}).status_code == 200
    with client.stream("GET", f"{ADMIN}/logs/stream", params={"after": after - 1, "level": "WARNING", "maxS": 0.3}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert any("visible line" in e["message"] for e in events)
    with TestClient(app) as anon:  # EventSource can't send headers: ?token= works there (only)
        assert anon.get(f"{ADMIN}/logs/stream", params={"token": client.token, "maxS": 0.1}).status_code == 200
        assert anon.get(f"{ADMIN}/logs", params={"token": client.token}).status_code == 401


# ---------- backups ----------


def test_backups(client, cfg):
    b = client.post(f"{ADMIN}/backups", json={"note": "before v4"}).json()
    assert b["kind"] == "manual" and b["bytes"] > 0 and b["id"] == b["name"] and b["note"] == "before v4"
    listed = {x["id"]: x for x in client.get(f"{ADMIN}/backups").json()}
    assert listed[b["id"]]["note"] == "before v4"
    r = client.get(f"{ADMIN}/backups/{b['id']}/download")
    assert r.status_code == 200 and r.content[:16] == b"SQLite format 3\x00"
    assert "attachment" in r.headers["content-disposition"]
    for bad in ("nasmusic.db", "..%2Fconfig.toml", "config.toml", "nasmusic-manual-1.db"):
        assert client.get(f"{ADMIN}/backups/{bad}/download").status_code == 404
    assert client.delete(f"{ADMIN}/backups/{b['id']}").status_code == 204
    assert client.delete(f"{ADMIN}/backups/{b['id']}").status_code == 404
    assert (cfg.data_dir / "nasmusic.db").exists()


# ---------- now playing ----------


def test_now_playing_rows(client, conn, app, bob):
    ids = track_ids(conn)
    did, tok = auth.create_device(conn, bob.user_id, "Bob's PC", "desktop")
    with client.websocket_connect(f"{API}/connect", headers={"Authorization": f"Bearer {tok}"}) as ws:
        ws.send_json({"type": "hello", "deviceName": "Bob's PC", "kind": "desktop", "canPlay": True})
        while ws.receive_json()["type"] != "welcome":
            pass
        ws.send_json({"type": "transfer", "play": True})
        while ws.receive_json()["type"] != "takeover":
            pass
        ws.send_json({"type": "state", "state": {"trackId": ids["Closing"], "playing": True, "positionMs": 5000}})
        while ws.receive_json()["type"] != "state":
            pass
        rows = client.get(f"{ADMIN}/now-playing").json()
        assert len(rows) == 1
        r = rows[0]
        assert (r["userId"], r["username"], r["deviceId"], r["deviceName"], r["kind"]) == (
            bob.user_id, "bob", did, "Bob's PC", "desktop")
        assert r["playing"] is True and r["positionMs"] >= 5000 and r["track"]["id"] == ids["Closing"]


# ---------- server settings ----------


def test_server_settings(client, app):
    s = client.get(f"{ADMIN}/settings").json()
    assert {k: s[k] for k in ("lanUrl", "tunnelUrl", "timezone", "opusCacheMaxGb", "lrclib")} == {
        "lanUrl": "https://192.168.1.50:8443", "tunnelUrl": "https://music.example.com", "timezone": "Europe/London",
        "opusCacheMaxGb": 1, "lrclib": True}
    assert s["overrides"] == {}
    r = client.put(f"{ADMIN}/settings", json={"lanUrl": "https://192.168.68.2:9443", "tunnelUrl":
                   "https://music.example.org:8443", "timezone": "Australia/Sydney", "opusCacheMaxGb": 5,
                   "lrclib": False, "serverName": "Den"}).json()
    assert r["restartRequired"] == [] and r["values"]["tunnelUrl"] == "https://music.example.org:8443"
    assert r["values"]["opusCacheMaxGb"] == 5.0 and r["fromConfig"]["timezone"] == "Europe/London"
    info = client.get(f"{API}/server-info").json()
    assert (info["lanUrl"], info["tunnelUrl"], info["name"]) == ("https://192.168.68.2:9443",
                                                                 "https://music.example.org:8443", "Den")
    assert app.state.ctx.opus.max_bytes == 5 * 1024**3 and app.state.ctx.jobs.cfg.timezone == "Australia/Sydney"
    assert client.get(f"{ADMIN}/status").json()["lyrics"]["lrclibEnabled"] is False
    assert client.post(f"{ADMIN}/invites", json={}).json()["url"].startswith("https://music.example.org:8443/join/")
    for bad in ({"lanUrl": "ftp://x"}, {"lanUrl": "https://host:99999"}, {"lanUrl": "https://h:1/path"},
                {"tunnelUrl": "music.example.org"}, {"timezone": "Mars/Base"}, {"opusCacheMaxGb": 0.5},
                {"opusCacheMaxGb": 5000}, {"lrclib": "yes"}, {"serverName": ""}, {"nope": 1},
                {"lanUrl": "https://bad host"}):
        assert client.put(f"{ADMIN}/settings", json=bad).status_code == 422, bad
    assert client.put(f"{ADMIN}/settings", json={"tunnelUrl": ""}).json()["values"]["tunnelUrl"] is None
    r = client.put(f"{ADMIN}/settings", json={"lanUrl": None, "tunnelUrl": None, "timezone": None}).json()
    assert r["values"]["lanUrl"] == "https://192.168.1.50:8443" and r["values"]["tunnelUrl"] == "https://music.example.com"
    assert set(r["overrides"]) == {"opusCacheMaxGb", "lrclib", "serverName"}


def test_settings_survive_restart(cfg, scanned, conn):
    from nasmusic.app import create_app
    from nasmusic.app.scheduler import Jobs
    from tests.conftest import offline, user_client

    app = offline(create_app(cfg, jobs=Jobs(cfg)))
    with user_client(app, conn, "alice") as c:
        c.put(f"{ADMIN}/settings", json={"lrclib": False, "timezone": "Asia/Tokyo"})
    app2 = create_app(cfg, jobs=Jobs(cfg))
    assert app2.state.ctx.cfg.lrclib_enabled is False and app2.state.ctx.jobs.cfg.timezone == "Asia/Tokyo"
    assert app2.state.ctx.base_cfg.lrclib_enabled is True


def test_timezone_reschedules_running_scheduler(app, conn):
    from tests.conftest import user_client

    jobs = app.state.ctx.jobs
    jobs.start(initial_scan=False)
    try:
        with user_client(app, conn, "alice") as c:
            c.put(f"{ADMIN}/settings", json={"timezone": "Asia/Tokyo"})
            nxt = {j["name"]: j for j in c.get(f"{ADMIN}/jobs").json()}["nightly"]["nextRunAt"]
            assert nxt.endswith("Z")
        assert str(jobs.scheduler.get_job("nightly").trigger.timezone) == "Asia/Tokyo"
    finally:
        jobs.shutdown()


# ---------- bug reports ----------


def test_bug_reports(client, bob, conn):
    r = bob.post(f"{API}/me/bug-report", json={"description": "For You crashes when tapping a mix\nSteps: …",
                                               "appVersion": "3.1", "platform": "Android 15 · Pixel 8",
                                               "logs": "line1\nline2", "crash": "java.lang.IllegalStateException",
                                               "extra": {"model": "Pixel 8"}})
    assert r.status_code == 200
    rid = r.json()["id"]
    lst = client.get(f"{ADMIN}/reports").json()
    x = lst[0]
    assert [y["id"] for y in lst] == [rid]
    assert (x["title"], x["status"], x["username"], x["kind"], x["appVersion"]) == (
        "For You crashes when tapping a mix", "open", "bob", "android", "3.1")
    assert "logs" not in x and x["deviceId"] == bob.device_id and x["hasCrash"]
    full = client.get(f"{ADMIN}/reports/{rid}").json()
    assert full["logs"] == "line1\nline2" and full["context"] == {"model": "Pixel 8"}
    assert full["description"].startswith("For You crashes") and full["crash"].startswith("java")
    p = client.patch(f"{ADMIN}/reports/{rid}", json={"status": "closed"}).json()
    assert p["status"] == "closed" and "logs" not in p
    assert client.patch(f"{ADMIN}/reports/{rid}", json={"status": "wontfix"}).status_code == 422
    assert client.patch(f"{ADMIN}/reports/nope", json={"status": "open"}).status_code == 404
    assert client.delete(f"{ADMIN}/reports/{rid}").status_code == 204
    assert client.get(f"{ADMIN}/reports/{rid}").status_code == 404


def test_bug_report_title_fallbacks(client):
    client.post(f"{API}/me/bug-report", json={"logs": "x", "crash": "\n  NullPointerException at Foo\n"})
    client.post(f"{API}/me/bug-report", json={"logs": "x", "description": "y" * 120})
    client.post(f"{API}/me/bug-report", json={"logs": "x"})
    titles = sorted(r["title"] for r in client.get(f"{ADMIN}/reports").json())
    assert "NullPointerException at Foo" in titles and any(len(t) == 80 and t.endswith("…") for t in titles)
    assert any(t.startswith("Report from") for t in titles)


def test_bug_report_limits(client):
    assert client.post(f"{API}/me/bug-report", json={"logs": "x" * (2 * 1024 * 1024 + 10)}).status_code == 413
    assert client.post(f"{API}/me/bug-report", content=b"{not json",
                       headers={"content-type": "application/json"}).status_code == 422
    assert client.post(f"{API}/me/bug-report", json={"description": "no logs"}).status_code == 422
    for _ in range(20):
        assert client.post(f"{API}/me/bug-report", json={"logs": "l"}).status_code == 200
    assert client.post(f"{API}/me/bug-report", json={"logs": "l"}).status_code == 429


def test_bug_reports_expire(client, conn, app):
    rid = client.post(f"{API}/me/bug-report", json={"logs": "old"}).json()["id"]
    conn.execute("UPDATE bug_reports SET created_at = '2000-01-01T00:00:00Z' WHERE id = ?", (rid,))
    assert client.get(f"{ADMIN}/reports").json() == []
    assert app.state.ctx.jobs.run("cleanup")["result"] == {"bugReportsDeleted": 1}


def test_bug_report_as_the_android_app_sends_it(client, bob):
    """The exact shape android/.../diag/BugReport.kt sends (≤ 1.5 MB after its own trimming), listed for admins."""
    logs = "\n".join(f'11-24 10:00:00.000 1234 1234 I nas-music: "q" line {i} ' + "é" * 300 for i in range(2000))
    crash = "===== crash-1764000000000.txt =====\ncrash at 2026-11-24T10:00:00Z\napp 0.7.0 (70) · Google Pixel 8\n" \
            "thread main · screen now\njava.lang.IllegalStateException: boom\n\tat x.y(Z.kt:1)"
    body = {"appVersion": "0.7.0 (70)", "platform": "Android 15 · Google Pixel 8", "logs": logs, "crash": crash,
            "extra": {"screen": "now", "device": "Google Pixel 8", "sdk": "35", "flavor": "foss", "route": "LAN",
                      "playing": "true"}}
    raw = json.dumps(body, ensure_ascii=False).encode()
    assert 1_000_000 < len(raw) < 1_500_000
    r = bob.post(f"{API}/me/bug-report", content=raw, headers={"content-type": "application/json; charset=UTF-8"})
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    x = next(y for y in client.get(f"{ADMIN}/reports").json() if y["id"] == rid)
    assert x["title"] == "===== crash-1764000000000.txt =====" and x["hasCrash"] and x["platform"] == body["platform"]
    full = client.get(f"{ADMIN}/reports/{rid}").json()
    assert full["logs"] == logs and full["context"]["route"] == "LAN"
    # The app before this fix sent `log` (no `logs`): the server says why, and the app now shows that message.
    old = bob.post(f"{API}/me/bug-report", json={"log": "x", "description": "d"})
    assert old.status_code == 422 and old.json()["detail"] == "invalid bug report"
