"""Smart playlists (schema v7): rule validation, SQL evaluation, caching, family mode, API, pins and radio seeds.

Library (tests/conftest.py): "Song 1", "Song 2" (Artist One, First Album, 2001, Rock), "Opening" (The Band feat.
X, 1999), "Closing" (The Band, 1999), "untagged". Every track is 2 s long."""
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from homebeat.app import smart
from tests.conftest import track_ids

API = "/api/v1"


def _iso(dt):
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


NOW = datetime.now(UTC)


def play(client, ids, title, at, typ="play"):
    r = client.post(f"{API}/events", json={"events": [
        {"id": str(uuid.uuid4()), "type": typ, "trackId": ids[title], "at": _iso(at)}]})
    assert r.status_code == 200, r.text


def titles(client, defn, **kw):
    r = client.post(f"{API}/smart-playlists/preview", json={"definition": defn, "sample": 200, **kw})
    assert r.status_code == 200, r.text
    return [t["title"] for t in r.json()["tracks"]]


def D(*rules, match="all", sort="title", limit=None):
    return {"match": match, "rules": list(rules), "sort": sort, "limit": limit or {"type": "none"}}


def R(field, op, value=None, **kw):
    return {"field": field, "op": op, "value": value, **kw}


# ---------- validation ----------


@pytest.mark.parametrize("bad, msg", [
    (D(R("nope", "eq", 1)), "unknown field"),
    (D(R("playCount", "contains", "x")), "unknown operator"),
    (D(R("playCount", "gt", "five")), "number"),
    (D(R("playCount", "between", [1])), "two numbers"),
    (D(R("lastPlayed", "inLast", 3, unit="fortnights")), "unit"),
    (D(R("mood", "is", "angry")), "must be one of"),
    (D(R("genre", "is", "  ")), "text"),
    ({"match": "all", "rules": [{"match": "any", "rules": [{"match": "any", "rules": [
        {"match": "any", "rules": []}]}]}]}, "nested"),
    ({**D(), "sort": "loudest"}, "unknown sort"),
    ({**D(), "limit": {"type": "songs", "value": 0}}, "out of range"),
])
def test_validation(bad, msg):
    with pytest.raises(smart.Invalid, match=msg):
        smart.normalise(bad)


def test_presets_and_fields_are_valid():
    for p in smart.PRESETS:
        smart.normalise(p["definition"])
    f = smart.fields()
    assert {x["id"] for x in f["fields"]} == set(smart.FIELDS)
    assert any(s["id"] == "random" for s in f["sorts"])


# ---------- evaluation ----------


def test_text_year_decade_and_groups(client, conn):
    assert titles(client, D(R("artist", "is", "artist one"))) == ["Song 1", "Song 2"]
    assert titles(client, D(R("artist", "contains", "band"))) == ["Closing", "Opening"]
    assert titles(client, D(R("artist", "startsWith", "The"), R("title", "notContains", "open"))) == ["Closing"]
    assert titles(client, D(R("genre", "is", "rock"))) == ["Song 1", "Song 2"]
    assert titles(client, D(R("genre", "isNot", "rock"))) == ["Closing", "Opening", "untagged"]
    assert titles(client, D(R("decade", "is", "1990"))) == ["Closing", "Opening"]
    assert titles(client, D(R("year", "between", [2000, 2005]))) == ["Song 1", "Song 2"]
    # OR inside AND: (Rock OR 1999) AND title contains "o"
    assert titles(client, D({"match": "any", "rules": [R("genre", "is", "Rock"), R("year", "eq", 1999)]},
                            R("title", "contains", "o"))) == ["Closing", "Opening", "Song 1", "Song 2"]
    assert titles(client, D(R("genre", "is", "Rock"), R("year", "eq", 1999), match="any")) == \
        ["Closing", "Opening", "Song 1", "Song 2"]
    assert titles(client, D(R("durationS", "lt", 1))) == []
    assert len(titles(client, D())) == 5  # no rules = everything
    # LIKE wildcards in user text are literal
    assert titles(client, D(R("title", "contains", "%"))) == []


