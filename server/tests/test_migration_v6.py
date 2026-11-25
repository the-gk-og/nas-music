"""v5 -> v6 (Jam tables) against the exact v5 schema (tests/fixtures/schema_v5.sql): additive only."""
from pathlib import Path

from homebeat import db
from homebeat.db import migrations

V5_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v5.sql").read_text()


def test_v5_to_v6(tmp_path):
    path = tmp_path / "homebeat.db"
    conn = db.connect(path)
    conn.executescript(V5_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (5)")
    conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1', 'owner', 'h', 'x')")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    for t in ("jams", "jam_guests", "jam_requests", "jam_votes"):
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()
    assert conn.execute("SELECT username FROM users").fetchone()[0] == "owner"
    assert len(list(tmp_path.glob("homebeat.db.v5-backup-*"))) == 1
    for _ in range(2):
        db.init(conn)
    assert len(list(tmp_path.glob("*-backup-*"))) == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
