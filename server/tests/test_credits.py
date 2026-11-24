"""Song credits (v10): MusicBrainz recording / work / release relationships (mocked HTTP), the cache, tag credits,
the credits and person endpoints, and family mode."""
from datetime import UTC, datetime, timedelta

import httpx

from nasmusic.library import credits

API = "/api/v1"
REC = "11111111-1111-1111-1111-111111111111"
RG = "33333333-3333-3333-3333-333333333333"
REL = "44444444-4444-4444-4444-444444444444"
PRODUCER = "aaaaaaaa-0000-0000-0000-000000000001"
WRITER = "aaaaaaaa-0000-0000-0000-000000000002"
GUITAR = "aaaaaaaa-0000-0000-0000-000000000003"
MASTERING = "aaaaaaaa-0000-0000-0000-000000000004"
SEARCHED = "55555555-5555-5555-5555-555555555555"


def artist(mbid, name):
    return {"id": mbid, "name": name}


RECORDING = {
    "id": REC, "title": "Opening",
    "relations": [
        {"target-type": "artist", "type": "producer", "direction": "backward", "attributes": ["co"],
         "artist": artist(PRODUCER, "Pat Producer")},
        {"target-type": "artist", "type": "instrument", "direction": "backward",
         "attributes": ["electric guitar", "bass guitar"], "artist": artist(GUITAR, "Gil Guitar")},
        {"target-type": "artist", "type": "vocal", "direction": "backward", "attributes": ["lead vocals"],
         "artist": artist(GUITAR, "Gil Guitar")},
        {"target-type": "artist", "type": "tribute", "direction": "backward", "artist": artist("x" * 36, "Skip Me")},
        {"target-type": "work", "type": "performance", "work": {"id": "w1", "title": "Opening", "relations": [
            {"target-type": "artist", "type": "composer", "direction": "backward", "artist": artist(WRITER, "Wendy Writer")},
            {"target-type": "artist", "type": "lyricist", "direction": "backward", "artist": artist(WRITER, "Wendy Writer")},
        ]}},
    ],
    "releases": [
        {"id": "other-release", "title": "Best Of", "release-group": {"id": "rg-other"}},
        {"id": REL, "title": "Second", "status": "Official", "release-group": {"id": RG}},
    ],
}
RELEASE = {"id": REL, "relations": [
    {"target-type": "artist", "type": "mastering", "direction": "backward", "artist": artist(MASTERING, "Max Master")},
    {"target-type": "artist", "type": "producer", "direction": "backward", "artist": artist(PRODUCER, "Pat Producer")},
]}
SEARCHED_REC = {"id": SEARCHED, "relations": [
    {"target-type": "artist", "type": "producer", "direction": "backward", "artist": artist(PRODUCER, "Pat Producer")},
], "releases": []}


class Fake:
    def __init__(self, **over):
        self.calls: list[httpx.URL] = []
        self.over = over

    def __call__(self, request: httpx.Request) -> httpx.Response:
        u = request.url
        self.calls.append(u)
        assert request.headers["user-agent"].startswith("nas-music/")
        if self.over.get("down"):
            raise httpx.ConnectError("down")
        assert u.host == "musicbrainz.org"
        if u.path == "/ws/2/recording":
            q = u.params["query"]
            if '"Song 1"' in q and '"Artist One"' in q:
                return httpx.Response(200, json=self.over.get("search", {"recordings": [
                    {"id": SEARCHED, "score": 100, "title": "Song 1", "length": 2100,
                     "artist-credit": [{"name": "Artist One", "artist": {"name": "Artist One"}}]},
                    {"id": "too-long", "score": 100, "title": "Song 1", "length": 300000,
                     "artist-credit": [{"name": "Artist One", "artist": {"name": "Artist One"}}]},
                ]}))
            return httpx.Response(200, json={"recordings": []})
        if u.path == f"/ws/2/recording/{REC}":
            assert "work-level-rels" in u.params["inc"]
            return httpx.Response(200, json=RECORDING)
        if u.path == f"/ws/2/recording/{SEARCHED}":
            return httpx.Response(200, json=SEARCHED_REC)
        if u.path == f"/ws/2/release/{REL}":
            return httpx.Response(200, json=RELEASE)
        return httpx.Response(404)


