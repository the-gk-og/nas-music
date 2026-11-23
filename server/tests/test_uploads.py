"""Uploads (schema v7): chunked/resumable protocol, sniffing, safe paths, quotas, permissions, visibility,
deletes restricted to upload_root, and the scanner's second library root."""
from __future__ import annotations

import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest

from nasmusic import config as config_mod
from nasmusic import db
from nasmusic.db import migrations
from nasmusic.library import scanner, uploads
from nasmusic.library.tags import TrackTags
from tests.conftest import make_flac, offline, user_client

V6_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v6.sql").read_text()


# ---------------------------------------------------------------- fixtures


@pytest.fixture
def upload_root(tmp_path) -> Path:
    p = tmp_path / "music-uploads"
    p.mkdir()
    return p


@pytest.fixture
def ucfg(cfg, upload_root):
    return dataclasses.replace(cfg, upload_root=upload_root)


@pytest.fixture
def uapp(ucfg, scanned, monkeypatch):
    from nasmusic.app import create_app
    from nasmusic.app.scheduler import Jobs
    from nasmusic.core import open_db

    jobs = Jobs(ucfg)

    def scan_now():  # synchronous instead of a background job
        c = open_db(ucfg)
        try:
            scanner.scan(ucfg, c, uploads_only=True)
        finally:
            c.close()

    monkeypatch.setattr(jobs, "request_upload_scan", scan_now)
    app = create_app(ucfg, jobs=jobs)
    offline(app)
    return app


@pytest.fixture
def alice(uapp, conn):
    with user_client(uapp, conn, "alice", admin=True) as c:
        yield c


@pytest.fixture
def bob(uapp, conn, alice):
    with user_client(uapp, conn, "bob", admin=False) as c:
        yield c


@pytest.fixture
def song(tmp_path, base_flac) -> bytes:
    p = make_flac(base_flac, tmp_path / "src" / "whatever.flac", title="Night Drive", artist="Neon Owl",
                  albumartist="Neon Owl", album="City Lights", tracknumber=3)
    return p.read_bytes()


def upload(client, data: bytes, filename: str = "song.flac", chunk: int = 50_000, visibility: str = "everyone"):
    r = client.post("/api/v1/uploads", json={"filename": filename, "bytes": len(data), "visibility": visibility})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    off = 0
    while off < len(data):
        r = client.patch(f"/api/v1/uploads/{sid}", content=data[off:off + chunk],
                         headers={"Upload-Offset": str(off), "Content-Type": "application/offset+octet-stream"})
        if r.status_code != 200:
            return r
        off = r.json()["offset"]
    return r


# ---------------------------------------------------------------- sniffing and names


