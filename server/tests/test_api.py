import json
import uuid

import pytest
from tests.conftest import track_ids

from homebeat.ai import api as ai


def ev(type_, track, at="2026-11-18T10:00:00Z", **kw):
    return {"id": kw.pop("id", str(uuid.uuid4())), "type": type_, "trackId": track, "at": at, **kw}


# ---------- library ----------


def test_library_browse(client, conn):
    artists = client.get("/api/v1/library/artists").json()
    assert [a["name"] for a in artists] == ["Artist One", "The Band", "Unknown Artist"]
    assert artists[1]["mbid"] == "22222222-2222-2222-2222-222222222222"
    assert client.get("/api/v1/library/artists?limit=1&offset=1").json()[0]["name"] == "The Band"

    one = client.get(f"/api/v1/library/artists/{artists[0]['id']}").json()
    assert one["albumCount"] == 1 and one["albums"][0]["title"] == "First Album"
    assert client.get("/api/v1/library/artists/nope").status_code == 404

    albums = client.get("/api/v1/library/albums?sort=year").json()
    assert [a["title"] for a in albums][:2] == ["Second", "First Album"]
    al = client.get(f"/api/v1/library/albums/{albums[0]['id']}").json()
    assert [t["title"] for t in al["tracks"]] == ["Opening", "Closing"]
    assert al["trackCount"] == 2 and al["hasArt"] is True
    t = al["tracks"][0]
    assert set(t) >= {"id", "title", "artistId", "artist", "albumId", "album", "durationS", "starred",
                      "flacBytes", "trackNo", "discNo", "rating"}
    assert t["artist"] == "The Band feat. X" and t["album"] == "Second"
    assert client.get(f"/api/v1/library/tracks/{t['id']}").json() == t
    assert client.get("/api/v1/library/tracks/nope").status_code == 404
    by_artist = client.get(f"/api/v1/library/albums?artistId={artists[0]['id']}").json()
    assert [a["title"] for a in by_artist] == ["First Album"]
    assert client.get("/api/v1/library/albums?limit=5000").status_code == 422


def test_search(client):
    r = client.get("/api/v1/library/search", params={"q": "band open"}).json()
    assert [t["title"] for t in r["tracks"]] == ["Opening"]
    r = client.get("/api/v1/library/search", params={"q": "band"}).json()
    assert [a["name"] for a in r["artists"]] == ["The Band"]
    assert [a["title"] for a in r["albums"]] == ["Second"]
    assert client.get("/api/v1/library/search", params={"q": "100%"}).json()["tracks"] == []


def test_search_tolerates_typos_and_accents(client, conn):
    conn.execute("UPDATE tracks SET title = 'Clôsing' WHERE title = 'Closing'")
    conn.commit()
    # Misspelt words fall back to close spellings from the library ("Openign" -> "Opening").
    r = client.get("/api/v1/library/search", params={"q": "openign"}).json()
    assert [t["title"] for t in r["tracks"]] == ["Opening"]
    r = client.get("/api/v1/library/search", params={"q": "teh bnad"}).json()
    assert [a["name"] for a in r["artists"]] == ["The Band"]
    # Accents: an accent-less query finds the accented title.
    r = client.get("/api/v1/library/search", params={"q": "closing"}).json()
    assert [t["title"] for t in r["tracks"]] == ["Clôsing"]
    # Nonsense still finds nothing, and the global search gets the same tolerance.
    assert client.get("/api/v1/library/search", params={"q": "zzzzqqq"}).json()["tracks"] == []
    g = client.get("/api/v1/search", params={"q": "openign", "scope": "library"}).json()
    assert [t["title"] for t in g["tracks"]] == ["Opening"]


# ---------- events ----------


