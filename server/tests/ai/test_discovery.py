from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import timedelta
from itertools import pairwise
from urllib.parse import parse_qs, urlparse

import httpx
import numpy as np
import pytest
from tests.ai.conftest import NOW

from nasmusic.ai import api, discovery
from nasmusic.ai.common import ensure_default_settings, norm_name
from nasmusic.ai.discovery import USER_AGENT, Discovery, Http, buy_links

# similar-artist graph served by the fake ListenBrainz
SIMILAR = {
    "Radiohead": [("Thom Yorke", 100), ("Portishead", 90), ("Muse", 80), ("Massive Attack", 70), ("Coldplay", 60)],
    "Massive Attack": [("Portishead", 100), ("Tricky", 90), ("Thom Yorke", 50)],
    "Miles Davis": [("John Coltrane", 100), ("Bill Evans", 80), ("Thelonious Monk", 70)],
    "Coldplay": [("Muse", 100), ("Keane", 90)],
}
# which synthetic sound cluster each candidate's previews sound like
SOUNDS = {"Thom Yorke": 0, "Portishead": 0, "Tricky": 0, "Muse": 5, "Keane": 5, "John Coltrane": 3,
          "Bill Evans": 3, "Thelonious Monk": 3, "Radiohead": 0}
ALL = set(SIMILAR) | {n for v in SIMILAR.values() for n, _ in v}


