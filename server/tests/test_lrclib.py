"""LRCLIB fallback (mocked lrclib.net): lookup order, caching incl. not-found retry, prefetch job, switch."""
from dataclasses import replace

import httpx
import pytest
from mutagen.flac import FLAC

from nasmusic.library.lrclib import Lrclib
from tests.conftest import track_ids

API = "/api/v1"
SYNCED = "[00:01.00]la la\n[00:02.00]la"


class Lrc:
    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.get: dict | None = None          # /api/get answer (None -> 404)
        self.search: list = []
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        assert request.url.host == "lrclib.net"
        if self.status != 200:
            return httpx.Response(self.status)
        if request.url.path == "/api/get":
            return httpx.Response(200, json=self.get) if self.get else httpx.Response(404, json={"code": 404})
        if request.url.path == "/api/search":
            return httpx.Response(200, json=self.search)
        return httpx.Response(404)


@pytest.fixture
def lrc(app):
    m = Lrc()
    client = Lrclib("9.9", httpx.MockTransport(m), min_interval_s=0)
    app.state.ctx.lrclib = client
    app.state.ctx.jobs.lrclib = client
    return m


def test_get_hit_synced_and_cached(client, conn, lrc):
    tid = track_ids(conn)["Song 1"]
    lrc.get = {"id": 7, "trackName": "Song 1", "artistName": "Artist One", "duration": 2, "instrumental": False,
               "plainLyrics": "la la\nla", "syncedLyrics": SYNCED}
    r = client.get(f"{API}/tracks/{tid}/lyrics").json()
    assert r["source"] == "lrclib" and r["synced"] is True and r["sources"] == ["lrclib"]
    assert r["lines"][0] == {"timeMs": 1000, "text": "la la"}
    q = dict(lrc.calls[0].url.params)
    assert q == {"artist_name": "Artist One", "track_name": "Song 1", "album_name": "First Album", "duration": "2"}
    assert lrc.calls[0].headers["user-agent"] == "nas-music/9.9 (https://github.com/the-gk-og/nas-music)"
    client.get(f"{API}/tracks/{tid}/lyrics")
    assert len(lrc.calls) == 1  # cached in the database
    assert conn.execute("SELECT status, lrclib_id FROM lyrics_cache").fetchone()[:] == ("found", 7)
    # The user's edit still wins; lrclib stays selectable.
    client.put(f"{API}/tracks/{tid}/lyrics", json={"raw": "mine"})
    g = client.get(f"{API}/tracks/{tid}/lyrics").json()
    assert g["source"] == "user" and g["sources"] == ["user", "lrclib"]
    assert client.get(f"{API}/tracks/{tid}/lyrics", params={"source": "lrclib"}).json()["synced"] is True


def test_search_fallback_picks_synced_close_duration(client, conn, lrc):
    tid = track_ids(conn)["Song 2"]  # 2 s long
    lrc.search = [
        {"id": 1, "trackName": "Song 2", "artistName": "Artist One", "duration": 9.0, "syncedLyrics": SYNCED},
        {"id": 2, "trackName": "Song 2", "artistName": "Artist One", "duration": 2.4, "plainLyrics": "plain only"},
        {"id": 3, "trackName": "Song 2 (Live)", "artistName": "Artist One", "duration": 2.0, "syncedLyrics": SYNCED},
        {"id": 4, "trackName": "song 2", "artistName": "Artist One feat. X", "duration": 1.5, "syncedLyrics": SYNCED},
        {"id": 5, "trackName": "Song 2", "artistName": "Someone Else", "duration": 2.0, "syncedLyrics": SYNCED},
    ]
    r = client.get(f"{API}/tracks/{tid}/lyrics").json()
    assert [c.url.path for c in lrc.calls] == ["/api/get", "/api/search"]
    assert r["synced"] is True
    assert conn.execute("SELECT lrclib_id FROM lyrics_cache WHERE track_id = ?", (tid,)).fetchone()[0] == 4


def test_not_found_cached_then_retried_after_30_days(client, conn, lrc):
    tid = track_ids(conn)["Opening"]
    assert client.get(f"{API}/tracks/{tid}/lyrics").status_code == 404
    assert len(lrc.calls) == 2  # get + search
    row = conn.execute("SELECT status, retry_after FROM lyrics_cache WHERE track_id = ?", (tid,)).fetchone()
    assert row["status"] == "not_found" and row["retry_after"] > "2026"
    client.get(f"{API}/tracks/{tid}/lyrics")
    assert len(lrc.calls) == 2  # not asked again
    conn.execute("UPDATE lyrics_cache SET retry_after = '2000-01-01T00:00:00Z'")
    lrc.get = {"id": 9, "trackName": "Opening", "artistName": "The Band", "plainLyrics": "now there"}
    r = client.get(f"{API}/tracks/{tid}/lyrics").json()
    assert r["source"] == "lrclib" and r["synced"] is False and r["text"] == "now there"