def test_events_idempotent_with_side_effects(client, conn):
    ids = track_ids(conn)
    s1, s2, op = ids["Song 1"], ids["Song 2"], ids["Opening"]
    batch = [
        ev("play", s1, positionS=120.5, durationS=200, source="library"),
        ev("star", s1, at="2026-11-18T10:01:00Z"),
        ev("rate", s2, rating=4),
        ev("thumb_down", op),
        ev("skip", s2, positionS=12, source="mix", mixId="m1", offline=True),
    ]
    r = client.post("/api/v1/events", json={"events": batch})
    assert r.status_code == 200 and r.json() == {"accepted": 5, "duplicates": 0}

    row = lambda t: conn.execute("SELECT * FROM user_tracks WHERE track_id = ? AND user_id = ?",  # noqa: E731
                                 (t, client.user_id)).fetchone()
    assert row(s1)["starred_at"] == "2026-11-18T10:01:00Z"
    assert row(s2)["rating"] == 4
    assert row(op)["blocked"] == 1

    # Replaying the same batch changes nothing.
    r = client.post("/api/v1/events", json={"events": batch})
    assert r.json() == {"accepted": 0, "duplicates": 5}
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 5
    e = conn.execute("SELECT * FROM events WHERE type = 'skip'").fetchone()
    assert (e["device_id"], e["position_s"], e["offline"], e["mix_id"]) == (client.device_id, 12, 1, "m1")

    # Out-of-order delivery: applied in event time order (unstar happened after star).
    r = client.post("/api/v1/events", json={"events": [
        ev("unstar", s1, at="2026-11-18T11:00:00Z"), ev("thumb_up", op, at="2026-11-18T11:00:00+01:00"),
        ev("star", s2, at="2026-11-18T10:59:00Z"),
    ]})
    assert r.json()["accepted"] == 3
    assert row(s1)["starred_at"] is None and row(op)["blocked"] == 0 and row(s2)["starred_at"]
    # Duplicate id inside one batch.
    dup = str(uuid.uuid4())
    r = client.post("/api/v1/events", json={"events": [ev("play", s1, id=dup), ev("play", s1, id=dup)]})
    assert r.json() == {"accepted": 1, "duplicates": 1}


def test_events_validation(client, conn):
    s1 = track_ids(conn)["Song 1"]
    assert client.post("/api/v1/events", json={"events": [ev("explode", s1)]}).status_code == 422
    assert client.post("/api/v1/events", json={"events": [ev("rate", s1, rating=9)]}).status_code == 422
    too_many = [ev("play", s1) for _ in range(1001)]
    assert client.post("/api/v1/events", json={"events": too_many}).status_code == 422
    # Unknown tracks are dropped, the rest stored.
    r = client.post("/api/v1/events", json={"events": [ev("play", "nope"), ev("play", s1)]})
    assert r.json() == {"accepted": 1, "duplicates": 0}


def test_favourites(client, conn):
    ids = track_ids(conn)
    assert client.put(f"/api/v1/favourites/{ids['Song 1']}").status_code == 204
    assert client.put(f"/api/v1/favourites/{ids['Closing']}").status_code == 204
    assert client.put(f"/api/v1/favourites/{ids['Closing']}").status_code == 204  # idempotent
    favs = client.get("/api/v1/favourites").json()
    assert {f["title"] for f in favs} == {"Song 1", "Closing"} and all(f["starred"] for f in favs)
    assert client.delete(f"/api/v1/favourites/{ids['Song 1']}").status_code == 204
    assert [f["title"] for f in client.get("/api/v1/favourites").json()] == ["Closing"]
    assert client.put("/api/v1/favourites/nope").status_code == 404


# ---------- mixes + manifest ----------


def _make_mix(conn, mix_id, kind, track_list, active=1, created="2026-11-18T02:00:00Z"):
    conn.execute("INSERT INTO mixes (id, user_id, kind, title, for_date, created_at, active) "
                 "VALUES (?,(SELECT id FROM users WHERE username = 'alice'),?,?,?,?,?)",
                 (mix_id, kind, f"{kind} mix", "2026-11-18", created, active))
    for i, t in enumerate(track_list):
        conn.execute("INSERT INTO mix_tracks (mix_id, position, track_id, score, reason) VALUES (?,?,?,?,?)",
                     (mix_id, i, t, 0.5, f"because {i}"))


