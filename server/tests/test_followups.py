"""Contract follow-ups (commit 8b89e6c): suggestion preview/cover proxies, hasPreview/hasCover,
more-like error mapping, manifest sizes + pre-transcode, thumb_clear, analysis progress per model."""
import io
import uuid
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from nasmusic import auth
from nasmusic.ai import api as ai
from nasmusic.app import create_app
from nasmusic.app.scheduler import Jobs
from nasmusic.sync import SYNC_KBPS, estimate_opus_bytes
from tests.conftest import track_ids

MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00" + bytes(range(256)) * 40  # fake MP3 bytes
DEEZER_API = "https://api.deezer.com/track/3135556"
DEEZER_CDN = "https://cdnt-preview.dzcdn.net/api/1/1/a/b/c/0/abc.mp3?hdnea=exp=1700000000~acl=x~hmac=y"
COVER = "https://e-cdns-images.dzcdn.net/images/cover/abc/1000x1000-000000-80-0-0.jpg"


class Upstream:
    """httpx.MockTransport handler standing in for Deezer; records every request."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.api_response = httpx.Response(200, json={"id": 3135556, "preview": DEEZER_CDN})
        self.mp3_status = 200
        self.cover_bytes = _jpeg((1000, 1000))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url)
        if url == DEEZER_API:
            r = self.api_response  # rebuild: an httpx.Response can only be streamed once
            return httpx.Response(r.status_code, content=r.content, headers=r.headers)
        if url.startswith("https://cdnt-preview.dzcdn.net/"):
            if self.mp3_status != 200:
                return httpx.Response(self.mp3_status)
            rng = request.headers.get("range")
            if rng:
                start = int(rng.removeprefix("bytes=").split("-")[0])
                return httpx.Response(206, content=MP3[start:], headers={
                    "content-type": "audio/mpeg", "content-range": f"bytes {start}-{len(MP3) - 1}/{len(MP3)}"})
            return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
        if url == COVER:
            return httpx.Response(200, content=self.cover_bytes, headers={"content-type": "image/jpeg"})
        return httpx.Response(404)


def _jpeg(size) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 20, 160)).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def upstream(app):
    u = Upstream()
    app.state.ctx.http_transport = httpx.MockTransport(u)
    return u


@pytest.fixture
def sugg(conn):
    rows = [("full", DEEZER_API, COVER), ("bare", None, None), ("direct", DEEZER_CDN, None)]
    for sid, preview, cover in rows:
        conn.execute(
            "INSERT INTO suggestions (id, artist, album, reason, preview_url, cover_url, created_at) "
            "VALUES (?, 'A', ?, 'r', ?, ?, '2026-11-15T03:00:00Z')", (sid, f"Album {sid}", preview, cover))


def _files(d):
    return {p for p in d.rglob("*") if p.is_file()}


# ---------- 1. preview proxy ----------


def test_preview_streams_through_fresh_each_time(client, upstream, sugg, cfg):
    before = _files(cfg.data_dir)
    for _ in range(2):
        r = client.get("/api/v1/suggestions/full/preview")
        assert r.status_code == 200
        assert r.headers["content-type"] == "audio/mpeg" and r.content == MP3
        assert r.headers["cache-control"] == "no-store"
    # The Deezer API lookup happens on every request (signed links expire)...
    assert [str(c.url) for c in upstream.calls].count(DEEZER_API) == 2
    # ...and the audio is never written to disk (only SQLite WAL churn is allowed).
    new = {p for p in _files(cfg.data_dir) - before if not p.name.startswith("nasmusic.db")}
    assert new == set()


def test_preview_range_passthrough(client, upstream, sugg):
    r = client.get("/api/v1/suggestions/full/preview", headers={"Range": "bytes=100-"})
    assert r.status_code == 206 and r.content == MP3[100:]
    assert r.headers["content-range"] == f"bytes 100-{len(MP3) - 1}/{len(MP3)}"
    assert upstream.calls[-1].headers["range"] == "bytes=100-"


def test_preview_direct_link(client, upstream, sugg):
    r = client.get("/api/v1/suggestions/direct/preview")
    assert r.status_code == 200 and r.content == MP3
    assert DEEZER_API not in [str(c.url) for c in upstream.calls]


def test_preview_404_and_502(client, upstream, sugg):
    assert client.get("/api/v1/suggestions/bare/preview").status_code == 404
    assert client.get("/api/v1/suggestions/nope/preview").status_code == 404
    # Deezer answers 200 with an error object, or with an empty preview.
    upstream.api_response = httpx.Response(200, json={"error": {"type": "DataException", "code": 800}})
    assert client.get("/api/v1/suggestions/full/preview").status_code == 502
    upstream.api_response = httpx.Response(200, json={"id": 1, "preview": ""})
    assert client.get("/api/v1/suggestions/full/preview").status_code == 502
    upstream.api_response = httpx.Response(503)
    assert client.get("/api/v1/suggestions/full/preview").status_code == 502
    # Lookup fine, MP3 link already expired.
    upstream.api_response = httpx.Response(200, json={"preview": DEEZER_CDN})
    upstream.mp3_status = 403
    assert client.get("/api/v1/suggestions/full/preview").status_code == 502


def test_preview_network_error_is_502(client, app, sugg):
    def down(request):
        raise httpx.ConnectError("no route to host")

    app.state.ctx.http_transport = httpx.MockTransport(down)
    assert client.get("/api/v1/suggestions/full/preview").status_code == 502


def test_preview_needs_token(anon, sugg):
    assert anon.get("/api/v1/suggestions/full/preview").status_code == 401


# ---------- 2. cover proxy ----------


def test_cover_resized_and_cached(client, upstream, sugg, cfg):
    r = client.get("/api/v1/suggestions/full/cover?size=200")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    with Image.open(io.BytesIO(r.content)) as im:
        assert im.size == (256, 256)  # bucketed like album art
    n = len(upstream.calls)
    assert client.get("/api/v1/suggestions/full/cover?size=200").content == r.content
    assert len(upstream.calls) == n  # served from the disk cache
    assert client.get("/api/v1/suggestions/full/cover").status_code == 200  # default 512 -> new fetch
    assert len(upstream.calls) == n + 1


def test_cover_404_and_502(client, upstream, sugg):
    assert client.get("/api/v1/suggestions/bare/cover").status_code == 404
    assert client.get("/api/v1/suggestions/nope/cover").status_code == 404
    upstream.cover_bytes = b"<html>not an image</html>"
    assert client.get("/api/v1/suggestions/full/cover").status_code == 502


# ---------- 3. hasPreview / hasCover ----------


def test_suggestion_flags(client, sugg):
    s = {x["id"]: x for x in client.get("/api/v1/suggestions").json()}
    assert (s["full"]["hasPreview"], s["full"]["hasCover"]) == (True, True)
    assert (s["bare"]["hasPreview"], s["bare"]["hasCover"]) == (False, False)
    assert (s["direct"]["hasPreview"], s["direct"]["hasCover"]) == (True, False)


def test_admin_suggestions_page_uses_proxy(cfg, scanned, sugg, monkeypatch):
    from nasmusic import web

    web._fails.clear()
    cfg = replace(cfg, admin_password_hash=auth.hash_password("hunter22"))
    app = create_app(cfg, jobs=Jobs(cfg))
    u = Upstream()
    app.state.ctx.http_transport = httpx.MockTransport(u)
    with TestClient(app) as c:
        assert c.get("/admin/suggestions/full/cover", follow_redirects=False).status_code == 303
        c.post("/admin/login", data={"password": "hunter22"})
        page = c.get("/admin/suggestions").text
        assert "/admin/suggestions/full/cover" in page and "/admin/suggestions/full/preview" in page
        assert "dzcdn.net" not in page  # browser never gets the upstream URLs
        assert c.get("/admin/suggestions/full/preview").content == MP3
        assert c.get("/admin/suggestions/full/cover").headers["content-type"] == "image/jpeg"


# ---------- 4. more-like errors ----------


def test_more_like_error_mapping(client, conn, monkeypatch):
    tid = track_ids(conn)["Song 1"]

    def raiser(exc):
        def f(*a, **k):
            raise exc
        return f

    monkeypatch.setattr(ai, "more_like", raiser(KeyError(tid)))
    assert client.post(f"/api/v1/mixes/more-like/{tid}").status_code == 404
    monkeypatch.setattr(ai, "more_like", raiser(NotImplementedError()))
    assert client.post(f"/api/v1/mixes/more-like/{tid}").status_code == 503
    monkeypatch.setattr(ai, "more_like", raiser(RuntimeError("boom")))
    r = client.post(f"/api/v1/mixes/more-like/{tid}")
    assert r.status_code == 500 and "boom" not in r.text
    monkeypatch.setattr(ai, "more_like", lambda *a, **k: "no-such-mix")
    assert client.post(f"/api/v1/mixes/more-like/{tid}").status_code == 500


def test_more_like_real_engine_unanalysed_track_is_404(client, conn):
    """With the real AI engine and nothing analysed yet, the seed is unknown to it -> 404."""
    tid = track_ids(conn)["Song 1"]
    assert client.post(f"/api/v1/mixes/more-like/{tid}").status_code == 404


# ---------- 5. manifest sizes + pre-transcode ----------


def _mix(conn, mix_id, tracks):
    conn.execute("INSERT INTO mixes (id, kind, title, for_date, created_at) "
                 "VALUES (?, 'daily', 'Daily', '2026-11-18', '2026-11-18T02:00:00Z')", (mix_id,))
    for i, t in enumerate(tracks):
        conn.execute("INSERT INTO mix_tracks (mix_id, position, track_id, reason) VALUES (?, ?, ?, 'r')",
                     (mix_id, i, t))


def test_manifest_sizes_estimate_then_exact(client, conn, app, cfg):
    ids = track_ids(conn)
    _mix(conn, "m1", [ids["Song 2"], ids["Opening"]])
    client.put(f"/api/v1/favourites/{ids['Song 1']}")

    items = {i["trackId"]: i for i in client.get("/api/v1/sync/manifest?favouritesFormat=flac").json()["items"]}
    fav = items[ids["Song 1"]]
    assert fav["format"] == "flac" and fav["bytesExact"] is True
    assert fav["bytes"] == (cfg.library_root / "Artist One/First Album/01 Song 1.flac").stat().st_size
    assert fav["durationS"] == pytest.approx(2.0, abs=0.05)
    mix_item = items[ids["Song 2"]]
    assert mix_item["bytesExact"] is False and mix_item["bytes"] == estimate_opus_bytes(mix_item["durationS"])

    # Pre-transcode (what runs after the nightly job) makes Opus sizes exact and equal to /stream.
    jobs: Jobs = app.state.ctx.jobs
    rec = jobs.run("pretranscode")
    assert rec["ok"] and rec["result"] == {"transcoded": 3, "alreadyCached": 0, "failed": 0}
    assert jobs.opus is app.state.ctx.opus  # same cache + locks as the API
    items = {i["trackId"]: i for i in client.get("/api/v1/sync/manifest").json()["items"]}
    for tid in (ids["Song 2"], ids["Opening"], ids["Song 1"]):
        assert items[tid]["bytesExact"] is True
        body = client.get(f"/api/v1/stream/{tid}?format=opus&bitrate={SYNC_KBPS}").content
        assert items[tid]["bytes"] == len(body)
    assert jobs.opus.transcodes == 3  # the stream requests above were cache hits
    assert jobs.run("pretranscode")["result"] == {"transcoded": 0, "alreadyCached": 3, "failed": 0}


def test_nightly_triggers_pretranscode(cfg, monkeypatch):
    jobs = Jobs(cfg)
    started = []
    monkeypatch.setattr(jobs, "trigger", lambda name: started.append(name) or True)
    monkeypatch.setattr(ai, "run_nightly", lambda conn, c: ["m"])
    assert jobs.run("nightly")["ok"]
    assert started == ["pretranscode"]


def test_pretranscode_runs_ffmpeg_niced(cfg, conn, scanned, monkeypatch):
    import subprocess

    seen = []
    real_run = subprocess.run

    def spy(*a, **k):
        seen.append(k.get("preexec_fn"))
        return real_run(*a, **k)

    monkeypatch.setattr(subprocess, "run", spy)
    conn.execute("UPDATE tracks SET starred_at = '2026-11-18T00:00:00Z' WHERE title = 'Song 1'")
    jobs = Jobs(cfg)
    assert jobs.run("pretranscode")["result"]["transcoded"] == 1
    assert len(seen) == 1 and seen[0] is not None


# ---------- 6. thumb_clear ----------


def test_thumb_clear(client, conn):
    tid = track_ids(conn)["Closing"]

    def post(t):
        return client.post("/api/v1/events", json={"events": [
            {"id": str(uuid.uuid4()), "type": t, "trackId": tid, "at": "2026-11-18T10:00:00Z"}]}).json()

    assert post("thumb_down")["accepted"] == 1
    assert conn.execute("SELECT blocked FROM tracks WHERE id = ?", (tid,)).fetchone()[0] == 1
    assert post("thumb_clear")["accepted"] == 1
    assert conn.execute("SELECT blocked FROM tracks WHERE id = ?", (tid,)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type = 'thumb_clear'").fetchone()[0] == 1


# ---------- 7. analysis progress follows the model actually used ----------


def test_dashboard_progress_uses_fallback_model(cfg, scanned, conn):
    from nasmusic import web

    web._fails.clear()
    ids = list(track_ids(conn).values())
    for tid in ids[:2]:
        conn.execute("INSERT INTO features (track_id, model_version, embedding, dims, analysed_at) "
                     "VALUES (?, 'basic-1', ?, 1, '2026-11-18T01:00:00Z')", (tid, b"\x00\x00\x00\x00"))
    cfg = replace(cfg, admin_password_hash=auth.hash_password("hunter22"))
    with TestClient(create_app(cfg, jobs=Jobs(cfg))) as c:
        c.post("/admin/login", data={"password": "hunter22"})
        page = c.get("/admin").text
    assert "2 of 5 tracks analysed (40.0%)" in page
    assert "basic-1" in page and "fallback; configured: effnet-discogs-1" in page
