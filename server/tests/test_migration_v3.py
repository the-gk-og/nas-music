"""v2 -> v3 against a database created by the exact v2 schema that is live on the NAS
(tests/fixtures/schema_v2.sql = server/homebeat/db/schema.sql at main 85940d9)."""
from pathlib import Path

import pytest

from homebeat import db
from homebeat.db import migrations

V2_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v2.sql").read_text()


def make_v2(path: Path):
    conn = db.connect(path)
    conn.executescript(V2_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (2)")
    conn.execute("INSERT INTO users (id, username, password_hash, is_admin, created_at) "
                 "VALUES ('u1', 'owner', 'scrypt$x', 1, '2026-11-01T00:00:00Z')")
    conn.execute("INSERT INTO artists VALUES ('ar', 'A', 'A', NULL)")
    conn.execute("INSERT INTO albums VALUES ('al', 'ar', 'Al', NULL, NULL, NULL, '2026-11-01T00:00:00Z')")
    conn.execute("INSERT INTO tracks (id, path, album_id, artist_id, title, duration_s, flac_bytes, mtime, added_at) "
                 "VALUES ('t1', 'a.flac', 'al', 'ar', 'T', 1, 1, 1, 'x')")
    conn.execute("INSERT INTO user_tracks (user_id, track_id, starred_at, play_count) VALUES ('u1', 't1', 'x', 7)")
    conn.execute("INSERT INTO queues (id, user_id, name, slot, current_index, position_ms, created_at, updated_at) "
                 "VALUES ('q1', 'u1', 'Album: Al', 1, 0, 1234, 'x', 'x')")
    conn.execute("INSERT INTO queue_tracks VALUES ('q1', 0, 't1')")
    conn.execute("INSERT INTO devices (id, user_id, name, kind, token_hash, created_at) "
                 "VALUES ('d1', 'u1', 'Pixel', 'android', 'h', 'x')")
    conn.execute("INSERT INTO events (id, user_id, type, track_id, at, received_at, context) "
                 "VALUES ('e1', 'u1', 'play', 't1', 'x', 'x', '{\"output\": \"wired\"}')")
    conn.execute("INSERT INTO playback_state VALUES ('u1', '{\"queueId\": \"q1\"}', 'x')")
    return conn


def test_v2_to_v3_is_additive(tmp_path):
    path = tmp_path / "homebeat.db"
    make_v2(path).close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    for t in ("pins", "toget", "catalog_items", "radio_sessions"):
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()
    q = conn.execute("SELECT * FROM queues").fetchone()
    assert (q["name"], q["position_ms"], q["source_type"], q["radio_seed"], q["radio_id"]) == (
        "Album: Al", 1234, None, None, None)
    assert conn.execute("SELECT play_count FROM user_tracks").fetchone()[0] == 7
    assert conn.execute("SELECT context FROM events").fetchone()[0] == '{"output": "wired"}'
    assert conn.execute("SELECT COUNT(*) FROM queue_tracks").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert "toget_user" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    backups = list(tmp_path.glob("homebeat.db.v2-backup-*"))
    assert len(backups) == 1
    assert db.connect(backups[0]).execute("SELECT version FROM schema_version").fetchone()[0] == 2
    # New things work.
    conn.execute("INSERT INTO pins VALUES ('u1', 'album', 'al', 0, 'x')")
    conn.execute("INSERT INTO toget (id, user_id, type, title, artist, match_key, created_at, updated_at) "
                 "VALUES ('g1', 'u1', 'album', 'X', 'Y', 'y|x', 'x', 'x')")
    conn.execute("UPDATE queues SET source_type = 'album', source_id = 'al' WHERE id = 'q1'")


def test_v2_to_v3_idempotent(tmp_path):
    path = tmp_path / "homebeat.db"
    make_v2(path).close()
    for _ in range(3):
        c = db.connect(path)
        db.init(c)
        c.close()
    conn = db.connect(path)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    assert len(list(tmp_path.glob("*.v2-backup-*"))) == 1
    cols = [r[1] for r in conn.execute("PRAGMA table_info(queues)")]
    assert cols.count("source_type") == 1


def test_v2_to_v3_failure_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "homebeat.db"
    make_v2(path).close()
    real = migrations._create_indexes

    def boom(conn, stmts):
        real(conn, stmts)
        raise RuntimeError("disk full")

    monkeypatch.setattr(migrations, "_create_indexes", boom)
    conn = db.connect(path)
    with pytest.raises(RuntimeError):
        db.init(conn)
    assert migrations.current_version(conn) == 2
    assert "source_type" not in {r[1] for r in conn.execute("PRAGMA table_info(queues)")}
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'toget'").fetchone()
    monkeypatch.setattr(migrations, "_create_indexes", real)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION


def test_v1_chains_to_v3_with_one_backup(tmp_path):
    from tests.test_migration import make_v1

    path = tmp_path / "homebeat.db"
    make_v1(path).close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    assert {"source_type", "radio_seed"} <= {r[1] for r in conn.execute("PRAGMA table_info(queues)")}
    assert len(list(tmp_path.glob("*.v1-backup-*"))) == 1 and list(tmp_path.glob("*.v2-backup-*")) == []


def test_v2_db_where_the_ai_already_created_radio_sessions(tmp_path):
    """The v3 AI engine creates radio_sessions itself if missing; the migration must not trip over it."""
    path = tmp_path / "homebeat.db"
    conn = make_v2(path)
    conn.execute("CREATE TABLE radio_sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) "
                 "ON DELETE CASCADE, seed_type TEXT NOT NULL, seed_id TEXT, title TEXT NOT NULL, anchors TEXT NOT NULL, "
                 "served TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    conn.execute("INSERT INTO radio_sessions VALUES ('r', 'u1', 'random', NULL, 'Your radio', '[]', '[]', 'x', 'x')")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    assert conn.execute("SELECT title FROM radio_sessions").fetchone()[0] == "Your radio"
    assert "radio_sessions_user" in {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