def tid(conn, title):
    return conn.execute("SELECT id FROM tracks WHERE title = ?", (title,)).fetchone()[0]


def http(fake):
    return credits.Http(httpx.MockTransport(fake), mb_interval_s=0)


def test_tag_names():
    assert credits.tag_names("Wendy Writer; Bob B / Carl C & Dee D") == ["Wendy Writer", "Bob B", "Carl C", "Dee D"]
    assert credits.tag_names(None) == [] and credits.tag_names(" ; ") == []


def test_labels():
    assert credits.labels_for("producer", "co") == ["Co-producer"]
    assert credits.labels_for("instrument", "electric guitar, bass guitar") == ["Electric guitar", "Bass guitar"]
    assert credits.labels_for("mastering", "") == ["Mastering"]
    assert credits.labels_for("composer", "") == ["Composer"]


def test_fetch_and_cache(scanned):
    conn = scanned
    fake = Fake()
    t = tid(conn, "Opening")
    row = credits.update(conn, http(fake), t)
    assert row["status"] == "ok" and row["recording_mbid"] == REC and row["release_mbid"] == REL
    # tagged MBID: no search; the release of the same release group, not the compilation
    assert not any(u.path == "/ws/2/recording" for u in fake.calls)
    assert any(u.path == f"/ws/2/release/{REL}" for u in fake.calls)
    got = {(r["person_id"], r["role"], r["detail"], r["scope"]) for r in conn.execute("SELECT * FROM track_credits")}
    assert ("mb:" + PRODUCER, "producer", "co", "recording") in got
    assert ("mb:" + WRITER, "composer", "", "work") in got
    assert ("mb:" + MASTERING, "mastering", "", "release") in got
    assert not any(p.startswith("mb:xxx") for p, *_ in got)  # unknown relationship types are skipped
    assert not credits.is_due(row)
    assert credits.is_due(row, datetime.now(UTC) + timedelta(days=181))

    # a second track of the same release reuses the album-wide credits from the database
    fake2 = Fake()
    closing = tid(conn, "Closing")
    conn.execute("UPDATE tracks SET mbid = ? WHERE id = ?", (REC, closing))
    credits.update(conn, http(fake2), closing)
    assert not any(u.path.startswith("/ws/2/release/") for u in fake2.calls)
    assert conn.execute("SELECT COUNT(*) FROM track_credits WHERE track_id = ? AND scope = 'release'",
                        (closing,)).fetchone()[0] == 2


def test_search_is_strict(scanned):
    conn = scanned
    row = credits.update(conn, http(Fake()), tid(conn, "Song 1"))
    # the 5-minute recording is a different version; only one candidate is left
    assert row["status"] == "ok" and row["recording_mbid"] == SEARCHED and row["release_mbid"] is None
    two = {"recordings": [
        {"id": "r1", "score": 100, "title": "Song 1", "artist-credit": [{"name": "Artist One"}]},
        {"id": "r2", "score": 100, "title": "Song 1", "artist-credit": [{"name": "Artist One"}]}]}
    row = credits.update(conn, http(Fake(search=two)), tid(conn, "Song 2"))
    assert row["status"] == "not_found"


def test_error_keeps_old_data(scanned):
    conn = scanned
    t = tid(conn, "Opening")
    credits.update(conn, http(Fake()), t)
    row = credits.update(conn, http(Fake(down=True)), t)
    assert row["status"] == "ok" and "unreachable" in row["error"]
    assert conn.execute("SELECT COUNT(*) FROM track_credits WHERE track_id = ?", (t,)).fetchone()[0] > 0
    assert credits.is_due(row, datetime.now(UTC) + timedelta(days=2))


def test_refresh_due(scanned):
    conn = scanned
    out = credits.refresh_due(conn, http(Fake()))
    assert out["looked_up"] == conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 0").fetchone()[0]
    assert credits.refresh_due(conn, http(Fake())) == {"looked_up": 0, "failed": 0}
    conn.execute("DELETE FROM credit_lookups")
    assert credits.refresh_due(conn, http(Fake(down=True)))["failed"] == 3  # stops after 3 failures in a row


