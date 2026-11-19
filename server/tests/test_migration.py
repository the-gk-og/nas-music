"""v1 -> v2 migration against a database created by the real v1 schema (tests/fixtures/schema_v1.sql)."""
from pathlib import Path

import pytest

from nasmusic import db
from nasmusic.db import migrations

V1_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v1.sql").read_text()
T1, T2, T3 = "a" * 40, "b" * 40, "c" * 40


def make_v1(path: Path, old_event_check: bool = False):
    conn = db.connect(path)
    sql = V1_SCHEMA
    if old_event_check:  # databases created before thumb_clear existed
        sql = sql.replace(",'thumb_clear'", "")
    conn.executescript(sql)
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.execute("INSERT INTO settings VALUES ('server_id', '\"abc\"'), ('job.scan', '{\"ok\": true}')")
    conn.execute("INSERT INTO artists VALUES ('ar1', 'Artist', 'Artist', NULL)")
    conn.execute("INSERT INTO albums VALUES ('al1', 'ar1', 'Album', 2001, NULL, 'Artist/Album/cover.jpg', "
                 "'2026-01-01T00:00:00Z')")
    rows = [
        (T1, "Artist/Album/01.flac", "2026-02-01T10:00:00Z", 5, 0),
        (T2, "Artist/Album/02.flac", None, None, 1),
        (T3, "loose.flac", None, None, 0),
    ]
    for tid, path_, starred, rating, blocked in rows:
        conn.execute(
            "INSERT INTO tracks (id, path, album_id, artist_id, title, track_no, duration_s, flac_bytes, mtime, "
            "starred_at, rating, blocked, added_at) VALUES (?, ?, 'al1', 'ar1', ?, 1, 200, 1000, 123.0, ?, ?, ?, "
            "'2026-01-01T00:00:00Z')", (tid, path_, path_, starred, rating, blocked))
    conn.execute("INSERT INTO devices VALUES ('d1', 'Pixel', 'hash1', '2026-01-01T00:00:00Z', NULL, 0)")
    conn.execute("INSERT INTO pair_codes VALUES ('CODE1234', '2099-01-01T00:00:00Z', 0)")
    evs = [("e1", "play", T1, "2026-03-01T10:00:00Z"), ("e2", "play", T1, "2026-03-02T10:00:00Z"),
           ("e3", "skip", T1, "2026-03-03T10:00:00Z"), ("e4", "play", T3, "2026-03-04T10:00:00Z"),
           ("e5", "thumb_down", T2, "2026-03-05T10:00:00Z")]
    for eid, typ, tid, at in evs:
        conn.execute("INSERT INTO events (id, device_id, type, track_id, at, received_at) VALUES (?, 'd1', ?, ?, ?, ?)",
                     (eid, typ, tid, at, at))
    conn.execute("INSERT INTO features (track_id, model_version, embedding, dims, analysed_at) "
                 "VALUES (?, 'basic-1', x'00000000', 1, '2026-01-01T00:00:00Z')", (T1,))
    conn.execute("INSERT INTO analysis_jobs VALUES (?, 'basic-1', 'done', NULL, '2026-01-01T00:00:00Z')", (T1,))
    conn.execute("INSERT INTO taste_snapshots VALUES ('ts1', '2026-03-01T02:00:00Z', 'basic-1', x'01', NULL)")
    conn.execute("INSERT INTO mixes VALUES ('m1', 'daily', 'Daily', '2026-03-01', '2026-03-01T02:00:00Z', NULL, 1)")
    conn.execute("INSERT INTO mix_tracks VALUES ('m1', 0, ?, 0.9, 'because')", (T1,))
    conn.execute("INSERT INTO suggestions (id, artist, album, release_group_mbid, reason, status, created_at) "
                 "VALUES ('s1', 'X', 'Y', 'rg1', 'r', 'want', '2026-03-01T03:00:00Z')")
    return conn


