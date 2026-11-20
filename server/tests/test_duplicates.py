"""Duplicate finder (schema v6): grouping on fingerprints, picking the copy to keep, hiding (never deleting),
playlists/favourites moved to the kept copy, hidden copies staying hidden across rescans; v5 -> v6 migration."""
from pathlib import Path

import numpy as np
import pytest

from nasmusic import db
from nasmusic.core import now_iso
from nasmusic.db import migrations
from nasmusic.library import chromaprint, duplicates, scanner
from tests.conftest import track_ids


def _rand(seed, n=300):
    return np.random.default_rng(seed).integers(0, 2**32, n, dtype=np.uint64).astype(np.uint32)


def _noisy(a, seed, rate=0.03):
    rng = np.random.default_rng(seed)
    flips = np.zeros(len(a), dtype=np.uint32)
    for bit in range(32):
        flips |= (rng.random(len(a)) < rate).astype(np.uint32) << bit
    return a ^ flips


def _put_fp(conn, tid, raw, dur=2):
    mtime = conn.execute("SELECT mtime FROM tracks WHERE id = ?", (tid,)).fetchone()[0]
    conn.execute("INSERT OR REPLACE INTO fingerprints (track_id, mtime, duration_s, fingerprint, raw, computed_at) "
                 "VALUES (?, ?, ?, 'x', ?, ?)", (tid, mtime, dur, chromaprint.to_blob(raw), now_iso()))


def test_find_pairs():
    a = _rand(1)
    fps = {"a": (a, 200), "a2": (_noisy(a, 2), 201), "a3": (np.concatenate([_rand(9, 4), _noisy(a, 3)]), 200),
           "b": (_rand(4), 200), "a_long": (a, 260)}
    pairs = duplicates.find_pairs(fps)
    assert set(pairs) == {("a", "a2"), ("a", "a3"), ("a2", "a3")}
    assert all(s > 0.9 for s in pairs.values())
    # shared AcoustID id makes a candidate, but the audio must still match
    pairs = duplicates.find_pairs({"x": (_rand(5), 100), "y": (_rand(6), 100)}, {"x": "aid", "y": "aid"})
    assert pairs == {}


@pytest.fixture
def dups(client, conn):
    ids = track_ids(conn)
    base = _rand(11)
    _put_fp(conn, ids["Song 1"], base)
    _put_fp(conn, ids["untagged"], _noisy(base, 12))
    _put_fp(conn, ids["Opening"], _rand(13))
    _put_fp(conn, ids["Closing"], _rand(14))
    # make the untagged copy look like a worse "(2)" copy
    conn.execute("UPDATE tracks SET codec = 'MP3', bitrate = 128 WHERE id = ?", (ids["untagged"],))
    return ids


def test_groups_and_recommendation(client, dups):
    r = client.get("/api/v1/admin/duplicates")
    assert r.status_code == 200
    gs = r.json()
    assert len(gs) == 1
    g = gs[0]
    assert {t["id"] for t in g["tracks"]} == {dups["Song 1"], dups["untagged"]}
    assert g["tracks"][0]["id"] == dups["Song 1"] and g["tracks"][0]["recommended"]   # FLAC beats MP3
    assert g["similarity"] > 0.85 and g["hiddenCount"] == 0


def test_resolve_hides_moves_and_survives_rescan(client, dups, conn, cfg):
    keep, hide = dups["Song 1"], dups["untagged"]
    files_before = sorted(p for p in cfg.library_root.rglob("*") if p.is_file())
    # the hidden copy is in a playlist and a favourite
    pl = client.post("/api/v1/playlists", json={"name": "P"}).json()
    client.post(f"/api/v1/playlists/{pl['id']}/tracks", json={"trackIds": [hide]})
    assert client.put(f"/api/v1/favourites/{hide}").status_code == 204
    conn.execute("INSERT OR IGNORE INTO user_tracks (user_id, track_id, play_count) VALUES (?, ?, 3) "
                 "ON CONFLICT DO UPDATE SET play_count = 3", (client.user_id, hide))
    r = client.post("/api/v1/admin/duplicates/resolve", json={"keepTrackId": keep, "hideTrackIds": [hide]})
    assert r.status_code == 200 and r.json() == {"hidden": 1, "keptId": keep}
    assert conn.execute("SELECT missing FROM tracks WHERE id = ?", (hide,)).fetchone()[0] == 2
    assert client.get(f"/api/v1/library/tracks/{hide}").status_code == 404
    assert all(t["id"] != hide for t in client.get("/api/v1/library/search", params={"q": "untagged"}).json()["tracks"])
    assert [t["id"] for t in client.get(f"/api/v1/playlists/{pl['id']}").json()["tracks"]] == [keep]
    ut = conn.execute("SELECT starred_at, play_count FROM user_tracks WHERE user_id = ? AND track_id = ?",
                      (client.user_id, keep)).fetchone()
    assert ut["starred_at"] is not None and ut["play_count"] >= 3
    # files are never touched; rescans keep it hidden
    scanner.scan(cfg, conn)
    scanner.scan(cfg, conn, full=True)
    assert conn.execute("SELECT missing FROM tracks WHERE id = ?", (hide,)).fetchone()[0] == 2
    assert sorted(p for p in cfg.library_root.rglob("*") if p.is_file()) == files_before
    # still listed as a group (so the admin can change their mind), now resolved
    g = client.get("/api/v1/admin/duplicates").json()[0]
    assert g["hiddenCount"] == 1 and next(t for t in g["tracks"] if t["id"] == hide)["keptId"] == keep
    # the health "missing files" fix never deletes hidden copies
    assert conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 1").fetchone()[0] == 0
    # unhide
    assert client.post("/api/v1/admin/duplicates/unhide", json={"trackIds": [hide]}).json() == {"unhidden": 1}
    assert client.get(f"/api/v1/library/tracks/{hide}").status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM duplicate_hidden").fetchone()[0] == 0


