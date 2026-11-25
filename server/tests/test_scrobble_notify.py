"""v7: scrobbling (rules, outbox retry/backoff/dedupe with mocked HTTP, Last.fm auth, ListenBrainz import) and
batched "new music added" notifications."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from homebeat.app import notify, scrobble
from homebeat.core import get_setting, iso
from tests.conftest import track_ids

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


# ---------- rules ----------


@pytest.mark.parametrize("etype,pos,dur,ok", [
    ("play", None, 200, True),
    ("play", None, 29, False),          # too short
    ("skip", 99, 200, False),           # less than half
    ("skip", 100, 200, True),           # exactly half
    ("skip", 240, 1200, True),          # 4 minutes of a 20-minute track
    ("skip", 239, 1200, False),
    ("skip", None, 200, False),
    ("star", None, 200, False),
])
def test_rules(etype, pos, dur, ok):
    assert scrobble.qualifies(etype, pos, dur) is ok


def test_started_at_and_backoff():
    assert scrobble.started_at("2026-11-20T12:03:00Z", "skip", 180, 300) == int(NOW.timestamp())
    assert scrobble.started_at("2026-11-20T12:04:00Z", "play", None, 300) == int(NOW.timestamp())  # 80 % of 300
    assert [scrobble.backoff_s(n) for n in (1, 2, 3)] == [60, 120, 240]
    assert scrobble.backoff_s(50) == scrobble.MAX_BACKOFF_S


def test_lastfm_signature():
    # sorted name+value pairs + secret, md5; `format` is not signed
    sig = scrobble.lastfm_sign({"method": "auth.getSession", "api_key": "k", "token": "t", "format": "json"}, "s")
    import hashlib

    assert sig == hashlib.md5(b"api_keykmethodauth.getSessiontokents").hexdigest()


# ---------- outbox ----------


@pytest.fixture
def long_tracks(scanned):
    scanned.execute("UPDATE tracks SET duration_s = 200")
    return track_ids(scanned)


def _event(tid, etype="play", at=None, pos=None, eid=None):
    return {"id": eid or str(uuid.uuid4()), "type": etype, "trackId": tid, "at": iso(at or datetime.now(UTC)),
            "positionS": pos, "durationS": 200}


def _connect(conn, uid, service="listenbrainz"):
    scrobble.connect_account(conn, uid, service, "tok-" + service, "me")


class FakeLB:
    def __init__(self):
        self.submits: list[dict] = []
        self.fail: list[int] = []      # status codes to return for the next submits

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"].startswith("Token ")
        if request.url.path == "/1/validate-token":
            ok = request.headers["authorization"] == "Token good-token-123"
            return httpx.Response(200, json={"valid": ok, "user_name": "lbuser" if ok else None})
        if request.url.path == "/1/submit-listens":
            if self.fail:
                return httpx.Response(self.fail.pop(0), json={"error": "nope"})
            self.submits.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404)


def test_queue_send_retry_dedupe(client, conn, long_tracks):
    uid = client.user_id
    _connect(conn, uid)
    t1, t2 = long_tracks["Song 1"], long_tracks["Song 2"]
    start = datetime.now(UTC) - timedelta(minutes=10)
    e1 = _event(t1, at=start + timedelta(seconds=160), pos=160)
    dup = _event(t1, at=start + timedelta(seconds=170), pos=170)       # same playthrough from another device
    e2 = _event(t2, "skip", at=start + timedelta(seconds=400), pos=50)  # not enough
    e3 = _event(t2, "skip", at=start + timedelta(seconds=700), pos=120)
    r = client.post("/api/v1/events", json={"events": [e1, dup, e2, e3]})
    assert r.status_code == 200
    client.post("/api/v1/events", json={"events": [e1]})  # resent batch: idempotent
    rows = conn.execute("SELECT event_id, status FROM scrobble_queue ORDER BY listened_at").fetchall()
    assert [r[0] for r in rows] == [e1["id"], e3["id"]]
    assert conn.execute("SELECT listened_at FROM scrobble_queue WHERE event_id = ?", (e1["id"],)).fetchone()[0] \
        == int(start.timestamp())

    lb = FakeLB()
    lb.fail = [503]
    t = httpx.MockTransport(lb)
    res = scrobble.flush(conn, client.app.state.ctx.cfg, t)
    assert res["retry"] == 2 and lb.submits == []
    q = conn.execute("SELECT status, attempts, next_attempt_at FROM scrobble_queue").fetchall()
    assert all(r[0] == "pending" and r[1] == 1 for r in q)
    assert scrobble.flush(conn, client.app.state.ctx.cfg, t)["sent"] == 0       # not due yet (backoff)
    res = scrobble.flush(conn, client.app.state.ctx.cfg, t, now=datetime.now(UTC) + timedelta(minutes=2))
    assert res["sent"] == 2
    sub = lb.submits[0]
    assert sub["listen_type"] == "import" and len(sub["payload"]) == 2
    md = sub["payload"][0]["track_metadata"]
    assert md["track_name"] == "Song 1" and md["artist_name"] == "Artist One" and md["release_name"] == "First Album"
    st = client.get("/api/v1/me/scrobble").json()["services"][0]
    assert st["service"] == "listenbrainz" and st["sent"] == 2 and st["pending"] == 0 and st["lastScrobbleAt"]
    assert st["lastError"] is None


def test_auth_failure_pauses_and_bad_batch_is_split(client, conn, long_tracks):
    uid = client.user_id
    _connect(conn, uid)
    t1, t2 = long_tracks["Song 1"], long_tracks["Song 2"]
    base = datetime.now(UTC) - timedelta(hours=1)
    client.post("/api/v1/events", json={"events": [_event(t1, at=base), _event(t2, at=base + timedelta(minutes=5))]})
    lb = FakeLB()
    lb.fail = [401]
    cfg = client.app.state.ctx.cfg
    assert scrobble.flush(conn, cfg, httpx.MockTransport(lb))["paused"] == 2
    st = client.get("/api/v1/me/scrobble").json()["services"][0]
    assert st["needsReauth"] and st["pending"] == 2
    assert scrobble.flush(conn, cfg, httpx.MockTransport(lb))["sent"] == 0   # paused until reconnected
    _connect(conn, uid)
    lb.fail = [400, 200, 400]  # batch 400 -> one by one: first ok, second bad
    res = scrobble.flush(conn, cfg, httpx.MockTransport(lb))
    assert res["sent"] == 1 and res["failed"] == 1
    assert client.post("/api/v1/me/scrobble/retry").json()["services"][0]["pending"] == 1


def test_gives_up_after_max_attempts(client, conn, long_tracks):
    _connect(conn, client.user_id)
    client.post("/api/v1/events", json={"events": [_event(long_tracks["Song 1"])]})
    lb = FakeLB()
    cfg = client.app.state.ctx.cfg
    now = datetime.now(UTC)
    for i in range(scrobble.MAX_ATTEMPTS):
        lb.fail = [500]
        now += timedelta(hours=7)
        scrobble.flush(conn, cfg, httpx.MockTransport(lb), now=now)
    assert conn.execute("SELECT status FROM scrobble_queue").fetchone()[0] == "failed"


def test_not_enabled_device_opt_out_and_erase(client, conn, long_tracks):
    t1 = long_tracks["Song 1"]
    client.post("/api/v1/events", json={"events": [_event(t1)]})
    assert conn.execute("SELECT COUNT(*) FROM scrobble_queue").fetchone()[0] == 0   # opt-in: nothing connected
    _connect(conn, client.user_id)
    client.put("/api/v1/me/scrobble/listenbrainz", json={"enabled": False})
    client.post("/api/v1/events", json={"events": [_event(t1, at=datetime.now(UTC) - timedelta(hours=1))]})
    assert conn.execute("SELECT COUNT(*) FROM scrobble_queue").fetchone()[0] == 0
    client.put("/api/v1/me/scrobble/listenbrainz", json={"enabled": True})
    client.put("/api/v1/me/settings", json={"settings": {f"device.{client.device_id}.scrobble": False}})
    client.post("/api/v1/events", json={"events": [_event(t1, at=datetime.now(UTC) - timedelta(hours=2))]})
    assert conn.execute("SELECT COUNT(*) FROM scrobble_queue").fetchone()[0] == 0
    client.put("/api/v1/me/settings", json={"settings": {f"device.{client.device_id}.scrobble": None}})
    client.post("/api/v1/events", json={"events": [_event(t1, at=datetime.now(UTC) - timedelta(hours=3))]})
    assert conn.execute("SELECT COUNT(*) FROM scrobble_queue").fetchone()[0] == 1
    client.delete("/api/v1/me/data", params={"signal": "all", "confirm": "true"})
    assert conn.execute("SELECT COUNT(*) FROM scrobble_queue").fetchone()[0] == 0


def test_lastfm_old_scrobbles_ignored(client, conn, long_tracks):
    _connect(conn, client.user_id, "lastfm")
    old = datetime.now(UTC) - timedelta(days=20)
    client.post("/api/v1/events", json={"events": [_event(long_tracks["Song 1"], at=old)]})
    assert conn.execute("SELECT status FROM scrobble_queue").fetchone()[0] == "ignored"


# ---------- Last.fm ----------


class FakeLastfm:
    def __init__(self):
        self.approved = False
        self.calls: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        p = dict(request.url.params) if request.method == "GET" else \
            {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.calls.append(p)
        sig = p.pop("api_sig")
        assert sig == scrobble.lastfm_sign(p, "secret")
        m = p["method"]
        if m == "auth.getToken":
            return httpx.Response(200, json={"token": "reqtok"})
        if m == "auth.getSession":
            if not self.approved:
                return httpx.Response(403, json={"error": 14, "message": "Unauthorized Token"})
            return httpx.Response(200, json={"session": {"name": "fmuser", "key": "sk1", "subscriber": 0}})
        if m == "track.scrobble":
            n = sum(1 for k in p if k.startswith("artist["))
            sc = [{"ignoredMessage": {"code": "1" if i == 1 else "0", "#text": "Artist ignored" if i == 1 else ""}}
                  for i in range(n)]
            return httpx.Response(200, json={"scrobbles": {"@attr": {"accepted": n - 1}, "scrobble": sc}})
        if m == "track.updateNowPlaying":
            return httpx.Response(200, json={"nowplaying": {}})
        return httpx.Response(400, json={"error": 3, "message": "Invalid method"})


def test_lastfm_auth_flow_and_scrobble(client, conn, long_tracks):
    ctx = client.app.state.ctx
    assert client.post("/api/v1/me/scrobble/lastfm/connect").status_code == 503   # not configured
    ctx.cfg.lastfm_api_key, ctx.cfg.lastfm_api_secret = "key", "secret"
    fm = FakeLastfm()
    ctx.http_transport = httpx.MockTransport(fm)
    r = client.post("/api/v1/me/scrobble/lastfm/connect")
    assert r.json()["authUrl"] == "https://www.last.fm/api/auth/?api_key=key&token=reqtok"
    assert client.post("/api/v1/me/scrobble/lastfm/complete").status_code == 409
    fm.approved = True
    st = client.post("/api/v1/me/scrobble/lastfm/complete").json()["services"][1]
    assert st["connected"] and st["enabled"] and st["username"] == "fmuser"
    assert "sk1" not in json.dumps(client.get("/api/v1/me/scrobble").json())   # secrets never returned
    base = datetime.now(UTC) - timedelta(hours=1)
    client.post("/api/v1/events", json={"events": [_event(long_tracks["Song 1"], at=base),
                                                   _event(long_tracks["Song 2"], at=base + timedelta(minutes=5))]})
    res = scrobble.flush(conn, ctx.cfg, ctx.http_transport)
    assert res == {"sent": 1, "ignored": 1, "failed": 0, "retry": 0, "paused": 0}
    sent = fm.calls[-1]
    assert sent["sk"] == "sk1" and sent["track[0]"] == "Song 1" and sent["timestamp[0]"].isdigit()
    assert scrobble.now_playing(conn, ctx.cfg, client.user_id, long_tracks["Song 1"], ctx.http_transport) == 1


def test_listenbrainz_connect(client, conn):
    lb = FakeLB()
    client.app.state.ctx.http_transport = httpx.MockTransport(lb)
    assert client.post("/api/v1/me/scrobble/listenbrainz/connect", json={"token": "bad-token-123"}).status_code == 400
    st = client.post("/api/v1/me/scrobble/listenbrainz/connect", json={"token": "good-token-123"}).json()
    assert st["services"][0]["username"] == "lbuser" and st["services"][0]["enabled"]
    assert client.delete("/api/v1/me/scrobble/listenbrainz").json()["services"][0]["connected"] is False


# ---------- ListenBrainz import ----------


def test_import_and_remove(client, conn, long_tracks):
    uid = client.user_id
    scrobble.connect_account(conn, uid, "listenbrainz", "good-token-123", "lbuser")
    t1, t2 = long_tracks["Song 1"], long_tracks["Opening"]
    ts0 = int((datetime.now(UTC) - timedelta(days=3)).timestamp())
    # one listen the server already has from a client
    client.post("/api/v1/events", json={"events": [_event(t1, at=datetime.fromtimestamp(ts0 + 160, UTC))]})
    listens = [
        {"listened_at": ts0, "track_metadata": {"artist_name": "Artist One", "track_name": "Song 1"}},
        {"listened_at": ts0 - 1000, "track_metadata": {"artist_name": "ARTIST ONE", "track_name": "song 1"}},
        {"listened_at": ts0 - 2000, "track_metadata": {"artist_name": "Someone", "track_name": "x",
                                                       "mbid_mapping": {"recording_mbid":
                                                                        "11111111-1111-1111-1111-111111111111"}}},
        {"listened_at": ts0 - 3000, "track_metadata": {"artist_name": "Nobody", "track_name": "Unknown"}},
    ]

    def handler(request):
        assert request.url.path == "/1/user/lbuser/listens"
        if "max_ts" in request.url.params:
            return httpx.Response(200, json={"payload": {"count": 0, "listens": []}})
        return httpx.Response(200, json={"payload": {"count": len(listens), "listens": listens}})

    client.app.state.ctx.http_transport = httpx.MockTransport(handler)
    before = conn.execute("SELECT play_count FROM user_tracks WHERE user_id = ? AND track_id = ?",
                          (uid, t1)).fetchone()[0]
    st = client.post("/api/v1/me/scrobble/listenbrainz/import", params={"wait": "true"}).json()
    assert st["import"]["state"] == "done"
    assert (st["import"]["imported"], st["import"]["alreadyKnown"], st["import"]["unmatched"]) == (2, 1, 1)
    assert st["importedEvents"] == 2
    ids = {r[0] for r in conn.execute("SELECT track_id FROM events WHERE imported = 'listenbrainz'")}
    assert ids == {t1, t2}
    # only the client's own play is queued; imported listens are never scrobbled back
    assert [r[0] for r in conn.execute("SELECT track_id FROM scrobble_queue")] == [t1]
    # idempotent
    client.post("/api/v1/me/scrobble/listenbrainz/import", params={"wait": "true"})
    assert scrobble.imported_count(conn, uid) == 2
    r = client.delete("/api/v1/me/scrobble/listenbrainz/import").json()
    assert r["removed"] == 2 and r["importedEvents"] == 0 and r["import"] is None
    after = conn.execute("SELECT play_count FROM user_tracks WHERE user_id = ? AND track_id = ?",
                         (uid, t1)).fetchone()[0]
    assert after == before


# ---------- notifications ----------


def _add_album(conn, artist, album, titles, when):
    from homebeat.library.scanner import sha1

    ar = sha1(artist)
    conn.execute("INSERT OR IGNORE INTO artists (id, name, sort_name) VALUES (?, ?, ?)", (ar, artist, artist))
    al = sha1(ar + album)
    conn.execute("INSERT OR IGNORE INTO albums (id, artist_id, title, added_at) VALUES (?, ?, ?, ?)",
                 (al, ar, album, when))
    for i, t in enumerate(titles):
        p = f"{artist}/{album}/{i} {t}.flac"
        conn.execute("INSERT INTO tracks (id, path, album_id, artist_id, title, duration_s, flac_bytes, mtime, "
                     "added_at, folder) VALUES (?, ?, ?, ?, ?, 200, 1, 0, ?, ?)",
                     (sha1(p), p, al, ar, t, when, f"{artist}/{album}"))
    return al


def test_new_music_batching(app, conn, client, bob):
    hub = app.state.ctx.hub
    pushed = []
    hub.notify_user = lambda uid, msg: pushed.append((uid, msg))
    hour_ago = iso(datetime.now(UTC) - timedelta(days=30))
    conn.execute("UPDATE tracks SET added_at = ?", (hour_ago,))
    conn.execute("UPDATE albums SET added_at = ?", (hour_ago,))
    assert notify.new_music(conn, hub) == {"initialised": True}   # existing library is not "new"
    assert notify.new_music(conn, hub) == {"tracks": 0, "notified": 0}
    client.put("/api/v1/me/settings", json={"settings": {"notify.newMusic": True}})   # opt-in: only alice
    when = iso(datetime.now(UTC) + timedelta(seconds=5))
    _add_album(conn, "AJR", "The Maybe Man", ["A", "B", "C"], when)
    _add_album(conn, "AJR", "OK Orchestra", ["D", "E"], when)
    # a song added to an album that already existed
    old_album = conn.execute("SELECT album_id, artist_id FROM tracks WHERE title = 'Song 1'").fetchone()
    conn.execute("INSERT INTO tracks (id, path, album_id, artist_id, title, duration_s, flac_bytes, mtime, added_at) "
                 "VALUES ('bonus', 'bonus.flac', ?, ?, 'Bonus', 200, 1, 0, ?)", (*old_album, when))
    res = notify.new_music(conn, hub)
    assert res == {"tracks": 6, "notified": 1}
    assert len(pushed) == 1 and pushed[0][0] == client.user_id
    n = pushed[0][1]["notification"]
    assert n["title"] == "6 new songs"
    assert n["body"] == "2 new albums from AJR, plus 1 song from Artist One"
    assert n["data"]["albums"] == 2 and n["data"]["tracks"] == 6
    assert notify.new_music(conn, hub)["tracks"] == 0          # watermark moved: one notification per scan
    lst = client.get("/api/v1/me/notifications").json()
    assert lst["unread"] == 1 and lst["items"][0]["kind"] == "new_music"
    assert bob.get("/api/v1/me/notifications").json() == {"items": [], "unread": 0}
    assert client.get("/api/v1/me/notifications", params={"since": lst["items"][0]["createdAt"]}).json()["items"] == []
    assert client.post("/api/v1/me/notifications/read", json={}).json() == {"marked": 1, "unread": 0}
    new = client.get("/api/v1/library/new").json()
    assert {a["title"] for a in new["albums"]} == {"The Maybe Man", "OK Orchestra"}
    assert [t["title"] for t in new["tracks"]] == ["Bonus"]


def test_describe_text():
    rows = [{"id": str(i), "album_id": "x", "artist": a, "album_artist_name": None} for i, a in
            enumerate(["A", "B", "C", "D"])]
    title, body, _ = notify.describe(rows[:1], set())
    assert (title, body) == ("1 new song", "From A")
    assert notify.describe(rows, set())[1] == "From A, B, C and 1 more"


def test_scan_job_notifies(cfg, app, conn, client):
    client.put("/api/v1/me/settings", json={"settings": {"notify.newMusic": True}})
    jobs = app.state.ctx.jobs
    jobs.hub = None
    assert jobs.run("scan")["result"]["newMusic"] == {"initialised": True}
    old = iso(datetime.now(UTC) - timedelta(hours=1))
    conn.execute("UPDATE tracks SET added_at = ?", (old,))
    conn.execute("UPDATE albums SET added_at = ?", (old,))
    conn.execute("UPDATE settings SET value = ? WHERE key = ?", (json.dumps(old), notify.WATERMARK))
    from tests.conftest import make_flac

    src = next(cfg.library_root.rglob("*.flac"))
    make_flac(src, cfg.library_root / "New Artist" / "Fresh" / "01 New.flac", title="New", artist="New Artist",
              albumartist="New Artist", album="Fresh")
    rec = jobs.run("scan")
    assert rec["result"]["newMusic"] == {"tracks": 1, "notified": 1}
    assert get_setting(conn, notify.WATERMARK)
    item = client.get("/api/v1/me/notifications").json()["items"][0]
    assert item["body"] == "1 new album from New Artist"
