"""Server configuration, loaded from a TOML file (path from --config or $NASMUSIC_CONFIG)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("/data/config.toml")

# tracks.path prefix of files under upload_root (a second, writable library root). The scanner never lets a
# top-level folder of library_root with this name in, so ids from the two roots cannot collide.
UPLOADS_PREFIX = "@uploads/"


class LibraryRoot(Path):
    """library_root that also knows upload_root: `root / "@uploads/a/b.flac"` is upload_root/a/b.flac,
    every other relative path is under library_root. Callers keep writing `cfg.library_root / row["path"]`.
    Derived paths (parent, `/` results) are plain Paths."""

    def __init__(self, *args, upload_root: Path | None = None):
        super().__init__(*args)
        self.upload_root = Path(upload_root) if upload_root else None

    def with_segments(self, *segments):
        return Path(*segments)

    def _route(self, key) -> Path:
        s = os.fspath(key)
        if s.startswith(UPLOADS_PREFIX):
            # No upload root configured: point somewhere that never exists rather than into the library.
            base = self.upload_root or Path("/nonexistent-upload-root")
            return base / s[len(UPLOADS_PREFIX):]
        return Path(os.fspath(self)) / key

    def __truediv__(self, key):
        return self._route(key)

    def joinpath(self, *parts):
        if not parts:
            return Path(os.fspath(self))
        out = self._route(parts[0])
        return out.joinpath(*parts[1:])

    def __reduce__(self):
        return (_library_root, (str(self), str(self.upload_root) if self.upload_root else None))


def _library_root(path: str, upload_root: str | None) -> LibraryRoot:
    return LibraryRoot(path, upload_root=Path(upload_root) if upload_root else None)


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
    # Where the plain-HTTP port listens; default bind_host. Set "127.0.0.1" when cloudflared runs on the host
    # network (or the container uses network_mode: host for casting), so other machines on the LAN can't reach
    # it and fake the visitor address the tunnel adds. Leave unset when cloudflared is a separate container
    # that connects to nasmusic:8080.
    http_bind_host: str | None = None
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
    lastfm_api_secret: str | None = None        # with lastfm_api_key: lets users connect Last.fm for scrobbling
    acoustid_api_key: str | None = None         # automatic tag fixing; free key from https://acoustid.org/new-application
    model_version: str = "effnet-discogs-1"   # audio-analysis model (see nasmusic/ai/README.md)
    scan_interval_minutes: int = 5
    lrclib_enabled: bool = True                 # lyrics fallback from lrclib.net when a file has none
    artist_info_enabled: bool = True            # artist pages: photo/bio/links from MusicBrainz + Wikipedia/Wikidata
    explicit_deezer: bool = True                # explicit-content job asks Deezer (free API) about untagged tracks
    lan_public_url: str | None = None           # public LAN address incl. port, if not https://lan_host:https_port
    # ---- uploads (nasmusic/library/uploads.py): a separate WRITABLE folder, never inside library_root ----
    upload_root: Path | None = None             # e.g. /uploads; unset = uploading is off
    upload_max_file_mb: int = 2048              # largest single file
    upload_quota_gb: float = 50.0               # default per-user quota (admins can change it per user)
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
    # ---- podcasts, audiobooks, internet radio (nasmusic/spoken; never part of music features) ----
    audiobooks_root: Path | None = None         # separate read-only folder of books (folder = book); unset = off
    podcast_poll_minutes: int = 60
    podcast_keep_last: int = 3                  # server downloads per show (newest N; 0 = stream from the feed)
    podcast_storage_gb: float = 20.0            # cap for data_dir/podcasts
    podcastindex_api_key: str | None = None     # free key + secret from https://api.podcastindex.org
    podcastindex_api_secret: str | None = None
    podcast_itunes_search: bool = True          # iTunes Search API for podcast discovery (no key)
    radio_browser_url: str = "https://all.api.radio-browser.info"
    # ---- off-site backups (nasmusic/app/backups.py, deploy/docker/README.md "Backups") ----
    backup_copy_to: str | None = None           # a folder on another disk/dataset ("/backup") or rclone "remote:path"
    backup_keep: int = 14                       # nightly copies kept there
    backup_include_config: bool | None = None   # also copy config.toml + TLS cert; default: folder yes, rclone no
    source_path: Path | None = field(default=None, compare=False)

    def __post_init__(self):
        if self.upload_root is not None:
            self.upload_root = Path(self.upload_root)
        self.library_root = LibraryRoot(self.library_root, upload_root=self.upload_root)

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
    if vals.get("upload_root"):
        up = Path(vals["upload_root"]).expanduser()
        vals["upload_root"] = up if up.is_absolute() else (base / up).resolve()
    else:
        vals["upload_root"] = None
    for key in ("library_root", "data_dir"):
        pth = Path(vals[key]).expanduser()
        vals[key] = pth if pth.is_absolute() else (base / pth).resolve()
    if vals.get("audiobooks_root"):
        pth = Path(vals["audiobooks_root"]).expanduser()
        vals["audiobooks_root"] = pth if pth.is_absolute() else (base / pth).resolve()
    else:
        vals["audiobooks_root"] = None
    for key in ("tunnel_url", "cf_access_client_id", "cf_access_client_secret", "admin_password_hash",
                "lastfm_api_key", "lastfm_api_secret", "acoustid_api_key", "lan_public_url", "cast_media_url",
                "homeassistant_url", "homeassistant_token", "snapcast_host", "podcastindex_api_key",
                "podcastindex_api_secret", "backup_copy_to"):
        if vals.get(key) == "":
            vals[key] = None
    if vals.get("tunnel_url"):
        vals["tunnel_url"] = vals["tunnel_url"].rstrip("/")
    cfg = Config(**vals, source_path=source)
    if cfg.opus_cache_gb <= 0:
        raise ConfigError("opus_cache_gb must be positive")
    if cfg.upload_root is not None:
        lib, up = Path(os.path.abspath(cfg.library_root)), Path(os.path.abspath(cfg.upload_root))
        if lib == up or lib in up.parents or up in lib.parents:
            raise ConfigError("upload_root must be a separate folder, not inside (or containing) library_root")
    if cfg.upload_max_file_mb <= 0 or cfg.upload_quota_gb < 0:
        raise ConfigError("upload_max_file_mb must be positive and upload_quota_gb not negative")
    if cfg.podcast_keep_last < 0 or cfg.podcast_storage_gb < 0 or cfg.podcast_poll_minutes < 5:
        raise ConfigError("podcast_keep_last/podcast_storage_gb must be >= 0 and podcast_poll_minutes >= 5")
    if cfg.backup_keep < 1:
        raise ConfigError("backup_keep must be at least 1")
    if cfg.backup_copy_to is not None:
        dest = str(cfg.backup_copy_to).strip()
        if dest.startswith("~") or (not dest.startswith("/") and ":" not in dest):
            raise ConfigError('backup_copy_to must be an absolute folder ("/backup") or an rclone remote ("name:path")')
        if dest.startswith("/"):
            d = Path(os.path.abspath(dest))
            lib = Path(os.path.abspath(cfg.library_root))
            if d == lib or lib in d.parents:
                raise ConfigError("backup_copy_to must not be inside library_root (the library is read-only)")
            if d == Path(os.path.abspath(cfg.data_dir)) or Path(os.path.abspath(cfg.data_dir)) in d.parents:
                raise ConfigError("backup_copy_to must be on another disk or dataset, not inside data_dir")
        cfg.backup_copy_to = dest
    return cfg