def test_sniff_formats(base_flac):
    assert uploads.sniff(base_flac.read_bytes()[:64]) == "flac"
    assert uploads.sniff(b"ID3\x04\x00\x00\x00\x00\x00\x05" + b"\x00" * 5 + b"fLaC....") == "flac"
    assert uploads.sniff(b"ID3\x04\x00\x00\x00\x00\x00\x05" + b"\x00" * 5 + b"\xff\xfb\x90\x00") == "mp3"
    assert uploads.sniff(b"ID3\x04\x00\x00\x00\x10\x00\x00") == "mp3"     # tag longer than what we have
    assert uploads.sniff(b"\xff\xfb\x90\x64" + b"\x00" * 20) == "mp3"
    assert uploads.sniff(b"\xff\xf1\x50\x80" + b"\x00" * 20) == "aac"
    assert uploads.sniff(b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00") == "m4a"
    assert uploads.sniff(b"OggS\x00\x02" + b"\x00" * 22 + b"OpusHead") == "opus"
    assert uploads.sniff(b"OggS\x00\x02" + b"\x00" * 22 + b"\x01vorbis") == "ogg"
    assert uploads.sniff(b"RIFF\x24\x00\x00\x00WAVEfmt ") == "wav"
    for junk in (b"<!doctype html><html>", b"%PDF-1.7\n", b"PK\x03\x04zipzipzip", b"\x7fELF\x02\x01\x01",
                 b"MZ\x90\x00", b"ID3\x04\x00\x00\x00\x00\x00\x05" + b"\x00" * 5 + b"<html>", b""):
        assert uploads.sniff(junk) is None, junk


def test_safe_component_and_paths():
    assert uploads.safe_component("../../etc/passwd", "x") == "_.._etc_passwd"
    assert uploads.safe_component("..", "fallback") == "fallback"
    assert uploads.safe_component(".hidden", "x") == "hidden"
    assert uploads.safe_component('a/b\\c:d*e?f"g<h>i|j\x00k', "x") == "a_b_c_d_e_f_g_h_i_j_k"
    assert uploads.safe_component("  trailing.  ", "x") == "trailing"
    assert len(uploads.safe_component("é" * 500, "x").encode()) <= 120
    t = TrackTags(title="../../../escape", artist="..", album="/abs", track_no=7, disc_no=2)
    rel = uploads.target_relpath("alice", "uid", t, "x.flac", ".flac")
    assert rel == "alice/Unknown Artist/_abs/2-07 .._.._.._escape.flac"
    assert ".." not in Path(rel).parts
    rel = uploads.target_relpath("...", "uid1", TrackTags(), "C:\\Users\\me\\..\\My Song.mp3", ".mp3")
    assert rel == "uid1/Unsorted/My Song.mp3"


# ---------------------------------------------------------------- protocol


def test_chunked_upload_resume_and_index(alice, conn, song, upload_root, library):
    r = alice.post("/api/v1/uploads", json={"filename": "night drive.flac", "bytes": len(song)})
    assert r.status_code == 201
    sid = r.json()["id"]
    half = len(song) // 2
    r = alice.patch(f"/api/v1/uploads/{sid}", content=song[:half], headers={"Upload-Offset": "0"})
    assert r.status_code == 200 and r.json()["offset"] == half and r.json()["status"] == "uploading"
    # a client that lost the reply re-sends the same chunk: 409 tells it where to carry on
    r = alice.patch(f"/api/v1/uploads/{sid}", content=song[:half], headers={"Upload-Offset": "0"})
    assert r.status_code == 409 and r.json()["offset"] == half and r.headers["Upload-Offset"] == str(half)
    r = alice.get(f"/api/v1/uploads/{sid}")
    assert r.json()["offset"] == half and r.headers["Upload-Offset"] == str(half)
    # too much data
    r = alice.patch(f"/api/v1/uploads/{sid}", content=song[half:] + b"xx", headers={"Upload-Offset": str(half)})
    assert r.status_code == 413
    r = alice.patch(f"/api/v1/uploads/{sid}?offset={half}", content=song[half:])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "done" and body["offset"] == len(song)
    up = body["upload"]
    assert up["path"] == "alice/Neon Owl/City Lights/03 Night Drive.flac"
    assert (upload_root / up["path"]).read_bytes() == song
    assert not list((upload_root / ".incoming").iterdir())
    # indexed by the uploads-only scan, with a namespaced path; streamable
    row = conn.execute("SELECT id, path, missing FROM tracks WHERE id = ?", (up["trackId"],)).fetchone()
    assert row["path"] == "@uploads/" + up["path"] and row["missing"] == 0
    assert alice.get(f"/api/v1/stream/{up['trackId']}?format=flac").content == song
    lst = alice.get("/api/v1/uploads").json()
    assert lst["canUpload"] and lst["usedBytes"] == len(song)
    assert lst["files"][0]["indexed"] and lst["files"][0]["title"] == "Night Drive"
    # the library itself is untouched
    assert not (library / "alice").exists()


def test_same_name_twice_gets_a_suffix(alice, song, upload_root):
    a = upload(alice, song).json()["upload"]["path"]
    b = upload(alice, song).json()["upload"]["path"]
    assert a != b and b.endswith("03 Night Drive (2).flac")


def test_untagged_goes_to_unsorted_as_uploaded(alice, base_flac, upload_root):
    r = upload(alice, base_flac.read_bytes(), filename="../../My Demo.flac")
    assert r.json()["upload"]["path"] == "alice/Unsorted/My Demo.flac"


def test_wav_kept_as_is(alice, tmp_path, upload_root):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg")
    wav = tmp_path / "t.wav"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=1",
                    str(wav)], check=True)
    r = upload(alice, wav.read_bytes(), filename="take one.wav")
    assert r.status_code == 200, r.text
    assert r.json()["upload"]["format"] == "wav" and r.json()["upload"]["path"] == "alice/Unsorted/take one.wav"


