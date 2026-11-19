"""Admin tag editing (schema v5): overrides layered on file tags, surviving rescans; permissions; history."""
import io
import json
import os

import httpx
import pytest
from mutagen.flac import FLAC
from PIL import Image

from nasmusic.library import scanner
from tests.conftest import png_bytes, track_ids


def _tid(conn, title):
    return track_ids(conn)[title]


def _album_of(conn, tid):
    return conn.execute("SELECT al.id, al.title, ar.name FROM tracks t JOIN albums al ON al.id = t.album_id "
                        "JOIN artists ar ON ar.id = al.artist_id WHERE t.id = ?", (tid,)).fetchone()


def _md5(path):
    import hashlib

    return hashlib.md5(path.read_bytes()).hexdigest()


def test_single_edit_is_effective_everywhere_and_files_untouched(client, conn, cfg):
    before = {p: _md5(p) for p in cfg.library_root.rglob("*") if p.is_file()}
    tid = _tid(conn, "Song 1")
    r = client.post("/api/v1/admin/tags/edit", json={"trackIds": [tid], "set": {"title": "Song One", "genre": "Jazz",
                                                                                  "year": 1984}})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == 3
    t = client.get(f"/api/v1/library/tracks/{tid}").json()
    assert (t["title"], t["genre"], t["year"]) == ("Song One", "Jazz", 1984)
    # search, genres and the detail view use the effective tags
    assert any(x["id"] == tid for x in client.get("/api/v1/library/search", params={"q": "Song One"}).json()["tracks"])
    assert "Jazz" in [g["name"] for g in client.get("/api/v1/library/genres").json()]
    d = client.get(f"/api/v1/admin/tags/tracks/{tid}").json()
    assert d["file"]["title"] == "Song 1" and d["effective"]["title"] == "Song One"
    assert sorted(d["overridden"]) == ["genre", "title", "year"]
    # the music files were never written
    assert {p: _md5(p) for p in cfg.library_root.rglob("*") if p.is_file()} == before


def test_overrides_survive_rescans_including_changed_files(client, conn, cfg):
    tid = _tid(conn, "Song 2")
    client.post("/api/v1/admin/tags/edit", json={"trackIds": [tid], "set": {"title": "Fixed", "artist": "Guest"}})
    scanner.scan(cfg, conn)
    scanner.scan(cfg, conn, full=True)
    row = conn.execute("SELECT t.title, ar.name FROM tracks t JOIN artists ar ON ar.id = t.artist_id WHERE t.id = ?",
                       (tid,)).fetchone()
    assert tuple(row) == ("Fixed", "Guest")
    # the file changes on disk (re-tagged outside): file tags update, the override still wins
    path = cfg.library_root / conn.execute("SELECT path FROM tracks WHERE id = ?", (tid,)).fetchone()[0]
    f = FLAC(path)
    f["title"] = "Retagged"
    f["genre"] = "Blues"
    f.save()
    os.utime(path, (1, 1))
    scanner.scan(cfg, conn)
    row = conn.execute("SELECT title, genre, file_tags FROM tracks WHERE id = ?", (tid,)).fetchone()
    assert row["title"] == "Fixed" and row["genre"] == "Blues"
    assert json.loads(row["file_tags"])["title"] == "Retagged"
    # revert to file tags
    r = client.post("/api/v1/admin/tags/edit", json={"trackIds": [tid], "revert": ["*"]})
    assert r.status_code == 200
    assert client.get(f"/api/v1/library/tracks/{tid}").json()["title"] == "Retagged"
    assert conn.execute("SELECT COUNT(*) FROM tag_overrides WHERE track_id = ?", (tid,)).fetchone()[0] == 0


