"""Config path resolution: --config > $HOMEBEAT_CONFIG > /data/config.toml (the Docker/TrueNAS layout)."""
from pathlib import Path

import pytest

from homebeat import config


def test_resolution_order(monkeypatch, tmp_path):
    monkeypatch.delenv("HOMEBEAT_CONFIG", raising=False)
    assert config.resolve_path() == Path("/data/config.toml")
    monkeypatch.setenv("HOMEBEAT_CONFIG", str(tmp_path / "env.toml"))
    assert config.resolve_path() == tmp_path / "env.toml"
    assert config.resolve_path(tmp_path / "cli.toml") == tmp_path / "cli.toml"


def test_env_config_loads_and_relative_paths(monkeypatch, tmp_path):
    (tmp_path / "config.toml").write_text('library_root = "music"\ndata_dir = "data"\nmodel_version = "basic-1"\n')
    monkeypatch.setenv("HOMEBEAT_CONFIG", str(tmp_path / "config.toml"))
    cfg = config.load()
    assert cfg.library_root == tmp_path / "music" and cfg.data_dir == tmp_path / "data"
    assert cfg.model_version == "basic-1" and cfg.source_path == tmp_path / "config.toml"


def test_missing_config_message_mentions_docker_layout(monkeypatch, tmp_path):
    monkeypatch.setenv("HOMEBEAT_CONFIG", str(tmp_path / "nope.toml"))
    with pytest.raises(config.ConfigError) as e:
        config.load()
    assert "/data/config.toml" in str(e.value) and "HOMEBEAT_CONFIG" in str(e.value)


def test_example_config_is_valid(tmp_path):
    example = Path(__file__).parents[1] / "config.example.toml"
    cfg = config.load(example)
    assert cfg.admin_username == "admin" and cfg.https_port == 8443
