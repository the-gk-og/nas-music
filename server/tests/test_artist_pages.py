"""Artist pages (v7): MusicBrainz / Wikidata / Wikipedia / Commons fetch + cache (mocked HTTP), the page
endpoint (top songs, albums, appears-on, similar artists), photo serving and family mode."""
import io
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from PIL import Image

from homebeat.library import artistinfo
from tests.conftest import track_ids

API = "/api/v1"
BAND_MBID = "22222222-2222-2222-2222-222222222222"


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (900, 1200), (1, 2, 3)).save(buf, "JPEG")
    return buf.getvalue()


MB_ARTIST = {
    "id": BAND_MBID, "name": "The Band", "type": "Group", "country": "GB", "area": {"name": "United Kingdom"},
    "begin-area": {"name": "London"}, "life-span": {"begin": "1994-03", "end": "2010", "ended": True},
    "genres": [{"name": "rock", "count": 3}, {"name": "britpop", "count": 7}],
    "relations": [
        {"type": "wikidata", "url": {"resource": "https://www.wikidata.org/wiki/Q42"}},
        {"type": "official homepage", "url": {"resource": "https://theband.example"}},
        {"type": "official homepage", "url": {"resource": "https://old.example"}, "ended": True},
        {"type": "free streaming", "url": {"resource": "https://stream.example"}},
    ],
}


