"""Podcasts, audiobooks and internet radio (schema v7, homebeat.spoken, app.spoken_api). All HTTP is mocked."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from homebeat import config as config_mod
from homebeat import db
from homebeat.core import init_db, open_db
from homebeat.db import migrations
from homebeat.spoken import audiobooks, feeds, podcasts, stations
from tests.conftest import offline, user_client

FEED_URL = "https://feeds.example.com/show.xml"

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0" xmlns:psc="http://podlove.org/simple-chapters"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
<channel>
  <title>Test Show</title>
  <itunes:author>Host Person</itunes:author>
  <description>A show about &lt;b&gt;tests&lt;/b&gt;.</description>
  <itunes:image href="https://cdn.example.com/show.jpg"/>
  <itunes:explicit>no</itunes:explicit>
  <language>en</language>
  <item>
    <title>Episode Two</title>
    <guid>ep-2</guid>
    <pubDate>Tue, 10 Nov 2026 08:00:00 GMT</pubDate>
    <enclosure url="https://cdn.example.com/ep2.mp3" length="3000" type="audio/mpeg"/>
    <itunes:duration>1:02:03</itunes:duration>
    <itunes:episode>2</itunes:episode>
    <podcast:chapters url="https://cdn.example.com/ep2.json" type="application/json+chapters"/>
    <content:encoded><![CDATA[<p>Second <i>one</i></p>]]></content:encoded>
  </item>
  <item>
    <title>Episode One</title>
    <guid>ep-1</guid>
    <pubDate>Tue, 03 Nov 2026 08:00:00 +0000</pubDate>
    <enclosure url="https://cdn.example.com/ep1.mp3" length="2000" type="audio/mpeg"/>
    <itunes:duration>754</itunes:duration>
    <itunes:explicit>yes</itunes:explicit>
    <psc:chapters version="1.2">
      <psc:chapter start="00:00:00.000" title="Intro"/>
      <psc:chapter start="00:01:30.500" title="Main" href="https://example.com/main"/>
    </psc:chapters>
  </item>
  <item><title>No audio</title><guid>x</guid></item>
</channel>
</rss>"""

EP3 = b"""<item><title>Episode Three</title><guid>ep-3</guid><pubDate>Tue, 17 Nov 2026 08:00:00 GMT</pubDate>
<enclosure url="https://cdn.example.com/ep3.mp3" length="1000" type="audio/mpeg"/></item>"""

CHAPTERS_JSON = b'{"version":"1.2.0","chapters":[{"startTime":0,"title":"Hello"},' \
                b'{"startTime":60.5,"title":"Hidden","toc":false},{"startTime":120,"title":"Topic","url":"https://x.org"}]}'


# ---------- feed parsing ----------


def test_parse_rss_feed():
    f = feeds.parse(RSS)
    assert f.title == "Test Show" and f.author == "Host Person" and f.language == "en"
    assert f.image_url == "https://cdn.example.com/show.jpg" and not f.explicit
    assert [e.guid for e in f.episodes] == ["ep-2", "ep-1"]  # the item without an enclosure is skipped
    e2, e1 = f.episodes
    assert e2.duration_s == 3723 and e2.episode == 2 and e2.published_at == "2026-11-10T08:00:00Z"
    assert e2.chapters_url == "https://cdn.example.com/ep2.json" and "Second" in e2.description
    assert e1.duration_s == 754 and e1.explicit
    assert e1.chapters == [{"start": 0.0, "title": "Intro"},
                           {"start": 90.5, "title": "Main", "url": "https://example.com/main"}]


def test_parse_durations_and_json_chapters():
    assert feeds.parse_duration("12:34") == 754
    assert feeds.parse_duration("90.5") == 90.5
    assert feeds.parse_duration("abc") is None and feeds.parse_duration("") is None
    ch = feeds.parse_json_chapters(CHAPTERS_JSON)
    assert [c["title"] for c in ch] == ["Hello", "Topic"] and ch[1]["url"] == "https://x.org"
    assert feeds.plain_text("<p>a &amp; <b>b</b></p>") == "a & b"


