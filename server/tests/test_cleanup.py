"""Library cleanup (schema v9): remove to trash, restore, purge (files deleted only when library_delete_files is
on), the delete list, pruning reports, the admin preview stream, and duplicates resolved into the trash."""
import os
import shutil
from datetime import UTC, datetime, timedelta

import pytest

from nasmusic.library import cleanup, scanner
from tests.conftest import track_ids, user_client


def _missing(conn, tid):
    return conn.execute("SELECT missing FROM tracks WHERE id = ?", (tid,)).fetchone()[0]


def _album_id(conn, title):
    return conn.execute("SELECT id FROM albums WHERE title = ?", (title,)).fetchone()[0]


def _search_titles(client, q):
    return {t["title"] for t in client.get("/api/v1/library/search", params={"q": q}).json()["tracks"]}


def test_remove_album_hides_it_and_restore_brings_it_back(client, conn, cfg):
    ids = track_ids(conn)
    assert "Song 1" in _search_titles(client, "Song")
    r = client.post("/api/v1/admin/cleanup/remove", json={"albumIds": [_album_id(conn, "First Album")],
                                                           "reason": "never liked it"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["removed"] == 2 and body["bytes"] > 0
    assert _missing(conn, ids["Song 1"]) == 3 and _missing(conn, ids["Song 2"]) == 3
    assert not _search_titles(client, "Song")
    # a rescan (full) keeps them removed
    scanner.scan(cfg, conn, full=True)
    assert _missing(conn, ids["Song 1"]) == 3
    trash = client.get("/api/v1/admin/cleanup/trash").json()
    assert trash["trashDays"] == 30 and trash["deleteFiles"] is False
    (b,) = trash["batches"]
    assert b["count"] == 2 and b["reason"] == "never liked it" and b["removedBy"] == "alice"
    assert len(client.get(f"/api/v1/admin/cleanup/trash/{b['batchId']}").json()) == 2
    # removing again is a 404 (already removed)
    assert client.post("/api/v1/admin/cleanup/remove", json={"trackIds": [ids["Song 1"]]}).status_code == 404
    r = client.post("/api/v1/admin/cleanup/trash/restore", json={"batchIds": [b["batchId"]]})
    assert r.json() == {"restored": 2}
    assert _missing(conn, ids["Song 1"]) == 0
    assert "Song 1" in _search_titles(client, "Song")
    assert client.get("/api/v1/admin/cleanup/trash").json()["batches"] == []


def test_remove_artist_and_admin_only(client, bob, conn):
    artist = conn.execute("SELECT id FROM artists WHERE name = 'The Band'").fetchone()[0]
    assert bob.post("/api/v1/admin/cleanup/remove", json={"artistIds": [artist]}).status_code == 403
    assert bob.get("/api/v1/admin/cleanup/summary").status_code == 403
    r = client.post("/api/v1/admin/cleanup/remove", json={"artistIds": [artist]})
    assert r.json()["removed"] == 2


def test_purge_without_deletion_keeps_file_and_makes_delete_list(client, conn, cfg, library):
    ids = track_ids(conn)
    pid = client.post("/api/v1/playlists", json={"name": "P", "trackIds": [ids["Song 1"], ids["Opening"]]}).json()["id"]
    b = client.post("/api/v1/admin/cleanup/remove", json={"trackIds": [ids["Song 1"]]}).json()["batchId"]
    # not expired yet: the nightly purge leaves it alone
    assert cleanup.purge(conn, cfg)["purged"] == 0
    r = client.post("/api/v1/admin/cleanup/trash/purge", json={"batchIds": [b]}).json()
    assert r == {"purged": 1, "filesDeleted": 0, "freedBytes": 0, "kept": 1, "errors": 0}
    f = library / "Artist One" / "First Album" / "01 Song 1.flac"
    assert f.exists()  # the library was not touched
    assert _missing(conn, ids["Song 1"]) == 3
    # left the playlist
    assert [t["id"] for t in client.get(f"/api/v1/playlists/{pid}").json()["tracks"]] == [ids["Opening"]]
    items = client.get("/api/v1/admin/cleanup/delete-list").json()
    assert [i["path"] for i in items] == ["Artist One/First Album/01 Song 1.flac"]
    sh = client.get("/api/v1/admin/cleanup/delete-list", params={"format": "sh"})
    assert sh.headers["content-type"].startswith("text/x-shellscript")
    assert "rm -fv -- 'Artist One/First Album/01 Song 1.flac'" in sh.text
    # once the admin deletes it by hand, the next purge drops the rows
    shutil.copyfile(f, library / "keep.flac.bak")
    f.unlink()
    cleanup.purge(conn, cfg)
    assert conn.execute("SELECT 1 FROM tracks WHERE id = ?", (ids["Song 1"],)).fetchone() is None
    assert client.get("/api/v1/admin/cleanup/delete-list").json() == []


def test_purge_with_deletion_deletes_files(client, app, conn, cfg, library):
    app.state.ctx.cfg.library_delete_files = True
    cfg.library_delete_files = True
    ids = track_ids(conn)
    album = library / "The Band" / "Second"
    (album / "01 Opening.lrc").write_text("[00:00.00] hi")
    b = client.post("/api/v1/admin/cleanup/remove", json={"albumIds": [_album_id(conn, "Second")]}).json()
    # expired: the nightly cleanup purges it
    later = datetime.now(UTC) + timedelta(days=31)
    r = cleanup.purge(conn, cfg, now=later)
    assert r["purged"] == 2 and r["filesDeleted"] == 2 and r["freedBytes"] == b["bytes"] and r["kept"] == 0
    assert not album.exists()          # emptied folder removed (its .lrc sidecar went with the song)
    assert (library / "The Band").exists() is False
    assert library.exists() and (library / "Artist One").exists()
    assert conn.execute("SELECT COUNT(*) FROM tracks WHERE id IN (?, ?)", (ids["Opening"], ids["Closing"])).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM library_removed").fetchone()[0] == 0


def test_purge_never_follows_symlinks_out_of_the_library(client, conn, cfg, library, tmp_path):
    cfg.library_delete_files = True
    outside = tmp_path / "precious.flac"
    shutil.copyfile(library / "Loose" / "untagged.flac", outside)
    ids = track_ids(conn)
    loose = library / "Loose" / "untagged.flac"
    loose.unlink()
    os.symlink(outside, loose)
    client.post("/api/v1/admin/cleanup/remove", json={"trackIds": [ids["untagged"]]})
    r = cleanup.purge(conn, cfg, now=datetime.now(UTC) + timedelta(days=31))
    assert r["kept"] == 1 and r["errors"] == 1 and r["filesDeleted"] == 0
    assert outside.exists() and loose.is_symlink()
    assert cleanup._library_file(cfg, "../precious.flac") is None


def test_read_only_library_falls_back_to_delete_list(client, conn, cfg, library):
    cfg.library_delete_files = True
    ids = track_ids(conn)
    d = library / "Loose"
    client.post("/api/v1/admin/cleanup/remove", json={"trackIds": [ids["untagged"]]})
    os.chmod(d, 0o555)
    try:
        if os.access(d, os.W_OK):  # running as root: permissions don't stop us
            pytest.skip("cannot make a read-only folder as root")
        r = cleanup.purge(conn, cfg, now=datetime.now(UTC) + timedelta(days=31))
    finally:
        os.chmod(d, 0o755)
    assert r["kept"] == 1 and r["errors"] == 1
    (item,) = cleanup.delete_list(conn)
    assert "PermissionError" in item["error"]


def test_reports(client, conn):
    ids = track_ids(conn)
    s = client.get("/api/v1/admin/cleanup/summary").json()
    assert {r["id"] for r in s["reports"]} == set(cleanup.REPORTS)
    assert s["inTrash"] == 0 and s["lossless"]["lossless"] == 5
    short = client.get("/api/v1/admin/cleanup/reports/short").json()
    assert short["total"] == 5  # the 2-second test files
    # nothing is older than 90 days yet; with 0 days everything unplayed counts
    assert client.get("/api/v1/admin/cleanup/reports/never_played").json()["total"] == 0
    conn.execute("UPDATE tracks SET added_at = '2020-01-01T00:00:00Z'")
    alice = conn.execute("SELECT id FROM users WHERE username = 'alice'").fetchone()[0]
    conn.execute("INSERT INTO user_tracks (user_id, track_id, play_count, skip_count, blocked) VALUES (?, ?, 5, 0, 0)",
                 (alice, ids["Song 1"]))
    conn.execute("INSERT INTO user_tracks (user_id, track_id, play_count, skip_count, blocked) VALUES (?, ?, 0, 4, 0)",
                 (alice, ids["Closing"]))
    conn.execute("INSERT INTO user_tracks (user_id, track_id, play_count, skip_count, blocked) VALUES (?, ?, 1, 0, 1)",
                 (alice, ids["Opening"]))
    never = client.get("/api/v1/admin/cleanup/reports/never_played").json()
    assert {i["title"] for i in never["items"]} == {"Song 2", "untagged", "Closing"}
    albums = client.get("/api/v1/admin/cleanup/reports/unplayed_albums").json()["items"]
    assert len(albums) == 1 and albums[0]["kind"] == "album" and albums[0]["trackIds"] == [ids["untagged"]]
    disliked = {i["title"]: i for i in client.get("/api/v1/admin/cleanup/reports/disliked").json()["items"]}
    assert set(disliked) == {"Closing", "Opening"}
    assert "thumbs-down" in disliked["Opening"]["note"]
    conn.execute("UPDATE tracks SET codec = 'MP3', bitrate = 96 WHERE id = ?", (ids["Song 2"],))
    low = client.get("/api/v1/admin/cleanup/reports/low_quality").json()["items"]
    assert [i["title"] for i in low] == ["Song 2"]
    assert client.get("/api/v1/admin/cleanup/reports/low_quality", params={"threshold": 64}).json()["total"] == 0
    assert client.get("/api/v1/admin/cleanup/reports/nope").status_code == 404


def test_fake_lossless_report_and_dismiss(client, conn):
    ids = track_ids(conn)
    mtime = conn.execute("SELECT mtime FROM tracks WHERE id = ?", (ids["Song 1"],)).fetchone()[0]
    conn.execute("INSERT INTO lossless_checks (track_id, mtime, verdict, cutoff_hz, source_guess, checked_at) "
                 "VALUES (?, ?, 'lossy_source', 16000, 'an MP3 ~128 kbps', '2026-01-01T00:00:00Z')",
                 (ids["Song 1"], mtime))
    (item,) = client.get("/api/v1/admin/cleanup/reports/fake_lossless").json()["items"]
    assert item["id"] == ids["Song 1"] and item["verdict"] == "lossy_source"
    assert "16.0 kHz" in item["note"] and "128" in item["note"]
    assert client.post("/api/v1/admin/cleanup/lossless/dismiss", json={"trackIds": [ids["Song 1"]]}).json() == {"updated": 1}
    assert client.get("/api/v1/admin/cleanup/reports/fake_lossless").json()["total"] == 0


def test_search(client, conn):
    r = client.get("/api/v1/admin/cleanup/search", params={"q": "band"}).json()
    assert "The Band" in [a["name"] for a in r["artists"]]
    assert [a["title"] for a in r["albums"]] == ["Second"]


def test_preview_plays_hidden_and_removed_copies(client, app, conn):
    ids = track_ids(conn)
    client.post("/api/v1/admin/cleanup/remove", json={"trackIds": [ids["Song 1"]]})
    assert client.get(f"/api/v1/stream/{ids['Song 1']}?format=original").status_code == 404
    r = client.get(f"/api/v1/admin/preview/{ids['Song 1']}")
    assert r.status_code == 200 and r.content[:4] == b"fLaC"
    assert r.headers["x-stream-format"].startswith("FLAC")
    # <audio> in bearer mode uses ?token=
    with user_client(app, conn, "carol", admin=True) as c:
        tok = c.token
    from fastapi.testclient import TestClient

    with TestClient(app) as anon:
        assert anon.get(f"/api/v1/admin/preview/{ids['Song 1']}?token={tok}").status_code == 200
        assert anon.get(f"/api/v1/admin/preview/{ids['Song 1']}").status_code == 401
    with user_client(app, conn, "dave", admin=False) as c:
        assert c.get(f"/api/v1/admin/preview/{ids['Song 1']}").status_code == 403


def test_duplicate_resolve_into_trash(client, conn):
    ids = track_ids(conn)
    r = client.post("/api/v1/admin/duplicates/resolve",
                    json={"keepTrackId": ids["Song 1"], "hideTrackIds": [ids["untagged"]], "remove": True})
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 1 and r.json()["batchId"]
    assert _missing(conn, ids["untagged"]) == 3
    (b,) = client.get("/api/v1/admin/cleanup/trash").json()["batches"]
    assert b["reason"] == "duplicate copy"
    # restoring puts it back as a hidden duplicate, not into the library
    client.post("/api/v1/admin/cleanup/trash/restore", json={"trackIds": [ids["untagged"]]})
    assert _missing(conn, ids["untagged"]) == 2


def test_delete_script_quotes_paths():
    s = cleanup.delete_script([{"path": "It's $(rm -rf ~)/a b.flac"}, {"path": "@uploads/x.flac"}])
    assert "rm -fv -- 'It'\"'\"'s $(rm -rf ~)/a b.flac'" in s
    assert "@uploads" not in s.split("set -eu")[1]