class Fake:
    """Routes requests to canned JSON by host/path; records what was asked."""

    def __init__(self, **over):
        self.calls: list[httpx.URL] = []
        self.over = over

    def __call__(self, request: httpx.Request) -> httpx.Response:
        u = request.url
        self.calls.append(u)
        assert request.headers["user-agent"].startswith("Homebeat/")
        assert "@" not in request.headers["user-agent"]  # generic UA, no personal contact
        if self.over.get("down"):
            raise httpx.ConnectError("down")
        if u.host == "musicbrainz.org":
            if u.path == "/ws/2/artist":
                return httpx.Response(200, json=self.over.get("search", {"artists": [
                    {"id": "aaaa", "name": "Artist One", "score": 100}]}))
            mbid = u.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json={**MB_ARTIST, "id": mbid})
        if u.host == "www.wikidata.org":
            return httpx.Response(200, json={"entities": {"Q42": {
                "sitelinks": {"enwiki": {"title": "The Band (British band)"}},
                "claims": {"P18": [{"mainsnak": {"datavalue": {"value": "The Band live.jpg"}}}]}}}})
        if u.host == "en.wikipedia.org":
            assert u.path.endswith("The_Band_%28British_band%29") or "The_Band" in u.path
            return httpx.Response(200, json={"type": "standard", "extract": "The Band were an English rock band.",
                                             "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/The_Band_(British_band)"}}})
        if u.host == "commons.wikimedia.org":
            return httpx.Response(200, json={"query": {"pages": {"1": {"imageinfo": [{
                "thumburl": "https://upload.wikimedia.org/thumb/band.jpg",
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:The_Band_live.jpg",
                "extmetadata": {"Artist": {"value": "<a href='x'>Jane Photographer</a>"},
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"}}}]}}}})
        if u.host == "upload.wikimedia.org":
            return httpx.Response(200, content=jpeg(), headers={"content-type": "image/jpeg"})
        return httpx.Response(404)


def band_id(conn):
    return conn.execute("SELECT id FROM artists WHERE name = 'The Band'").fetchone()[0]


# ---------- fetch + cache (no app) ----------


def test_fetch_and_cache(scanned, cfg):
    conn = scanned
    fake = Fake()
    http = artistinfo.Http(httpx.MockTransport(fake), mb_interval_s=0)
    aid = band_id(conn)
    row = artistinfo.update(conn, cfg.data_dir, http, aid)
    assert row["status"] == "ok" and row["mbid"] == BAND_MBID
    # tagged MBID: no name search
    assert not any(u.path == "/ws/2/artist" for u in fake.calls)
    info = artistinfo.as_api(row)
    assert info["genres"] == ["britpop", "rock"]
    assert info["yearsActive"] == {"from": 1994, "to": 2010}
    assert info["area"] == "United Kingdom" and info["type"] == "Group"
    assert info["bio"]["text"].startswith("The Band were") and info["bio"]["license"] == "CC BY-SA 4.0"
    kinds = {link["kind"]: link["url"] for link in info["links"]}
    assert kinds["musicbrainz"] == f"https://musicbrainz.org/artist/{BAND_MBID}"
    assert kinds["wikipedia"].startswith("https://en.wikipedia.org/wiki/The_Band")
    assert kinds["official"] == "https://theband.example" and "wikidata" in kinds
    assert info["image"] == {"filePage": "https://commons.wikimedia.org/wiki/File:The_Band_live.jpg",
                             "author": "Jane Photographer", "license": "CC BY-SA 4.0",
                             "licenseUrl": "https://creativecommons.org/licenses/by-sa/4.0", "credit": None,
                             "source": "Wikimedia Commons"}
    p = artistinfo.art_path(cfg.data_dir, aid)
    assert p.exists() and Image.open(p).size[0] <= artistinfo.IMAGE_WIDTH
    # cached: not due again for ~90 days
    assert not artistinfo.is_due(row)
    assert artistinfo.is_due(row, datetime.now(UTC) + timedelta(days=91))


def test_name_search_is_strict(scanned, cfg):
    conn = scanned
    aid = conn.execute("SELECT id FROM artists WHERE name = 'Artist One'").fetchone()[0]
    http = artistinfo.Http(httpx.MockTransport(Fake()), mb_interval_s=0)
    row = artistinfo.update(conn, cfg.data_dir, http, aid)
    assert row["status"] == "ok" and row["mbid"] == "aaaa"
    # an ambiguous or fuzzy match is "not found", retried in 30 days
    fake = Fake(search={"artists": [{"id": "b1", "name": "Artist One", "score": 100},
                                    {"id": "b2", "name": "Artist One", "score": 100}]})
    conn.execute("DELETE FROM artist_info")
    row = artistinfo.update(conn, cfg.data_dir, artistinfo.Http(httpx.MockTransport(fake), mb_interval_s=0), aid)
    assert row["status"] == "not_found" and row["data"] is None
    fake = Fake(search={"artists": [{"id": "c", "name": "Artist Onex", "score": 98}]})
    assert artistinfo.resolve_mbid(artistinfo.Http(httpx.MockTransport(fake), 0), "Artist One", None) is None


def test_errors_keep_old_data(scanned, cfg):
    conn = scanned
    aid = band_id(conn)
    artistinfo.update(conn, cfg.data_dir, artistinfo.Http(httpx.MockTransport(Fake()), mb_interval_s=0), aid)
    conn.execute("UPDATE artist_info SET refresh_after = '2000-01-01T00:00:00Z'")
    row = artistinfo.update(conn, cfg.data_dir, artistinfo.Http(httpx.MockTransport(Fake(down=True)), 0), aid)
    assert row["status"] == "ok" and row["error"] and json.loads(row["data"])["genres"]
    assert row["refresh_after"] > row["fetched_at"]  # retried tomorrow, old data kept
    # a first lookup that fails is an error row with a short retry
    other = conn.execute("SELECT id FROM artists WHERE name = 'Artist One'").fetchone()[0]
    row = artistinfo.update(conn, cfg.data_dir, artistinfo.Http(httpx.MockTransport(Fake(down=True)), 0), other)
    assert row["status"] == "error"


def test_nightly_refresh_due(scanned, cfg):
    conn = scanned
    res = artistinfo.refresh_due(conn, cfg.data_dir, artistinfo.Http(httpx.MockTransport(Fake()), 0))
    assert res["looked_up"] >= 2
    again = artistinfo.refresh_due(conn, cfg.data_dir, artistinfo.Http(httpx.MockTransport(Fake()), 0))
    assert again == {"looked_up": 0, "failed": 0}


# ---------- API ----------


def test_artist_page(app, client, conn, cfg):
    app.state.ctx.http_transport = httpx.MockTransport(Fake())
    ids = track_ids(conn)
    aid = band_id(conn)
    for _ in range(2):
        client.post(f"{API}/events", json={"events": [{"id": str(uuid.uuid4()), "type": "play",
                                                       "trackId": ids["Closing"], "at": "2026-11-01T10:00:00Z"}]})
    r = client.get(f"{API}/library/artists/{aid}/page")
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["name"] == "The Band" and p["info"]["status"] == "pending"
    assert p["topTracks"][0]["title"] == "Closing" and p["topTracks"][0]["playCount"] == 2
    assert [a["title"] for a in p["singles"]] == ["Second"] and p["albums"] == []
    assert p["stats"]["trackCount"] == 2 and p["stats"]["playCount"] == 2
    assert p["radioSeed"] == {"type": "artist", "id": aid, "title": "The Band"}
    assert p["artUrl"] and p["artUrl"].startswith("art/")  # album cover until the photo arrives
    app.state.ctx.artist_fetcher.wait()
    p = client.get(f"{API}/library/artists/{aid}/page").json()
    assert p["info"]["status"] == "ok" and p["photoUrl"] == f"art/artist/{aid}" and p["artUrl"] == p["photoUrl"]
    img = client.get(f"{API}/{p['photoUrl']}")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    info = client.get(f"{API}/library/artists/{aid}/info").json()
    assert info["status"] == "ok" and info["photoUrl"]
    tr = client.get(f"{API}/library/artists/{aid}/tracks").json()
    assert {t["title"] for t in tr} == {"Opening", "Closing"}
    assert client.get(f"{API}/library/artists/nope/page").status_code == 404
    # Home pins use the photo for artists
    client.post(f"{API}/pins", json={"type": "artist", "id": aid})
    assert client.get(f"{API}/pins").json()[0]["artUrl"] == f"art/artist/{aid}"


def test_appears_on_and_similar(app, client, conn, monkeypatch):
    from homebeat.app import ai_adapter

    feat = conn.execute("SELECT id FROM artists WHERE name = 'The Band feat. X'").fetchone()[0]
    band = band_id(conn)
    one = conn.execute("SELECT id FROM artists WHERE name = 'Artist One'").fetchone()[0]
    monkeypatch.setattr(ai_adapter, "similar_artists",
                        lambda conn, cfg, aid, limit=12: [("ghost", 0.99), (one, 0.8), (band, 0.7)])
    import dataclasses

    app.state.ctx.cfg = dataclasses.replace(app.state.ctx.cfg, artist_info_enabled=False)
    p = client.get(f"{API}/library/artists/{feat}/page").json()
    assert [a["title"] for a in p["appearsOn"]] == ["Second"] and p["albums"] == [] and p["singles"] == []
    assert [s["name"] for s in p["similar"]] == ["Artist One", "The Band"]  # unknown ids dropped
    assert p["similar"][0]["similarity"] == 0.8 and p["similar"][0]["artUrl"]
    assert p["info"] == {"status": "none"}  # lookups turned off


def test_family_mode_hides_artist(client, conn):
    ids = track_ids(conn)
    for t in ("Opening", "Closing"):
        conn.execute("INSERT INTO track_explicit (track_id, status, updated_at) VALUES (?, 'explicit', 'x')", (ids[t],))
    conn.execute("INSERT INTO family_mode (user_id, enabled, updated_at) VALUES (?, 1, 'x')", (client.user_id,))
    assert client.get(f"{API}/library/artists/{band_id(conn)}/page").status_code == 404
    assert client.get(f"{API}/library/artists/{band_id(conn)}/tracks").status_code == 404