def test_album_edit_regroups_and_cleans_up(client, conn, cfg):
    tid = _tid(conn, "Song 1")
    old_album = _album_of(conn, tid)["id"]
    r = client.post(f"/api/v1/admin/tags/albums/{old_album}", json={"set": {"album": "First Album (Remaster)",
                                                                             "albumArtist": "Artist 1"}})
    assert r.status_code == 200 and r.json()["changed"] == 4
    al = _album_of(conn, tid)
    assert (al["title"], al["name"]) == ("First Album (Remaster)", "Artist 1")
    assert conn.execute("SELECT COUNT(*) FROM albums WHERE id = ?", (old_album,)).fetchone()[0] == 0
    both = conn.execute("SELECT COUNT(DISTINCT album_id) FROM tracks WHERE title IN ('Song 1','Song 2')").fetchone()[0]
    assert both == 1
    # folder art still found for the new album id
    assert client.get(f"/api/v1/library/albums/{al['id']}").json()["hasArt"] is True
    assert client.get(f"/api/v1/art/{al['id']}", params={"size": 64}).status_code == 200
    # a full rescan gives the same grouping
    scanner.scan(cfg, conn, full=True)
    assert _album_of(conn, tid)["id"] == al["id"]
    # history is one batch over both tracks
    h = client.get("/api/v1/admin/tags/history", params={"albumId": al["id"]}).json()
    assert len(h) == 4 and len({e["batchId"] for e in h}) == 1 and h[0]["user"] == "alice"
    fields = {(e["field"], e["oldValue"], e["newValue"]) for e in h}
    assert ("album", "First Album", "First Album (Remaster)") in fields


def test_bulk_edit_track_numbers_and_clear(client, conn):
    ids = [_tid(conn, "Opening"), _tid(conn, "Closing")]
    r = client.post("/api/v1/admin/tags/edit", json={"trackIds": ids, "set": {"genre": None, "discNo": 2}})
    assert r.status_code == 200
    for i in ids:
        t = client.get(f"/api/v1/library/tracks/{i}").json()
        assert t["genre"] is None and t["discNo"] == 2
    assert client.post("/api/v1/admin/tags/edit", json={"trackIds": ids, "set": {"bogus": 1}}).status_code == 422
    assert client.post("/api/v1/admin/tags/edit", json={"trackIds": ["nope"], "set": {"genre": "x"}}).status_code == 404
    assert client.post("/api/v1/admin/tags/edit", json={"trackIds": ids}).status_code == 422


def test_artist_override_drops_mbid_grouping(client, conn):
    tid = _tid(conn, "Opening")  # file has a MusicBrainz album artist id
    al = _album_of(conn, tid)["id"]
    client.post(f"/api/v1/admin/tags/albums/{al}", json={"set": {"albumArtist": "Band, The"}})
    a = _album_of(conn, tid)
    assert a["name"] == "Band, The"
    aid = conn.execute("SELECT artist_id FROM albums WHERE id = ?", (a["id"],)).fetchone()[0]
    assert not aid.startswith("mb:")


def test_cover_upload_and_revert(client, conn, cfg):
    tid = _tid(conn, "Song 1")
    al = _album_of(conn, tid)["id"]
    before = client.get(f"/api/v1/art/{al}", params={"size": 64}).content
    r = client.put(f"/api/v1/admin/tags/albums/{al}/cover", content=png_bytes((0, 200, 0), (300, 300)),
                   headers={"Content-Type": "image/png"})
    assert r.status_code == 200, r.text
    img = Image.open(io.BytesIO(client.get(f"/api/v1/art/{al}", params={"size": 64}).content))
    assert img.getpixel((32, 32))[1] > 150  # green upload, not the blue folder cover
    scanner.scan(cfg, conn, full=True)
    assert conn.execute("SELECT art_path FROM albums WHERE id = ?", (al,)).fetchone()[0].startswith("@upload/")
    assert client.put(f"/api/v1/admin/tags/albums/{al}/cover", content=b"not an image").status_code == 422
    assert client.delete(f"/api/v1/admin/tags/albums/{al}/cover").status_code == 200
    assert client.get(f"/api/v1/art/{al}", params={"size": 64}).content == before