def test_parse_atom_and_rejects():
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom Cast</title><author><name>A</name></author>
      <entry><id>urn:1</id><title>E1</title><updated>2026-01-02T03:04:05Z</updated>
      <link rel="enclosure" href="https://x.org/1.ogg" type="audio/ogg" length="5"/></entry></feed>"""
    f = feeds.parse(atom)
    assert f.title == "Atom Cast" and f.author == "A"
    assert f.episodes[0].enclosure_url == "https://x.org/1.ogg" and f.episodes[0].published_at == "2026-01-02T03:04:05Z"
    for bad in (b"", b"<html><body>hi</body></html>", b"not xml <",
                b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><rss><channel/></rss>'):
        with pytest.raises(feeds.FeedError):
            feeds.parse(bad)


# ---------- app with mocked upstreams ----------


class Upstream:
    """A tiny fake internet: url -> handler/response; records requests."""

    def __init__(self):
        self.routes: dict[str, object] = {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url).split("?")[0]
        h = self.routes.get(url)
        if h is None:
            raise httpx.ConnectError(f"offline: {url}")
        if callable(h):
            return h(request)
        return httpx.Response(h.status_code, headers=h.headers, content=h.content)  # fresh body each time

    def transport(self):
        return httpx.MockTransport(self)


@pytest.fixture
def books_root(tmp_path, base_flac) -> Path:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    root = tmp_path / "books"
    one = root / "Author A - Long Book"
    one.mkdir(parents=True)
    meta = tmp_path / "chapters.txt"
    meta.write_text(";FFMETADATA1\ntitle=Long Book\nartist=Author A\nalbum=Long Book\ncomposer=Reader R\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=Opening\n"
                    "[CHAPTER]\nTIMEBASE=1/1000\nSTART=1000\nEND=3000\ntitle=The Middle\n")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=3",
                    "-i", str(meta), "-map", "0", "-map_metadata", "1", "-map_chapters", "1", "-c:a", "aac",
                    "-b:a", "32k", "-f", "mp4", str(one / "book.m4b")], check=True)
    two = root / "Second Book"
    two.mkdir()
    for i in (1, 2, 10):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=500:duration=2", "-c:a", "libmp3lame", "-b:a", "32k",
                        "-metadata", f"title=Part {i}", "-metadata", "album=Second Book",
                        "-metadata", "artist=Writer B", str(two / f"{i} part.mp3")], check=True)
    return root


@pytest.fixture
def up():
    return Upstream()


@pytest.fixture
def sapp(tmp_path, library, books_root, up):
    from homebeat.app import create_app

    cfg = config_mod.from_dict({"library_root": str(library), "data_dir": str(tmp_path / "data"),
                                "lan_host": "192.168.1.50", "opus_cache_gb": 1, "audiobooks_root": str(books_root),
                                "podcast_keep_last": 1, "podcast_itunes_search": True})
    init_db(cfg)
    app = create_app(cfg, jobs=None)
    offline(app)
    app.state.ctx.http_transport = up.transport()
    return app


@pytest.fixture
def sconn(sapp):
    c = open_db(sapp.state.ctx.cfg)
    yield c
    c.close()


@pytest.fixture
def alice(sapp, sconn):
    with user_client(sapp, sconn, "alice", admin=True) as c:
        yield c


def _rss(extra: bytes = b"") -> bytes:
    return RSS.replace(b"<item>", extra + b"<item>", 1) if extra else RSS


def test_subscribe_poll_and_episodes(sapp, sconn, alice, up):
    calls = {"n": 0}

    def feed(req):
        calls["n"] += 1
        if req.headers.get("if-none-match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, headers={"etag": '"v1"', "content-type": "application/rss+xml"}, content=RSS)

    up.routes[FEED_URL] = feed
    r = alice.post("/api/v1/podcasts", json={"feedUrl": FEED_URL})
    assert r.status_code == 201, r.text
    show = r.json()
    assert show["title"] == "Test Show" and show["subscribed"] and show["speed"] == 1.0
    pid = show["id"]
    lst = alice.get("/api/v1/podcasts").json()["podcasts"]
    assert [p["id"] for p in lst] == [pid] and lst[0]["unplayed"] == 2
    eps = alice.get(f"/api/v1/podcasts/{pid}/episodes").json()["episodes"]
    assert [e["title"] for e in eps] == ["Episode Two", "Episode One"]
    assert eps[0]["durationS"] == 3723 and eps[0]["podcastTitle"] == "Test Show"

    # poll: 304 -> nothing new, then the feed grows
    assert podcasts.poll_one(sconn, pid, up.transport()) == {"id": pid, "ok": True, "new": 0, "notModified": True}
    up.routes[FEED_URL] = httpx.Response(200, headers={"etag": '"v2"'}, content=_rss(EP3))
    assert podcasts.poll_one(sconn, pid, up.transport())["new"] == 1
    assert sconn.execute("SELECT etag FROM podcasts WHERE id = ?", (pid,)).fetchone()[0] == '"v2"'
    eps = alice.get("/api/v1/podcasts/episodes?filter=all").json()["episodes"]
    assert eps[0]["title"] == "Episode Three"
    # broken feed: error recorded, episodes kept
    up.routes[FEED_URL] = httpx.Response(500)
    res = podcasts.poll_one(sconn, pid, up.transport(), force=True)
    assert not res["ok"] and "500" in alice.get(f"/api/v1/podcasts/{pid}").json()["lastError"]
    assert len(alice.get(f"/api/v1/podcasts/{pid}/episodes").json()["episodes"]) == 3

    # per-show settings
    r = alice.patch(f"/api/v1/podcasts/{pid}", json={"speed": 1.5, "skipSilence": True, "serverKeepLast": 2})
    assert r.json()["speed"] == 1.5 and r.json()["skipSilence"] and r.json()["serverKeepLast"] == 2
    assert alice.patch(f"/api/v1/podcasts/{pid}", json={"speed": 9}).status_code == 422


def test_subscribe_rejects_bad_feeds(alice, up):
    up.routes["https://feeds.example.com/page"] = httpx.Response(200, content=b"<html>no</html>")
    assert alice.post("/api/v1/podcasts", json={"feedUrl": "https://feeds.example.com/page"}).status_code == 422
    assert alice.post("/api/v1/podcasts", json={"feedUrl": "http://127.0.0.1:8443/api"}).status_code == 422
    assert alice.post("/api/v1/podcasts", json={"feedUrl": "file:///etc/passwd"}).status_code == 422


def test_server_downloads_keep_last_and_cap(sapp, sconn, alice, up):
    cfg = sapp.state.ctx.cfg
    up.routes[FEED_URL] = httpx.Response(200, content=_rss(EP3))
    for n in (1, 2, 3):
        up.routes[f"https://cdn.example.com/ep{n}.mp3"] = httpx.Response(
            200, headers={"content-type": "audio/mpeg"}, content=bytes([n]) * (1000 * n))
    pid = alice.post("/api/v1/podcasts", json={"feedUrl": FEED_URL}).json()["id"]
    res = podcasts.run(sconn, cfg, up.transport())   # keep_last = 1 (config)
    assert res["polled"] == 1 and res["downloaded"] == 1
    local = sconn.execute("SELECT title, local_path FROM podcast_episodes WHERE local_path IS NOT NULL").fetchall()
    assert [r["title"] for r in local] == ["Episode Three"]
    assert (podcasts.podcast_dir(cfg) / local[0]["local_path"]).read_bytes() == bytes([3]) * 3000
    # served from disk now
    eid = sconn.execute("SELECT id FROM podcast_episodes WHERE guid = 'ep-3'").fetchone()[0]
    r = alice.get(f"/api/v1/stream/episode/{eid}")
    assert r.status_code == 200 and r.content == bytes([3]) * 3000
    # keep 2 -> Episode Two downloaded too; back to 0 -> everything deleted
    sconn.execute("UPDATE podcasts SET keep_last = 2 WHERE id = ?", (pid,))
    assert podcasts.sync_downloads(sconn, cfg, up.transport())["downloaded"] == 1
    sconn.execute("UPDATE podcasts SET keep_last = 0 WHERE id = ?", (pid,))
    assert podcasts.sync_downloads(sconn, cfg, up.transport())["deleted"] == 2
    assert not list(podcasts.podcast_dir(cfg).glob("*/*"))
    # storage cap: 3500 bytes fits the newest (3000 B) but no older episode next to it
    sconn.execute("UPDATE podcasts SET keep_last = 3 WHERE id = ?", (pid,))
    small = config_mod.from_dict({**{k: str(v) for k, v in {"library_root": cfg.library_root,
                                                              "data_dir": cfg.data_dir}.items()},
                                  "podcast_storage_gb": 3500 / 1024**3})
    res = podcasts.sync_downloads(sconn, small, up.transport())
    assert res["downloaded"] == 1 and res["bytes"] == 3000
    assert sconn.execute("SELECT guid FROM podcast_episodes WHERE local_path IS NOT NULL").fetchall()[0][0] == "ep-3"
    # unsubscribing the last subscriber forgets the feed and its files
    assert alice.delete(f"/api/v1/podcasts/{pid}").status_code == 204
    assert sconn.execute("SELECT COUNT(*) FROM podcast_episodes").fetchone()[0] == 0
    assert not (podcasts.podcast_dir(cfg) / pid).exists()


def test_episode_proxy_passes_range(alice, sconn, up):
    up.routes[FEED_URL] = httpx.Response(200, content=RSS)
    alice.post("/api/v1/podcasts", json={"feedUrl": FEED_URL})
    data = bytes(range(256)) * 10

    def audio(req):
        rng = req.headers.get("range")
        if rng:
            a, b = rng.split("=")[1].split("-")
            part = data[int(a):int(b) + 1]
            return httpx.Response(206, headers={"content-type": "audio/mpeg", "content-range":
                                               f"bytes {a}-{b}/{len(data)}", "content-length": str(len(part))},
                                  content=part)
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=data)

    up.routes["https://cdn.example.com/ep2.mp3"] = audio
    eid = sconn.execute("SELECT id FROM podcast_episodes WHERE guid = 'ep-2'").fetchone()[0]
    r = alice.get(f"/api/v1/stream/episode/{eid}", headers={"Range": "bytes=10-19"})
    assert r.status_code == 206 and r.content == data[10:20]
    assert r.headers["content-range"] == f"bytes 10-19/{len(data)}"
    # ?token= works for <audio> on /stream/
    from fastapi.testclient import TestClient

    anon = TestClient(alice.app)
    assert anon.get(f"/api/v1/stream/episode/{eid}").status_code == 401
    assert anon.get(f"/api/v1/stream/episode/{eid}?token={alice.token}").content == data


def test_chapters_from_feed_and_json(alice, sconn, up):
    up.routes[FEED_URL] = httpx.Response(200, content=RSS)
    alice.post("/api/v1/podcasts", json={"feedUrl": FEED_URL})
    up.routes["https://cdn.example.com/ep2.json"] = httpx.Response(200, content=CHAPTERS_JSON)
    e1, e2 = (sconn.execute("SELECT id FROM podcast_episodes WHERE guid = ?", (g,)).fetchone()[0]
              for g in ("ep-1", "ep-2"))
    assert [c["title"] for c in alice.get(f"/api/v1/podcasts/episodes/{e1}").json()["chapters"]] == ["Intro", "Main"]
    d = alice.get(f"/api/v1/podcasts/episodes/{e2}").json()
    assert [c["start"] for c in d["chapters"]] == [0, 120] and d["description"].startswith("<p>")
    n = len(up.requests)
    alice.get(f"/api/v1/podcasts/episodes/{e2}")  # stored: not fetched again
    assert len(up.requests) == n


# ---------- progress sync ----------


def test_progress_sync_newest_wins(sapp, sconn, alice, up):
    up.routes[FEED_URL] = httpx.Response(200, content=RSS)
    pid = alice.post("/api/v1/podcasts", json={"feedUrl": FEED_URL}).json()["id"]
    eid = sconn.execute("SELECT id FROM podcast_episodes WHERE guid = 'ep-2'").fetchone()[0]
    phone = alice
    with user_client(sapp, sconn, "alice", kind="web") as web:
        r = phone.put("/api/v1/spoken/progress", json={"items": [
            {"kind": "episode", "id": eid, "positionS": 600, "durationS": 3723, "updatedAt": "2026-11-20T10:00:00Z"}]})
        assert r.json()["items"][0]["applied"]
        # the web player sees it
        got = web.get(f"/api/v1/podcasts/episodes/{eid}").json()["progress"]
        assert got["positionS"] == 600 and not got["played"]
        # a stale write from an offline device is ignored
        r = web.put("/api/v1/spoken/progress", json={"items": [
            {"kind": "episode", "id": eid, "positionS": 30, "updatedAt": "2026-11-20T09:00:00Z"}]})
        assert not r.json()["items"][0]["applied"] and r.json()["items"][0]["progress"]["positionS"] == 600
        r = web.put("/api/v1/spoken/progress", json={"items": [
            {"kind": "episode", "id": eid, "positionS": 900, "updatedAt": "2026-11-20T10:05:00.250Z"}]})
        assert r.json()["items"][0]["applied"]
        assert phone.get("/api/v1/spoken/progress?since=2026-11-20T10:01:00Z").json()["items"][0]["positionS"] == 900
        assert phone.get("/api/v1/spoken/continue").json()["items"][0]["episode"]["id"] == eid
        # near the end -> played; unplayed count drops
        phone.put("/api/v1/spoken/progress", json={"items": [{"kind": "episode", "id": eid, "positionS": 3710}]})
        assert phone.get(f"/api/v1/podcasts/episodes/{eid}").json()["progress"]["played"]
        assert phone.get("/api/v1/podcasts").json()["podcasts"][0]["unplayed"] == 1
        assert [e["title"] for e in phone.get("/api/v1/podcasts/episodes").json()["episodes"]] == ["Episode One"]
        assert phone.post(f"/api/v1/podcasts/{pid}/played").json()["marked"] == 2
        assert phone.get("/api/v1/podcasts").json()["podcasts"][0]["unplayed"] == 0
        # unknown ids are not stored
        r = phone.put("/api/v1/spoken/progress", json={"items": [{"kind": "book", "id": "nope", "positionS": 1}]})
        assert r.json()["items"][0]["applied"] is False
    # another user sees nothing of alice's
    with user_client(sapp, sconn, "bob", admin=False) as bob:
        assert bob.get("/api/v1/spoken/progress").json()["items"] == []
        assert bob.get("/api/v1/podcasts").json()["podcasts"] == []
    # not music: no events, no user_tracks
    assert sconn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


# ---------- audiobooks ----------


def test_audiobook_scan_and_api(sapp, sconn, alice, books_root):
    cfg = sapp.state.ctx.cfg
    res = audiobooks.scan(sconn, cfg.audiobooks_root, cfg.data_dir)
    assert res["added"] == 2
    assert audiobooks.scan(sconn, cfg.audiobooks_root, cfg.data_dir)["unchanged"] == 2
    books = alice.get("/api/v1/audiobooks").json()
    assert books["enabled"] and [b["title"] for b in books["books"]] == ["Long Book", "Second Book"]
    long_id = books["books"][0]["id"]
    d = alice.get(f"/api/v1/audiobooks/{long_id}").json()
    assert d["author"] == "Author A" and d["narrator"] == "Reader R"
    assert [(c["title"], round(c["startS"])) for c in d["chapters"]] == [("Opening", 0), ("The Middle", 1)]
    assert len(d["parts"]) == 1 and d["parts"][0]["contentType"] == "audio/mp4"
    second = alice.get(f"/api/v1/audiobooks/{books['books'][1]['id']}").json()
    # natural order (1, 2, 10), one chapter per part at book-wide offsets
    assert [c["title"] for c in second["chapters"]] == ["Part 1", "Part 2", "Part 10"]
    starts = [p["startS"] for p in second["parts"]]
    assert starts[0] == 0 and 1.9 < starts[1] < 2.2 and 3.9 < starts[2] < 4.3
    assert [round(c["startS"]) for c in second["chapters"]] == [round(s) for s in starts]
    r = alice.get(f"/api/v1/stream/book/{second['id']}/2", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.content) == 10
    assert alice.get(f"/api/v1/stream/book/{second['id']}/9").status_code == 404
    # bookmarks + per-book speed in progress
    bm = alice.post(f"/api/v1/audiobooks/{long_id}/bookmarks", json={"positionS": 1.5, "note": "nice"}).json()
    alice.put("/api/v1/spoken/progress", json={"items": [{"kind": "book", "id": long_id, "positionS": 1.2,
                                                           "durationS": 3, "speed": 1.25}]})
    d = alice.get(f"/api/v1/audiobooks/{long_id}").json()
    assert d["bookmarks"][0]["note"] == "nice" and d["progress"]["speed"] == 1.25
    assert alice.delete(f"/api/v1/audiobooks/{long_id}/bookmarks/{bm['id']}").status_code == 204
    # a vanished folder: missing (positions kept), back again: found
    shutil.move(books_root / "Second Book", books_root.parent / "moved")
    assert audiobooks.scan(sconn, cfg.audiobooks_root, cfg.data_dir)["missing"] == 1
    assert len(alice.get("/api/v1/audiobooks").json()["books"]) == 1
    # the library folder is never written to
    assert sorted(p.name for p in books_root.rglob("*")) == ["Author A - Long Book", "book.m4b"]


def test_audiobooks_off_without_root(tmp_path, conn, cfg):
    assert "skipped" in audiobooks.scan(conn, None, cfg.data_dir)


# ---------- internet radio ----------


def icy_body(metaint: int = 16) -> bytes:
    meta = b"StreamTitle='Artist - Song';".ljust(32, b"\0")
    return b"A" * metaint + bytes([2]) + meta + b"B" * metaint + b"\0" + b"C" * 10


def test_icy_stripper_any_chunking():
    body = icy_body()
    for size in (1, 3, 7, 16, 17, 100):
        titles = []
        s = stations.IcyStripper(16, titles.append)
        out = b"".join(s.feed(body[i:i + size]) for i in range(0, len(body), size))
        assert out == b"A" * 16 + b"B" * 16 + b"C" * 10 and titles == ["Artist - Song"]


def test_station_add_stream_and_now_playing(alice, sconn, up):
    up.routes["https://radio.example.com/listen.pls"] = httpx.Response(
        200, headers={"content-type": "audio/x-scpls"},
        content=b"[playlist]\nNumberOfEntries=1\nFile1=https://radio.example.com/live\n")
    up.routes["https://radio.example.com/live"] = httpx.Response(
        200, headers={"content-type": "audio/mpeg", "icy-metaint": "16", "icy-name": "Example FM",
                      "icy-genre": "jazz,chill", "icy-br": "128"}, content=icy_body())
    r = alice.post("/api/v1/stations", json={"streamUrl": "https://radio.example.com/listen.pls"})
    assert r.status_code == 201, r.text
    st = r.json()
    assert st["name"] == "Example FM" and st["streamUrl"] == "https://radio.example.com/live"
    assert st["tags"] == ["jazz", "chill"] and st["bitrate"] == 128 and st["favourite"]
    r = alice.get(f"/api/v1/stream/station/{st['id']}")
    assert r.status_code == 200 and r.content == b"A" * 16 + b"B" * 16 + b"C" * 10
    assert r.headers["content-type"].startswith("audio/mpeg") and r.headers["cache-control"] == "no-store"
    req = [q for q in up.requests if str(q.url) == "https://radio.example.com/live"][-1]
    assert req.headers["icy-metadata"] == "1"
    assert alice.get(f"/api/v1/stations/{st['id']}/now-playing").json()["title"] == "Artist - Song"
    assert alice.get("/api/v1/stations").json()["stations"][0]["lastPlayedAt"]
    # HLS and non-audio are refused
    up.routes["https://radio.example.com/x.m3u8"] = httpx.Response(
        200, headers={"content-type": "application/vnd.apple.mpegurl"}, content=b"#EXTM3U")
    assert alice.post("/api/v1/stations", json={"streamUrl": "https://radio.example.com/x.m3u8"}).status_code == 422
    up.routes["https://radio.example.com/page"] = httpx.Response(200, headers={"content-type": "text/html"},
                                                                 content=b"<html>")
    assert alice.post("/api/v1/stations", json={"streamUrl": "https://radio.example.com/page"}).status_code == 422
    assert alice.delete(f"/api/v1/stations/{st['id']}").status_code == 204
    assert alice.get("/api/v1/stations").json()["stations"] == []


def test_now_playing_fetched_when_idle(alice, up):
    stations._now.clear()
    up.routes["https://radio.example.com/live"] = httpx.Response(
        200, headers={"content-type": "audio/mpeg", "icy-metaint": "16"}, content=icy_body())
    sid = alice.post("/api/v1/stations", json={"streamUrl": "https://radio.example.com/live", "name": "X",
                                               "probe": False}).json()["id"]
    assert alice.get(f"/api/v1/stations/{sid}/now-playing").json()["title"] == "Artist - Song"


def test_directory_searches(sapp, alice, up):
    up.routes["https://all.api.radio-browser.info/json/stations/search"] = httpx.Response(200, json=[
        {"stationuuid": "u1", "name": "Jazz One", "url_resolved": "https://s1.example.com/jazz", "tags": "jazz",
         "countrycode": "GB", "codec": "MP3", "bitrate": 128, "favicon": "https://s1.example.com/i.png", "hls": 0},
        {"stationuuid": "u2", "name": "HLS", "url_resolved": "https://s2.example.com/a.m3u8", "hls": 1},
        {"stationuuid": "u3", "name": "Late", "url_resolved": "https://s3.example.com/x", "tags": "talk,explicit"},
    ])
    res = alice.get("/api/v1/stations/search?q=jazz").json()["results"]
    assert [s["name"] for s in res] == ["Jazz One", "Late"] and res[1]["explicit"]
    assert alice.get("/api/v1/stations/search").status_code == 422
    up.routes["https://itunes.apple.com/search"] = httpx.Response(200, json={"results": [
        {"collectionName": "Show A", "artistName": "A", "feedUrl": "https://a.example.com/rss",
         "collectionExplicitness": "notExplicit", "artworkUrl600": "https://a.example.com/a.jpg"},
        {"collectionName": "No feed"}]})
    res = alice.get("/api/v1/podcasts/search?q=show").json()
    assert [r["title"] for r in res["results"]] == ["Show A"] and res["results"][0]["source"] == "itunes"
    # Podcast Index auth header when a key is configured
    hdrs = {}

    def pi(req):
        hdrs.update(req.headers)
        return httpx.Response(200, json={"feeds": [{"title": "Show A", "url": "https://a.example.com/rss",
                                                    "explicit": False}]})

    up.routes["https://api.podcastindex.org/api/1.0/search/byterm"] = pi
    out = podcasts.search_podcastindex("x", "KEY", "SECRET", transport=up.transport(), now=1700000000)
    import hashlib

    assert out[0]["source"] == "podcastindex"
    assert hdrs["authorization"] == hashlib.sha1(b"KEYSECRET1700000000").hexdigest()
    assert hdrs["x-auth-key"] == "KEY" and hdrs["x-auth-date"] == "1700000000"


def test_family_mode_hides_explicit(sapp, sconn, alice, up):
    explicit_feed = RSS.replace(b"<itunes:explicit>no</itunes:explicit>", b"<itunes:explicit>yes</itunes:explicit>")
    up.routes[FEED_URL] = httpx.Response(200, content=explicit_feed)
    up.routes["https://clean.example.com/rss"] = httpx.Response(200, content=RSS)
    dirty = alice.post("/api/v1/podcasts", json={"feedUrl": FEED_URL}).json()["id"]
    clean = alice.post("/api/v1/podcasts", json={"feedUrl": "https://clean.example.com/rss"}).json()["id"]
    sid = alice.post("/api/v1/stations", json={"streamUrl": "https://r.example.com/s", "name": "Rude",
                                               "tags": ["explicit"], "probe": False}).json()["id"]
    assert alice.put("/api/v1/me/family", json={"enabled": True}).status_code == 200
    assert [p["id"] for p in alice.get("/api/v1/podcasts").json()["podcasts"]] == [clean]
    assert alice.get(f"/api/v1/podcasts/{dirty}").status_code == 404
    # in the clean show, the explicit episode is hidden too
    eps = alice.get(f"/api/v1/podcasts/{clean}/episodes").json()["episodes"]
    assert [e["title"] for e in eps] == ["Episode Two"]
    e1 = sconn.execute("SELECT id FROM podcast_episodes WHERE podcast_id = ? AND guid = 'ep-1'", (clean,)).fetchone()[0]
    assert alice.get(f"/api/v1/stream/episode/{e1}").status_code == 404
    assert alice.get("/api/v1/stations").json()["stations"] == []
    assert alice.get(f"/api/v1/stream/station/{sid}").status_code == 404
    assert alice.post("/api/v1/cast/sign-spoken", json={"kind": "station", "id": sid}).status_code == 404


def test_cast_signed_spoken_urls(sapp, sconn, alice, up):
    from fastapi.testclient import TestClient

    from homebeat.app.cast_api import cast_app

    up.routes[FEED_URL] = httpx.Response(200, content=RSS)
    alice.post("/api/v1/podcasts", json={"feedUrl": FEED_URL})
    up.routes["https://cdn.example.com/ep2.mp3"] = httpx.Response(200, headers={"content-type": "audio/mpeg"},
                                                                  content=b"MP3DATA")
    up.routes["https://radio.example.com/live"] = httpx.Response(
        200, headers={"content-type": "audio/mpeg", "icy-metaint": "16"}, content=icy_body())
    sid = alice.post("/api/v1/stations", json={"streamUrl": "https://radio.example.com/live", "name": "R",
                                               "probe": False}).json()["id"]
    eid = sconn.execute("SELECT id FROM podcast_episodes WHERE guid = 'ep-2'").fetchone()[0]
    speakers = TestClient(cast_app(sapp.state.ctx))
    ep = alice.post("/api/v1/cast/sign-spoken", json={"kind": "episode", "id": eid}).json()["items"][0]
    path = ep["url"].split(":8090", 1)[1]
    assert speakers.get(path).content == b"MP3DATA" and not ep["live"]
    assert speakers.get(path.replace("sig=", "sig=x")).status_code == 403
    st = alice.post("/api/v1/cast/sign-spoken", json={"kind": "station", "id": sid}).json()["items"][0]
    assert st["live"] and speakers.get(st["url"].split(":8090", 1)[1]).content.startswith(b"A" * 16)
    audiobooks.scan(sconn, sapp.state.ctx.cfg.audiobooks_root, sapp.state.ctx.cfg.data_dir)
    bid = alice.get("/api/v1/audiobooks").json()["books"][1]["id"]
    parts = alice.post("/api/v1/cast/sign-spoken", json={"kind": "book", "id": bid}).json()["items"]
    assert [p["part"] for p in parts] == [0, 1, 2]
    assert speakers.get(parts[1]["url"].split(":8090", 1)[1]).status_code == 200


def test_art_routes(sapp, sconn, alice, up):
    from tests.conftest import png_bytes

    up.routes[FEED_URL] = httpx.Response(200, content=RSS)
    pid = alice.post("/api/v1/podcasts", json={"feedUrl": FEED_URL}).json()["id"]
    up.routes["https://cdn.example.com/show.jpg"] = httpx.Response(200, content=png_bytes())
    r = alice.get(f"/api/v1/art/podcast/{pid}?size=128")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    audiobooks.scan(sconn, sapp.state.ctx.cfg.audiobooks_root, sapp.state.ctx.cfg.data_dir)
    bid = alice.get("/api/v1/audiobooks").json()["books"][0]["id"]
    assert alice.get(f"/api/v1/art/book/{bid}").status_code == 404   # no cover in that book


# ---------- migration ----------


def test_v6_to_v7(tmp_path):
    from tests.test_migration_v5 import make_v4

    path = tmp_path / "homebeat.db"
    conn = make_v4(path)
    assert migrations.migrate_v4_to_v5(conn, db.schema_sql(), make_backup=False)
    assert migrations.migrate_v5_to_v6(conn, db.schema_sql(), make_backup=False)
    assert not migrations._table_exists(conn, "podcasts")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    for t in migrations.V7_TABLES:
        assert migrations._table_exists(conn, t)
    assert not migrations.migrate_v6_to_v7(conn, db.schema_sql())


def test_gzipped_feed_is_decoded_once():
    """Feed servers send gzip when asked (Accept-Encoding). The size-capped read must not leave a
    Content-Encoding header on the response, or reading .content decodes the body a second time."""
    import gzip

    import httpx

    from homebeat.spoken import net

    rss = b'<?xml version="1.0"?><rss version="2.0"><channel><title>T</title></channel></rss>'
    body = gzip.compress(rss)

    def handler(request):
        return httpx.Response(200, content=body, headers={"Content-Encoding": "gzip",
                                                          "Content-Type": "application/rss+xml"})

    with net.client(httpx.MockTransport(handler)) as c:
        r = net.get_limited(c, "https://feeds.example.com/show.xml", 1_000_000)
        assert r.content == rss
        assert "content-encoding" not in r.headers
