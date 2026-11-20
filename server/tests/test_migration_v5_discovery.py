"""v4 -> v5 (Release Radar, Blend tables) against the exact v4 schema that is live on the NAS."""
from pathlib import Path

import pytest

from nasmusic import db
from nasmusic.db import migrations

V4_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v4.sql").read_text()


def make_v4(path: Path):
    conn = db.connect(path)
    conn.executescript(V4_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (4)")
    conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1', 'owner', 'h', 'x')")
    return conn


def test_v4_to_v5(tmp_path):
    path = tmp_path / "nasmusic.db"
    make_v4(path).close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    for t in ("release_radar", "blends", "blend_members", "blend_tracks"):
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'blend_members_user'").fetchone()
    assert conn.execute("SELECT username FROM users").fetchone()[0] == "owner"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert len(list(tmp_path.glob("nasmusic.db.v4-backup-*"))) == 1
    for _ in range(2):
        db.init(conn)
    assert len(list(tmp_path.glob("*-backup-*"))) == 1
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1


def test_v4_to_v5_failure_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "nasmusic.db"
    make_v4(path).close()
    real = migrations._create_indexes

    def boom(conn, stmts):
        real(conn, stmts)
        raise RuntimeError("disk full")

    monkeypatch.setattr(migrations, "_create_indexes", boom)
    conn = db.connect(path)
    with pytest.raises(RuntimeError):
        db.init(conn)
    assert migrations.current_version(conn) == 4
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'blends'").fetchone()
