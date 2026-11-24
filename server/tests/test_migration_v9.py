"""v8 -> v9 (playlist folders, classical track fields) against the exact v8 schema (tests/fixtures/schema_v8.sql)."""
from pathlib import Path

from nasmusic import db
from nasmusic.db import migrations

V8_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v8.sql").read_text()


def test_v8_to_v9(tmp_path):
    path = tmp_path / "nasmusic.db"
    conn = db.connect(path)
    conn.executescript(V8_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (8)")
    conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1', 'owner', 'h', 'x')")
    conn.execute("INSERT INTO artists (id, name, sort_name) VALUES ('a1', 'A', 'A')")
    conn.execute("INSERT INTO albums (id, title, artist_id, added_at) VALUES ('al1', 'Al', 'a1', 'x')")
    conn.execute("INSERT INTO tracks (id, path, album_id, artist_id, title, duration_s, flac_bytes, mtime, added_at) "
                 "VALUES ('t1', 'x.flac', 'al1', 'a1', 'T', 1, 1, 1, 'x')")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION >= 9
    assert {"work", "movement", "movement_no"} <= migrations._columns(conn, "tracks")
    assert conn.execute("SELECT work FROM tracks WHERE id = 't1'").fetchone()[0] is None  # backfilled by a scan
    assert {"id", "user_id", "parent_id", "name", "position"} <= migrations._columns(conn, "playlist_folders")
    assert {"user_id", "playlist_id", "folder_id"} <= migrations._columns(conn, "playlist_folder_items")
    assert len(list(tmp_path.glob("nasmusic.db.v8-backup-*"))) == 1
    assert not migrations.migrate_v8_to_v9(conn, db.schema_sql())
    db.init(conn)
    assert len(list(tmp_path.glob("*-backup-*"))) == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
