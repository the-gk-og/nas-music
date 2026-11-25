"""Schema v12 -> v13 (audio-content hashes; additive, existing analysis kept)."""
from pathlib import Path

from homebeat import db
from homebeat.db import migrations

V12_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v12.sql").read_text()


def test_v12_to_v13_keeps_analysis(tmp_path):
    path = tmp_path / "homebeat.db"
    conn = db.connect(path)
    conn.executescript(V12_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (12)")
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("INSERT INTO features (track_id, model_version, embedding, dims, analysed_at) "
                 "VALUES ('t1', 'effnet-discogs-1', x'0000803f', 1, '2026-01-01T00:00:00Z')")
    conn.execute("INSERT INTO track_dj (track_id, version, status, bpm, analysed_at) "
                 "VALUES ('t1', 'dj-basic-1', 'done', 124.0, '2026-01-01T00:00:00Z')")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION >= 13
    assert "audio_hash" in migrations._columns(conn, "features")
    assert "audio_hash" in migrations._columns(conn, "track_dj")
    assert {"track_id", "mtime", "bytes", "audio_hash", "computed_at"} <= migrations._columns(conn, "audio_hashes")
    f = conn.execute("SELECT embedding, audio_hash FROM features WHERE track_id = 't1'").fetchone()
    assert f[0] == b"\x00\x00\x80\x3f" and f[1] is None          # kept; hash filled in later by the analyser
    assert conn.execute("SELECT bpm FROM track_dj WHERE track_id = 't1'").fetchone()[0] == 124.0
    idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"features_audio_hash", "track_dj_audio_hash", "audio_hashes_hash"} <= idx
    assert len(list(tmp_path.glob("homebeat.db.v12-backup-*"))) == 1
    assert not migrations.migrate_v12_to_v13(conn, db.schema_sql())