def test_shared_lyrics_override(client, bob, conn):
    tid = _tid(conn, "Song 1")
    assert client.post("/api/v1/admin/tags/edit", json={"trackIds": [tid], "set": {"lyrics": "[00:01.00]la la"}}
                       ).status_code == 200
    r = bob.get(f"/api/v1/tracks/{tid}/lyrics").json()
    assert r["source"] == "library" and r["synced"] and "library" in r["sources"]
    bob.put(f"/api/v1/tracks/{tid}/lyrics", json={"raw": "my own"})
    assert bob.get(f"/api/v1/tracks/{tid}/lyrics").json()["source"] == "user"
    client.post("/api/v1/admin/tags/edit", json={"trackIds": [tid], "set": {"lyrics": None}})
    assert client.get(f"/api/v1/tracks/{tid}/lyrics").status_code == 404


def test_tag_editing_is_admin_only(bob, conn):
    tid = _tid(conn, "Song 1")
    assert bob.post("/api/v1/admin/tags/edit", json={"trackIds": [tid], "set": {"title": "x"}}).status_code == 403
    assert bob.get(f"/api/v1/admin/tags/tracks/{tid}").status_code == 403
    assert bob.get("/api/v1/admin/tags/history").status_code == 403


def test_promote_demote_and_last_admin(client, bob, conn):
    assert client.patch(f"/api/v1/admin/users/{bob.user_id}", json={"isAdmin": True}).json()["isAdmin"] is True
    assert bob.get("/api/v1/admin/tags/history").status_code == 200
    assert bob.patch(f"/api/v1/admin/users/{client.user_id}", json={"isAdmin": False}).status_code == 200
    for (uid,) in conn.execute("SELECT id FROM users WHERE is_admin = 1 AND id != ?", (bob.user_id,)).fetchall():
        assert bob.patch(f"/api/v1/admin/users/{uid}", json={"isAdmin": False}).status_code == 200
    # bob is now the only admin and can't demote themself
    assert bob.patch(f"/api/v1/admin/users/{bob.user_id}", json={"isAdmin": False}).status_code == 409


def test_musicbrainz_lookup(client, conn, app):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        if request.url.path.endswith("/recording"):
            return httpx.Response(200, json={"recordings": [{
                "id": "rec1", "score": 100, "title": "Song 1",
                "artist-credit": [{"name": "Artist One", "joinphrase": ""}],
                "releases": [{"id": "rel1", "title": "First Album", "date": "2001-05-01",
                              "media": [{"position": 1, "track": [{"number": "1"}]}]}]}]})
        return httpx.Response(200, json={"id": "rel1", "title": "First Album", "date": "2001",
                                         "artist-credit": [{"name": "Artist One"}], "genres": [{"name": "rock", "count": 3}],
                                         "media": [{"position": 1, "tracks": [{"number": "1", "title": "Song 1",
                                                                               "recording": {"id": "rec1"}}]}]})

    app.state.ctx.http_transport = httpx.MockTransport(handler)
    r = client.get("/api/v1/admin/tags/musicbrainz", params={"trackId": _tid(conn, "Song 1")})
    assert r.status_code == 200, r.text
    res = r.json()["results"][0]
    assert (res["title"], res["album"], res["year"], res["trackNo"]) == ("Song 1", "First Album", 2001, 1)
    ua = seen[0].headers["user-agent"]
    assert ua.startswith("nas-music/") and "@" not in ua
    rel = client.get("/api/v1/admin/tags/musicbrainz/release/00000000-0000-0000-0000-000000000001").json()
    assert rel["genre"] == "Rock" and rel["tracks"][0]["title"] == "Song 1"


@pytest.mark.parametrize("missing_file_tags", [True])
def test_pre_v5_rows_read_file_tags_on_first_edit(client, conn, missing_file_tags):
    tid = _tid(conn, "Song 1")
    conn.execute("UPDATE tracks SET file_tags = NULL")
    client.post("/api/v1/admin/tags/edit", json={"trackIds": [tid], "set": {"title": "X"}})
    assert json.loads(conn.execute("SELECT file_tags FROM tracks WHERE id = ?", (tid,)).fetchone()[0])["title"] == "Song 1"
