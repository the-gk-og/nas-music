"""v6 -> v7 (DJ automix analysis: track_dj) against the exact v6 schema (tests/fixtures/schema_v6.sql)."""
from pathlib import Path

from homebeat import db
from homebeat.db import migrations

V6_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v6.sql").read_text()


def test_v6_to_v7(tmp_path):
    path = tmp_path / "homebeat.db"
    conn = db.connect(path)
    conn.executescript(V6_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (6)")
    conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1', 'owner', 'h', 'x')")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    assert migrations._table_exists(conn, "track_dj")
    assert {"bpm", "downbeat_s", "mix_in_s", "mix_out_s", "camelot", "energy_curve"} <= migrations._columns(conn, "track_dj")
    assert conn.execute("SELECT username FROM users").fetchone()[0] == "owner"
    assert len(list(tmp_path.glob("homebeat.db.v6-backup-*"))) == 1
    assert not migrations.migrate_v6_to_v7(conn, db.schema_sql())
    db.init(conn)
    assert len(list(tmp_path.glob("*-backup-*"))) == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
