"""Explicit-content flags + family mode (schema v6): flag precedence, the job, server-side filtering across
endpoints, the admin lock."""
import json

import httpx
import pytest
from mutagen.flac import FLAC

from homebeat import db
from homebeat.ai import api as ai
from homebeat.db import family, migrations
from homebeat.library import explicit
from tests.conftest import track_ids, user_client

API = "/api/v1"


# ---------- precedence + detectors (no I/O) ----------


def test_resolve_precedence():
    r = explicit.resolve
    assert r("clean", "explicit", "explicit", "probable") == ("clean", "admin")
    assert r(None, "explicit", "clean", None) == ("explicit", "tags")
    assert r(None, None, "clean", "probable") == ("clean", "deezer")
    assert r(None, None, "not_found", "probable") == ("probable", "lyrics")
    assert r(None, None, None, "clean") == ("unknown", None)  # no swear words is not proof of clean
    assert r(None, None, None, None) == ("unknown", None)


def test_markers_and_word_list():
    assert explicit.marker("Song (Explicit)") == "explicit"
    assert explicit.marker("Album [Clean Version]") == "clean"
    assert explicit.marker("Song - Explicit") == "explicit"
    assert explicit.marker("Clean") is None and explicit.marker("Explicit Content Song") is None
    assert explicit.lyrics_verdict("what the fuck")[0] == "probable"
    assert explicit.lyrics_verdict("shit happens")[0] == "clean"  # one mild word is not enough
    assert explicit.lyrics_verdict("shit shit bitch")[0] == "probable"
    assert explicit.lyrics_verdict("Scunthorpe United, a classic")[0] == "clean"
    assert explicit.lyrics_verdict("")[0] == "none"


def test_deezer_best_match_prefers_album_and_explicit_on_ties():
    items = [
        {"id": 1, "title": "Song", "artist": {"name": "A"}, "album": {"title": "X"}, "duration": 200,
         "explicit_lyrics": False, "explicit_content_lyrics": 0},
        {"id": 2, "title": "Song", "artist": {"name": "A"}, "album": {"title": "X"}, "duration": 200,
         "explicit_lyrics": True, "explicit_content_lyrics": 1},
        {"id": 3, "title": "Song", "artist": {"name": "A"}, "album": {"title": "Mine"}, "duration": 201,
         "explicit_lyrics": False, "explicit_content_lyrics": 0},
        {"id": 4, "title": "Other", "artist": {"name": "A"}, "duration": 200, "explicit_lyrics": True},
    ]
    assert explicit.best_match(items, "A", "Song", "Mine", 200).deezer_id == 3
    assert explicit.best_match(items, "A", "Song", "Nope", 200).deezer_id == 2   # tie -> explicit
    assert explicit.best_match(items, "A", "Song", None, 260).flag == "not_found"  # duration too far off
    assert explicit.best_match(items, "B", "Song", None, 200).flag == "not_found"


# ---------- the job ----------


def _tag(cfg, conn, title, **tags):
    path = cfg.library_root / conn.execute("SELECT path FROM tracks WHERE title = ?", (title,)).fetchone()[0]
    f = FLAC(path)
    for k, v in tags.items():
        f[k] = v
    f.save()


def _deezer(calls):
    def handler(request):
        q = request.url.params["q"].casefold()
        calls.append(q)
        if "closing" in q:
            return httpx.Response(200, json={"data": [{"id": 9, "title": "Closing", "artist": {"name": "The Band"},
                                                      "album": {"title": "Second"}, "duration": 2,
                                                      "explicit_lyrics": True, "explicit_content_lyrics": 1}]})
        if "song 2" in q:
            return httpx.Response(200, json={"data": [{"id": 8, "title": "Song 2", "artist": {"name": "Artist One"},
                                                      "duration": 2, "explicit_lyrics": False}]})
        return httpx.Response(200, json={"data": []})
    return explicit.Deezer(httpx.MockTransport(handler), min_interval_s=0)


