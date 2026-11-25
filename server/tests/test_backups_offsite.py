"""Off-site backup copies (a second folder or an rclone remote) and one-click restore (homebeat.app.backups)."""
from __future__ import annotations

import gzip
import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from homebeat import config as config_mod
from homebeat import db
from homebeat.app import backups
from tests.conftest import track_ids

API = "/api/v1"
ADMIN = f"{API}/admin"
FIXTURES = Path(__file__).parent / "fixtures"


def _dest(app, value: str | None, **kw):
    c = app.state.ctx
    c.cfg.backup_copy_to = value
    for k, v in kw.items():
        setattr(c.cfg, k, v)
    c.jobs.cfg = c.cfg
    return c.cfg


def _playlists(client) -> set[str]:
    return {p["name"] for p in client.get(f"{API}/playlists").json()}


# ---------- copying off-site ----------


def test_nightly_job_copies_to_a_folder(client, app, tmp_path):
    dest = tmp_path / "second-dataset"
    _dest(app, str(dest))
    (app.state.ctx.cfg.data_dir / "rclone.conf").write_text("[x]\n")
    rec = app.state.ctx.jobs.run("backup")
    assert rec["ok"], rec["error"]
    res = rec["result"]
    assert res["kind"] == "scheduled" and res["offsite"]["name"] == res["name"] + ".gz"
    gz = dest / res["offsite"]["name"]
    raw = gzip.decompress(gz.read_bytes())
    assert raw[:16] == b"SQLite format 3\x00"
    # config mirror: TLS cert (the apps are pinned to it), rclone.conf; key files private
    assert res["offsite"]["configCopied"] and (dest / "config" / "tls").is_dir()
    assert (dest / "config" / "rclone.conf").read_text() == "[x]\n"
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in (dest / "config" / "tls").iterdir())
    st = client.get(f"{ADMIN}/backups/offsite").json()
    assert st["configured"] and st["kind"] == "folder" and st["keep"] == 14 and st["includeConfig"] is True
    assert st["last"]["ok"] is True and st["last"]["name"] == gz.name and st["error"] is None
    assert [i["name"] for i in st["items"]] == [gz.name] and st["items"][0]["kind"] == "scheduled"
    assert any(b["id"] == res["name"] and b["kind"] == "scheduled" for b in client.get(f"{ADMIN}/backups").json())


def test_not_configured(client, app):
    st = client.get(f"{ADMIN}/backups/offsite").json()
    assert st == {"configured": False, "destination": None, "kind": None, "keep": 14, "includeConfig": False,
                  "last": None, "items": [], "error": None}
    rec = app.state.ctx.jobs.run("backup")  # still makes the local nightly copy
    assert rec["ok"] and rec["result"]["offsite"] is None
    name = rec["result"]["name"]
    assert client.post(f"{ADMIN}/backups/{name}/offsite").status_code == 400


def test_keep_prunes_offsite_and_local_scheduled(client, app, tmp_path):
    dest = tmp_path / "off"
    _dest(app, str(dest), backup_keep=2)
    dest.mkdir()
    (dest / "holiday-photos.jpg").write_bytes(b"x")     # never ours: never touched
    for _ in range(5):
        assert app.state.ctx.jobs.run("backup")["ok"]
    assert len(list(dest.glob("homebeat-*.db.gz"))) == 2 and (dest / "holiday-photos.jpg").exists()
    local = [b for b in client.get(f"{ADMIN}/backups").json() if b["kind"] == "scheduled"]
    assert len(local) == backups.KEEP_LOCAL_SCHEDULED


def test_manual_backup_copied_on_request(client, app, tmp_path):
    dest = tmp_path / "off"
    _dest(app, str(dest), backup_include_config=False)
    b = client.post(f"{ADMIN}/backups", json={"note": "before tidy-up"}).json()
    r = client.post(f"{ADMIN}/backups/{b['id']}/offsite")
    assert r.status_code == 200 and r.json()["configCopied"] is False and not (dest / "config").exists()
    assert (dest / f"{b['id']}.gz").is_file()
    assert client.post(f"{ADMIN}/backups/homebeat-manual-20990101T000000Z.db/offsite").status_code == 404
    assert client.post(f"{ADMIN}/backups/..%2F..%2Fetc%2Fpasswd/offsite").status_code in (404, 400, 405)


