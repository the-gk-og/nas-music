"""Spotify / Last.fm / M3U imports and M3U export (homebeat.app.imports)."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from homebeat.app import imports
from tests.conftest import track_ids


@pytest.fixture
def long_tracks(scanned):
    scanned.execute("UPDATE tracks SET duration_s = 200")
    return track_ids(scanned)


def _ts(days_ago: float) -> datetime:
    return (datetime.now(UTC) - timedelta(days=days_ago)).replace(microsecond=0)


def _sp(title, artist, when: datetime, ms: int) -> dict:
    return {"ts": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "ms_played": ms, "master_metadata_track_name": title,
            "master_metadata_album_artist_name": artist, "master_metadata_album_album_name": "x",
            "spotify_track_uri": "spotify:track:abc", "skipped": False}


def _plays(conn, uid, tid) -> int:
    r = conn.execute("SELECT play_count FROM user_tracks WHERE user_id = ? AND track_id = ?", (uid, tid)).fetchone()
    return r[0] if r else 0


def _toget(conn, uid) -> list[tuple[str, str]]:
    return sorted((r[0], r[1]) for r in conn.execute("SELECT artist, title FROM toget WHERE user_id = ?", (uid,)))


def test_matcher(long_tracks, scanned):
    m = imports.Matcher(scanned)
    t = long_tracks
    assert m.match("Artist One", "Song 1") == t["Song 1"]
    assert m.match("artist one, Somebody Else", "Song 1 - Remastered 2011") == t["Song 1"]
    assert m.match("The Band", "Opening (feat. X)") == t["Opening"]
    assert m.match("X", "whatever", "11111111-1111-1111-1111-111111111111") == t["Opening"]
    assert m.match("Wrong Artist", "Closing", duration_s=201) == t["Closing"]    # unique title + same length
    assert m.match("Wrong Artist", "Closing", duration_s=150) is None
    assert m.match("Nobody", "Nothing") is None
    assert m.match_path("/mnt/tank/music/Artist One/First Album/01 Song 1.flac") == t["Song 1"]
    assert m.match_path(r"..\Music\The Band\Second\02 Closing.flac") == t["Closing"]
    assert m.match_path("file:///x/The%20Band/Second/01%20Opening.flac") == t["Opening"]
    assert m.match_path(f"https://nas:8443/api/v1/stream/{t['Song 2']}?format=opus") == t["Song 2"]
    assert m.match_path("https://example.com/a.mp3") is None


def test_spotify_history(client, conn, long_tracks):
    uid, t = client.user_id, long_tracks
    rows = [
        _sp("Song 1", "Artist One", _ts(10), 150_000),
        _sp("Song 1", "Artist One", _ts(9), 10_000),            # skipped after 10 s: not a play
        _sp("Opening", "The Band", _ts(8), 200_000),
        {"ts": _ts(7).isoformat(), "ms_played": 900_000, "master_metadata_track_name": None,
         "episode_name": "A podcast"},                           # not music
    ] + [_sp("Elsewhere", "Not Here", _ts(6 - i), 180_000) for i in range(3)]
    r = client.post("/api/v1/me/import/spotify", params={"name": "Streaming_History_Audio_2024.json",
                                                         "missingMinPlays": 3},
                    content=json.dumps(rows).encode())
    assert r.status_code == 200, r.text
    h = r.json()["history"]
    assert (h["scanned"], h["imported"], h["tooShort"], h["unmatched"]) == (6, 2, 1, 3)
    assert h["topMissing"] == [{"artist": "Not Here", "title": "Elsewhere", "plays": 3}]
    assert r.json()["toGetAdded"] == 1 and _toget(conn, uid) == [("Not Here", "Elsewhere")]
    assert _plays(conn, uid, t["Song 1"]) == 1 and _plays(conn, uid, t["Opening"]) == 1
    # the 30-day "StreamingHistory_music_0.json" format, and re-sending is idempotent
    basic = [{"endTime": _ts(3).strftime("%Y-%m-%d %H:%M"), "artistName": "Artist One", "trackName": "Song 2",
              "msPlayed": 199_000}]
    client.post("/api/v1/me/import/spotify", params={"name": "a.json"}, content=json.dumps(rows).encode())
    r = client.post("/api/v1/me/import/spotify", params={"name": "b.json"}, content=json.dumps(basic).encode())
    assert r.json()["history"]["imported"] == 1
    st = client.get("/api/v1/me/import").json()
    assert st["imported"] == {"listenbrainz": 0, "lastfm": 0, "spotify": 3}
    assert conn.execute("SELECT COUNT(*) FROM scrobble_queue").fetchone()[0] == 0   # never scrobbled back
    r = client.delete("/api/v1/me/import/history/spotify").json()
    assert r["removed"] == 3 and r["imported"]["spotify"] == 0 and _plays(conn, uid, t["Song 1"]) == 0


def _exportify() -> bytes:
    return ("Track URI,Track Name,Artist Name(s),Album Name,Duration (ms)\n"
            "spotify:track:1,Song 2,\"Artist One,Guest\",First Album,200000\n"
            "spotify:track:2,Opening - 2011 Remaster,The Band,Second,200000\n"
            "spotify:track:3,Not In Library,Some Artist,X,180000\n").encode()


def test_spotify_playlists_and_zip(client, conn, long_tracks):
    uid, t = client.user_id, long_tracks
    r = client.post("/api/v1/me/import/spotify", params={"name": "Road_Trip.csv"}, content=_exportify()).json()
    [p] = r["playlists"]
    assert (p["name"], p["total"], p["matched"], p["missing"], p["toGetAdded"]) == ("Road Trip", 3, 2, 1, 1)
    got = client.get(f"/api/v1/playlists/{p['playlistId']}").json()
    assert [x["id"] for x in got["tracks"]] == [t["Song 2"], t["Opening"]]
    assert _toget(conn, uid) == [("Some Artist", "Not In Library")]
    # same file again: no duplicate playlist, no duplicate To-get item
    r = client.post("/api/v1/me/import/spotify", params={"name": "Road_Trip.csv"}, content=_exportify()).json()
    assert r["playlists"][0]["unchanged"] and r["toGetAdded"] == 0
    assert conn.execute("SELECT COUNT(*) FROM playlists WHERE user_id = ?", (uid,)).fetchone()[0] == 1

    # the whole data export as a zip: history + Playlist1.json (+ files that aren't ours)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Spotify Extended Streaming History/Streaming_History_Audio_2023.json",
                   json.dumps([_sp("Closing", "The Band", _ts(20), 120_000)]))
        z.writestr("Spotify Account Data/Playlist1.json", json.dumps({"playlists": [
            {"name": "Chill", "items": [{"track": {"trackName": "Song 1", "artistName": "Artist One"}},
                                        {"track": None, "episode": {"episodeName": "pod"}}]}]}))
        z.writestr("Spotify Account Data/Userdata.json", json.dumps({"username": "x"}))
        z.writestr("ReadMeFirst.pdf", b"%PDF")
    r = client.post("/api/v1/me/import/spotify", params={"name": "my_spotify_data.zip", "addMissing": "false"},
                    content=buf.getvalue())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["history"]["imported"] == 1
    assert [(p["name"], p["matched"]) for p in body["playlists"]] == [("Chill", 1)]
    assert client.post("/api/v1/me/import/spotify", content=b"hello").status_code == 422


def test_lastfm_csv_and_cross_source_dedupe(client, conn, long_tracks):
    uid, t = client.user_id, long_tracks
    when = _ts(5)
    client.post("/api/v1/me/import/spotify", params={"name": "h.json"},
                content=json.dumps([_sp("Song 1", "Artist One", when + timedelta(seconds=150), 150_000)]).encode())
    csv_text = (f"Artist One,First Album,Song 1,{when.strftime('%d %b %Y %H:%M')}\n"          # the same play
                f"Artist One,First Album,Song 2,{(when - timedelta(hours=1)).strftime('%d %b %Y %H:%M')}\n"
                f"Gone,X,Missing Song,{(when - timedelta(hours=2)).strftime('%d %b %Y %H:%M')}\n")
    r = client.post("/api/v1/me/import/lastfm/csv", content=csv_text.encode()).json()
    assert (r["imported"], r["alreadyKnown"], r["unmatched"]) == (1, 1, 1)
    assert _plays(conn, uid, t["Song 1"]) == 1
    # header form with unix timestamps
    r = client.post("/api/v1/me/import/lastfm/csv",
                    content=f"uts,artist,album,track\n{int(_ts(30).timestamp())},The Band,,Closing\n".encode()).json()
    assert r["imported"] == 1
    assert client.post("/api/v1/me/import/lastfm/csv", content=b"a,b\nc,d\n").status_code == 422


def test_lastfm_api(client, conn, long_tracks, monkeypatch):
    uid = client.user_id
    c = client.app.state.ctx
    assert client.post("/api/v1/me/import/lastfm", json={"username": "rj"}).status_code == 503
    monkeypatch.setattr(c.cfg, "lastfm_api_key", "k")
    monkeypatch.setattr(imports, "LASTFM_PAGE_DELAY_S", 0)
    assert client.post("/api/v1/me/import/lastfm", json={}).status_code == 422   # no name, not connected
    base = int(_ts(2).timestamp())

    def page(n):
        if n == 1:
            return [{"name": "Song 1", "artist": {"#text": "Artist One"}, "@attr": {"nowplaying": "true"}},
                    {"name": "Song 1", "artist": {"#text": "Artist One", "mbid": ""}, "date": {"uts": str(base)}},
                    {"name": "Unknown", "artist": {"#text": "Nobody"}, "date": {"uts": str(base - 500)}}]
        return {"name": "Opening", "artist": {"#text": "The Band"}, "mbid": "", "date": {"uts": str(base - 9000)}}

    def handler(request: httpx.Request):
        p = request.url.params
        assert p["method"] == "user.getrecenttracks" and p["user"] == "rj" and p["api_key"] == "k"
        n = int(p["page"])
        return httpx.Response(200, json={"recenttracks": {"track": page(n), "@attr": {"totalPages": "2"}}})

    c.http_transport = httpx.MockTransport(handler)
    st = client.post("/api/v1/me/import/lastfm", params={"wait": "true"}, json={"username": "rj"}).json()
    job = st["lastfm"]
    assert job["state"] == "done", job
    assert (job["scanned"], job["imported"], job["unmatched"], job["pages"]) == (3, 2, 1, 2)
    assert st["imported"]["lastfm"] == 2

    c.http_transport = httpx.MockTransport(lambda r: httpx.Response(200, json={"error": 6, "message": "x"}))
    st = client.post("/api/v1/me/import/lastfm", params={"wait": "true"}, json={"username": "nope"}).json()
    assert st["lastfm"]["state"] == "error" and "no user" in st["lastfm"]["error"]
    assert imports.imported_by_source(conn, uid)["lastfm"] == 2


def test_m3u_import_and_export(client, conn, long_tracks):
    uid, t = client.user_id, long_tracks
    m3u = ("#EXTM3U\n#PLAYLIST:Kitchen\n"
           "#EXTINF:200,Artist One - Song 2\n/volume1/music/Artist One/First Album/02 Song 2.flac\n"
           "#EXTINF:200,The Band - Closing\nC:\\Users\\me\\Music\\elsewhere\\closing.mp3\n"
           "#EXTINF:180,Lost Artist - Lost Song\nLost/lost.mp3\n"
           "The Band/Second/01 Opening.flac\n").encode()
    r = client.post("/api/v1/me/import/m3u", params={"name": "ignored.m3u8"}, content=m3u)
    assert r.status_code == 200, r.text
    p = r.json()
    assert (p["name"], p["matched"], p["missing"], p["toGetAdded"]) == ("Kitchen", 3, 1, 1)
    assert _toget(conn, uid) == [("Lost Artist", "Lost Song")]
    pid = p["playlistId"]
    r = client.get(f"/api/v1/playlists/{pid}/m3u")
    assert r.status_code == 200 and r.headers["content-type"].startswith("audio/x-mpegurl")
    assert "Kitchen.m3u8" in r.headers["content-disposition"]
    lines = r.text.splitlines()
    assert lines[:4] == ["#EXTM3U", "#PLAYLIST:Kitchen", "#EXTINF:200,Artist One - Song 2",
                         "Artist One/First Album/02 Song 2.flac"]
    assert lines[-1] == "The Band/Second/01 Opening.flac"
    urls = client.get(f"/api/v1/playlists/{pid}/m3u", params={"paths": "url"}).text
    assert f"/api/v1/stream/{t['Song 2']}" in urls
    # round trip: the exported file imports to the same tracks (identical -> not created twice)
    again = client.post("/api/v1/me/import/m3u", content=r.content).json()
    assert again["unchanged"] and again["playlistId"] == pid
    assert client.post("/api/v1/me/import/m3u", content=b"#EXTM3U\n").status_code == 422
    assert client.get("/api/v1/playlists/nope/m3u").status_code == 404


def test_bob_cannot_export_alices_playlist(client, bob, long_tracks):
    p = client.post("/api/v1/playlists", json={"name": "Mine", "trackIds": [long_tracks["Song 1"]]}).json()
    assert bob.get(f"/api/v1/playlists/{p['id']}/m3u").status_code == 404


def test_spotify_takes_bigger_uploads_than_other_routes(client, long_tracks):
    big = b"x" * (21 * 1024 * 1024)
    assert client.post("/api/v1/me/import/spotify", content=big).status_code == 422   # read, then refused
    assert client.post("/api/v1/me/import/m3u", content=big).status_code == 413