def mbid(name: str) -> str:
    h = hashlib.md5(name.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


NAME_BY_MBID = {mbid(n): n for n in ALL}


class FakeWeb:
    def __init__(self, fail: bool = False):
        self.requests: list[httpx.Request] = []
        self.fail = fail

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        if self.fail:
            raise httpx.ConnectError("offline", request=req)
        url = urlparse(str(req.url))
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        host, path = url.hostname, url.path
        if host == "musicbrainz.org" and path == "/ws/2/artist":
            name = q["query"].split('"')[1]
            hits = [{"id": mbid(n), "name": n, "score": 100} for n in ALL if norm_name(n) == norm_name(name)]
            return httpx.Response(200, json={"artists": hits})
        if host == "labs.api.listenbrainz.org":
            name = NAME_BY_MBID.get(q["artist_mbids"])
            return httpx.Response(200, json=[{"artist_mbid": mbid(n), "name": n, "score": s}
                                             for n, s in SIMILAR.get(name, [])])
        if host == "api.listenbrainz.org" and "top-release-groups-for-artist" in path:
            name = NAME_BY_MBID[path.rsplit("/", 1)[-1]]
            titles = ["Album 0-0", "Kid A"] if name == "Radiohead" else [f"{name} Album A", f"{name} Album B"]
            return httpx.Response(200, json=[
                {"release_group_mbid": mbid(name + t), "release_group": {"name": t, "date": "2001-05-01",
                                                                          "type": "Album"}} for t in titles])
        if host == "api.deezer.com" and path == "/search/album":
            artist = q["q"].split('"')[1]
            album = q["q"].split('"')[3]
            return httpx.Response(200, json={"data": [{"id": 1000 + len(artist), "title": album,
                                                       "cover_medium": "https://cdn.test/c.jpg",
                                                       "artist": {"name": artist}}]})
        if host == "api.deezer.com" and path.startswith("/album/"):
            return httpx.Response(200, json={"data": [
                {"id": 77, "preview": f"https://cdn.test/{self._artist_for(path)}/1.mp3"},
                {"id": 78, "preview": f"https://cdn.test/{self._artist_for(path)}/2.mp3"}]})
        if host == "cdn.test":
            return httpx.Response(200, content=path.split("/")[1].encode())
        if host == "ws.audioscrobbler.com":
            assert q["api_key"] == "k3y"
            return httpx.Response(200, json={"similarartists": {"artist": [
                {"name": "Thelonious Monk", "mbid": mbid("Thelonious Monk"), "match": "0.9"}]}})
        return httpx.Response(404)

    def _artist_for(self, path: str) -> str:
        # Deezer album ids above encode len(artist); map back through the last search request
        last = next(r for r in reversed(self.requests) if r.url.path == "/search/album")
        return parse_qs(urlparse(str(last.url)).query)["q"][0].split('"')[1].replace(" ", "_")


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def setup_taste(conn, synth):
    """Owned: Radiohead (cluster 0), Massive Attack (cluster 0), Miles Davis (cluster 3); likes in 0 and 3."""
    renames = {synth.by_cluster(0)[0]["artist_id"]: "Radiohead", synth.by_cluster(0)[8]["artist_id"]: "Massive Attack",
               synth.by_cluster(3)[0]["artist_id"]: "Miles Davis"}
    for aid, name in renames.items():
        conn.execute("UPDATE artists SET name = ?, sort_name = ? WHERE id = ?", (name, name, aid))
    for t in synth.tracks:
        if t["artist_id"] in renames:
            for d in (10, 20, 30):
                synth.play(t, NOW - timedelta(days=d))
    for t in synth.by_cluster(5)[:20]:
        synth.skip(t, NOW - timedelta(days=5))
    # answered before
    conn.execute("INSERT INTO suggestions (id, artist, album, reason, status, created_at, release_group_mbid) "
                 "VALUES ('s1', 'Coldplay', 'Coldplay Album A', 'x', 'not_interested', '2026-01-01T00:00:00Z', ?)",
                 (mbid("ColdplayColdplay Album A"),))
    conn.execute("INSERT INTO suggestions (id, artist, album, reason, status, created_at) "
                 "VALUES ('s2', 'Tricky', 'Tricky Album A', 'x', 'have', '2026-01-01T00:00:00Z')")
    ensure_default_settings(conn)


def make(conn, cfg, synth, web=None, clock=None, **kw):
    web = web or FakeWeb()
    clock = clock or FakeClock()
    http = Http(httpx.Client(transport=httpx.MockTransport(web)), sleep=clock.sleep, clock=clock.clock)
    rng = np.random.default_rng(11)

    def embed(audio: bytes) -> np.ndarray:
        name = audio.decode().replace("_", " ")
        return synth.centres[SOUNDS.get(name, 1)] + rng.normal(size=synth.centres.shape[1]) * 0.5
    return Discovery(conn, cfg, http=http, embed=embed, **kw), web, clock


def suggestions(conn):
    return conn.execute("SELECT artist, album, score, reason, buy_links, preview_url, cover_url, status "
                        "FROM suggestions WHERE status = 'new' ORDER BY score DESC").fetchall()


def test_discovery_scoring_and_rules(conn, cfg, synth):
    setup_taste(conn, synth)
    d, web, clock = make(conn, cfg, synth)
    n = d.run()
    rows = suggestions(conn)
    assert n == len(rows) and 3 <= n <= 10
    got = {(r[0], r[1]) for r in rows}
    assert ("Radiohead", "Album 0-0") not in got            # owned (matched by normalised name)
    assert ("Tricky", "Tricky Album A") not in got           # answered before
    assert not any(r[0] == "Coldplay" for r in rows)         # "not interested" artist
    assert len({r[0] for r in rows}) == len(rows)            # one album per artist
    score = {r[0]: r[2] for r in rows}
    # sound check: Portishead (sounds like your favourites) beats Muse (sounds like what you skip,
    # and is a close neighbour of a "not interested" artist)
    assert "Portishead" in score
    assert score["Portishead"] > score.get("Muse", -1)
    for artist, album, _, reason, links, preview, cover, _ in rows:
        assert reason
        names = [lk["name"] for lk in json.loads(links)]
        assert names == ["Bandcamp", "Qobuz"]
        assert preview is None or preview.startswith("https://api.deezer.com/track/")
        assert cover and cover.startswith("https://coverartarchive.org/release-group/")
    # politeness: our User-Agent everywhere, no personal contact details, MusicBrainz <= 1 req/s
    assert all(r.headers["User-Agent"] == USER_AGENT for r in web.requests)
    assert "@" not in USER_AGENT
    mb_reqs = [r for r in web.requests if r.url.host == "musicbrainz.org"]
    assert mb_reqs, "names were resolved through MusicBrainz"
    assert clock.sleeps, "requests to MusicBrainz were spaced out"
    # previews never touch the disk
    files = [p.relative_to(cfg.data_dir).as_posix() for p in cfg.data_dir.rglob("*") if p.is_file()]
    assert files == ["cache/discovery.json"]
    # second run: everything already suggested is skipped, MBIDs come from the cache
    d2, web2, _ = make(conn, cfg, synth)
    d2.run()
    again = Counter((r[0], r[1]) for r in conn.execute("SELECT artist, album FROM suggestions"))
    assert max(again.values()) == 1
    assert not any(r.url.host == "musicbrainz.org" and r.url.path == "/ws/2/artist" for r in web2.requests
                   if "Radiohead" in str(r.url))


def test_musicbrainz_rate_limit():
    clock = FakeClock()
    stamps = []

    def handler(req):
        stamps.append((req.url.host, clock.t))
        return httpx.Response(200, json={})
    http = Http(httpx.Client(transport=httpx.MockTransport(handler)), sleep=clock.sleep, clock=clock.clock)
    for _ in range(4):
        http.get("https://musicbrainz.org/ws/2/artist", {"query": "x"})
        http.get("https://api.deezer.com/search/album", {"q": "x"})
    mb = [t for h, t in stamps if h == "musicbrainz.org"]
    assert all(b - a >= 1.0 for a, b in pairwise(mb))


def test_max_two_per_cluster(conn, cfg, synth, monkeypatch):
    setup_taste(conn, synth)
    d, _, _ = make(conn, cfg, synth)
    captured = []
    monkeypatch.setattr(Discovery, "write", lambda self, picked: captured.extend(picked) or len(picked))
    d.run()
    assert captured
    assert max(Counter(a.cand.cluster for a in captured).values()) <= 2
    conn.execute("UPDATE settings SET value = '1' WHERE key = 'ai.discovery.max_per_cluster'")
    captured.clear()
    make(conn, cfg, synth)[0].run()
    assert max(Counter(a.cand.cluster for a in captured).values()) == 1


def test_lastfm_used_when_key_configured(conn, cfg, synth):
    from dataclasses import replace
    setup_taste(conn, synth)
    d, web, _ = make(conn, replace(cfg, lastfm_api_key="k3y"), synth)
    d.run()
    assert any(r.url.host == "ws.audioscrobbler.com" for r in web.requests)
    d, web, _ = make(conn, cfg, synth)
    d.run()
    assert not any(r.url.host == "ws.audioscrobbler.com" for r in web.requests)


def test_offline_and_empty_library_are_graceful(conn, cfg, synth, monkeypatch):
    setup_taste(conn, synth)
    d, _, _ = make(conn, cfg, synth, web=FakeWeb(fail=True))
    assert d.run() == 0
    # the api entry point never raises, whatever happens inside
    monkeypatch.setattr(discovery, "run", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert api.run_weekly_discovery(conn, cfg) == 0


def test_empty_library(conn, cfg):
    d = Discovery(conn, cfg, http=Http(httpx.Client(transport=httpx.MockTransport(FakeWeb()))))
    assert d.run() == 0


@pytest.mark.parametrize("a,b", [("The Beatles", "beatles"), ("Sigur Rós", "sigur ros"),
                                 ("Simon & Garfunkel", "simon and garfunkel"),
                                 ("OK Computer (Deluxe Edition)", "ok computer"),
                                 ("AC/DC", "ac dc")])
def test_norm_name(a, b):
    assert norm_name(a) == b


def test_buy_links():
    links = buy_links("Sigur Rós", "Ágætis byrjun")
    assert links[0]["url"].startswith("https://bandcamp.com/search?q=Sigur+R")
    assert links[1]["url"].startswith("https://www.qobuz.com/gb-en/search/albums/Sigur%20R")
