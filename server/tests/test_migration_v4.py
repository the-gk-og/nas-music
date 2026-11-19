"""v3 -> v4 against the exact v3 schema that is live on the NAS (tests/fixtures/schema_v3.sql)."""
from pathlib import Path

import pytest

from nasmusic import db
from nasmusic.db import migrations

V3_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v3.sql").read_text()


def make_v3(path: Path):
    conn = db.connect(path)
    conn.executescript(V3_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (3)")
    conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1', 'owner', 'h', 'x')")
    conn.execute("INSERT INTO artists VALUES ('ar', 'A', 'A', NULL)")
    conn.execute("INSERT INTO albums VALUES ('al', 'ar', 'Al', NULL, NULL, NULL, 'x')")
    conn.execute("INSERT INTO tracks (id, path, album_id, artist_id, title, duration_s, flac_bytes, mtime, added_at) "
                 "VALUES ('t1', 'a.flac', 'al', 'ar', 'T', 1, 1, 1, 'x')")
    conn.execute("INSERT INTO queues (id, user_id, name, slot, created_at, updated_at, radio_id) "
                 "VALUES ('q', 'u1', 'Q', 1, 'x', 'x', 'r1')")
    conn.execute("INSERT INTO radio_sessions VALUES ('r1', 'u1', 'random', NULL, 'Your radio', '[]', '[]', 'x', 'x')")
    conn.execute("INSERT INTO toget (id, user_id, type, title, artist, match_key, created_at, updated_at) "
                 "VALUES ('g', 'u1', 'album', 'X', 'Y', 'y|x', 'x', 'x')")
    return conn


def test_v3_to_v4(tmp_path):  # chains on to v5
    path = tmp_path / "nasmusic.db"
    make_v3(path).close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == 5
    for t in ("lyrics_cache", "bug_reports"):
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()
    assert conn.execute("SELECT radio_id FROM queues").fetchone()[0] == "r1"
    assert conn.execute("SELECT title FROM radio_sessions").fetchone()[0] == "Your radio"
    assert conn.execute("SELECT COUNT(*) FROM toget").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert len(list(tmp_path.glob("nasmusic.db.v3-backup-*"))) == 1
    conn.execute("INSERT INTO lyrics_cache (track_id, status, fetched_at) VALUES ('t1', 'not_found', 'x')")
    for _ in range(2):
        db.init(conn)
    assert len(list(tmp_path.glob("*-backup-*"))) == 1


def test_v3_to_v4_failure_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "nasmusic.db"
    make_v3(path).close()
    real = migrations._create_indexes

    def boom(conn, stmts):
        real(conn, stmts)
        raise RuntimeError("disk full")

    monkeypatch.setattr(migrations, "_create_indexes", boom)
    conn = db.connect(path)
    with pytest.raises(RuntimeError):
        db.init(conn)
    assert migrations.current_version(conn) == 3
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'lyrics_cache'").fetchone()


def test_v2_chains_to_v4_with_one_backup(tmp_path):
    from tests.test_migration_v3 import make_v2

    path = tmp_path / "nasmusic.db"
    make_v2(path).close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == 5
    assert [p.name.split(".")[2].split("-")[0] for p in tmp_path.glob("*-backup-*")] == ["v2"]
