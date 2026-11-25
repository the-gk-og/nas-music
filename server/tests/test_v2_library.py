"""v2 library browsing, song info, smart lists, playlists, queues and lyrics overrides."""
import uuid

from tests.conftest import track_ids

API = "/api/v1"


def test_folders(client):
    root = client.get(f"{API}/library/folders").json()
    assert root["path"] == "" and root["parent"] is None and root["tracks"] == []
    assert [(f["name"], f["trackCount"]) for f in root["folders"]] == [("Artist One", 2), ("Loose", 1), ("The Band", 2)]
    sub = client.get(f"{API}/library/folders", params={"path": "The Band"}).json()
    assert sub["parent"] == "" and [f["path"] for f in sub["folders"]] == ["The Band/Second"]
    leaf = client.get(f"{API}/library/folders", params={"path": "The Band/Second"}).json()
    assert leaf["parent"] == "The Band" and [t["title"] for t in leaf["tracks"]] == ["Opening", "Closing"]
    assert client.get(f"{API}/library/folders", params={"path": "Nope"}).status_code == 404
    assert client.get(f"{API}/library/folders", params={"path": "../etc"}).status_code == 404


def test_genres_and_all_tracks(client):
    assert client.get(f"{API}/library/genres").json() == [{"name": "Rock", "trackCount": 2, "albumCount": 1}]
    assert [t["title"] for t in client.get(f"{API}/library/genres/rock/tracks").json()] == ["Song 1", "Song 2"]
    titles = [t["title"] for t in client.get(f"{API}/library/tracks").json()]
    assert titles == sorted(titles, key=str.casefold) and len(titles) == 5
    assert len(client.get(f"{API}/library/tracks", params={"sort": "year", "limit": 2}).json()) == 2
    assert client.get(f"{API}/library/tracks", params={"sort": "nope"}).status_code == 422


def test_track_info(client, conn):
    tid = track_ids(conn)["Song 1"]
    i = client.get(f"{API}/library/tracks/{tid}/info").json()
    assert i["fileName"] == "01 Song 1.flac" and i["folder"] == "Artist One/First Album"
    assert (i["codec"], i["sampleRate"], i["bitsPerSample"], i["channels"]) == ("FLAC", 44100, 16, 2)
    assert i["bitrateKbps"] > 0 and i["fileBytes"] == i["flacBytes"] and i["albumArtist"] == "Artist One"
    assert i["playCount"] == 0 and i["addedAt"]
    assert client.get(f"{API}/library/tracks/nope/info").status_code == 404


def test_scanner_reads_song_info_tags(cfg, conn, base_flac):
    from homebeat.library import scanner
    from tests.conftest import make_flac

    make_flac(base_flac, cfg.library_root / "Extra" / "x.flac", title="X", artist="A", composer="Bach",
              lyricist="Someone", comment="great take")
    scanner.scan(cfg, conn)
    r = conn.execute("SELECT * FROM tracks WHERE title = 'X'").fetchone()
    assert (r["composer"], r["lyricist"], r["comment"], r["folder"]) == ("Bach", "Someone", "great take", "Extra")


def test_smart_lists(client, conn):
    ids = track_ids(conn)
    ev = lambda t, typ, at: {"id": str(uuid.uuid4()), "type": typ, "trackId": ids[t], "at": at}  # noqa: E731
    client.post(f"{API}/events", json={"events": [
        ev("Song 1", "play", "2026-11-18T10:00:00Z"), ev("Song 1", "play", "2026-11-18T11:00:00Z"),
        ev("Opening", "play", "2026-11-18T12:00:00Z"), ev("Closing", "skip", "2026-11-18T12:30:00Z")]})
    client.put(f"{API}/favourites/{ids['Closing']}")
    get = lambda k: [t["title"] for t in client.get(f"{API}/lists/{k}").json()]  # noqa: E731
    assert get("most_played") == ["Song 1", "Opening"]
    assert get("recently_played") == ["Opening", "Song 1"]
    assert set(get("not_played")) == {"Song 2", "Closing", "untagged"}
    assert get("favourites") == ["Closing"]
    assert len(get("recently_added")) == 5
    assert client.get(f"{API}/lists/bogus").status_code == 422
    info = client.get(f"{API}/library/tracks/{ids['Song 1']}/info").json()
    assert info["playCount"] == 2 and info["lastPlayed"] == "2026-11-18T11:00:00Z"