def test_listening_rules(client, conn):
    ids = track_ids(conn)
    old = NOW - timedelta(days=60)
    for _ in range(3):
        play(client, ids, "Song 1", old)
    play(client, ids, "Opening", NOW - timedelta(days=1))
    play(client, ids, "Opening", NOW - timedelta(days=1), "skip")
    play(client, ids, "Closing", NOW, "skip")
    client.put(f"{API}/favourites/{ids['Song 2']}")
    client.put(f"{API}/favourites/{ids['Song 1']}")

    assert titles(client, D(R("playCount", "gte", 2))) == ["Song 1"]
    assert titles(client, D(R("lastPlayed", "inLast", 1, unit="weeks"))) == ["Opening"]
    assert titles(client, D(R("lastPlayed", "never"))) == ["Closing", "Song 2", "untagged"]
    assert titles(client, D(R("favourite", "is", True), R("lastPlayed", "notInLast", 1, unit="months"))) == \
        ["Song 1", "Song 2"]
    assert titles(client, D(R("skipRate", "gte", 0.5))) == ["Closing", "Opening"]
    assert titles(client, D(R("skipCount", "eq", 0), R("playCount", "gt", 0))) == ["Song 1"]
    assert titles(client, D(R("addedAt", "inLast", 1, unit="days"))) and \
        titles(client, D(R("addedAt", "before", "2000-01-01"))) == []
    # sort + limits
    assert titles(client, D(sort="most_played", limit={"type": "songs", "value": 2})) == ["Song 1", "Opening"]
    assert len(titles(client, D(limit={"type": "minutes", "value": 1}))) == 5  # 5 x 2 s fits in a minute

    # thumbs: down = blocked
    play(client, ids, "untagged", NOW, "thumb_down")
    play(client, ids, "Song 2", NOW, "thumb_up")
    assert titles(client, D(R("thumbs", "is", "down"))) == ["untagged"]
    assert titles(client, D(R("thumbs", "is", "up"))) == ["Song 2"]
    assert "untagged" not in titles(client, D(R("thumbs", "isNot", "down")))


def test_audio_features_and_explicit(client, conn):
    ids = track_ids(conn)
    feats = {"Song 1": (0.9, 140, 0.8, {"mood:happy": 0.9}), "Song 2": (0.2, 80, 0.3, {"mood:sad": 0.8}),
             "Opening": (0.75, 120, 0.5, {"mood:happy": 0.2})}
    for t, (energy, bpm, dance, tags) in feats.items():
        conn.execute("INSERT INTO features (track_id, model_version, embedding, dims, bpm, energy, danceability, tags, "
                     "analysed_at) VALUES (?, 'm', x'00000000', 1, ?, ?, ?, ?, '2026-01-01T00:00:00Z')",
                     (ids[t], bpm, energy, dance, json.dumps(tags)))
    assert titles(client, D(R("energy", "gte", 0.7), sort="energy_high")) == ["Song 1", "Opening"]
    assert titles(client, D(R("tempo", "between", [100, 130]))) == ["Opening"]
    assert titles(client, D(R("danceability", "lt", 0.4))) == ["Song 2"]
    assert titles(client, D(R("mood", "is", "happy"))) == ["Song 1"]
    assert "Song 1" not in titles(client, D(R("mood", "isNot", "happy")))
    conn.execute("INSERT INTO track_explicit (track_id, status, updated_at) VALUES (?, 'explicit', 'x')", (ids["Closing"],))
    assert titles(client, D(R("explicit", "is", "explicit"))) == ["Closing"]
    assert "Closing" not in titles(client, D(R("explicit", "isNot", "explicit")))
    assert "untagged" in titles(client, D(R("explicit", "is", "unknown")))


def test_family_mode_filters(client, conn):
    ids = track_ids(conn)
    conn.execute("INSERT INTO track_explicit (track_id, status, updated_at) VALUES (?, 'explicit', 'x')", (ids["Closing"],))
    sp = client.post(f"{API}/smart-playlists", json={"name": "All", "definition": D()}).json()
    assert sp["trackCount"] == 5
    conn.execute("INSERT INTO family_mode (user_id, enabled, updated_at) VALUES (?, 1, 'x')", (client.user_id,))
    got = client.get(f"{API}/smart-playlists/{sp['id']}").json()
    assert got["trackCount"] == 4 and "Closing" not in [t["title"] for t in got["tracks"]]
    assert "Closing" not in titles(client, D())


# ---------- CRUD, cache, sharing boundaries ----------