def test_failed_copy_fails_the_job_but_keeps_the_local_copy(client, app, tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    _dest(app, str(blocker / "sub"))
    rec = app.state.ctx.jobs.run("backup")
    assert rec["ok"] is False and "can't write" in rec["error"]
    st = client.get(f"{ADMIN}/backups/offsite").json()
    assert st["last"]["ok"] is False and "can't write" in st["last"]["error"]
    assert any(b["kind"] == "scheduled" for b in client.get(f"{ADMIN}/backups").json())


@pytest.fixture
def fake_rclone(tmp_path, monkeypatch):
    """An `rclone` on PATH that maps "fake:<path>" to a local folder (lsjson, copyto, deletefile)."""
    root = tmp_path / "remote"
    root.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    script = bindir / "rclone"
    script.write_text(f"""#!{sys.executable}
import json, os, shutil, sys
ROOT = {str(root)!r}
def loc(s):
    if s.startswith("fake:"):
        return os.path.join(ROOT, s[5:].lstrip("/"))
    return s
cmd, *a = sys.argv[1:]
a = [x for x in a if not x.startswith("--")]
if cmd == "lsjson":
    d = loc(a[0])
    if not os.path.isdir(d):
        sys.stderr.write("error listing: directory not found\\n"); sys.exit(3)
    print(json.dumps([{{"Name": n, "Size": os.path.getsize(os.path.join(d, n))}} for n in os.listdir(d)
                      if os.path.isfile(os.path.join(d, n))]))
elif cmd == "copyto":
    os.makedirs(os.path.dirname(loc(a[1])), exist_ok=True); shutil.copyfile(loc(a[0]), loc(a[1]))
elif cmd == "deletefile":
    os.remove(loc(a[0]))
else:
    sys.exit(1)
""")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return root


def test_rclone_remote_copy_list_and_restore(client, app, fake_rclone, conn):
    _dest(app, "fake:music-backups", backup_keep=3)
    st = client.get(f"{ADMIN}/backups/offsite").json()
    assert st["kind"] == "rclone" and st["items"] == [] and st["error"] is None and st["includeConfig"] is False
    client.post(f"{API}/playlists", json={"name": "Keep me", "trackIds": []})
    rec = app.state.ctx.jobs.run("backup")
    assert rec["ok"], rec["error"]
    name = rec["result"]["offsite"]["name"]
    assert (fake_rclone / "music-backups" / name).is_file() and not (fake_rclone / "music-backups" / "config").exists()
    assert not list((app.state.ctx.cfg.data_dir / "backups").glob(".upload-*"))
    client.post(f"{API}/playlists", json={"name": "Added later", "trackIds": []})
    r = client.post(f"{ADMIN}/backups/{name}/restore", json={"source": "offsite"})
    assert r.status_code == 200, r.text
    assert _playlists(client) == {"Keep me"}


def test_rclone_missing(client, app, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    _dest(app, "nowhere:x")
    st = client.get(f"{ADMIN}/backups/offsite").json()
    assert "rclone is not installed" in st["error"]


# ---------- restore ----------


def test_restore_local_and_undo(client, app, conn):
    client.post(f"{API}/playlists", json={"name": "Old", "trackIds": [track_ids(conn)["Song 1"]]})
    b = client.post(f"{ADMIN}/backups").json()
    client.post(f"{API}/playlists", json={"name": "New", "trackIds": []})
    assert _playlists(client) == {"Old", "New"}
    r = client.post(f"{ADMIN}/backups/{b['id']}/restore", json={})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["restored"] == b["id"] and res["source"] == "local"
    assert res["schemaFrom"] == res["schemaTo"] == db.SCHEMA_VERSION
    assert _playlists(client) == {"Old"}
    listed = {x["id"]: x for x in client.get(f"{ADMIN}/backups").json()}
    assert listed[res["safetyBackup"]]["kind"] == "prerestore"
    # Undo: restore the safety copy
    assert client.post(f"{ADMIN}/backups/{res['safetyBackup']}/restore").status_code == 200
    assert _playlists(client) == {"Old", "New"}
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_restore_brings_back_settings_and_server_id(client, app, conn):
    b = client.post(f"{ADMIN}/backups").json()
    sid = app.state.ctx.server_id
    assert client.put(f"{ADMIN}/settings", json={"serverName": "Changed"}).status_code == 200
    assert app.state.ctx.cfg.server_name == "Changed"
    conn.execute("UPDATE settings SET value = '\"other-id\"' WHERE key = 'server_id'")
    assert client.post(f"{ADMIN}/backups/{b['id']}/restore").status_code == 200
    assert app.state.ctx.cfg.server_name == "Home NAS" and app.state.ctx.server_id == sid


def test_restore_migrates_an_older_backup(client, app, cfg):
    old = cfg.data_dir / "homebeat.db.v7-backup-20260101T000000Z"
    c = sqlite3.connect(old)
    c.executescript((FIXTURES / "schema_v7.sql").read_text())
    c.execute("INSERT INTO schema_version (version) VALUES (7)")
    c.commit()
    c.close()
    r = client.post(f"{ADMIN}/backups/{old.name}/restore")
    # The v7 copy has no device for this client: the restore still answers, then the token is gone.
    assert r.status_code == 200, r.text
    assert r.json()["schemaFrom"] == 7 and r.json()["schemaTo"] == db.SCHEMA_VERSION
    assert client.get(f"{API}/playlists").status_code == 401
    live = db.connect(cfg.db_path)
    try:
        assert live.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == db.SCHEMA_VERSION
        assert live.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1").fetchone()[0] >= 1
    finally:
        live.close()


def test_restore_refuses_bad_files(client, app, cfg):
    d = cfg.data_dir / "backups"
    d.mkdir(exist_ok=True)
    (d / "homebeat-manual-20260101T000000Z.db").write_bytes(b"this is not sqlite" * 100)
    r = client.post(f"{ADMIN}/backups/homebeat-manual-20260101T000000Z.db/restore")
    assert r.status_code == 422
    newer = d / "homebeat-manual-20260102T000000Z.db"
    b = client.post(f"{ADMIN}/backups").json()
    (d / b["id"]).rename(newer)
    c = sqlite3.connect(newer)
    c.execute("INSERT INTO schema_version (version) VALUES (?)", (db.SCHEMA_VERSION + 1,))
    c.commit()
    c.close()
    r = client.post(f"{ADMIN}/backups/{newer.name}/restore")
    assert r.status_code == 409 and "newer server" in r.json()["detail"]
    other = d / "homebeat-manual-20260103T000000Z.db"
    c = sqlite3.connect(other)
    c.execute("CREATE TABLE notes (x)")
    c.close()
    assert client.post(f"{ADMIN}/backups/{other.name}/restore").status_code == 422
    assert client.post(f"{ADMIN}/backups/nope.db/restore").status_code == 404
    assert client.post(f"{ADMIN}/backups/x/restore", json={"source": "offsite"}).status_code == 404
    assert client.post(f"{ADMIN}/backups/x/restore", json={"source": "web"}).status_code == 422
    assert not list(d.glob(".restore-*"))
    # nothing was restored, so no safety copies either
    assert not [b for b in client.get(f"{ADMIN}/backups").json() if b["kind"] == "prerestore"]


def test_non_admin_cannot_restore(bob):
    assert bob.post(f"{ADMIN}/backups/x/restore").status_code == 403
    assert bob.get(f"{ADMIN}/backups/offsite").status_code == 403
    assert bob.post(f"{ADMIN}/backups/x/offsite").status_code == 403


# ---------- CLI + config ----------


def test_cli_restore(cfg, conn, tmp_path, capsys, monkeypatch):
    from homebeat.__main__ import main

    toml = tmp_path / "config.toml"
    dest = tmp_path / "off"
    toml.write_text(f'library_root = "{cfg.library_root}"\ndata_dir = "{cfg.data_dir}"\n'
                    f'backup_copy_to = "{dest}"\n')
    c2 = config_mod.load(toml)
    b = backups.create(c2, conn, "manual")
    off = backups.copy_offsite(c2, b["name"])
    assert (dest / "config" / "config.toml").read_text() == toml.read_text()
    conn.execute("INSERT INTO settings (key, value) VALUES ('marker', '1')")
    main(["restore", "--config", str(toml)])
    out = capsys.readouterr().out
    assert b["name"] in out and off["name"] in out
    main(["restore", "--config", str(toml), "--offsite", off["name"]])
    assert "restored" in capsys.readouterr().out
    assert conn.execute("SELECT 1 FROM settings WHERE key = 'marker'").fetchone() is None
    with pytest.raises(SystemExit, match="no such backup"):
        main(["restore", "--config", str(toml), "homebeat-manual-20990101T000000Z.db"])


@pytest.mark.parametrize("value,ok", [("/mnt/backup", True), ("b2:bucket/music", True), ("relative/dir", False),
                                      ("~/backup", False), ("DATA/backups", False), ("LIB/sub", False)])
def test_config_validation(tmp_path, value, ok):
    lib, data = tmp_path / "music", tmp_path / "data"
    value = value.replace("DATA", str(data)).replace("LIB", str(lib))
    raw = {"library_root": str(lib), "data_dir": str(data), "backup_copy_to": value}
    if ok:
        assert config_mod.from_dict(raw).backup_copy_to == value
    else:
        with pytest.raises(config_mod.ConfigError):
            config_mod.from_dict(raw)
    assert config_mod.from_dict({**raw, "backup_copy_to": ""}).backup_copy_to is None
    with pytest.raises(config_mod.ConfigError):
        config_mod.from_dict({"library_root": str(lib), "data_dir": str(data), "backup_keep": 0})
