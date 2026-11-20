"""v5 -> v6 (AI radio): radio_sessions.station_key/params, radio_stations, radio_feedback. Additive."""
from nasmusic import db
from nasmusic.db import migrations
from tests.test_migration_v5 import make_v4


def test_v5_to_v6(tmp_path):
    path = tmp_path / "nasmusic.db"
    conn = make_v4(path)
    assert migrations.migrate_v4_to_v5(conn, db.schema_sql(), make_backup=False)
    assert migrations.current_version(conn) == 5
    assert "station_key" not in migrations._columns(conn, "radio_sessions")
    conn.execute("INSERT INTO radio_sessions (id, user_id, seed_type, seed_id, title, anchors, served, created_at, "
                 "updated_at) VALUES ('r', 'u1', 'random', NULL, 'Random radio', '[]', '[\"t1\"]', 'x', 'x')")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == 7
    assert {"station_key", "params"} <= migrations._columns(conn, "radio_sessions")
    row = conn.execute("SELECT title, served, station_key FROM radio_sessions WHERE id = 'r'").fetchone()
    assert tuple(row) == ("Random radio", '["t1"]', None)
    for t in ("radio_stations", "radio_feedback"):
        assert migrations._table_exists(conn, t)
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'radio_feedback_user'").fetchone()
    # idempotent
    assert not migrations.migrate_v5_to_v6(conn, db.schema_sql())
    db.init(conn)
    assert migrations.current_version(conn) == 7