def test_instrumental_and_errors(client, conn, lrc):
    ids = track_ids(conn)
    lrc.get = {"id": 3, "instrumental": True, "plainLyrics": None, "syncedLyrics": None}
    assert client.get(f"{API}/tracks/{ids['Closing']}/lyrics").status_code == 404
    assert conn.execute("SELECT status FROM lyrics_cache").fetchone()[0] == "instrumental"
    lrc.status = 503  # server trouble: not cached, asked again next time
    assert client.get(f"{API}/tracks/{ids['Song 1']}/lyrics").status_code == 404
    assert conn.execute("SELECT COUNT(*) FROM lyrics_cache WHERE track_id = ?", (ids["Song 1"],)).fetchone()[0] == 0
    lrc.status, lrc.get = 200, {"id": 1, "syncedLyrics": SYNCED}
    assert client.get(f"{API}/tracks/{ids['Song 1']}/lyrics").json()["source"] == "lrclib"


def test_unknown_artist_not_searched(client, conn, lrc):
    assert client.get(f"{API}/tracks/{track_ids(conn)['untagged']}/lyrics").status_code == 404
    assert lrc.calls == []


def test_file_lyrics_mean_no_lrclib(client, conn, cfg, lrc):
    tid = track_ids(conn)["Song 1"]
    path = cfg.library_root / conn.execute("SELECT path FROM tracks WHERE id = ?", (tid,)).fetchone()[0]
    f = FLAC(path)
    f["LYRICS"] = SYNCED
    f.save()
    assert client.get(f"{API}/tracks/{tid}/lyrics").json()["source"] == "embedded"
    assert lrc.calls == []


def test_switched_off(client, conn, app, lrc):
    app.state.ctx.cfg = replace(app.state.ctx.cfg, lrclib_enabled=False)
    lrc.get = {"id": 1, "syncedLyrics": SYNCED}
    assert client.get(f"{API}/tracks/{track_ids(conn)['Song 1']}/lyrics").status_code == 404
    assert lrc.calls == []


def test_prefetch_job(client, conn, app, lrc, cfg):
    ids = track_ids(conn)
    client.put(f"{API}/favourites/{ids['Song 1']}")
    client.put(f"{API}/favourites/{ids['Song 2']}")
    conn.execute("INSERT INTO mixes (id, user_id, kind, title, for_date, created_at) "
                 "VALUES ('m', ?, 'daily', 'D', 'd', 'x')", (client.user_id,))
    conn.execute("INSERT INTO mix_tracks VALUES ('m', 0, ?, 1, 'r')", (ids["Opening"],))
    path = cfg.library_root / conn.execute("SELECT path FROM tracks WHERE id = ?", (ids["Song 2"],)).fetchone()[0]
    f = FLAC(path)
    f["UNSYNCEDLYRICS"] = "in the file"
    f.save()
    lrc.get = {"id": 1, "syncedLyrics": SYNCED}
    jobs = app.state.ctx.jobs
    res = jobs.run("lyrics")["result"]
    index = res.pop("lyricsIndex")  # found lyrics go into the lyrics search index straight away
    assert res == {"candidates": 3, "fetched": 2, "found": 2, "inFile": 1, "unreachable": 0}
    assert index["withLyrics"] >= 2
    assert jobs.run("lyrics")["result"]["candidates"] == 1  # only the in-file track remains a candidate
    jobs.cfg = replace(jobs.cfg, lrclib_enabled=False)
    assert jobs.run("lyrics")["result"] == {"skipped": "LRCLIB is turned off"}


def test_prefetch_stops_when_lrclib_is_down(client, conn, app, lrc):
    ids = track_ids(conn)
    for t in ("Song 1", "Song 2", "Opening", "Closing"):
        client.put(f"{API}/favourites/{ids[t]}")
    lrc.status = 500
    res = app.state.ctx.jobs.run("lyrics")["result"]
    assert res["unreachable"] == 3 and res["fetched"] == 0
    assert conn.execute("SELECT COUNT(*) FROM lyrics_cache").fetchone()[0] == 0


def test_strict_title():
    from nasmusic.library.lrclib import strict_title

    assert strict_title("Song 2") != strict_title("Song 2 (Live)")
    assert strict_title("Café feat. X") == strict_title("cafe")
    assert strict_title("Rock & Roll!") == strict_title("rock and roll")
