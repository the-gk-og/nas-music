"""Per-process state shared by the API and web UI, plus FastAPI dependencies."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status

from nasmusic import auth
from nasmusic.auth import users
from nasmusic.app.scheduler import Jobs
from nasmusic.config import Config
from nasmusic.core import open_db
from nasmusic.stream.transcode import OpusCache


@dataclass
class AppContext:
    cfg: Config
    server_id: str
    cert_sha256: str | None
    opus: OpusCache
    version: str
    web_secret: bytes = field(repr=False, default=b"")
    jobs: Jobs | None = None
    http_transport: object | None = None   # httpx transport override for upstream calls (tests)
    hub: object | None = None              # nasmusic.connect.Hub
    weather: object | None = None          # nasmusic.app.weather.Weather
    catalog: object | None = None          # nasmusic.catalog.Catalog (online catalogue for search)
    base_cfg: Config | None = None         # config.toml as loaded; `cfg` = this + admin-console overrides
    lrclib: object | None = None           # nasmusic.library.lrclib.Lrclib
    autoeq: object | None = None           # nasmusic.app.autoeq.AutoEq (created on first use)
    started_at: float = 0.0                # time.time() at start (uptime)
    artist_fetcher: object | None = None   # nasmusic.library.artistinfo.Fetcher (created on first use)
    scrobbler: object | None = None        # nasmusic.app.scrobble.Scrobbler (v7)
    dj_voice: object | None = None         # nasmusic.djvoice.tts.Engine (created on first use, see app.djvoice_api)
    jam_hub: object | None = None          # nasmusic.jam.hub.JamHub (created on first use, see app.jam_api)
    login_limiter: users.RateLimiter = field(default_factory=lambda: users.RateLimiter(5, 60.0))
    # Family-mode unlock PINs can be 4 digits: 5 tries per 15 minutes makes guessing one take weeks, not a day.
    pin_limiter: users.RateLimiter = field(default_factory=lambda: users.RateLimiter(5, 900.0))

    @property
    def data_dir(self) -> Path:
        return self.cfg.data_dir


SESSION_COOKIE = "nm_session"


def ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = open_db(request.app.state.ctx.cfg)
    try:
        yield conn
    finally:
        conn.close()


QUERY_TOKEN_PATHS = ("/api/v1/stream/", "/api/v1/art/", "/api/v1/admin/logs/stream", "/api/v1/admin/preview/")


def request_token(request: Request) -> str | None:
    """Bearer header first; else the web player's httpOnly `nm_session` cookie; else, for /stream and /art
    only (<audio>/<img> in cross-origin clients can't send headers), `?token=`."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie
    if request.url.path.startswith(QUERY_TOKEN_PATHS):
        return request.query_params.get("token") or None
    return None


def _netloc(url_or_host: str | None, scheme: str | None = None) -> str | None:
    """Lower-case host[:port] with the scheme's default port dropped, for comparing origins."""
    if not url_or_host:
        return None
    if "://" in url_or_host:
        u = urlsplit(url_or_host)
        scheme, netloc = u.scheme, u.netloc
    else:
        netloc = url_or_host
    netloc = netloc.strip().lower().rsplit("@", 1)[-1]
    default = {"https": ":443", "wss": ":443", "http": ":80", "ws": ":80"}.get(scheme or "")
    if default:
        netloc = netloc.removesuffix(default)
    return netloc or None


def origin_allowed(headers: Mapping[str, str], cfg: Config) -> bool:
    """CSRF / cross-site WebSocket guard for requests authenticated by the `nm_session` cookie.

    A browser sends `Origin` on every cross-origin request, on POST/PUT/PATCH/DELETE, and on WebSocket
    handshakes. It must name this server: the Host the request came in on (or the proxy's X-Forwarded-Host),
    the configured LAN or tunnel address, or an allowed CORS origin. No Origin (curl, the apps, old browsers)
    passes: those either don't use the cookie or are covered by SameSite=Lax."""
    origin = headers.get("origin")
    if not origin:
        return True
    if origin == "null":
        return False
    o = _netloc(origin)
    scheme = urlsplit(origin).scheme
    ok = {_netloc(headers.get("host"), scheme), _netloc(headers.get("x-forwarded-host"), scheme),
          _netloc(cfg.lan_url), _netloc(cfg.tunnel_url)}
    ok |= {_netloc(x) for x in cfg.cors_origins}
    ok.discard(None)
    return o in ok


def device(request: Request, conn: sqlite3.Connection = Depends(db)) -> auth.Device:
    token = request_token(request)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    dev = auth.device_for_token(conn, token)
    if dev is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked token",
                            headers={"WWW-Authenticate": "Bearer"})
    return dev


def admin_device(dev: auth.Device = Depends(device)) -> auth.Device:
    if not dev.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admin only")
    return dev


def client_ip(request: Request) -> str:
    # Behind cloudflared every request comes from the tunnel container; Cloudflare adds the real IP.
    # Only trust that header on the internal plain-HTTP port, which only cloudflared can reach.
    c: AppContext = request.app.state.ctx
    server = request.scope.get("server") or (None, None)
    if server[1] == c.cfg.http_port:
        if request.headers.get("cf-connecting-ip"):
            return request.headers["cf-connecting-ip"]
        # Behind the frp relay, Caddy (same Docker network, PROXY protocol from frp) sets X-Forwarded-For
        # to the real visitor. Caddy replaces any X-Forwarded-For a visitor sends, so the last hop is it.
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[-1].strip() or (request.client.host if request.client else "?")
    return request.client.host if request.client else "?"
