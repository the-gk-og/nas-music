"""Admin console API (/api/v1/admin/*) and bug reports."""
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


ENDPOINTS = [("get", "/status"), ("get", "/jobs"), ("post", "/jobs/scan"), ("get", "/analysis/failed"),
             ("post", "/analysis/retry"), ("get", "/health/library"), ("get", "/health/missing"),
             ("delete", "/health/missing"), ("get", "/health/duplicates"), ("get", "/health/missing-tags"),
             ("get", "/health/missing-art"), ("get", "/health/unanalysed"), ("get", "/health/cache"),
             ("post", "/health/cache/clean"), ("get", "/server"), ("delete", "/opus-cache"), ("get", "/logs"),
             ("get", "/backups"), ("post", "/backups"), ("get", "/now-playing"), ("get", "/settings"),
             ("put", "/settings"), ("get", "/bug-reports"), ("get", "/users/x/detail")]


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_admin_only(bob, method, path):
    kw = {"json": {}} if method == "put" else {}
    assert getattr(bob, method)(f"{ADMIN}{path}", **kw).status_code == 403


def test_user_detail_and_device_revoke(client, bob, conn):
    bob.post(f"{API}/events", json={"events": [{"id": str(uuid.uuid4()), "type": "play",
                                                 "trackId": track_ids(conn)["Song 1"], "at": "2026-11-19T01:00:00Z"}]})
    d = client.get(f"{ADMIN}/users/{bob.user_id}/detail").json()
    assert d["username"] == "bob" and d["eventCount"] == 1 and d["lastActivity"]
    assert [x["id"] for x in d["devices"]] == [bob.device_id] and d["devices"][0]["current"] is False
    lst = {u["username"]: u for u in client.get(f"{ADMIN}/users").json()}
    assert lst["bob"]["lastActivity"] == d["lastActivity"]
    assert client.delete(f"{ADMIN}/users/{bob.user_id}/devices/{client.device_id}").status_code == 404  # not bob's
    assert client.delete(f"{ADMIN}/users/{bob.user_id}/devices/{bob.device_id}").status_code == 204
    assert bob.get(f"{API}/me").status_code == 401
    assert client.get(f"{ADMIN}/users/nope/detail").status_code == 404


def test_status_jobs_and_history(client, app, conn):
    jobs = app.state.ctx.jobs
    jobs.run("scan")
    jobs.run("scan")
    s = client.get(f"{ADMIN}/status").json()
    assert s["library"]["tracks"] == 5 and s["library"]["lastScan"]["ok"] is True
    a = s["analysis"]
    assert a["total"] == 5 and a["analysed"] == 0 and a["running"] in (True, False)
    assert s["lyrics"] == {"lrclibEnabled": True, "cached": 0, "found": 0, "notFound": 0}
    j = {x["name"]: x for x in client.get(f"{ADMIN}/jobs").json()}
    assert set(j) >= {"scan", "analyse", "nightly", "lyrics", "cleanup", "pretranscode", "discovery"}
    assert len(j["scan"]["history"]) == 2 and j["scan"]["last"]["ok"]
    r = client.post(f"{ADMIN}/jobs/cleanup")
    assert r.status_code == 202 and "started" in r.json()
    assert client.post(f"{ADMIN}/jobs/explode").status_code == 422


def test_analysis_progress_eta_and_retry(client, conn):
    ids = list(track_ids(conn).values())
    now = "2099-01-01T00:00:00Z"  # "within the last hour" relative to any test clock
    for tid in ids[:2]:
        conn.execute("INSERT INTO features (track_id, model_version, embedding, dims, analysed_at) "
                     "VALUES (?, 'basic-1', x'00000000', 1, ?)", (tid, now))
    conn.execute("INSERT INTO analysis_jobs VALUES (?, 'basic-1', 'failed', 'decode error', 'x')", (ids[2],))
    a = client.get(f"{ADMIN}/status").json()["analysis"]
    assert (a["model"], a["analysed"], a["failed"], a["ratePerHour"]) == ("basic-1", 2, 1, 2.0)
    assert a["etaS"] == 2 / 2 * 3600  # 2 remaining (5 - 2 analysed - 1 failed) at 2/hour
    f = client.get(f"{ADMIN}/analysis/failed").json()
    assert [x["track"]["id"] for x in f] == [ids[2]] and f[0]["error"] == "decode error"
    assert client.post(f"{ADMIN}/analysis/retry", json={"trackIds": [ids[2]]}).json() == {"retried": 1}
    assert client.get(f"{ADMIN}/analysis/failed").json() == []
    assert len(client.get(f"{ADMIN}/health/unanalysed").json()) == 3