def test_playlists(client, conn):
    ids = track_ids(conn)
    a, b, c_, d = ids["Song 1"], ids["Song 2"], ids["Opening"], ids["Closing"]
    p = client.post(f"{API}/playlists", json={"name": "Road trip"}).json()
    assert p["tracks"] == [] and p["trackCount"] == 0
    pid = p["id"]
    p = client.post(f"{API}/playlists/{pid}/tracks", json={"trackIds": [a, b, c_]}).json()
    p = client.post(f"{API}/playlists/{pid}/tracks", json={"trackIds": [d], "position": 1}).json()
    assert [t["id"] for t in p["tracks"]] == [a, d, b, c_] and p["trackCount"] == 4
    p = client.post(f"{API}/playlists/{pid}/move", json={"from": 0, "to": 3}).json()
    assert [t["id"] for t in p["tracks"]] == [d, b, c_, a]
    p = client.delete(f"{API}/playlists/{pid}/tracks/1").json()
    assert [t["id"] for t in p["tracks"]] == [d, c_, a]
    assert client.delete(f"{API}/playlists/{pid}/tracks/9").status_code == 422
    assert client.post(f"{API}/playlists/{pid}/move", json={"from": 0, "to": 7}).status_code == 422
    assert client.post(f"{API}/playlists/{pid}/tracks", json={"trackIds": ["nope"]}).status_code == 404
    assert client.patch(f"{API}/playlists/{pid}", json={"name": "Renamed"}).json()["name"] == "Renamed"
    # Missing files stay listed, flagged.
    conn.execute("UPDATE tracks SET missing = 1 WHERE id = ?", (c_,))
    p = client.get(f"{API}/playlists/{pid}").json()
    assert [t.get("missing", False) for t in p["tracks"]] == [False, True, False]
    lst = client.get(f"{API}/playlists").json()
    assert [x["name"] for x in lst] == ["Renamed"] and lst[0]["durationS"] > 0
    assert client.delete(f"{API}/playlists/{pid}").status_code == 204
    assert client.get(f"{API}/playlists/{pid}").status_code == 404


def test_queues_editing_keeps_current_track(client, conn):
    ids = track_ids(conn)
    a, b, c_, d, e = ids["Song 1"], ids["Song 2"], ids["Opening"], ids["Closing"], ids["untagged"]
    q = client.post(f"{API}/queues", json={"name": "Search: song", "trackIds": [a, b, c_], "startIndex": 1}).json()
    qid = q["id"]
    assert (q["slot"], q["currentIndex"], q["trackCount"]) == (1, 1, 3)
    q = client.post(f"{API}/queues/{qid}/tracks", json={"trackIds": [d], "position": "next"}).json()
    assert [t["id"] for t in q["tracks"]] == [a, b, d, c_] and q["currentIndex"] == 1
    q = client.post(f"{API}/queues/{qid}/tracks", json={"trackIds": [e], "position": 0}).json()
    assert q["tracks"][q["currentIndex"]]["id"] == b  # current follows its track
    q = client.post(f"{API}/queues/{qid}/tracks", json={"trackIds": [e]}).json()  # default: end
    assert q["tracks"][-1]["id"] == e
    q = client.delete(f"{API}/queues/{qid}/tracks/0").json()
    assert q["tracks"][q["currentIndex"]]["id"] == b
    q = client.post(f"{API}/queues/{qid}/move", json={"from": q["currentIndex"], "to": 3}).json()
    assert q["currentIndex"] == 3 and q["tracks"][3]["id"] == b
    q = client.post(f"{API}/queues/{qid}/move", json={"from": 0, "to": 4}).json()
    assert q["tracks"][q["currentIndex"]]["id"] == b
    q = client.patch(f"{API}/queues/{qid}", json={"currentIndex": 0, "positionMs": 5000, "name": "Renamed"}).json()
    assert (q["currentIndex"], q["positionMs"], q["name"]) == (0, 5000, "Renamed")
    assert client.patch(f"{API}/queues/{qid}", json={"currentIndex": 99}).status_code == 422
    assert client.post(f"{API}/queues/{qid}/tracks", json={"trackIds": [a], "position": "middle"}).status_code == 422
    pl = client.post(f"{API}/queues/{qid}/save-as-playlist", json={"name": "From queue"}).json()
    assert [t["id"] for t in pl["tracks"]] == [t["id"] for t in q["tracks"]]
    assert client.delete(f"{API}/queues/{qid}").status_code == 204
    assert client.get(f"{API}/queues").json() == []


