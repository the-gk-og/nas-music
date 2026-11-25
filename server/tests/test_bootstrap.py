"""First-run config from HOMEBEAT_* environment variables (deploy/docker)."""
import os
import stat

import pytest

from homebeat import bootstrap, config
from homebeat.__main__ import main
from homebeat.auth import verify_password

ENV = ("HOMEBEAT_LAN_HOST", "HOMEBEAT_SERVER_NAME", "HOMEBEAT_TIMEZONE", "TZ", "HOMEBEAT_ADMIN_USERNAME",
       "HOMEBEAT_ADMIN_PASSWORD", "HOMEBEAT_TUNNEL_URL", "HOMEBEAT_LAN_PORT", "HOMEBEAT_CAST_PORT",
       "HOMEBEAT_UPLOAD_ROOT", "HOMEBEAT_AUDIOBOOKS_ROOT", "HOMEBEAT_CAST_DISCOVERY", "HOMEBEAT_OPUS_CACHE_GB",
       "HOMEBEAT_LIBRARY_ROOT", "HOMEBEAT_DATA_DIR")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for k in ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOMEBEAT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("HOMEBEAT_LIBRARY_ROOT", str(tmp_path / "music"))


def test_minimal_env_writes_valid_private_config_with_generated_password(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMEBEAT_LAN_HOST", "192.168.1.50")
    path = tmp_path / "data" / "config.toml"
    generated = bootstrap.write_config(path)
    assert generated and len(generated) >= 12
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    cfg = config.load(path)
    assert cfg.lan_host == "192.168.1.50" and cfg.lan_url == "https://192.168.1.50:8443"
    assert cfg.admin_username == "admin" and verify_password(generated, cfg.admin_password_hash)
    assert generated not in path.read_text()            # only the hash is stored
    assert cfg.tunnel_url is None and cfg.upload_root is None and cfg.cast_discovery is True


def test_all_options(monkeypatch, tmp_path):
    for k, v in {"HOMEBEAT_LAN_HOST": "10.0.0.5", "HOMEBEAT_SERVER_NAME": 'Eli\'s "NAS"',
                 "TZ": "America/New_York", "HOMEBEAT_ADMIN_USERNAME": "eli", "HOMEBEAT_ADMIN_PASSWORD": "hunter2hunter2",
                 "HOMEBEAT_TUNNEL_URL": "https://music.example.com/", "HOMEBEAT_LAN_PORT": "9443",
                 "HOMEBEAT_CAST_PORT": "9090", "HOMEBEAT_UPLOAD_ROOT": str(tmp_path / "uploads"),
                 "HOMEBEAT_CAST_DISCOVERY": "false", "HOMEBEAT_OPUS_CACHE_GB": "5"}.items():
        monkeypatch.setenv(k, v)
    path = tmp_path / "data" / "config.toml"
    assert bootstrap.write_config(path) is None
    cfg = config.load(path)
    assert cfg.server_name == 'Eli\'s "NAS"' and cfg.timezone == "America/New_York"
    assert cfg.admin_username == "eli" and verify_password("hunter2hunter2", cfg.admin_password_hash)
    assert cfg.tunnel_url == "https://music.example.com"
    assert cfg.lan_url == "https://10.0.0.5:9443" and cfg.cast_base_url == "http://10.0.0.5:9090"
    assert cfg.upload_root == tmp_path / "uploads" and cfg.cast_discovery is False and cfg.opus_cache_gb == 5


@pytest.mark.parametrize("env,msg", [({}, "HOMEBEAT_LAN_HOST"),
                                     ({"HOMEBEAT_ADMIN_PASSWORD": "short"}, "at least 8"),
                                     ({"HOMEBEAT_LAN_PORT": "abc"}, "port number")])
def test_bad_env_writes_nothing(monkeypatch, tmp_path, env, msg):
    if env:
        monkeypatch.setenv("HOMEBEAT_LAN_HOST", "192.168.1.50")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    path = tmp_path / "data" / "config.toml"
    with pytest.raises(config.ConfigError, match=msg):
        bootstrap.write_config(path)
    assert not path.exists()


def test_init_config_cli_never_overwrites(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOMEBEAT_LAN_HOST", "192.168.1.50")
    path = tmp_path / "config.toml"
    main(["init-config", "--config", str(path)])
    out = capsys.readouterr().out
    assert "First admin user:  admin" in out and "Password:" in out
    before = path.read_text()
    with pytest.raises(SystemExit, match="already exists"):
        main(["init-config", "--config", str(path)])
    assert path.read_text() == before
