"""Server configuration, loaded from a TOML file (path from --config or $NASMUSIC_CONFIG)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("/data/config.toml")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    library_root: Path
    data_dir: Path
    lan_host: str = "127.0.0.1"           # NAS static IP; goes into the cert SAN and the pairing QR
    https_port: int = 8443
    http_port: int = 8080                 # plain HTTP for cloudflared only; don't publish it
    bind_host: str = "0.0.0.0"
    server_name: str = "Home NAS"
    tunnel_url: str | None = None
    cf_access_client_id: str | None = None      # only ever put into the pairing QR
    cf_access_client_secret: str | None = None
    admin_password_hash: str | None = None      # from `nasmusic hash-password`; seeds the first admin user
    admin_username: str = "admin"               # name of the first admin user (v1 data is migrated to it)
    web_root: Path | None = None                # built web player (web/dist); default /opt/nasmusic/web
    cors_origins: list[str] = field(default_factory=list)  # extra CORS origins (e.g. a Vite dev server)
    timezone: str = "Europe/London"
    opus_cache_gb: float = 20.0
    lastfm_api_key: str | None = None
    acoustid_api_key: str | None = None         # automatic tag fixing; free key from https://acoustid.org/new-application
    model_version: str = "effnet-discogs-1"   # audio-analysis model (see nasmusic/ai/README.md)
    scan_interval_minutes: int = 5
    lrclib_enabled: bool = True                 # lyrics fallback from lrclib.net when a file has none
    artist_info_enabled: bool = True            # artist pages: photo/bio/links from MusicBrainz + Wikipedia/Wikidata
    explicit_deezer: bool = True                # explicit-content job asks Deezer (free API) about untagged tracks
    lan_public_url: str | None = None           # public LAN address incl. port, if not https://lan_host:https_port
    # ---- casting to speakers (nasmusic/cast, deploy/README.md "Casting") ----
    cast_enabled: bool = True
    cast_port: int = 8090                       # plain-HTTP port serving ONLY signed /cast/ media URLs to speakers
    cast_media_url: str | None = None           # base URL speakers fetch from; default http://lan_host:cast_port
    cast_discovery: bool = True                 # mDNS (Chromecast) + SSDP (DLNA); needs host networking in Docker
    cast_url_ttl_s: int = 3600                  # signed URL lifetime on top of the track's duration
    chromecast_hosts: list[str] = field(default_factory=list)   # manual IPs when mDNS can't reach the LAN
    dlna_renderers: list[str] = field(default_factory=list)     # manual device description URLs (http://ip:port/x.xml)
    homeassistant_url: str | None = None        # e.g. http://192.168.1.10:8123
    homeassistant_token: str | None = None      # long-lived access token (config.toml only, never the repo)
    homeassistant_entities: list[str] = field(default_factory=list)  # media_player.* to offer; empty = all
    snapcast_host: str | None = None            # snapserver host (JSON-RPC on snapcast_control_port)
    snapcast_control_port: int = 1705
    snapcast_stream_port: int = 4953            # snapserver `tcp://...?mode=server` source the server feeds PCM to
    snapcast_stream_id: str = "nasmusic"        # that source's name= in snapserver.conf
    source_path: Path | None = field(default=None, compare=False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "nasmusic.db"

    @property
    def cast_base_url(self) -> str:
        """Where speakers fetch signed media URLs (plain HTTP: DLNA/Snapcast can't do self-signed TLS)."""
        if self.cast_media_url:
            return self.cast_media_url.rstrip("/")
        host = f"[{self.lan_host}]" if ":" in self.lan_host else self.lan_host
        return f"http://{host}:{self.cast_port}"

    @property
    def lan_url(self) -> str:
        if self.lan_public_url:
            return self.lan_public_url.rstrip("/")
        host = f"[{self.lan_host}]" if ":" in self.lan_host else self.lan_host
        return f"https://{host}:{self.https_port}"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)


def resolve_path(cli_path: str | os.PathLike | None = None) -> Path:
    if cli_path:
        return Path(cli_path)
    env = os.environ.get("NASMUSIC_CONFIG")
    if env:
        return Path(env)
    return DEFAULT_CONFIG_PATH


def load(path: str | os.PathLike | None = None) -> Config:
    p = resolve_path(path)
    try:
        raw = tomllib.loads(p.read_text())
    except FileNotFoundError as e:
        raise ConfigError(
            f"config file not found: {p}. Create it (in Docker: /data/config.toml in the data volume; the image "
            f"ships an example at /opt/nasmusic/config.example.toml), or point NASMUSIC_CONFIG / --config at it."
        ) from e
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {p}: {e}") from e
    return from_dict(raw, source=p)


def from_dict(raw: dict, source: Path | None = None) -> Config:
    known = {f.name for f in fields(Config)} - {"source_path"}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"unknown config keys: {', '.join(sorted(unknown))}")
    for req in ("library_root", "data_dir"):
        if not raw.get(req):
            raise ConfigError(f"missing required config key: {req}")
    vals = dict(raw)
    base = source.parent if source else Path.cwd()
    if vals.get("web_root"):
        wr = Path(vals["web_root"]).expanduser()
        vals["web_root"] = wr if wr.is_absolute() else (base / wr).resolve()
    else:
        vals["web_root"] = None
    for key in ("library_root", "data_dir"):
        pth = Path(vals[key]).expanduser()
        vals[key] = pth if pth.is_absolute() else (base / pth).resolve()
    for key in ("tunnel_url", "cf_access_client_id", "cf_access_client_secret", "admin_password_hash",
                "lastfm_api_key", "acoustid_api_key", "lan_public_url", "cast_media_url", "homeassistant_url", "homeassistant_token",
                "snapcast_host"):
        if vals.get(key) == "":
            vals[key] = None
    if vals.get("tunnel_url"):
        vals["tunnel_url"] = vals["tunnel_url"].rstrip("/")
    cfg = Config(**vals, source_path=source)
    if cfg.opus_cache_gb <= 0:
        raise ConfigError("opus_cache_gb must be positive")
    return cfg
