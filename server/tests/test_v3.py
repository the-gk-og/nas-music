"""v3 phase 1 (docs/v3-ux.md): global search + online catalogue (Deezer + MusicBrainz, mocked), catalogue
samples/covers, To get (incl. auto-got on scan and suggestions), pins, Home, For You, radio, queue
source/replace, ?token= on stream/art, appearance settings."""
import io
import logging
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from nasmusic import catalog
from nasmusic.ai import api as ai
from nasmusic.catalog import Catalog
from tests.conftest import make_flac, track_ids, user_client

API = "/api/v1"
MP3 = b"ID3" + bytes(range(256)) * 20


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (900, 900), (5, 80, 160)).save(buf, "JPEG")
    return buf.getvalue()


class Upstream:
    """Deezer + MusicBrainz + CDNs, recording every request."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.down:
            raise httpx.ConnectError("offline")
        host, path = request.url.host, request.url.path
        cover = {"cover_xl": "https://e-cdns-images.dzcdn.net/c.jpg"}
        if host == "api.deezer.com":
            if path == "/search/album":
                return httpx.Response(200, json={"data": [
                    {"id": 1, "title": "First Album (Remastered)", "artist": {"name": "Artist One"}, **cover},
                    {"id": 2, "title": "New Record", "artist": {"name": "Someone Else"}, **cover}]})
            if path == "/search/track":
                return httpx.Response(200, json={"data": [
                    {"id": 10, "title": "Song 1", "duration": 2, "preview": "https://cdns-preview.dzcdn.net/a.mp3",
                     "artist": {"name": "Artist One"}, "album": {"title": "First Album", **cover}},
                    {"id": 11, "title": "Fresh Tune", "duration": 200, "preview": "https://cdns-preview.dzcdn.net/b.mp3",
                     "artist": {"name": "Someone Else"}, "album": {"title": "New Record", **cover}}]})
            if path == "/search/artist":
                return httpx.Response(200, json={"data": [{"id": 20, "name": "The Artist One"},
                                                          {"id": 21, "name": "Someone Else", "picture_xl": None}]})
            if path == "/album/2":
                return httpx.Response(200, json={"id": 2, "title": "New Record", "tracks": {"data": [
                    {"id": 30, "preview": ""}, {"id": 31, "preview": "https://cdns-preview.dzcdn.net/c.mp3"}]}})
            if path == "/track/11":
                return httpx.Response(200, json={"id": 11, "preview": "https://cdns-preview.dzcdn.net/b.mp3"})
            if path == "/track/99":
                return httpx.Response(200, json={"id": 99, "title": "Looked Up", "duration": 100,
                                                 "artist": {"name": "Remote"},
                                                 "album": {"title": "Remote LP", **cover}, "release_date": "2020-01-02"})
            return httpx.Response(200, json={"error": {"type": "DataException", "code": 800}})
        if host == "musicbrainz.org":
            assert "nas-music" in request.headers["user-agent"]
            return httpx.Response(200, json={"release-groups": [
                {"id": "rg-new", "title": "New Record", "first-release-date": "2024-05-01", "primary-type": "Album",
                 "artist-credit": [{"name": "Someone Else"}]},
                {"id": "rg-rare", "title": "Rare One", "primary-type": "Album", "artist-credit": [{"name": "Obscure"}]}]})
        if host.endswith("dzcdn.net") and path.endswith(".mp3"):
            return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})
        if host.endswith("dzcdn.net") or host == "coverartarchive.org":
            return httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg"})
        return httpx.Response(404)


@pytest.fixture
def up(app):
    u = Upstream()
    t = httpx.MockTransport(u)
    app.state.ctx.catalog = Catalog(t)
    app.state.ctx.catalog.mb_limit.interval = 0  # no 1 s waits in tests
    app.state.ctx.http_transport = t
    return u


# ---------- matching ----------


def test_normalisation():
    n = catalog.norm
    assert n("The Beatles") == n("beatles") == "beatles"
    assert n("Help! (Remastered 2009)") == n("help") == "help"
    assert n("Song - 2011 Remaster") == "song" and n("Tune feat. Someone") == "tune"
    assert n("Beyoncé & Jay") == "beyonce and jay"
    assert catalog.match_key("Artist One", "First Album [Deluxe]") == catalog.match_key("artist one", "First Album")


def test_rate_limiter():
    lim = catalog.MinInterval(10)
    assert lim.wait(0) is True
    assert lim.wait(0.01) is False  # would have to wait ~10 s


# ---------- search ----------


def test_search_groups_and_not_in_library(client, conn, up):
    client.post(f"{API}/playlists", json={"name": "Song favourites"})
    client.post(f"{API}/queues", json={"name": "Search: song", "trackIds": [track_ids(conn)["Song 1"]]})
    r = client.get(f"{API}/search", params={"q": "song"}).json()
    assert {t["title"] for t in r["tracks"]} >= {"Song 1", "Song 2"}
    assert [p["name"] for p in r["playlists"]] == ["Song favourites"]
    assert [q["name"] for q in r["queues"]] == ["Search: song"]
    assert r["top"]["type"] == "track" and r["catalogError"] is None
    cat = {i["id"]: i for i in r["catalog"]}
    # Owned things (First Album, Song 1, The Artist One) are not "Not in your library".
    assert set(cat) == {"dz:album:2", "mb:rg:rg-rare", "dz:track:11", "dz:artist:21"}
    assert cat["dz:album:2"]["mbid"] == "rg-new" and cat["dz:album:2"]["year"] == 2024  # merged with MusicBrainz
    assert cat["dz:album:2"]["hasPreview"] and cat["dz:album:2"]["artUrl"] == "catalog/dz:album:2/cover"
    assert cat["mb:rg:rg-rare"]["hasPreview"] is False and cat["mb:rg:rg-rare"]["owned"] is False
    assert [l["name"] for l in cat["dz:album:2"]["links"]] == ["Bandcamp", "Qobuz"]
    # scope=catalog shows owned items too, flagged.
    r = client.get(f"{API}/search", params={"q": "song", "scope": "catalog"}).json()
    owned = {i["id"]: i for i in r["catalog"] if i["owned"]}
    assert set(owned) == {"dz:album:1", "dz:track:10", "dz:artist:20"}
    assert owned["dz:album:1"]["ownedId"] == conn.execute("SELECT id FROM albums WHERE title='First Album'").fetchone()[0]
    assert r["tracks"] == [] and r["playlists"] == []


def test_search_is_cached_and_library_scope_is_offline(client, up):
    client.get(f"{API}/search", params={"q": "record"})
    n = len(up.calls)
    assert n == 4  # 3 Deezer searches + 1 MusicBrainz
    client.get(f"{API}/search", params={"q": "  RECORD "})
    assert len(up.calls) == n
    client.get(f"{API}/search", params={"q": "other", "scope": "library"})
    assert len(up.calls) == n


def test_search_survives_catalogue_outage(client, up):
    up.down = True
    r = client.get(f"{API}/search", params={"q": "song"}).json()
    assert r["catalog"] == [] and r["catalogError"] == "online catalogue unavailable"
    assert r["tracks"]  # library still answers


def test_catalog_preview_and_cover(client, up, bob):
    client.get(f"{API}/search", params={"q": "record"})
    r = client.get(f"{API}/catalog/dz:album:2/preview")
    assert r.status_code == 200 and r.content == MP3 and r.headers["cache-control"] == "no-store"
    assert any(str(c.url).endswith("/c.mp3") for c in up.calls)  # first track WITH a sample
    assert client.get(f"{API}/catalog/dz:track:11/preview").content == MP3
    assert client.get(f"{API}/catalog/mb:rg:rg-rare/preview").status_code == 404
    c = client.get(f"{API}/catalog/dz:album:2/cover", params={"size": 300})
    assert c.status_code == 200 and c.headers["content-type"] == "image/jpeg"
    assert client.get(f"{API}/catalog/mb:rg:rg-rare/cover").status_code == 200  # Cover Art Archive
    assert client.get(f"{API}/catalog/nope/cover").status_code == 404
    # Deezer ids never searched are looked up once and cached.
    assert client.get(f"{API}/catalog/dz:track:99/cover").status_code == 200
    up.down = True
    assert bob.get(f"{API}/catalog/dz:track:99/cover").status_code == 200  # disk cache
    assert bob.get(f"{API}/catalog/dz:track:11/preview").status_code == 502


# ---------- To get ----------


def test_toget_crud_and_dedupe(client, bob, conn, up):
    client.get(f"{API}/search", params={"q": "record"})
    a = client.post(f"{API}/toget", json={"catalogId": "dz:album:2", "note": "vinyl?"}).json()
    assert (a["type"], a["title"], a["artist"], a["status"], a["source"]) == ("album", "New Record", "Someone Else",
                                                                              "want", "search")
    assert a["mbid"] == "rg-new" and a["artUrl"] == "catalog/dz:album:2/cover" and a["hasPreview"]
    again = client.post(f"{API}/toget", json={"type": "album", "title": "new record", "artist": "SOMEONE ELSE"}).json()
    assert again["id"] == a["id"]  # de-duplicated by normalised names
    assert client.post(f"{API}/toget", json={"catalogId": "dz:artist:21"}).status_code == 422
    assert client.post(f"{API}/toget", json={"type": "album"}).status_code == 422
    t = client.post(f"{API}/toget", json={"type": "track", "title": "Fresh Tune", "artist": "Someone Else"}).json()
    assert t["source"] == "manual" and t["artUrl"] is None
    # Search marks what's already on the list.
    r = client.get(f"{API}/search", params={"q": "record"}).json()
    assert {i["id"]: i["toGet"] for i in r["catalog"]}["dz:album:2"] == {"id": a["id"], "status": "want"}
    p = client.patch(f"{API}/toget/{a['id']}", json={"status": "dismissed"}).json()
    assert p["status"] == "dismissed" and p["note"] == "vinyl?"
    assert [x["id"] for x in client.get(f"{API}/toget").json()] == [t["id"]]
    assert len(client.get(f"{API}/toget", params={"status": "all"}).json()) == 2
    back = client.post(f"{API}/toget", json={"catalogId": "dz:album:2"}).json()
    assert back["id"] == a["id"] and back["status"] == "want"  # re-adding revives it
    assert client.patch(f"{API}/toget/{a['id']}", json={"note": None}).json()["note"] is None
    # Per user.
    assert bob.get(f"{API}/toget").json() == []
    assert bob.patch(f"{API}/toget/{a['id']}", json={"status": "got"}).status_code == 404
    assert bob.delete(f"{API}/toget/{a['id']}").status_code == 404
    assert client.delete(f"{API}/toget/{a['id']}").status_code == 204


def test_toget_owned_is_got_and_scan_marks_got(client, conn, app, base_flac, cfg):
    owned = client.post(f"{API}/toget", json={"type": "album", "title": "First Album", "artist": "Artist One"}).json()
    assert owned["status"] == "got" and owned["ownedId"]
    want = client.post(f"{API}/toget", json={"type": "album", "title": "New Record", "artist": "Someone Else"}).json()
    song = client.post(f"{API}/toget", json={"type": "track", "title": "Fresh Tune", "artist": "Someone Else"}).json()
    assert want["status"] == song["status"] == "want"
    make_flac(base_flac, cfg.library_root / "Someone Else" / "New Record" / "01.flac", title="Fresh Tune",
              artist="Someone Else", albumartist="Someone Else", album="New Record (Deluxe)")
    rec = app.state.ctx.jobs.run("scan")
    assert rec["ok"] and rec["result"]["togetGot"] == 2
    got = {x["id"]: x for x in client.get(f"{API}/toget", params={"status": "got"}).json()}
    assert set(got) == {owned["id"], want["id"], song["id"]} and got[want["id"]]["gotAt"]


def test_suggestion_want_goes_to_toget(client, conn):
    conn.execute("INSERT INTO suggestions (id, user_id, artist, album, year, release_group_mbid, cover_url, "
                 "preview_url, reason, created_at) VALUES ('s1', ?, 'Band X', 'LP', 2021, 'rg-lp', "
                 "'https://coverartarchive.org/x', 'https://api.deezer.com/track/11', 'r', 'x')", (client.user_id,))
    conn.execute("INSERT INTO suggestions (id, user_id, artist, album, reason, created_at) "
                 "VALUES ('s2', ?, 'Band Y', 'EP', 'r', 'x')", (client.user_id,))
    client.post(f"{API}/suggestions/s1", json={"status": "want"})
    client.post(f"{API}/suggestions/s2", json={"status": "want"})
    client.post(f"{API}/suggestions/s2", json={"status": "want"})  # idempotent
    items = {x["title"]: x for x in client.get(f"{API}/toget").json()}
    assert set(items) == {"LP", "EP"}
    lp = items["LP"]
    assert (lp["source"], lp["suggestionId"], lp["catalogId"], lp["mbid"], lp["year"]) == (
        "suggestion", "s1", "mb:rg:rg-lp", "rg-lp", 2021)
    assert lp["artUrl"] == "catalog/mb:rg:rg-lp/cover" and lp["hasPreview"]
    assert items["EP"]["catalogId"] == "sg:s2" and items["EP"]["artUrl"] is None


def test_sg_catalog_ids_are_private(client, bob, conn):
    conn.execute("INSERT INTO suggestions (id, user_id, artist, album, cover_url, reason, created_at) "
                 "VALUES ('s9', ?, 'A', 'B', 'https://coverartarchive.org/y', 'r', 'x')", (client.user_id,))
    client.post(f"{API}/suggestions/s9", json={"status": "want"})
    assert bob.get(f"{API}/catalog/sg:s9/cover").status_code == 404


# ---------- pins ----------


def test_pins(client, bob, conn):
    ids = track_ids(conn)
    album = conn.execute("SELECT id FROM albums WHERE title = 'Second'").fetchone()[0]
    artist = conn.execute("SELECT artist_id FROM albums WHERE id = ?", (album,)).fetchone()[0]
    pl = client.post(f"{API}/playlists", json={"name": "Pinned list", "trackIds": [ids["Opening"]]}).json()
    client.post(f"{API}/pins", json={"type": "album", "id": album})
    client.post(f"{API}/pins", json={"type": "playlist", "id": pl["id"]})
    client.post(f"{API}/pins", json={"type": "smart_list", "id": "favourites"})
    client.post(f"{API}/pins", json={"type": "genre", "id": "ROCK"})
    pins = client.post(f"{API}/pins", json={"type": "artist", "id": artist, "position": 0}).json()
    assert [(p["type"], p["id"]) for p in pins] == [("artist", artist), ("album", album), ("playlist", pl["id"]),
                                                     ("smart_list", "favourites"), ("genre", "Rock")]
    assert pins[1]["artUrl"] == f"art/{album}" and pins[2]["artUrl"] == f"art/{album}"
    assert pins[0]["title"] == "The Band" and pins[1]["subtitle"] == "The Band"
    moved = client.post(f"{API}/pins", json={"type": "playlist", "id": pl["id"], "position": 0}).json()
    assert moved[0]["id"] == pl["id"] and len(moved) == 5  # re-pin moves, no duplicate
    assert client.post(f"{API}/pins", json={"type": "album", "id": "nope"}).status_code == 404
    assert bob.post(f"{API}/pins", json={"type": "playlist", "id": pl["id"]}).status_code == 404  # alice's
    assert bob.get(f"{API}/pins").json() == []
    assert client.delete(f"{API}/pins/genre/rock").status_code == 204
    client.delete(f"{API}/playlists/{pl['id']}")  # a pin to something gone is skipped
    assert [p["type"] for p in client.get(f"{API}/pins").json()] == ["artist", "album", "smart_list"]
    assert client.post(f"{API}/pins", json={"type": "planet", "id": "x"}).status_code == 422


# ---------- Home ----------


def _ago(**kw) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(**kw)).isoformat()


def _play(client, tid, at, mix=None):
    client.post(f"{API}/events", json={"events": [{"id": str(uuid.uuid4()), "type": "play", "trackId": tid,
                                                     "at": at, "mixId": mix, "source": "mix" if mix else None}]})


def test_home_shelves(client, conn, app):
    ids = track_ids(conn)
    album1 = conn.execute("SELECT id FROM albums WHERE title = 'First Album'").fetchone()[0]
    album2 = conn.execute("SELECT id FROM albums WHERE title = 'Second'").fetchone()[0]
    h = client.get(f"{API}/home").json()
    assert [s["kind"] for s in h["shelves"]] == ["start_radio"]  # empty shelves hidden
    start = h["shelves"][0]
    assert start["layout"] == "button" and start["items"][0]["seed"] == {"type": "random", "id": None,
                                                                         "title": "Random"}
    conn.execute("INSERT INTO mixes (id, user_id, kind, title, for_date, created_at) "
                 "VALUES ('m1', ?, 'daily', 'Daily Mix', 'd', '2026-11-19T02:00:00Z')", (client.user_id,))
    conn.execute("INSERT INTO mix_tracks VALUES ('m1', 0, ?, 1, 'r')", (ids["Opening"],))
    _play(client, ids["Song 1"], _ago(hours=2))
    _play(client, ids["Opening"], _ago(hours=1), mix="m1")
    q = client.post(f"{API}/queues", json={"name": "Album: Second", "trackIds": [ids["Opening"], ids["Closing"]],
                                           "source": {"type": "album", "id": album2}}).json()
    client.patch(f"{API}/queues/{q['id']}", json={"positionMs": 30_000})
    client.post(f"{API}/pins", json={"type": "album", "id": album1})
    h = {s["kind"]: s for s in client.get(f"{API}/home").json()["shelves"]}
    assert list(h) == ["start_radio", "recently_played", "continue_listening", "pinned"]
    recent = h["recently_played"]
    assert recent["layout"] == "grid2"
    assert [(i["type"], i["id"]) for i in recent["items"]] == [("album", album2), ("mix", "m1"), ("album", album1)]
    assert recent["items"][1]["mix"]["kind"] == "daily" and recent["items"][0]["playedAt"]
    cont = h["continue_listening"]["items"]
    assert [i["id"] for i in cont] == [q["id"]] and cont[0]["progress"] == {
        "index": 0, "count": 2, "positionMs": 30_000, "queueId": q["id"]}
    assert h["pinned"]["items"][0]["id"] == album1
    # Order + hiding from settings.
    client.put(f"{API}/me/settings", json={"settings": {"home.shelves": ["pinned", "start_radio"],
                                                         "home.hidden": ["continue_listening"]}})
    assert [s["kind"] for s in client.get(f"{API}/home").json()["shelves"]] == [
        "pinned", "start_radio", "recently_played"]


def test_home_devices_shelf(client, conn, app):
    ids = track_ids(conn)
    from nasmusic import auth

    did, tok = auth.create_device(conn, client.user_id, "Living room PC", "desktop")
    with client.websocket_connect(f"{API}/connect", headers={"Authorization": f"Bearer {tok}"}) as ws:
        ws.send_json({"type": "hello", "deviceName": "Living room PC", "kind": "desktop", "canPlay": True})
        while ws.receive_json()["type"] != "welcome":
            pass
        ws.send_json({"type": "transfer", "play": True})
        while ws.receive_json()["type"] != "takeover":
            pass
        ws.send_json({"type": "state", "state": {"trackId": ids["Song 2"], "playing": True}})
        while ws.receive_json()["type"] != "state":
            pass
        h = {s["kind"]: s for s in client.get(f"{API}/home").json()["shelves"]}
        d = h["devices"]["items"]
        assert [i["id"] for i in d] == [did]  # the caller's own device isn't listed
        assert d[0]["subtitle"] == "Playing Song 2" and d[0]["device"]["isActive"] is True
        assert d[0]["device"]["nowPlaying"]["id"] == ids["Song 2"]


# ---------- For You ----------


def test_foryou(client, conn, monkeypatch):
    ids = track_ids(conn)
    uid = client.user_id
    assert client.get(f"{API}/foryou").json() == {"shelves": []}
    conn.execute("INSERT INTO mixes (id, user_id, kind, title, for_date, created_at) VALUES "
                 "('mc', ?, 'context', 'Gym', 'd', 'x'), ('md', ?, 'daily', 'Daily Mix', 'd', 'x')", (uid, uid))
    conn.execute("INSERT INTO mix_tracks VALUES ('mc', 0, ?, 1, 'r'), ('md', 0, ?, 1, 'r')",
                 (ids["Song 1"], ids["Closing"]))
    client.put(f"{API}/favourites/{ids['Opening']}")
    _play(client, ids["Song 1"], "2026-11-19T08:00:00Z")
    conn.execute("INSERT INTO suggestions (id, user_id, artist, album, reason, status, created_at) VALUES "
                 "('s1', ?, 'A', 'B', 'r', 'new', 'x'), ('s2', ?, 'C', 'D', 'r', 'not_interested', 'x')", (uid, uid))
    client.post(f"{API}/toget", json={"type": "album", "title": "Wish", "artist": "W"})

    def byp(conn_, cfg, user_id, limit):
        assert user_id == uid
        album = conn_.execute("SELECT id FROM albums WHERE title = 'Second'").fetchone()[0]
        return [{"seed": {"type": "album", "id": album, "title": "Second"}, "trackIds": [ids["Song 2"], "gone"],
                 "albumIds": [album]}]

    monkeypatch.setattr(ai, "because_you_played", byp, raising=False)
    s = {x["kind"]: x for x in client.get(f"{API}/foryou").json()["shelves"]}
    assert list(s) == ["mixes", "radio", "because_you_played", "suggestions", "to_get"]
    assert {i["mix"]["kind"] for i in s["mixes"]["items"]} == {"daily", "context"}
    radio = s["radio"]["items"]
    assert {i["seed"]["type"] for i in radio} == {"artist", "track"}
    assert any(i["title"] == "Opening radio" for i in radio) and all(i["type"] == "radio" for i in radio)
    b = s["because_you_played"]
    assert b["title"] == "Because you played Second" and [i["type"] for i in b["items"]] == ["track", "album"]
    assert [i["id"] for i in s["suggestions"]["items"]] == ["s1"]
    assert s["to_get"]["items"][0]["toget"]["title"] == "Wish"
    assert not {"release_radar", "blends", "wrapped"} & set(s)
    client.put(f"{API}/me/settings", json={"settings": {"foryou.hidden": ["radio"]}})
    assert "radio" not in [x["kind"] for x in client.get(f"{API}/foryou").json()["shelves"]]


# ---------- radio ----------


def test_radio_needs_ai(client, conn):
    r = client.post(f"{API}/radio", json={"seed": {"type": "random"}})
    assert r.status_code == 503


def test_radio_with_engine(client, conn, monkeypatch, bob):
    ids = list(track_ids(conn).values())
    seen = {}

    def start(conn_, cfg, user_id, seed, n):
        seen["start"] = (user_id, seed, n)
        return ids[:3]

    def more(conn_, cfg, user_id, queue_id, seed, exclude, n):
        seen["more"] = (queue_id, sorted(exclude), n)
        return ids[2:5]  # one repeat: dropped

    monkeypatch.setattr(ai, "radio_start", start, raising=False)
    monkeypatch.setattr(ai, "radio_more", more, raising=False)
    album = conn.execute("SELECT id FROM albums WHERE title = 'Second'").fetchone()[0]
    q = client.post(f"{API}/radio", json={"seed": {"type": "album", "id": album}}).json()
    assert q["name"] == "Second radio" and [t["id"] for t in q["tracks"]] == ids[:3]
    assert q["radio"] == {"type": "album", "id": album, "title": "Second"}
    assert q["source"] == {"type": "radio", "id": album}
    assert seen["start"] == (client.user_id, {"type": "album", "id": album, "title": "Second"}, 25)
    q2 = client.post(f"{API}/radio/{q['id']}/more", json={"count": 10}).json()
    assert [t["id"] for t in q2["tracks"]] == ids[:5]
    assert seen["more"] == (q["id"], sorted(ids[:3]), 10)
    r = client.post(f"{API}/radio", json={"seed": {"type": "random"}}).json()
    assert r["name"] == "Random radio"
    assert client.post(f"{API}/radio", json={"seed": {"type": "track", "id": "nope"}}).status_code == 404
    assert client.post(f"{API}/radio", json={"seed": {"type": "track"}}).status_code == 404
    plain = client.post(f"{API}/queues", json={"name": "Q", "trackIds": ids[:1]}).json()
    assert client.post(f"{API}/radio/{plain['id']}/more").status_code == 404
    assert bob.post(f"{API}/radio/{q['id']}/more").status_code == 404
    # A radio is listed in Home's recently played as its queue.
    recent = {s["kind"]: s for s in client.get(f"{API}/home").json()["shelves"]}["recently_played"]["items"]
    assert any(i["type"] == "queue" and i.get("seed", {}).get("type") == "album" for i in recent)


# ---------- queue replace, ?token=, settings, admin UI ----------


def test_replace_queue_tracks(client, conn):
    ids = list(track_ids(conn).values())
    q = client.post(f"{API}/queues", json={"name": "Q", "trackIds": ids[:4], "startIndex": 2}).json()
    cur = q["tracks"][2]["id"]
    r = client.put(f"{API}/queues/{q['id']}/tracks", json={"trackIds": list(reversed(ids[:4]))}).json()
    assert r["tracks"][r["currentIndex"]]["id"] == cur  # follows the current track
    r = client.put(f"{API}/queues/{q['id']}/tracks", json={"trackIds": [], "currentIndex": None}).json()
    assert r["trackCount"] == 0 and r["currentIndex"] == 0
    assert client.put(f"{API}/queues/{q['id']}/tracks", json={"trackIds": ids[:2], "currentIndex": 5}).status_code == 422


def test_query_token_only_for_stream_and_art(client, conn, caplog):
    tid = track_ids(conn)["Song 1"]
    album = conn.execute("SELECT album_id FROM tracks WHERE id = ?", (tid,)).fetchone()[0]
    with TestClient(client.app) as anon:
        assert anon.get(f"{API}/stream/{tid}?format=flac&token={client.token}").status_code == 200
        assert anon.get(f"{API}/art/{album}?token={client.token}").status_code == 200
        assert anon.get(f"{API}/stream/{tid}?format=flac&token=wrong").status_code == 401
        assert anon.get(f"{API}/me?token={client.token}").status_code == 401  # not for the rest of the API
    from nasmusic.app import _RedactTokens

    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                            ("1.2.3.4", "GET", f"/api/v1/stream/x?format=opus&token={client.token}", "1.1", 200), None)
    _RedactTokens().filter(rec)
    assert client.token not in rec.getMessage() and "token=REDACTED" in rec.getMessage()


def test_appearance_settings_validated(client):
    ok = {"appearance.animations": "expressive", "appearance.defaultView": "list", "appearance.albumPage": "simple",
          "appearance.accent": "#4ADE80", "home.shelves": ["pinned"], "library.view.albums": "grid",
          f"device.{client.device_id}.library.view.albums": "list", f"device.{client.device_id}.appearance.animations": "off"}
    assert client.put(f"{API}/me/settings", json={"settings": ok}).status_code == 200
    for bad in ({"appearance.animations": "wild"}, {"appearance.accent": "rainbow"}, {"home.hidden": "pinned"},
                {"library.view.songs": "carousel"}, {f"device.{client.device_id}.appearance.albumPage": "huge"}):
        assert client.put(f"{API}/me/settings", json={"settings": bad}).status_code == 422, bad
    assert client.put(f"{API}/me/settings", json={"settings": {"appearance.accent": "purple"}}).status_code == 200


def test_admin_ui_make_and_remove_admin(app, conn):
    from tests.conftest import make_user

    make_user(conn, "alice", admin=True)
    bob = make_user(conn, "bob")
    with TestClient(app) as c:
        c.post("/admin/login", data={"username": "alice", "password": "password123"})
        page = c.get("/admin/users").text
        assert "Make admin" in page and "Remove admin" in page
        c.post(f"/admin/users/{bob.id}", data={"action": "make_admin"})
        assert conn.execute("SELECT is_admin FROM users WHERE id = ?", (bob.id,)).fetchone()[0] == 1
        c.post(f"/admin/users/{bob.id}", data={"action": "remove_admin"})
        assert conn.execute("SELECT is_admin FROM users WHERE id = ?", (bob.id,)).fetchone()[0] == 0


def test_isolation_of_v3_state(client, bob, conn, up):
    album = conn.execute("SELECT id FROM albums WHERE title = 'Second'").fetchone()[0]
    client.post(f"{API}/pins", json={"type": "album", "id": album})
    client.post(f"{API}/toget", json={"type": "album", "title": "Wish", "artist": "W"})
    client.post(f"{API}/queues", json={"name": "Album", "trackIds": [track_ids(conn)["Opening"]],
                                       "source": {"type": "album", "id": album}})
    hb = {s["kind"] for s in bob.get(f"{API}/home").json()["shelves"]}
    assert hb == {"start_radio"}
    assert bob.get(f"{API}/foryou").json() == {"shelves": []}
    assert bob.get(f"{API}/search", params={"q": "album", "scope": "library"}).json()["queues"] == []


def test_user_client_helper_still_works(app, conn):
    with user_client(app, conn, "zed", admin=False) as c:
        assert c.get(f"{API}/home").status_code == 200