@pytest.mark.parametrize("old_check", [False, True])
def test_v1_to_v2_keeps_everything(tmp_path, old_check):
    path = tmp_path / "nasmusic.db"
    make_v1(path, old_check).close()
    conn = db.connect(path)
    db.init(conn, admin_username="owner", admin_password_hash="scrypt$x")

    assert migrations.current_version(conn) == 4
    admin = conn.execute("SELECT * FROM users").fetchall()
    assert len(admin) == 1 and admin[0]["username"] == "owner" and admin[0]["is_admin"] == 1
    assert admin[0]["password_hash"] == "scrypt$x"
    uid = admin[0]["id"]

    ut = {r["track_id"]: dict(r) for r in conn.execute("SELECT * FROM user_tracks")}
    assert ut[T1]["starred_at"] == "2026-02-01T10:00:00Z" and ut[T1]["rating"] == 5
    assert (ut[T1]["play_count"], ut[T1]["skip_count"], ut[T1]["last_played"]) == (2, 1, "2026-03-02T10:00:00Z")
    assert ut[T2]["blocked"] == 1 and ut[T3]["play_count"] == 1
    assert {r["user_id"] for r in conn.execute("SELECT user_id FROM user_tracks")} == {uid}

    cols = {r[1] for r in conn.execute("PRAGMA table_info(tracks)")}
    assert not cols & {"starred_at", "rating", "blocked"}
    assert {"composer", "codec", "folder", "bits_per_sample"} <= cols
    t = {r["id"]: r for r in conn.execute("SELECT * FROM tracks")}
    assert t[T1]["folder"] == "Artist/Album" and t[T3]["folder"] == ""
    assert t[T1]["mtime"] == 0  # forces a tag re-read on the next scan
    assert t[T1]["added_at"] == "2026-01-01T00:00:00Z"

    assert conn.execute("SELECT COUNT(*) FROM events WHERE user_id = ?", (uid,)).fetchone()[0] == 5
    d = conn.execute("SELECT * FROM devices").fetchone()
    assert (d["user_id"], d["kind"], d["token_hash"]) == (uid, "android", "hash1")
    assert conn.execute("SELECT user_id FROM pair_codes").fetchone()[0] == uid
    for table in ("mixes", "taste_snapshots", "suggestions"):
        assert conn.execute(f"SELECT user_id FROM {table}").fetchone()[0] == uid
    assert conn.execute("SELECT COUNT(*) FROM mix_tracks").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM features").fetchone()[0] == 1
    assert conn.execute("SELECT value FROM settings WHERE key = 'server_id'").fetchone()[0] == '"abc"'
    # thumb_clear is now accepted; foreign keys are intact.
    conn.execute("INSERT INTO events (id, user_id, type, track_id, at, received_at) "
                 "VALUES ('e9', ?, 'thumb_clear', ?, 'x', 'x')", (uid, T2))
    conn.execute("INSERT INTO mixes (id, user_id, kind, title, for_date, created_at) "
                 "VALUES ('mc', ?, 'context', 'Gym', 'd', 'x')", (uid,))  # v2 context mixes
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # New indexes exist on the rebuilt tables.
    idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"events_user", "events_track", "tracks_folder", "mixes_user", "suggestions_user"} <= idx
    # Backup of the v1 file next to it.
    backups = list(tmp_path.glob("nasmusic.db.v1-backup-*"))
    assert len(backups) == 1
    b = db.connect(backups[0])
    assert b.execute("SELECT version FROM schema_version").fetchone()[0] == 1
    assert b.execute("SELECT starred_at FROM tracks WHERE id = ?", (T1,)).fetchone()[0]


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "nasmusic.db"
    make_v1(path).close()
    for _ in range(3):
        conn = db.connect(path)
        db.init(conn, admin_password_hash="h")
        conn.close()
    conn = db.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 5
    assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
    assert len(list(tmp_path.glob("*.v1-backup-*"))) == 1  # only backed up once


def test_failed_migration_leaves_v1_untouched(tmp_path, monkeypatch):
    path = tmp_path / "nasmusic.db"
    make_v1(path).close()

    real = migrations._migrate

    def boom(conn, *a, **k):
        real(conn, *a, **k)  # do all the work, then fail before commit
        raise RuntimeError("disk full")

    monkeypatch.setattr(migrations, "_migrate", boom)
    conn = db.connect(path)
    with pytest.raises(RuntimeError):
        db.init(conn)
    conn.close()
    conn = db.connect(path)
    assert migrations.current_version(conn) == 1
    assert conn.execute("SELECT starred_at FROM tracks WHERE id = ?", (T1,)).fetchone()[0]
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'users'").fetchone()
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    monkeypatch.setattr(migrations, "_migrate", real)
    db.init(conn)  # and a retry works
    assert migrations.current_version(conn) == 4


def test_fresh_database_is_v2(tmp_path):
    conn = db.connect(tmp_path / "new.db")
    db.init(conn)
    db.init(conn)
    assert migrations.current_version(conn) == 4
    assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0  # app creates the admin
    assert list(tmp_path.glob("*.v1-backup-*")) == []


def test_live_server_start_migrates_and_keeps_admin_login(tmp_path, base_flac):
    """build_context on a v1 data dir: admin can log in with the old config password and sees their stars."""
    from fastapi.testclient import TestClient

    from nasmusic import auth, config
    from nasmusic.app import create_app

    data = tmp_path / "data"
    data.mkdir()
    make_v1(data / "nasmusic.db").close()
    music = tmp_path / "music"
    music.mkdir()
    cfg = config.from_dict({"library_root": str(music), "data_dir": str(data),
                            "admin_password_hash": auth.hash_password("old-admin-pw")})
    with TestClient(create_app(cfg)) as c:
        r = c.post("/api/v1/auth/login", json={"username": "admin", "password": "old-admin-pw",
                                               "deviceName": "t", "deviceKind": "web"})
        assert r.status_code == 200, r.text
        assert r.json()["isAdmin"] is True
        # The v1 phone's token still works and is now the admin's device.
        conn = db.connect(data / "nasmusic.db")
        assert conn.execute("SELECT u.username FROM devices d JOIN users u ON u.id = d.user_id "
                            "WHERE d.id = 'd1'").fetchone()[0] == "admin"
