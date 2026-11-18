from nasmusic import db


def test_schema_initialises(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init(conn)
    db.init(conn)  # idempotent
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"tracks", "events", "mixes", "mix_tracks", "features", "suggestions", "devices"} <= tables
