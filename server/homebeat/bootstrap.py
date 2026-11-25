"""First-run config: write config.toml from HOMEBEAT_* environment variables (Docker installs).

Used by `homebeat serve` when the config file doesn't exist yet and HOMEBEAT_LAN_HOST is set, and by
`homebeat init-config`. An existing config file is never touched: after the first start, config.toml in
the data folder is the one place to change settings, and the environment variables are ignored.
"""
from __future__ import annotations

import os
import secrets
import tomllib
from pathlib import Path

from homebeat.config import ConfigError, env, from_dict

# Environment variable -> what it sets. Documented in deploy/docker/.env.example and deploy/docker/README.md.
ENV_LAN_HOST = "HOMEBEAT_LAN_HOST"


class BootstrapError(ConfigError):
    pass


def _env(name: str, default: str | None = None) -> str | None:
    return env(name, default)


def _port(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        n = int(raw)
    except ValueError as e:
        raise BootstrapError(f"{name} must be a port number, got {raw!r}") from e
    if not 1 <= n <= 65535:
        raise BootstrapError(f"{name} must be between 1 and 65535, got {n}")
    return n


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    s = str(v)
    out = []
    for ch in s:
        if ch in '"\\':
            out.append("\\" + ch)
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _host_url(scheme: str, host: str, port: int) -> str:
    h = f"[{host}]" if ":" in host else host
    return f"{scheme}://{h}:{port}"


def values_from_env() -> tuple[dict, str | None]:
    """The config values the environment asks for, and the generated admin password (None if one was given)."""
    lan_host = _env(ENV_LAN_HOST)
    if not lan_host:
        raise BootstrapError(
            f"{ENV_LAN_HOST} is not set. Set it to your NAS's LAN IP address (for example 192.168.1.50) in the "
            ".env file next to compose.yaml, or create config.toml in the data folder yourself."
        )
    vals: dict = {
        "library_root": _env("HOMEBEAT_LIBRARY_ROOT", "/music"),
        "data_dir": _env("HOMEBEAT_DATA_DIR", "/data"),
        "lan_host": lan_host,
        "server_name": _env("HOMEBEAT_SERVER_NAME", "Home NAS"),
        "timezone": _env("HOMEBEAT_TIMEZONE") or _env("TZ") or "Europe/London",
        "admin_username": _env("HOMEBEAT_ADMIN_USERNAME", "admin"),
        "opus_cache_gb": float(_env("HOMEBEAT_OPUS_CACHE_GB", "10")),
    }
    # Ports as published on the NAS. The container always listens on 8443 / 8080 / 8090; when the host
    # side is different, the app and speakers have to be told the outside address.
    lan_port = _port("HOMEBEAT_LAN_PORT", 8443)
    if lan_port != 8443:
        vals["lan_public_url"] = _host_url("https", lan_host, lan_port)
    cast_port = _port("HOMEBEAT_CAST_PORT", 8090)
    if cast_port != 8090:
        vals["cast_media_url"] = _host_url("http", lan_host, cast_port)
    for var, key in (("HOMEBEAT_TUNNEL_URL", "tunnel_url"),
                     ("HOMEBEAT_UPLOAD_ROOT", "upload_root"),
                     ("HOMEBEAT_AUDIOBOOKS_ROOT", "audiobooks_root")):
        if v := _env(var):
            vals[key] = v
    if _env("HOMEBEAT_CAST_DISCOVERY", "").lower() in ("0", "false", "no", "off"):
        vals["cast_discovery"] = False

    from homebeat.auth import hash_password

    password = _env("HOMEBEAT_ADMIN_PASSWORD")
    generated = None
    if password is None:
        generated = password = secrets.token_urlsafe(12)
    elif len(password) < 8:
        raise BootstrapError("HOMEBEAT_ADMIN_PASSWORD must be at least 8 characters")
    vals["admin_password_hash"] = hash_password(password)
    return vals, generated


def render(vals: dict) -> str:
    lines = [
        "# Homebeat server configuration, written on first start from the HOMEBEAT_* environment variables.",
        "# From now on this file is the one place to change settings; restart the container afterwards.",
        "# Every option is explained in /opt/homebeat/config.example.toml (server/config.example.toml in the repo).",
        "",
    ]
    lines += [f"{k} = {_toml_value(v)}" for k, v in vals.items()]
    return "\n".join(lines) + "\n"


def write_config(path: Path) -> str | None:
    """Create `path` from the environment. Returns the generated admin password, if one was generated."""
    vals, generated = values_from_env()
    text = render(vals)
    from_dict(tomllib.loads(text), source=path)  # validate before anything is written
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # holds the admin hash (+ later API keys)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)
    return generated


def banner(path: Path, username: str, generated: str | None) -> str:
    lines = [f"Created {path} from the HOMEBEAT_* environment variables."]
    if generated:
        lines += [
            "",
            "=" * 64,
            f"  First admin user:  {username}",
            f"  Password:          {generated}",
            "  Sign in to the web player and change it (Account page), or run",
            f"  `homebeat user passwd {username}` inside the container.",
            "=" * 64,
        ]
    return "\n".join(lines)