def test_resolve_validation(client, bob, dups):
    keep = dups["Song 1"]
    assert client.post("/api/v1/admin/duplicates/resolve", json={"keepTrackId": keep,
                                                                 "hideTrackIds": [keep]}).status_code == 422
    assert client.post("/api/v1/admin/duplicates/resolve", json={"keepTrackId": keep,
                                                                 "hideTrackIds": ["nope"]}).status_code == 404
    assert bob.get("/api/v1/admin/duplicates").status_code == 403


def test_hidden_file_that_vanishes_and_unhide(client, dups, conn, cfg):
    hide = dups["untagged"]
    client.post("/api/v1/admin/duplicates/resolve", json={"keepTrackId": dups["Song 1"], "hideTrackIds": [hide]})
    path = cfg.library_root / conn.execute("SELECT path FROM tracks WHERE id = ?", (hide,)).fetchone()[0]
    moved = path.with_suffix(".bak")
    path.rename(moved)   # (test library, not the real one)
    scanner.scan(cfg, conn)
    assert conn.execute("SELECT missing FROM tracks WHERE id = ?", (hide,)).fetchone()[0] == 2
    client.post("/api/v1/admin/duplicates/unhide", json={"trackIds": [hide]})
    scanner.scan(cfg, conn)
    assert conn.execute("SELECT missing FROM tracks WHERE id = ?", (hide,)).fetchone()[0] == 1


# ---------- migration ----------

V5_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v5.sql").read_text()


def test_v5_to_v6(tmp_path):
    path = tmp_path / "nasmusic.db"
    conn = db.connect(path)
    conn.executescript(V5_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (5)")
    conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1', 'owner', 'h', 'x')")
    conn.execute("INSERT INTO artists VALUES ('ar', 'A', 'A', NULL)")
    conn.execute("INSERT INTO albums VALUES ('al', 'ar', 'Al', NULL, NULL, NULL, 'x')")
    conn.execute("INSERT INTO tracks (id, path, album_id, artist_id, title, duration_s, flac_bytes, mtime, added_at) "
                 "VALUES ('t1', 't1.flac', 'al', 'ar', 'T', 1, 1, 1, 'x')")
    conn.execute("INSERT INTO tag_overrides VALUES ('t1', 'title', '\"Fixed\"', 'u1', 'x')")
    with pytest.raises(Exception):
        conn.execute("INSERT INTO tag_overrides VALUES ('t1', 'mb_track', '\"x\"', 'u1', 'x')")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    assert [tuple(r) for r in conn.execute("SELECT track_id, field, value, updated_by FROM tag_overrides")] == \
        [("t1", "title", '"Fixed"', "u1")]
    conn.execute("INSERT INTO tag_overrides VALUES ('t1', 'mb_track', '\"x\"', 'u1', 'x')")
    for t in migrations.V6_TABLES:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert len(list(tmp_path.glob("nasmusic.db.v5-backup-*"))) == 1
    db.init(conn)
    assert len(list(tmp_path.glob("*-backup-*"))) == 1


def test_fresh_db_matches_migrated_tag_overrides(tmp_path):
    conn = db.connect(tmp_path / "fresh.db")
    db.init(conn)
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'tag_overrides'").fetchone()[0]
    assert "mb_albumartist" in sql