def test_queue_limit_replaces_least_recently_used(client, conn):
    t = list(track_ids(conn).values())[0]
    made = [client.post(f"{API}/queues", json={"name": f"q{i}", "trackIds": [t]}).json() for i in range(20)]
    assert sorted(q["slot"] for q in made) == list(range(1, 21))
    client.patch(f"{API}/queues/{made[0]['id']}", json={"name": "q0 used"})  # q0 is now recent; q1 is oldest
    conn.execute("UPDATE queues SET updated_at = '2000-01-01T00:00:00Z' WHERE id = ?", (made[1]["id"],))
    new = client.post(f"{API}/queues", json={"name": "q20", "trackIds": [t]}).json()
    qs = client.get(f"{API}/queues").json()
    assert len(qs) == 20 and new["slot"] == made[1]["slot"]
    assert made[1]["id"] not in {q["id"] for q in qs} and made[0]["id"] in {q["id"] for q in qs}
    assert [q["slot"] for q in qs] == list(range(1, 21))


def test_lyrics_override(client, conn):
    tid = track_ids(conn)["Song 1"]
    assert client.get(f"{API}/tracks/{tid}/lyrics").status_code == 404
    assert client.put(f"{API}/tracks/{tid}/lyrics", json={"raw": "   "}).status_code == 422
    r = client.put(f"{API}/tracks/{tid}/lyrics", json={"raw": "[00:01.00]Hello\n[00:02.50]World"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "user" and body["synced"] is True and body["canEdit"] is True
    assert body["sources"] == ["user"] and body["lines"][1] == {"timeMs": 2500, "text": "World"}
    assert client.get(f"{API}/tracks/{tid}/lyrics").json()["source"] == "user"
    # A sidecar .lrc on disk is offered as another source; the user's edit still wins by default.
    path = conn.execute("SELECT path FROM tracks WHERE id = ?", (tid,)).fetchone()[0]
    lrc = client.app.state.ctx.cfg.library_root / path
    lrc.with_suffix(".lrc").write_text("[00:00.50]From file")
    g = client.get(f"{API}/tracks/{tid}/lyrics").json()
    assert g["source"] == "user" and g["sources"] == ["user", "lrc_file"]
    assert client.get(f"{API}/tracks/{tid}/lyrics", params={"source": "lrc_file"}).json()["text"] == "From file"
    assert client.get(f"{API}/tracks/{tid}/lyrics", params={"source": "embedded"}).status_code == 404
    assert client.delete(f"{API}/tracks/{tid}/lyrics").status_code == 204
    assert client.get(f"{API}/tracks/{tid}/lyrics").json()["source"] == "lrc_file"
    assert client.delete(f"{API}/tracks/{tid}/lyrics").status_code == 204  # idempotent
    assert client.put(f"{API}/tracks/nope/lyrics", json={"raw": "x"}).status_code == 404