def test_credits_and_person_api(app, client, scanned):
    conn = scanned
    opening, closing, song1 = tid(conn, "Opening"), tid(conn, "Closing"), tid(conn, "Song 1")
    # composer tags: one matches the MusicBrainz writer, one is a tag-only person on two songs
    conn.execute("UPDATE tracks SET composer = 'Wendy Writer; Tara Tag' WHERE id = ?", (opening,))
    conn.execute("UPDATE tracks SET composer = 'Tara Tag' WHERE id = ?", (song1,))
    h = http(Fake())
    credits.update(conn, h, opening)
    credits.update(conn, h, song1)

    r = client.get(f"{API}/library/tracks/{opening}/credits")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["musicbrainzUrl"].endswith(REC)
    groups = {g["id"]: g for g in body["groups"]}
    assert [g["id"] for g in body["groups"]] == ["producers", "writers", "musicians", "engineers"]
    prod = groups["producers"]["people"][0]
    assert prod["name"] == "Pat Producer" and prod["roles"] == ["Co-producer", "Producer"]
    assert prod["otherTracks"] == 1 and not prod["albumWide"]  # also produced Song 1
    writers = {p["name"]: p for p in groups["writers"]["people"]}
    assert writers["Wendy Writer"]["roles"] == ["Composer", "Lyricist"] and not writers["Wendy Writer"]["fromTags"]
    tara = writers["Tara Tag"]
    assert tara["fromTags"] and tara["personId"] == "n:tara tag" and tara["otherTracks"] == 1
    gil = groups["musicians"]["people"][0]
    assert gil["roles"] == ["Electric guitar", "Bass guitar", "Lead vocals"]
    assert groups["engineers"]["people"][0] == {
        "personId": "mb:" + MASTERING, "name": "Max Master", "mbid": MASTERING, "roles": ["Mastering"],
        "albumWide": True, "fromTags": False, "otherTracks": 0, "artistId": None}

    p = client.get(f"{API}/people/mb:{PRODUCER}").json()
    assert p["name"] == "Pat Producer" and p["trackCount"] == 2 and p["musicbrainzUrl"].endswith(PRODUCER)
    assert {t["title"]: t["credits"] for t in p["tracks"]} == {"Opening": ["Co-producer", "Producer"],
                                                                "Song 1": ["Producer"]}
    p = client.get(f"{API}/people/n:tara tag").json()
    assert p["name"] == "Tara Tag" and {t["title"] for t in p["tracks"]} == {"Opening", "Song 1"}
    assert p["roles"] == ["Composer"] and p["mbid"] is None
    assert client.get(f"{API}/people/n:nobody").status_code == 404
    assert client.get(f"{API}/people/drop table").status_code == 404
    assert client.get(f"{API}/library/tracks/nope/credits").status_code == 404
    _ = closing


def test_first_open_queues_a_lookup(app, client, scanned):
    from nasmusic.app import credits_api

    c = app.state.ctx
    c.cfg.credits_enabled = True
    fake = Fake()
    c.credits_fetcher = credits.Fetcher(lambda: __import__("nasmusic.core", fromlist=["open_db"]).open_db(c.cfg),
                                        httpx.MockTransport(fake))
    c.credits_fetcher.http.mb_interval_s = 0
    opening = tid(scanned, "Opening")
    body = client.get(f"{API}/library/tracks/{opening}/credits").json()
    assert body["status"] in ("pending", "ok")
    credits_api.fetcher(c).wait()
    body = client.get(f"{API}/library/tracks/{opening}/credits").json()
    assert body["status"] == "ok" and body["groups"]


def test_credits_off(app, client, scanned):
    app.state.ctx.cfg.credits_enabled = False
    body = client.get(f"{API}/library/tracks/{tid(scanned, 'Opening')}/credits").json()
    assert body == {"trackId": tid(scanned, "Opening"), "status": "none", "fetchedAt": None, "recordingMbid": None,
                    "musicbrainzUrl": None, "groups": []}