def test_mixes(client, conn):
    ids = track_ids(conn)
    _make_mix(conn, "m1", "daily", [ids["Song 2"], ids["Opening"]])
    _make_mix(conn, "m0", "daily", [ids["Song 1"]], active=0, created="2026-11-17T02:00:00Z")
    _make_mix(conn, "ml", "more_like", [ids["Closing"]])
    today = client.get("/api/v1/mixes/today").json()
    assert [m["id"] for m in today] == ["m1"]
    m = today[0]
    assert m["kind"] == "daily" and m["createdAt"] == "2026-11-18T02:00:00Z"
    assert [x["track"]["title"] for x in m["tracks"]] == ["Song 2", "Opening"]
    assert m["tracks"][0]["reason"] == "because 0" and m["durationS"] == pytest.approx(4.0, abs=0.1)
    assert client.get("/api/v1/mixes/m0").json()["id"] == "m0"
    assert client.get("/api/v1/mixes/nope").status_code == 404

    why = client.get(f"/api/v1/tracks/{ids['Opening']}/why").json()
    assert why["reason"] == "because 1" and why["similarTo"] == []


def test_more_like_503_until_ai_ready(client, conn, monkeypatch):
    ids = track_ids(conn)

    def not_ready(*a, **k):
        raise NotImplementedError

    monkeypatch.setattr(ai, "more_like", not_ready)
    assert client.post(f"/api/v1/mixes/more-like/{ids['Song 1']}").status_code == 503
    assert client.post("/api/v1/mixes/more-like/nope").status_code == 404

    def fake(conn_, cfg, track_id, minutes=40):
        _make_mix(conn_, "new", "more_like", [track_id, ids["Closing"]])
        return "new"

    monkeypatch.setattr(ai, "more_like", fake)
    r = client.post(f"/api/v1/mixes/more-like/{ids['Song 1']}?minutes=20")
    assert r.status_code == 200 and r.json()["kind"] == "more_like" and len(r.json()["tracks"]) == 2
    assert client.post(f"/api/v1/mixes/more-like/{ids['Song 1']}?minutes=5").status_code == 422

    monkeypatch.setattr(ai, "explain", not_ready)
    assert client.get(f"/api/v1/tracks/{ids['Song 1']}/why").status_code == 503


def test_manifest(client, conn):
    ids = track_ids(conn)
    _make_mix(conn, "m1", "daily", [ids["Song 2"], ids["Opening"]])
    _make_mix(conn, "m2", "evening", [ids["Opening"]])
    _make_mix(conn, "old", "daily", [ids["untagged"]], active=0)
    client.put(f"/api/v1/favourites/{ids['Song 1']}")
    client.put(f"/api/v1/favourites/{ids['Song 2']}")

    m = client.get("/api/v1/sync/manifest").json()
    assert m["generatedAt"].endswith("Z")
    items = {i["trackId"]: i for i in m["items"]}
    assert set(items) == {ids["Song 1"], ids["Song 2"], ids["Opening"]}
    assert items[ids["Opening"]].items() >= {"trackId": ids["Opening"], "format": "opus", "why": "mix",
                                             "mixIds": ["m1", "m2"]}.items()
    assert items[ids["Song 1"]]["why"] == "favourite" and items[ids["Song 1"]]["format"] == "opus"
    assert items[ids["Song 2"]]["why"] == "favourite" and items[ids["Song 2"]]["mixIds"] == ["m1"]

    m = client.get("/api/v1/sync/manifest?favouritesFormat=flac").json()
    items = {i["trackId"]: i for i in m["items"]}
    assert items[ids["Song 1"]]["format"] == "flac" and items[ids["Opening"]]["format"] == "opus"

    # Missing tracks never appear.
    conn.execute("UPDATE tracks SET missing = 1 WHERE id = ?", (ids["Song 1"],))
    m = client.get("/api/v1/sync/manifest").json()
    assert ids["Song 1"] not in {i["trackId"] for i in m["items"]}