@pytest.fixture
def flagged(cfg, scanned):
    """Song 1 tagged explicit (ITUNESADVISORY), Opening tagged clean (EXPLICIT=0), Closing explicit on Deezer,
    Song 2 clean on Deezer, the untagged file has swearing in its embedded lyrics. Tag edits here are test
    set-up; the server itself never writes to the library."""
    conn = scanned
    _tag(cfg, conn, "Song 1", ITUNESADVISORY="1")
    _tag(cfg, conn, "Opening", EXPLICIT="0")
    untagged = conn.execute("SELECT title FROM tracks WHERE path LIKE 'Loose/%'").fetchone()[0]
    _tag(cfg, conn, untagged, LYRICS="this fucking song")
    calls: list[str] = []
    res = explicit.run(conn, cfg.library_root, _deezer(calls))
    return conn, res, calls, untagged


def test_job_fills_flags_from_every_source_and_resumes(cfg, flagged):
    conn, res, calls, untagged = flagged
    ids = track_ids(conn)
    st = family.status_of(conn, ids.values())
    assert st[ids["Song 1"]] == "explicit"     # file tag
    assert st[ids["Opening"]] == "clean"       # file tag
    assert st[ids["Closing"]] == "explicit"    # Deezer
    assert st[ids["Song 2"]] == "clean"        # Deezer
    assert st[ids[untagged]] == "probable"     # lyrics word list ("Unknown Artist" is never sent to Deezer)
    src = dict(conn.execute("SELECT track_id, source FROM track_explicit").fetchall())
    assert (src[ids["Song 1"]], src[ids["Closing"]], src[ids[untagged]]) == ("tags", "deezer", "lyrics")
    assert res["complete"] and res["counts"] == {"explicit": 2, "probable": 1, "clean": 2, "unknown": 0}
    assert len(calls) == 2  # only the two tracks the tags left open
    # resumable: a second run reads nothing and asks Deezer nothing
    again = explicit.run(conn, cfg.library_root, _deezer(calls))
    assert again["tagsRead"] == 0 and again["deezerAsked"] == 0 and len(calls) == 2


def test_job_time_budget_and_unreachable_deezer(cfg, scanned):
    conn = scanned
    res = explicit.run(conn, cfg.library_root, None, time_budget_s=0)
    assert res["tagsRead"] == 0 and not res["complete"]  # nothing done, nothing lost
    refuse = explicit.Deezer(httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))),
                             min_interval_s=0)
    res = explicit.run(conn, cfg.library_root, refuse)
    assert res["tagsRead"] == 5 and res["deezerUnreachable"] and res["deezerAsked"] == 0
    # network errors are not cached: the tracks are still waiting for Deezer (only "Unknown Artist", which is
    # never sent, has an answer)
    assert conn.execute("SELECT COUNT(*) FROM track_explicit WHERE deezer_flag IS NOT NULL").fetchone()[0] == 1


# ---------- admin verdicts (tag override 'explicit') ----------


