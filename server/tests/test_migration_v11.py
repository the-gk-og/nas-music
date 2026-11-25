"""Schema v10 -> v11 (library cleanup: library_removed, lossless_checks; additive)."""
from pathlib import Path

from nasmusic import db
from nasmusic.db import migrations

V10_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v10.sql").read_text()


def test_v10_to_v11(tmp_path):
    path = tmp_path / "nasmusic.db"
    conn = db.connect(path)
    conn.executescript(V10_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (10)")
    conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1', 'owner', 'h', 'x')")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION >= 11
    assert {"track_id", "batch_id", "purge_after", "purged_at", "purge_error"} <= migrations._columns(conn, "library_removed")
    assert {"track_id", "mtime", "verdict", "cutoff_hz", "dismissed"} <= migrations._columns(conn, "lossless_checks")
    assert conn.execute("SELECT username FROM users").fetchone()[0] == "owner"
    assert len(list(tmp_path.glob("nasmusic.db.v10-backup-*"))) == 1
    assert not migrations.migrate_v10_to_v11(conn, db.schema_sql())
    db.init(conn)
    assert len(list(tmp_path.glob("*-backup-*"))) == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