def test_missing_tracks_removed_from_db_with_backup(client, bob, conn, cfg, app):
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
    assert [t["id"] for t in client.get(f"{ADMIN}/health/missing").json()] == [gone]
    assert client.get(f"{ADMIN}/health/library").json()["missing"] == 1
    r = client.request("DELETE", f"{ADMIN}/health/missing", json={}).json()
    assert r["removed"] == 1 and r["backup"].startswith("nasmusic-prune-")
    assert conn.execute("SELECT COUNT(*) FROM tracks WHERE id = ?", (gone,)).fetchone()[0] == 0
    assert [t["id"] for t in client.get(f"{API}/playlists/{pl['id']}").json()["tracks"]] == [ids["Song 1"],
                                                                                           ids["Opening"]]
    qq = client.get(f"{API}/queues/{q['id']}").json()
    assert qq["tracks"][qq["currentIndex"]]["id"] == ids["Opening"]  # still on the same track
    assert [t["track"]["id"] for t in client.get(f"{API}/mixes/m").json()["tracks"]] == [ids["Closing"]]
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert any(b["name"] == r["backup"] and b["kind"] == "prune" for b in client.get(f"{ADMIN}/backups").json())
    assert client.request("DELETE", f"{ADMIN}/health/missing", json={}).json() == {"removed": 0, "backup": None}


def test_duplicates_tags_art(client, conn, cfg, app, base_flac):
    make_flac(base_flac, cfg.library_root / "Dupes" / "a.flac", title="Song 1", artist="Artist One", album="Other")
    make_flac(base_flac, cfg.library_root / "Dupes" / "b.flac", title="Twin", artist="X",
              musicbrainz_trackid="11111111-1111-1111-1111-111111111111")
    app.state.ctx.jobs.run("scan")
    groups = {g["reason"]: g for g in client.get(f"{ADMIN}/health/duplicates").json()}
    assert {t["path"] for t in groups["name"]["tracks"]} == {"Artist One/First Album/01 Song 1.flac", "Dupes/a.flac"}
    assert {t["title"] for t in groups["mbid"]["tracks"]} == {"Opening", "Twin"}
    tags = {x["track"]["path"]: x["missing"] for x in client.get(f"{ADMIN}/health/missing-tags").json()}
    assert set(tags["Loose/untagged.flac"]) >= {"artist", "album", "title", "year", "genre", "trackNo"}
    assert tags["Dupes/a.flac"] == ["year", "genre", "trackNo"]
    assert {a["title"] for a in client.get(f"{ADMIN}/health/missing-art").json()} == {"Loose", "Other", "Dupes"}


def test_cache_orphans_and_opus_clear(client, conn, cfg, app):
    tid = track_ids(conn)["Song 1"]
    client.get(f"{API}/stream/{tid}?bitrate=64")
    opus = cfg.data_dir / "opus"
    (opus / f"{'f' * 40}_64_123.opus").write_bytes(b"x" * 10)  # track that doesn't exist
    (cfg.data_dir / "art" / "512").mkdir(parents=True, exist_ok=True)
    (cfg.data_dir / "art" / "512" / "deadbeef.jpg").write_bytes(b"y" * 5)  # album that doesn't exist
    rep = client.get(f"{ADMIN}/health/cache").json()
    assert rep["files"] == 2 and rep["bytes"] == 15 and rep["byKind"]["opus"]["files"] == 1
    assert client.post(f"{ADMIN}/health/cache/clean").json()["files"] == 2
    assert client.get(f"{ADMIN}/health/cache").json()["files"] == 0
    assert len(list(opus.glob("*.opus"))) == 1  # the real transcode survived
    assert client.delete(f"{ADMIN}/opus-cache").json()["files"] == 1
    assert list(opus.glob("*.opus")) == []


def test_server_health(client):
    s = client.get(f"{ADMIN}/server").json()
    assert s["version"] and s["uptimeS"] >= 0 and s["cpu"]["cores"] >= 1
    assert s["disks"]["data"]["totalBytes"] > 0 and s["dataDirBytes"] > 0 and s["databaseBytes"] > 0
    assert s["opusCache"]["limitBytes"] == 1024**3


