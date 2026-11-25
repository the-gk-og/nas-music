"""v7 -> v8 (Continue listening: continue_dismissed) against the exact v7 schema (tests/fixtures/schema_v7.sql)."""
from pathlib import Path

from homebeat import db
from homebeat.db import migrations

V7_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v7.sql").read_text()


def test_v7_to_v8(tmp_path):
    path = tmp_path / "homebeat.db"
    conn = db.connect(path)
    conn.executescript(V7_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (7)")
    conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1', 'owner', 'h', 'x')")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    assert {"user_id", "kind", "item_id", "dismissed_at"} <= migrations._columns(conn, "continue_dismissed")
    assert conn.execute("SELECT username FROM users").fetchone()[0] == "owner"
    assert len(list(tmp_path.glob("homebeat.db.v7-backup-*"))) == 1
    assert not migrations.migrate_v7_to_v8(conn, db.schema_sql())
    db.init(conn)
    assert len(list(tmp_path.glob("*-backup-*"))) == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