def test_non_audio_rejected_on_first_chunk(alice, upload_root):
    data = b"<html><script>alert(1)</script></html>" * 100
    r = upload(alice, data, filename="evil.flac")
    assert r.status_code == 415
    sessions = alice.get("/api/v1/uploads").json()["sessions"]
    assert sessions[0]["status"] == "failed"
    assert not any((upload_root / ".incoming").iterdir())
    assert not [p for p in upload_root.rglob("*") if p.is_file()]


def test_fake_magic_rejected_at_the_end(alice, upload_root):
    r = upload(alice, b"fLaC" + b"\x00" * 5000, filename="fake.flac")
    assert r.status_code == 415
    assert not [p for p in upload_root.rglob("*") if p.is_file()]


def test_bad_extension_size_limit_and_quota(alice, conn, ucfg, song):
    assert alice.post("/api/v1/uploads", json={"filename": "x.exe", "bytes": 10}).status_code == 415
    too_big = ucfg.upload_max_file_mb * 1024 * 1024 + 1
    assert alice.post("/api/v1/uploads", json={"filename": "x.flac", "bytes": too_big}).status_code == 413
    uploads.set_access(conn, alice.user_id, quota_bytes=len(song) + 10)
    assert upload(alice, song).status_code == 200
    r = alice.post("/api/v1/uploads", json={"filename": "y.flac", "bytes": 20})
    assert r.status_code == 413 and "quota" in r.json()["detail"]
    # unfinished uploads count against the quota too
    uploads.set_access(conn, alice.user_id, quota_bytes=len(song) + 100)
    assert alice.post("/api/v1/uploads", json={"filename": "a.flac", "bytes": 60}).status_code == 201
    assert alice.post("/api/v1/uploads", json={"filename": "b.flac", "bytes": 60}).status_code == 413


def test_chunk_size_limit(alice, song):
    sid = alice.post("/api/v1/uploads", json={"filename": "x.flac", "bytes": len(song)}).json()["id"]
    r = alice.patch(f"/api/v1/uploads/{sid}", content=b"x" * (uploads.MAX_CHUNK + 1),
                    headers={"Upload-Offset": "0"})
    assert r.status_code == 413


def test_cancel(alice, song, upload_root):
    sid = alice.post("/api/v1/uploads", json={"filename": "x.flac", "bytes": len(song)}).json()["id"]
    alice.patch(f"/api/v1/uploads/{sid}", content=song[:100], headers={"Upload-Offset": "0"})
    assert alice.delete(f"/api/v1/uploads/{sid}").status_code == 204
    assert alice.get(f"/api/v1/uploads/{sid}").status_code == 404
    assert not any((upload_root / ".incoming").iterdir())


def test_stale_sessions_cleaned(alice, conn, ucfg, song, upload_root):
    from datetime import UTC, datetime, timedelta

    sid = alice.post("/api/v1/uploads", json={"filename": "x.flac", "bytes": len(song)}).json()["id"]
    assert uploads.cleanup(conn, ucfg)["uploadSessionsDropped"] == 0
    assert uploads.cleanup(conn, ucfg, datetime.now(UTC) + timedelta(days=8))["uploadSessionsDropped"] == 1
    assert not (upload_root / ".incoming" / f"{sid}.part").exists()


# ---------------------------------------------------------------- permissions, visibility, deletes