def test_crud_cache_and_refresh(client, bob, conn):
    ids = track_ids(conn)
    r = client.post(f"{API}/smart-playlists", json={"name": "Played", "definition": D(R("playCount", "gte", 1))})
    assert r.status_code == 201, r.text
    sp = r.json()
    assert sp["trackCount"] == 0 and sp["tracks"] == [] and sp["id"].startswith("sp")
    rev = sp["rev"]
    play(client, ids, "Song 2", NOW)  # a new event makes the cache stale -> recomputed on read
    got = client.get(f"{API}/smart-playlists/{sp['id']}").json()
    assert [t["title"] for t in got["tracks"]] == ["Song 2"] and got["rev"] == rev + 1
    stamp = conn.execute("SELECT stamp FROM smart_playlists WHERE id = ?", (sp["id"],)).fetchone()[0]
    client.get(f"{API}/smart-playlists/{sp['id']}")  # nothing changed -> same stamp, no new rev
    assert tuple(conn.execute("SELECT stamp, rev FROM smart_playlists WHERE id = ?", (sp["id"],)).fetchone()) == \
        (stamp, rev + 1)
    # edit rules + name
    r = client.patch(f"{API}/smart-playlists/{sp['id']}", json={"name": "Rock", "definition": D(R("genre", "is", "Rock"))})
    assert r.json()["name"] == "Rock" and r.json()["trackCount"] == 2
    assert [x["name"] for x in client.get(f"{API}/smart-playlists").json()] == ["Rock"]
    assert client.post(f"{API}/smart-playlists/{sp['id']}/refresh").json()["trackCount"] == 2
    # someone else's smart playlist does not exist for you
    assert bob.get(f"{API}/smart-playlists/{sp['id']}").status_code == 404
    assert bob.get(f"{API}/smart-playlists").json() == []
    assert bob.post(f"{API}/smart-playlists/preview", json={"definition": D(R("inPlaylist", "is", sp["id"]))}
                    ).status_code == 422
    # bad definitions are 422 with a message
    r = client.post(f"{API}/smart-playlists", json={"name": "x", "definition": D(R("nope", "eq", 1))})
    assert r.status_code == 422 and "unknown field" in r.text
    assert client.patch(f"{API}/smart-playlists/{sp['id']}", json={
        "definition": D(R("inPlaylist", "is", sp["id"]))}).status_code == 422  # self-reference
    assert client.delete(f"{API}/smart-playlists/{sp['id']}").status_code == 204
    assert client.get(f"{API}/smart-playlists/{sp['id']}").status_code == 404


def test_in_playlist_and_nightly_refresh(client, conn, cfg):
    ids = track_ids(conn)
    pl = client.post(f"{API}/playlists", json={"name": "P", "trackIds": [ids["Song 1"], ids["Opening"]]}).json()
    assert titles(client, D(R("inPlaylist", "is", pl["id"]))) == ["Opening", "Song 1"]
    assert titles(client, D(R("inPlaylist", "isNot", pl["id"]))) == ["Closing", "Song 2", "untagged"]
    a = client.post(f"{API}/smart-playlists", json={"name": "A", "definition": D(R("artist", "contains", "band"))}).json()
    assert titles(client, D(R("inPlaylist", "is", a["id"]))) == ["Closing", "Opening"]
    # the nightly job refreshes stale caches
    conn.execute("UPDATE smart_playlists SET stamp = 'old'")
    from homebeat.app.scheduler import Jobs

    rec = Jobs(cfg).run("smart", "manual")
    assert rec["ok"] and rec["result"]["smartPlaylists"] == 1
    assert conn.execute("SELECT stamp FROM smart_playlists").fetchone()[0] != "old"


def test_random_order_is_stable_for_the_day(conn, scanned, client):
    defn = smart.normalise(D(sort="random"))
    a = smart.evaluate(conn, client.user_id, defn, seed="x:2026-01-01")
    b = smart.evaluate(conn, client.user_id, defn, seed="x:2026-01-01")
    assert a == b and len(a) == 5


def test_pin_queue_and_radio_seed(client, conn):
    sp = client.post(f"{API}/smart-playlists", json={"name": "Rocky", "definition": D(R("genre", "is", "Rock")),
                                                     "preset": None}).json()
    assert client.post(f"{API}/pins", json={"type": "smart_playlist", "id": sp["id"]}).status_code == 200
    pins = client.get(f"{API}/pins").json()
    assert pins[0]["type"] == "smart_playlist" and pins[0]["title"] == "Rocky" and pins[0]["subtitle"] == "2 songs"
    from homebeat.app import discover

    assert discover.seed_title(conn, client.user_id, {"type": "smart_playlist", "id": sp["id"]}) == "Rocky"
    with pytest.raises(discover.NotFound):
        discover.seed_title(conn, client.user_id, {"type": "smart_playlist", "id": "spnope"})
    client.delete(f"{API}/smart-playlists/{sp['id']}")
    assert client.get(f"{API}/pins").json() == []


def test_migration_v6_to_v7(tmp_path):
    from homebeat import db
    from homebeat.db import migrations

    path = tmp_path / "x.db"
    conn = db.connect(path)
    db.init(conn)
    for t in migrations.V7_TABLES:
        conn.execute(f"DROP TABLE {t}")
    conn.execute("UPDATE schema_version SET version = 6")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    for t in migrations.V7_TABLES:
        assert migrations._table_exists(conn, t)
    assert not migrations.migrate_v6_to_v7(conn, db.schema_sql())
    assert list(tmp_path.glob("x.db.v6-backup-*"))
