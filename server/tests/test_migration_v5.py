"""v4 -> v5 against the exact v4 schema (tests/fixtures/schema_v4.sql): playlist_tracks rebuilt with entry ids
and fractional positions, order and data kept; new tag-override and collaboration tables."""
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
    conn.execute("INSERT INTO artists VALUES ('ar', 'A', 'A', NULL)")
    conn.execute("INSERT INTO albums VALUES ('al', 'ar', 'Al', NULL, NULL, NULL, 'x')")
    for t in ("t1", "t2", "t3"):
        conn.execute("INSERT INTO tracks (id, path, album_id, artist_id, title, duration_s, flac_bytes, mtime, added_at) "
                     "VALUES (?, ?, 'al', 'ar', 'T', 1, 1, 1, 'x')", (t, f"{t}.flac"))
    conn.execute("INSERT INTO playlists VALUES ('p', 'u1', 'P', 'x', 'x')")
    for pos, t in enumerate(("t3", "t1", "t3")):
        conn.execute("INSERT INTO playlist_tracks VALUES ('p', ?, ?, ?)", (pos, t, f"2020-01-0{pos + 1}"))
    return conn


def test_v4_to_v5(tmp_path):
    path = tmp_path / "nasmusic.db"
    make_v4(path).close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == 5
    rows = conn.execute("SELECT id, track_id, position, added_at, added_by FROM playlist_tracks "
                        "ORDER BY position").fetchall()
    assert [r["track_id"] for r in rows] == ["t3", "t1", "t3"]
    assert [r["position"] for r in rows] == [1.0, 2.0, 3.0]
    assert len({r["id"] for r in rows}) == 3 and all(r["added_by"] == "u1" for r in rows)
    assert rows[0]["added_at"] == "2020-01-01"
    assert conn.execute("SELECT rev FROM playlists").fetchone()[0] == 0
    assert "file_tags" in {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
    for t in ("tag_overrides", "tag_edits", "playlist_members", "playlist_activity"):
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert len(list(tmp_path.glob("nasmusic.db.v4-backup-*"))) == 1
    for _ in range(2):
        db.init(conn)
    assert len(list(tmp_path.glob("*-backup-*"))) == 1


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
    assert conn.execute("SELECT COUNT(*) FROM playlist_tracks").fetchone()[0] == 3
    assert "id" not in {r[1] for r in conn.execute("PRAGMA table_info(playlist_tracks)")}