def test_permissions_default_admins_only(alice, bob, song):
    assert bob.get("/api/v1/uploads").json()["canUpload"] is False
    assert bob.post("/api/v1/uploads", json={"filename": "x.flac", "bytes": 10}).status_code == 403
    assert bob.put(f"/api/v1/admin/users/{bob.user_id}/uploads", json={"canUpload": True}).status_code == 403
    r = alice.put(f"/api/v1/admin/users/{bob.user_id}/uploads", json={"canUpload": True, "quotaBytes": 10**9})
    assert r.status_code == 200 and r.json()["canUpload"] and r.json()["quotaBytes"] == 10**9
    assert upload(bob, song).status_code == 200
    users = {u["username"]: u for u in alice.get("/api/v1/admin/uploads").json()["users"]}
    assert users["bob"]["files"] == 1 and users["alice"]["canUpload"] and not users["alice"]["explicit"]
    # quota back to the default, permission kept
    r = alice.put(f"/api/v1/admin/users/{bob.user_id}/uploads", json={"quotaBytes": None})
    assert r.json()["canUpload"] and r.json()["quotaBytes"] == int(50 * 1024**3)
    alice.put(f"/api/v1/admin/users/{bob.user_id}/uploads", json={"canUpload": False})
    assert bob.post("/api/v1/uploads", json={"filename": "x.flac", "bytes": 10}).status_code == 403


def test_sessions_are_per_user(alice, bob, conn, song):
    sid = alice.post("/api/v1/uploads", json={"filename": "x.flac", "bytes": len(song)}).json()["id"]
    assert bob.get(f"/api/v1/uploads/{sid}").status_code == 404
    assert bob.patch(f"/api/v1/uploads/{sid}", content=song[:10], headers={"Upload-Offset": "0"}).status_code == 404


def test_private_upload_visible_only_to_uploader(alice, bob, conn, song):
    uploads.set_access(conn, bob.user_id, allowed=True)
    up = upload(bob, song, visibility="private").json()["upload"]
    tid = up["trackId"]
    assert bob.get(f"/api/v1/library/tracks/{tid}").status_code == 200
    assert alice.get(f"/api/v1/library/tracks/{tid}").status_code == 404
    assert alice.get(f"/api/v1/stream/{tid}?format=flac").status_code == 404
    assert not alice.get("/api/v1/library/search?q=Night Drive").json()["tracks"]
    assert bob.get("/api/v1/library/search?q=Night Drive").json()["tracks"]
    # into a queue: dropped for alice
    q = alice.post("/api/v1/queues", json={"name": "x", "trackIds": [tid]})
    if q.status_code in (200, 201):
        assert tid not in [t["id"] for t in q.json()["tracks"]]
    # make it public
    r = bob.patch(f"/api/v1/uploads/files/{up['id']}", json={"visibility": "everyone"})
    assert r.status_code == 200 and r.json()["visibility"] == "everyone"
    assert alice.get(f"/api/v1/library/tracks/{tid}").status_code == 200


def test_delete_restrictions(alice, bob, conn, song, upload_root, library):
    up = upload(alice, song).json()["upload"]
    before = sorted(p for p in library.rglob("*"))
    # bob may not delete alice's upload, nor change it
    assert bob.delete(f"/api/v1/uploads/files/{up['id']}").status_code == 404
    assert bob.patch(f"/api/v1/uploads/files/{up['id']}", json={"visibility": "private"}).status_code == 404
    assert (upload_root / up["path"]).exists()
    # alice can: file and its now-empty folders are gone, the track is marked missing
    r = alice.delete(f"/api/v1/uploads/files/{up['id']}")
    assert r.status_code == 200
    assert not (upload_root / up["path"]).exists()
    assert not (upload_root / "alice").exists() and upload_root.exists()
    assert conn.execute("SELECT missing FROM tracks WHERE id = ?", (up["trackId"],)).fetchone()[0] == 1
    assert sorted(p for p in library.rglob("*")) == before
    # an admin can delete someone else's
    uploads.set_access(conn, bob.user_id, allowed=True)
    bu = upload(bob, song).json()["upload"]
    assert alice.delete(f"/api/v1/uploads/files/{bu['id']}").status_code == 200


def test_delete_never_leaves_upload_root(ucfg, conn, alice, song, library):
    """Even a tampered uploads row pointing into the library cannot delete library files."""
    up = upload(alice, song).json()["upload"]
    victim = next(p for p in library.rglob("*.flac"))
    for bad in ("../music/" + victim.relative_to(library).as_posix(), str(victim), "alice/../../music/x.flac"):
        conn.execute("UPDATE uploads SET path = ? WHERE id = ?", (bad, up["id"]))
        with pytest.raises(uploads.UploadError):
            uploads.delete(conn, ucfg, alice.user_id, up["id"], is_admin=True)
        assert victim.exists()


