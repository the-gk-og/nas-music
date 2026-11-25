"""v9 -> v10 (playlist folders, classical track fields) against the exact v8 schema (tests/fixtures/schema_v9.sql)."""
from pathlib import Path

from homebeat import db
from homebeat.db import migrations

V9_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v9.sql").read_text()


def test_v9_to_v10(tmp_path):
    path = tmp_path / "homebeat.db"
    conn = db.connect(path)
    conn.executescript(V9_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (9)")
    conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1', 'owner', 'h', 'x')")
    conn.execute("INSERT INTO artists (id, name, sort_name) VALUES ('a1', 'A', 'A')")
    conn.execute("INSERT INTO albums (id, title, artist_id, added_at) VALUES ('al1', 'Al', 'a1', 'x')")
    conn.execute("INSERT INTO tracks (id, path, album_id, artist_id, title, duration_s, flac_bytes, mtime, added_at) "
                 "VALUES ('t1', 'x.flac', 'al1', 'a1', 'T', 1, 1, 1, 'x')")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION >= 10
    assert {"work", "movement", "movement_no"} <= migrations._columns(conn, "tracks")
    assert conn.execute("SELECT work FROM tracks WHERE id = 't1'").fetchone()[0] is None  # backfilled by a scan
    assert {"id", "user_id", "parent_id", "name", "position"} <= migrations._columns(conn, "playlist_folders")
    assert {"user_id", "playlist_id", "folder_id"} <= migrations._columns(conn, "playlist_folder_items")
    assert len(list(tmp_path.glob("homebeat.db.v9-backup-*"))) == 1
    assert not migrations.migrate_v9_to_v10(conn, db.schema_sql())
    db.init(conn)
    assert len(list(tmp_path.glob("*-backup-*"))) == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