def test_admin_override_beats_everything_single_bulk_album(client, bob, flagged):
    conn, *_ = flagged
    ids = track_ids(conn)
    r = client.post(f"{API}/admin/explicit", json={"trackIds": [ids["Song 1"]], "value": "clean"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == {ids["Song 1"]: "clean"}
    assert client.get(f"{API}/library/tracks/{ids['Song 1']}").json()["explicit"] == "clean"
    d = client.get(f"{API}/admin/tags/tracks/{ids['Song 1']}").json()
    assert d["explicit"]["status"] == "clean" and d["explicit"]["source"] == "admin" and d["explicit"]["tags"] == "explicit"
    assert "explicit" in d["overridden"]
    # back to automatic: the file tag decides again
    client.post(f"{API}/admin/explicit", json={"trackIds": [ids["Song 1"]], "value": None})
    assert family.status_of(conn, [ids["Song 1"]])[ids["Song 1"]] == "explicit"
    # per album, and through the generic tag editor
    album = conn.execute("SELECT id FROM albums WHERE title = 'Second'").fetchone()[0]
    client.post(f"{API}/admin/explicit", json={"albumIds": [album], "value": "explicit"})
    assert set(family.status_of(conn, [ids["Opening"], ids["Closing"]]).values()) == {"explicit"}
    client.post(f"{API}/admin/tags/edit", json={"trackIds": [ids["Opening"]], "set": {"explicit": "clean"}})
    assert family.status_of(conn, [ids["Opening"]])[ids["Opening"]] == "clean"
    assert conn.execute("SELECT COUNT(*) FROM tag_edits WHERE field = 'explicit'").fetchone()[0] >= 4
    assert client.post(f"{API}/admin/explicit", json={"trackIds": [ids["Opening"]], "value": "maybe"}).status_code == 422
    # admins only
    assert bob.post(f"{API}/admin/explicit", json={"trackIds": [ids["Opening"]], "value": "clean"}).status_code == 403
    assert bob.get(f"{API}/admin/explicit").status_code == 403


def test_review_list(client, flagged):
    conn, _, _, untagged = flagged
    r = client.get(f"{API}/admin/explicit").json()  # default: probable + unknown
    assert [i["title"] for i in r["items"]] == [untagged] and r["items"][0]["lyricsHits"] == 1
    r = client.get(f"{API}/admin/explicit", params={"status": ["explicit"], "source": "deezer"}).json()
    assert [i["title"] for i in r["items"]] == ["Closing"]
    assert r["counts"]["explicit"] == 2


# ---------- badges ----------


def test_badges_for_everyone(client, flagged):
    conn, *_ = flagged
    ids = track_ids(conn)
    first = conn.execute("SELECT id FROM albums WHERE title = 'First Album'").fetchone()[0]
    al = client.get(f"{API}/library/albums/{first}").json()
    assert al["explicit"] is True
    assert {t["title"]: t["explicit"] for t in al["tracks"]} == {"Song 1": "explicit", "Song 2": "clean"}
    albums = {a["title"]: a["explicit"] for a in client.get(f"{API}/library/albums").json()}
    assert albums["First Album"] and albums["Second"] and not albums["Loose"]
    assert client.get(f"{API}/library/tracks/{ids['Song 1']}").json()["explicit"] == "explicit"


# ---------- filtering across endpoints ----------


def _titles(tracks):
    return sorted(t["title"] for t in tracks)


def test_family_mode_filters_every_endpoint(client, bob, flagged, monkeypatch):
    conn, _, _, untagged = flagged
    ids = track_ids(conn)
    assert bob.put(f"{API}/me/family", json={"enabled": True}).json() == {
        "enabled": True, "hideProbable": True, "hideUnknown": False, "locked": False, "hasPin": False}
    allowed = ["Opening", "Song 2"]
    assert _titles(bob.get(f"{API}/library/tracks").json()) == allowed
    assert _titles(client.get(f"{API}/library/tracks").json()) == sorted(ids)  # alice is not filtered
    first = conn.execute("SELECT id FROM albums WHERE title = 'First Album'").fetchone()[0]
    al = bob.get(f"{API}/library/albums/{first}").json()
    assert _titles(al["tracks"]) == ["Song 2"] and al["trackCount"] == 1 and al["explicit"] is False
    assert {a["title"] for a in bob.get(f"{API}/library/albums").json()} == {"First Album", "Second"}  # Loose: empty
    assert bob.get(f"{API}/library/tracks/{ids['Song 1']}").status_code == 404
    assert bob.get(f"{API}/library/tracks/{ids['Song 1']}/info").status_code == 404
    s = bob.get(f"{API}/library/search", params={"q": "Song"}).json()
    assert _titles(s["tracks"]) == ["Song 2"]
    s = bob.get(f"{API}/search", params={"q": "Closing", "scope": "library"}).json()
    assert s["tracks"] == [] and s["albums"] == []  # "Second" has no Closing match left
    assert {g["name"]: g["trackCount"] for g in bob.get(f"{API}/library/genres").json()} == {"Rock": 1}
    f = bob.get(f"{API}/library/folders", params={"path": "Artist One/First Album"}).json()
    assert _titles(f["tracks"]) == ["Song 2"]
    assert all(a["name"] != "Unknown Artist" for a in bob.get(f"{API}/library/artists").json())
    # smart lists + favourites + sync manifest
    for t in ("Song 1", "Song 2"):
        conn.execute("INSERT INTO user_tracks (user_id, track_id, starred_at) VALUES (?, ?, '2026-01-01T00:00:00Z')",
                     (bob.user_id, ids[t]))
    assert _titles(bob.get(f"{API}/lists/favourites").json()) == ["Song 2"]
    assert _titles(bob.get(f"{API}/favourites").json()) == ["Song 2"]
    assert [i["trackId"] for i in bob.get(f"{API}/sync/manifest").json()["items"]] == [ids["Song 2"]]
    # mixes written by the AI
    conn.execute("INSERT INTO mixes (id, user_id, kind, title, for_date, created_at) VALUES "
                 "('m1', ?, 'daily', 'Daily', '2026-11-20', '2026-11-20T00:00:00Z')", (bob.user_id,))
    for pos, t in enumerate(("Song 1", "Closing", "Song 2")):
        conn.execute("INSERT INTO mix_tracks VALUES ('m1', ?, ?, 1.0, 'r')", (pos, ids[t]))
    assert _titles(x["track"] for x in bob.get(f"{API}/mixes/m1").json()["tracks"]) == ["Song 2"]
    # radio: the engine's picks are filtered before they reach the queue
    monkeypatch.setattr(ai, "radio_start", lambda c_, cfg, uid, seed: ("r1", [ids["Song 1"], ids["Song 2"]]))
    q = bob.post(f"{API}/radio", json={"seed": {"type": "random"}}).json()
    assert _titles(q["tracks"]) == ["Song 2"]
    # new queues / adds drop hidden tracks (this is also where Jam guest adds land)
    q = bob.post(f"{API}/queues", json={"name": "Mine", "trackIds": [ids["Song 1"], ids["Song 2"], ids["Opening"]],
                                        "startIndex": 0}).json()
    assert _titles(q["tracks"]) == ["Opening", "Song 2"] and q["currentIndex"] == 0
    q = bob.post(f"{API}/queues/{q['id']}/tracks", json={"trackIds": [ids["Closing"]], "position": "end"}).json()
    assert len(q["tracks"]) == 2
    # tracks already in a queue stay but are flagged, so clients skip them
    conn.execute("INSERT INTO queue_tracks VALUES (?, 2, ?)", (q["id"], ids["Closing"]))
    q = bob.get(f"{API}/queues/{q['id']}").json()
    assert q["tracks"][2]["familyHidden"] is True and "familyHidden" not in q["tracks"][0]
    # stream is NOT blocked while family mode is unlocked (the user could turn it off anyway)
    assert bob.get(f"{API}/stream/{ids['Song 1']}", params={"format": "flac"}).status_code == 200


def test_hide_options(bob, flagged):
    conn, _, _, untagged = flagged
    bob.put(f"{API}/me/family", json={"enabled": True, "hideProbable": False})
    assert untagged in _titles(bob.get(f"{API}/library/tracks").json())
    conn.execute("DELETE FROM track_explicit WHERE track_id = ?", (track_ids(conn)["Song 2"],))  # -> unknown
    bob.put(f"{API}/me/family", json={"hideUnknown": True})
    assert _titles(bob.get(f"{API}/library/tracks").json()) == sorted(["Opening", untagged])


def test_ai_mask_and_shared_helpers(bob, flagged):
    conn, *_ = flagged
    ids = track_ids(conn)
    bob.put(f"{API}/me/family", json={"enabled": True})
    hidden = family.all_hidden(conn, bob.user_id)
    assert {ids["Song 1"], ids["Closing"]} <= hidden and ids["Song 2"] not in hidden
    assert family.filter_ids(conn, bob.user_id, [ids["Song 2"], ids["Song 1"], ids["Song 2"]]) == [ids["Song 2"]] * 2
    from homebeat.ai.recommender import family_mask

    lib_ids = [ids["Song 1"], ids["Song 2"]]

    class Lib:
        ids = lib_ids

        def __len__(self):
            return 2

    assert family_mask(conn, bob.user_id, Lib()).tolist() == [True, False]
    assert family_mask(conn, "nobody", Lib()) is None


# ---------- the lock ----------


def test_admin_lock_pin_and_stream_enforcement(app, conn, client, bob, flagged):
    ids = track_ids(conn)
    r = client.put(f"{API}/admin/users/{bob.user_id}/family", json={"locked": True, "pin": "2468"})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] and r.json()["locked"] and r.json()["hasPin"] and r.json()["lockedBy"] == "alice"
    assert bob.get(f"{API}/me/family").json()["locked"] is True
    # the kid can tighten but not loosen
    assert bob.put(f"{API}/me/family", json={"hideUnknown": True}).status_code == 200
    assert bob.put(f"{API}/me/family", json={"hideUnknown": False}).status_code == 403
    assert bob.put(f"{API}/me/family", json={"enabled": False}).status_code == 403
    assert bob.put(f"{API}/me/family", json={"enabled": False, "pin": "0000"}).status_code == 403
    assert family.policy(conn, bob.user_id).enabled
    # locked: hidden tracks cannot be streamed or cast either
    assert bob.get(f"{API}/stream/{ids['Song 1']}", params={"format": "flac"}).status_code == 403
    assert bob.get(f"{API}/stream/{ids['Song 2']}", params={"format": "flac"}).status_code == 200
    # bob cannot unlock himself through the admin API or user settings
    assert bob.put(f"{API}/admin/users/{bob.user_id}/family", json={"locked": False}).status_code == 403
    bob.put(f"{API}/me/settings", json={"settings": {"familyMode": False}})
    assert family.policy(conn, bob.user_id).locked
    # the right PIN turns it off and unlocks
    r = bob.put(f"{API}/me/family", json={"enabled": False, "pin": "2468"})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["locked"] is False
    # admin-only unlock (no PIN): even the old PIN no longer works
    client.put(f"{API}/admin/users/{bob.user_id}/family", json={"locked": True, "clearPin": True})
    assert bob.put(f"{API}/me/family", json={"enabled": False, "pin": "2468"}).status_code == 403
    listing = {u["username"]: u for u in client.get(f"{API}/admin/family").json()}
    assert listing["bob"]["locked"] and not listing["bob"]["hasPin"] and not listing["alice"]["enabled"]
    r = client.put(f"{API}/admin/users/{bob.user_id}/family", json={"locked": False, "enabled": False})
    assert r.json()["locked"] is False and r.json()["enabled"] is False
    assert client.put(f"{API}/admin/users/nope/family", json={"locked": True}).status_code == 404


def test_wrong_pin_rate_limit(app, conn, client, flagged):
    kid = user_client(app, conn, "kid", admin=False)
    with kid:
        client.put(f"{API}/admin/users/{kid.user_id}/family", json={"locked": True, "pin": "1357"})
        codes = [kid.put(f"{API}/me/family", json={"enabled": False, "pin": "0000"}).status_code for _ in range(6)]
        assert codes[:5] == [403] * 5 and codes[5] == 429


def test_cast_sign_skips_hidden(client, bob, flagged):
    conn, *_ = flagged
    ids = track_ids(conn)
    bob.put(f"{API}/me/family", json={"enabled": True})
    r = bob.post(f"{API}/cast/sign", json={"trackIds": [ids["Song 1"], ids["Song 2"]]})
    assert r.status_code == 200
    assert [i["trackId"] for i in r.json()["items"]] == [ids["Song 2"]]


# ---------- migration ----------


def test_v5_to_v6_keeps_overrides(tmp_path):
    from tests.test_migration_v5 import make_v4

    path = tmp_path / "homebeat.db"
    conn = make_v4(path)
    migrations.migrate_v4_to_v5(conn, db.schema_sql(), make_backup=False)
    # what a real v5 database has (this code base's v4->v5 step would already create the v6 CHECK)
    conn.execute("DROP TABLE tag_overrides")
    conn.execute("""CREATE TABLE tag_overrides (track_id TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
        field TEXT NOT NULL CHECK (field IN ('title','artist','album_artist','album','track_no','disc_no',
        'year','genre','lyrics','cover')), value TEXT NOT NULL, updated_by TEXT REFERENCES users(id) ON DELETE SET NULL,
        updated_at TEXT NOT NULL, PRIMARY KEY (track_id, field))""")
    conn.execute("INSERT INTO tag_overrides VALUES ('t1', 'title', '\"X\"', 'u1', 'now')")
    with pytest.raises(Exception):
        conn.execute("INSERT INTO tag_overrides VALUES ('t2', 'explicit', '\"clean\"', 'u1', 'now')")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    assert conn.execute("SELECT value FROM tag_overrides WHERE track_id = 't1'").fetchone()[0] == '"X"'
    conn.execute("INSERT INTO tag_overrides VALUES ('t2', 'explicit', '\"clean\"', 'u1', 'now')")
    for t in ("track_explicit", "family_mode"):
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert list(tmp_path.glob("homebeat.db.v5-backup-*"))
    assert json.loads(conn.execute("SELECT value FROM tag_overrides WHERE track_id = 't2'").fetchone()[0]) == "clean"