def test_symlink_escape_refused(alice, song, upload_root, library, tmp_path):
    (upload_root / "alice").symlink_to(library)
    r = upload(alice, song)
    assert r.status_code == 422
    assert not (library / "Neon Owl").exists()


def test_uploads_disabled_without_upload_root(client):
    assert client.get("/api/v1/uploads").json()["enabled"] is False
    assert client.post("/api/v1/uploads", json={"filename": "x.flac", "bytes": 10}).status_code == 503


# ---------------------------------------------------------------- scanner second root


def test_scanner_second_root(ucfg, cfg, conn, scanned, base_flac, upload_root, library):
    lib_ids = {r["id"]: r["path"] for r in conn.execute("SELECT id, path FROM tracks")}
    art_before = dict(conn.execute("SELECT id, art_path FROM albums").fetchall())
    # the same relative path in both roots gives two different tracks
    rel = next(iter(lib_ids.values()))
    shutil.copyfile(library / rel, upload_root / Path(rel).name)
    (upload_root / Path(rel).parent).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(library / rel, upload_root / rel)
    # a library folder named like the prefix is never indexed (no id collisions)
    make_flac(base_flac, library / "@uploads" / rel, title="Sneaky")
    res = scanner.scan(ucfg, conn, uploads_only=True)
    assert res.added == 2 and res.missing == 0
    paths = {r["path"] for r in conn.execute("SELECT path FROM tracks WHERE missing = 0")}
    assert "@uploads/" + rel in paths and rel in paths
    assert dict(conn.execute("SELECT id, art_path FROM albums WHERE id IN (%s)" % ",".join(
        "?" * len(art_before)), list(art_before)).fetchall()) == art_before   # library art untouched
    res = scanner.scan(ucfg, conn)  # full walk of both roots
    assert res.missing == 0
    assert not conn.execute("SELECT 1 FROM tracks WHERE title = 'Sneaky'").fetchone()
    # upload folder not mounted: uploads are left alone, not marked missing
    gone = dataclasses.replace(ucfg, upload_root=upload_root.parent / "not-mounted")
    assert scanner.scan(gone, conn).missing == 0
    # file removed: marked missing by the next upload scan
    (upload_root / rel).unlink()
    assert scanner.scan(ucfg, conn, uploads_only=True).missing == 1
    # upload_root removed from the config: the uploaded tracks disappear
    assert scanner.scan(cfg, conn).missing == 1


def test_library_root_routing(ucfg, upload_root):
    assert ucfg.library_root / "@uploads/a/b.flac" == upload_root / "a/b.flac"
    assert ucfg.library_root / "a/b.flac" == Path(str(ucfg.library_root)) / "a/b.flac"
    plain = dataclasses.replace(ucfg, upload_root=None)
    assert not (plain.library_root / "@uploads/a").exists()
    assert str(plain.library_root / "@uploads/a").startswith("/nonexistent")


def test_config_upload_root_validation(tmp_path):
    base = {"library_root": str(tmp_path / "m"), "data_dir": str(tmp_path / "d")}
    for bad in (tmp_path / "m", tmp_path / "m" / "up", tmp_path):
        with pytest.raises(config_mod.ConfigError):
            config_mod.from_dict({**base, "upload_root": str(bad)})
    c = config_mod.from_dict({**base, "upload_root": str(tmp_path / "up")})
    assert c.upload_root == tmp_path / "up"


def test_migration_v6_to_v7(tmp_path):
    path = tmp_path / "nasmusic.db"
    conn = db.connect(path)
    conn.executescript(V6_SCHEMA)
    conn.execute("INSERT INTO schema_version (version) VALUES (6)")
    conn.execute("INSERT INTO users (id, username, password_hash, created_at) VALUES ('u1', 'owner', 'h', 'x')")
    conn.close()
    conn = db.connect(path)
    db.init(conn)
    assert migrations.current_version(conn) == db.SCHEMA_VERSION
    for t in ("upload_access", "uploads", "upload_sessions"):
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()
    assert len(list(tmp_path.glob("nasmusic.db.v6-backup-*"))) == 1
    db.init(conn)
    assert len(list(tmp_path.glob("*-backup-*"))) == 1