# ---------- suggestions ----------


def test_suggestions(client, conn):
    for i, status in enumerate(["want", "new"]):
        conn.execute(
            "INSERT INTO suggestions (id, user_id, artist, album, year, reason, status, created_at, buy_links) "
            "VALUES (?,(SELECT id FROM users WHERE username = 'alice'),?,?,?,?,?,?,?)",
            (f"s{i}", "Someone", f"Record {i}", 2020, "Sounds like your favourites", status,
             "2026-11-15T03:00:00Z", json.dumps([{"name": "Bandcamp", "url": "https://example.com"}])),
        )
    s = client.get("/api/v1/suggestions").json()
    assert [x["id"] for x in s] == ["s1", "s0"]
    assert s[0]["buyLinks"][0]["name"] == "Bandcamp" and s[0]["status"] == "new"
    assert client.post("/api/v1/suggestions/s1", json={"status": "have"}).status_code == 204
    assert conn.execute("SELECT status, answered_at FROM suggestions WHERE id='s1'").fetchone()[0] == "have"
    assert client.post("/api/v1/suggestions/s1", json={"status": "new"}).status_code == 422
    assert client.post("/api/v1/suggestions/zzz", json={"status": "want"}).status_code == 404


# ---------- smart downloads ----------


def test_sync_smart_candidates(client, conn, monkeypatch):
    from homebeat.app import ai_adapter as adapter
    ids = track_ids(conn)
    _make_mix(conn, "m1", "daily", [ids["Opening"]])
    _make_mix(conn, "wk", "daily", [ids["Closing"]], active=0,
              created=__import__("homebeat.core", fromlist=["now_iso"]).now_iso())
    _make_mix(conn, "old", "daily", [ids["untagged"]], active=0, created="2020-01-01T00:00:00Z")
    client.put(f"/api/v1/favourites/{ids['Song 1']}")
    monkeypatch.setattr(adapter, "likely_next", lambda *a: {ids["Song 2"]: 0.9, ids["Opening"]: 0.5})
    monkeypatch.setattr(adapter, "because_you_played", lambda *a: [])
    monkeypatch.setattr(adapter, "release_radar", lambda *a: {})
    r = client.get("/api/v1/sync/smart")
    assert r.status_code == 200
    items = {i["trackId"]: i for i in r.json()["items"]}
    assert items[ids["Song 1"]]["reasons"] == ["favourite"]
    assert items[ids["Opening"]]["reasons"][0] == "mix" and items[ids["Opening"]]["score"] == 0.5
    assert items[ids["Closing"]]["reasons"] == ["week_mix"]
    assert "predicted" in items[ids["Song 2"]]["reasons"] and items[ids["Song 2"]]["score"] == 0.9
    assert ids["untagged"] not in items or "week_mix" not in items[ids["untagged"]]["reasons"]
    assert items[ids["Song 1"]]["track"]["title"] == "Song 1"
    assert r.json()["items"][0]["trackId"] == ids["Song 1"]          # favourites first
    # Thumbs-down tracks never come back, even when predicted.
    conn.execute("UPDATE user_tracks SET blocked = 1 WHERE track_id = ?", (ids["Song 1"],))
    assert ids["Song 1"] not in {i["trackId"] for i in client.get("/api/v1/sync/smart").json()["items"]}


def test_sync_smart_without_ai(client, conn):
    ids = track_ids(conn)
    client.put(f"/api/v1/favourites/{ids['Song 2']}")
    items = client.get("/api/v1/sync/smart").json()["items"]
    assert ids["Song 2"] in {i["trackId"] for i in items}