def test_logs_and_sse_redacted(client, app):
    log = logging.getLogger("nasmusic.test")
    log.setLevel(logging.INFO)
    log.warning("visible line /api/v1/stream/x?token=SECRET123 and Bearer abc.def")
    recs = client.get(f"{ADMIN}/logs", params={"level": "WARNING"}).json()
    mine = [r for r in recs if r["logger"] == "nasmusic.test"]
    assert mine and "SECRET123" not in mine[-1]["message"] and "abc.def" not in mine[-1]["message"]
    after = mine[-1]["seq"] - 1
    with client.stream("GET", f"{ADMIN}/logs/stream", params={"after": after, "level": "WARNING", "maxS": 0.3}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        body = "".join(r.iter_text())
    events = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert any("visible line" in e["message"] for e in events)
    # EventSource can't send headers: ?token= works on the stream (and only there).
    with TestClient(app) as anon:
        r = anon.get(f"{ADMIN}/logs/stream", params={"token": client.token, "maxS": 0.1})
        assert r.status_code == 200
        assert anon.get(f"{ADMIN}/logs", params={"token": client.token}).status_code == 401


def test_backups(client, cfg):
    b = client.post(f"{ADMIN}/backups").json()
    assert b["kind"] == "manual" and b["bytes"] > 0
    names = [x["name"] for x in client.get(f"{ADMIN}/backups").json()]
    assert b["name"] in names
    r = client.get(f"{ADMIN}/backups/{b['name']}")
    assert r.status_code == 200 and r.content[:16] == b"SQLite format 3\x00"
    assert "attachment" in r.headers["content-disposition"]
    for bad in ("nasmusic.db", "..%2Fconfig.toml", "config.toml", "nasmusic-manual-1.db"):
        assert client.get(f"{ADMIN}/backups/{bad}").status_code == 404
    assert client.delete(f"{ADMIN}/backups/{b['name']}").status_code == 204
    assert client.delete(f"{ADMIN}/backups/{b['name']}").status_code == 404
    assert (cfg.data_dir / "nasmusic.db").exists()


def test_now_playing(client, conn, app, bob):
    ids = track_ids(conn)
    did, tok = auth.create_device(conn, bob.user_id, "Bob's PC", "desktop")
    with client.websocket_connect(f"{API}/connect", headers={"Authorization": f"Bearer {tok}"}) as ws:
        ws.send_json({"type": "hello", "deviceName": "Bob's PC", "kind": "desktop", "canPlay": True})
        while ws.receive_json()["type"] != "welcome":
            pass
        ws.send_json({"type": "transfer", "play": True})
        while ws.receive_json()["type"] != "takeover":
            pass
        ws.send_json({"type": "state", "state": {"trackId": ids["Closing"], "playing": True}})
        while ws.receive_json()["type"] != "state":
            pass
        np = client.get(f"{ADMIN}/now-playing").json()
        assert [x["user"]["username"] for x in np] == ["bob"]
        assert np[0]["track"]["id"] == ids["Closing"] and np[0]["state"]["playing"] is True
        assert np[0]["devices"][0]["id"] == did


def test_server_settings(client, app):
    s = client.get(f"{ADMIN}/settings").json()
    assert s["overrides"] == {} and s["values"]["lanUrl"] == "https://192.168.1.50:8443"
    assert s["values"]["lrclibEnabled"] is True
    r = client.put(f"{ADMIN}/settings", json={"lanUrl": "https://192.168.68.2:9443", "tunnelUrl":
                   "https://music.example.org:8443/nas", "timezone": "Australia/Sydney", "opusCacheGb": 5,
                   "lrclibEnabled": False, "serverName": "Den"}).json()
    assert r["values"]["tunnelUrl"] == "https://music.example.org:8443/nas" and r["values"]["opusCacheGb"] == 5.0
    assert r["fromConfig"]["timezone"] == "Europe/London"
    info = client.get(f"{API}/server-info").json()
    assert (info["lanUrl"], info["tunnelUrl"], info["name"]) == ("https://192.168.68.2:9443",
                                                                 "https://music.example.org:8443/nas", "Den")
    assert app.state.ctx.opus.max_bytes == 5 * 1024**3 and app.state.ctx.jobs.cfg.timezone == "Australia/Sydney"
    assert client.get(f"{ADMIN}/status").json()["lyrics"]["lrclibEnabled"] is False
    inv = client.post(f"{ADMIN}/invites", json={}).json()
    assert inv["url"].startswith("https://music.example.org:8443/nas/join/")
    for bad in ({"lanUrl": "ftp://x"}, {"lanUrl": "https://host:99999"}, {"lanUrl": "https://h:1/path"},
                {"tunnelUrl": "music.example.org"}, {"timezone": "Mars/Base"}, {"opusCacheGb": 0},
                {"lrclibEnabled": "yes"}, {"serverName": ""}, {"nope": 1}, {"lanUrl": "https://bad host"}):
        assert client.put(f"{ADMIN}/settings", json=bad).status_code == 422, bad
    r = client.put(f"{ADMIN}/settings", json={"tunnelUrl": ""}).json()  # explicitly no tunnel
    assert r["values"]["tunnelUrl"] is None
    r = client.put(f"{ADMIN}/settings", json={"lanUrl": None, "tunnelUrl": None, "timezone": None}).json()
    assert r["values"]["lanUrl"] == "https://192.168.1.50:8443" and r["values"]["tunnelUrl"] == "https://music.example.com"
    assert set(r["overrides"]) == {"opusCacheGb", "lrclibEnabled", "serverName"}


def test_settings_survive_restart(cfg, scanned, conn):
    from nasmusic.app import create_app
    from nasmusic.app.scheduler import Jobs
    from tests.conftest import offline, user_client

    app = offline(create_app(cfg, jobs=Jobs(cfg)))
    with user_client(app, conn, "alice") as c:
        c.put(f"{ADMIN}/settings", json={"lrclibEnabled": False, "timezone": "Asia/Tokyo"})
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
        assert str(jobs.scheduler.get_job("nightly").trigger.timezone) == "Asia/Tokyo"
    finally:
        jobs.shutdown()


# ---------- bug reports ----------


def test_bug_reports(client, bob, conn, app):
    r = bob.post(f"{API}/me/bug-report", json={"description": "crashed on For You", "appVersion": "3.1",
                                               "platform": "Android 15 · Pixel 8", "logs": "line1\nline2",
                                               "crash": "java.lang.IllegalStateException", "extra": {"a": 1}})
    assert r.status_code == 200
    rid = r.json()["id"]
    lst = client.get(f"{ADMIN}/bug-reports").json()
    assert [x["id"] for x in lst] == [rid] and lst[0]["user"]["username"] == "bob" and lst[0]["hasCrash"]
    assert "logs" not in lst[0] and lst[0]["deviceId"] == bob.device_id and lst[0]["expiresAt"] > lst[0]["createdAt"]
    full = client.get(f"{ADMIN}/bug-reports/{rid}").json()
    assert full["logs"] == "line1\nline2" and full["extra"] == {"a": 1}
    assert bob.get(f"{ADMIN}/bug-reports/{rid}").status_code == 403
    assert client.delete(f"{ADMIN}/bug-reports/{rid}").status_code == 204
    assert client.get(f"{ADMIN}/bug-reports/{rid}").status_code == 404


def test_bug_report_limits(client, conn, app):
    big = "x" * (2 * 1024 * 1024 + 10)
    assert client.post(f"{API}/me/bug-report", json={"logs": big}).status_code == 413
    assert client.post(f"{API}/me/bug-report", content=b"{not json", headers={"content-type": "application/json"}
                       ).status_code == 422
    assert client.post(f"{API}/me/bug-report", json={"description": "no logs"}).status_code == 422
    for _ in range(20):
        assert client.post(f"{API}/me/bug-report", json={"logs": "l"}).status_code == 200
    assert client.post(f"{API}/me/bug-report", json={"logs": "l"}).status_code == 429


def test_bug_reports_expire(client, conn, app):
    rid = client.post(f"{API}/me/bug-report", json={"logs": "old"}).json()["id"]
    conn.execute("UPDATE bug_reports SET created_at = '2000-01-01T00:00:00Z' WHERE id = ?", (rid,))
    assert client.get(f"{ADMIN}/bug-reports").json() == []  # hidden once past 30 days
    assert app.state.ctx.jobs.run("cleanup")["result"] == {"bugReportsDeleted": 1}
