"""Installs from before the nas-music -> Homebeat rename keep their database, env vars, backups and CLI name."""
import sqlite3
import tomllib
from pathlib import Path

from homebeat import config as config_mod
from homebeat.app import backups


def _cfg(tmp_path: Path):
    return config_mod.from_dict({"library_root": str(tmp_path / "music"), "data_dir": str(tmp_path / "data"),
                                 "lan_host": "192.168.1.5"})


def test_fresh_install_uses_homebeat_db(tmp_path):
    assert _cfg(tmp_path).db_path.name == "homebeat.db"


def test_existing_nasmusic_db_is_kept_in_place(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.ensure_dirs()
    sqlite3.connect(cfg.data_dir / "nasmusic.db").close()
    assert cfg.db_path == cfg.data_dir / "nasmusic.db"
    # if both exist (should never happen), the new name wins
    sqlite3.connect(cfg.data_dir / "homebeat.db").close()
    assert cfg.db_path.name == "homebeat.db"


def test_legacy_env_vars_still_work(monkeypatch):
    monkeypatch.delenv("HOMEBEAT_CONFIG", raising=False)
    monkeypatch.setenv("NASMUSIC_CONFIG", "/old/config.toml")
    assert config_mod.resolve_path() == Path("/old/config.toml")
    monkeypatch.setenv("HOMEBEAT_CONFIG", "/new/config.toml")
    assert config_mod.resolve_path() == Path("/new/config.toml")


def test_pre_rename_backups_still_recognised():
    assert backups.MIGRATION.match("nasmusic.db.v11-backup-20261124T041500Z")
    assert backups.OWN.match("nasmusic-scheduled-20261124T041500Z.db").group(1) == "scheduled"
    assert backups.OFFSITE.match("nasmusic-manual-20261124T041500Z-2.db.gz").group(2) == "20261124T041500Z"
    assert backups.OWN.match("homebeat-manual-20261124T041500Z.db")


def test_old_cli_name_kept():
    scripts = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts["homebeat"] == scripts["nasmusic"] == "homebeat.__main__:main"
