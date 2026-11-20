"""Server settings editable from the admin console. Stored in `settings` as `server.<key>` (JSON) and applied
over config.toml at start and on every change (docs: PUT /admin/settings)."""
from __future__ import annotations

import ipaddress
import sqlite3
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nasmusic.config import Config
from nasmusic.core import get_setting, set_setting

# API key -> Config field
FIELDS = {"serverName": "server_name", "lanUrl": "lan_public_url", "tunnelUrl": "tunnel_url",
          "timezone": "timezone", "opusCacheMaxGb": "opus_cache_gb", "lrclib": "lrclib_enabled",
          "explicitDeezer": "explicit_deezer"}
PREFIX = "server."


def _url(value: Any, key: str, allow_path: bool) -> str | None:
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a URL string")
    v = value.strip().rstrip("/")
    if v == "" and key == "tunnelUrl":
        return ""  # explicitly: no tunnel address
    parts = urlsplit(v)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError(f"{key} must look like https://host[:port]{'[/path]' if allow_path else ''}")
    try:
        port = parts.port  # raises ValueError on a bad port
    except ValueError as e:
        raise ValueError(f"{key}: invalid port") from e
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{key}: invalid port")
    if (parts.path and not allow_path) or parts.query or parts.fragment or parts.username:
        raise ValueError(f"{key} must not have a {'path, ' if not allow_path else ''}query, fragment or user")
    host = parts.hostname
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not all(lbl and len(lbl) < 64 and lbl.replace("-", "").isalnum() for lbl in host.split(".")):
            raise ValueError(f"{key}: invalid host name") from None
    return v


def validate(key: str, value: Any) -> Any:
    """Return the normalised value or raise ValueError."""
    if key not in FIELDS:
        raise ValueError(f"unknown setting {key}")
    if key == "serverName":
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= 60:
            raise ValueError("serverName must be 1-60 characters")
        return value.strip()
    if key == "lanUrl":
        return _url(value, key, allow_path=False)
    if key == "tunnelUrl":
        return _url(value, key, allow_path=True)
    if key == "timezone":
        try:
            ZoneInfo(str(value))
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise ValueError("timezone must be an IANA zone like Europe/London") from e
        return str(value)
    if key == "opusCacheMaxGb":
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 2000:
            raise ValueError("opusCacheMaxGb must be a number between 1 and 2000")
        return float(value)
    if key in ("lrclib", "explicitDeezer"):
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false")
        return value
    raise ValueError(key)


def overrides(conn: sqlite3.Connection) -> dict[str, Any]:
    out = {}
    for key in FIELDS:
        v = get_setting(conn, PREFIX + key, None)
        if v is not None:
            out[key] = v
    return out


def apply(base: Config, ov: dict[str, Any]) -> Config:
    vals = {}
    for key, v in ov.items():
        if key in FIELDS:
            field = FIELDS[key]
            vals[field] = (None if v == "" else v) if key == "tunnelUrl" else v
    return replace(base, **vals) if vals else base


def values(cfg: Config) -> dict:
    return {"lanUrl": cfg.lan_url, "tunnelUrl": cfg.tunnel_url, "timezone": cfg.timezone,
            "opusCacheMaxGb": cfg.opus_cache_gb, "lrclib": cfg.lrclib_enabled, "explicitDeezer": cfg.explicit_deezer,
            "serverName": cfg.server_name}


def view(base: Config, ov: dict[str, Any]) -> dict:
    """GET /admin/settings: the effective values flat at the top level (AdminSettings), plus `overrides`
    (keys set in the database) and `fromConfig` (config.toml, what a null resets to)."""
    return {**values(apply(base, ov)), "overrides": ov, "fromConfig": values(base)}


def put(conn: sqlite3.Connection, changes: dict[str, Any]) -> dict[str, Any]:
    """Validate everything first, then write. Returns the new overrides."""
    clean = {k: (None if v is None else validate(k, v)) for k, v in changes.items()}
    for k, v in clean.items():
        if v is None:
            conn.execute("DELETE FROM settings WHERE key = ?", (PREFIX + k,))
        else:
            set_setting(conn, PREFIX + k, v)
    return overrides(conn)
