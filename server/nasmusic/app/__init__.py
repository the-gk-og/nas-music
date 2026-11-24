"""FastAPI application factory: the /api/v1 API and the admin web UI on one app."""
from __future__ import annotations

import logging
import re
import secrets
import time
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from nasmusic.app.context import SESSION_COOKIE, AppContext, origin_allowed
from nasmusic.app.scheduler import Jobs
from nasmusic.auth import tls
from nasmusic.config import Config
from nasmusic.core import get_setting, init_db, open_db, server_id, set_setting
from nasmusic.stream.transcode import OpusCache

try:
    VERSION = version("nasmusic")
except PackageNotFoundError:  # running from a source tree without install
    VERSION = "0.0.0"


def _web_secret(conn) -> bytes:
    s = get_setting(conn, "web_secret")
    if not s:
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('web_secret', ?)",
                     (f'"{secrets.token_hex(32)}"',))
        s = get_setting(conn, "web_secret")
    return bytes.fromhex(s)


def build_context(cfg: Config, jobs: Jobs | None = None) -> AppContext:
    from nasmusic.app import serversettings

    init_db(cfg)
    base_cfg = cfg
    _c = open_db(cfg)
    try:
        cfg = serversettings.apply(base_cfg, serversettings.overrides(_c))  # admin-console overrides
    finally:
        _c.close()
    if jobs is not None:
        jobs.cfg = cfg
        jobs.opus.max_bytes = int(cfg.opus_cache_gb * 1024**3)
        from nasmusic.library.lrclib import Lrclib

        jobs.lrclib = Lrclib(VERSION, min_interval_s=1.0)  # background prefetch: gentle
    files, _ = tls.ensure_cert(cfg.data_dir, cfg.lan_host)
    conn = open_db(cfg)
    try:
        sid = server_id(conn)
        secret = _web_secret(conn)
        set_setting(conn, "server_version", VERSION)
    finally:
        conn.close()
    from nasmusic.app.connect_api import make_hub
    from nasmusic.catalog import Catalog
    from nasmusic.library.lrclib import Lrclib
    from nasmusic.app.weather import Weather

    c = AppContext(
        cfg=cfg,
        server_id=sid,
        cert_sha256=tls.fingerprint(files.cert),
        opus=jobs.opus if jobs else OpusCache(cfg.data_dir / "opus", int(cfg.opus_cache_gb * 1024**3)),
        version=VERSION,
        web_secret=secret,
        jobs=jobs,
        hub=make_hub(cfg),
        weather=Weather(),
        catalog=Catalog(),
        base_cfg=base_cfg,
        lrclib=Lrclib(VERSION),
        started_at=time.time(),
    )
    from nasmusic.app.cast_api import make_registry

    c.hub.attach_speakers(make_registry(c))
    from nasmusic.app.scrobble import Scrobbler

    c.scrobbler = Scrobbler(cfg, version=VERSION)
    c.hub.listeners.append(c.scrobbler.on_hub)
    if jobs is not None:
        jobs.hub = c.hub
        jobs.scrobbler = c.scrobbler
    return c


_TOKEN_RE = re.compile(r"([?&](?:token|sig)=)[^&\s\"]+")


class _RedactTokens(logging.Filter):
    """Keep `?token=` (accepted on /stream and /art) out of the access log."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_TOKEN_RE.sub(r"\1REDACTED", a) if isinstance(a, str) else a for a in record.args)
        elif isinstance(record.msg, str):
            record.msg = _TOKEN_RE.sub(r"\1REDACTED", record.msg)
        return True


def install_token_redaction() -> None:
    lg = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, _RedactTokens) for f in lg.filters):
        lg.addFilter(_RedactTokens())


MAX_BODY_BYTES = 20 * 1024 * 1024  # largest legitimate body: a 16 MiB upload chunk
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


class BodyLimit:
    """Refuse request bodies over `limit` bytes (413) before anything reads them into memory. FastAPI parses
    JSON bodies before running auth dependencies, so without this an unauthenticated client could make the
    server buffer an arbitrarily large body on /auth/login, /pair, /voice/command, ..."""

    def __init__(self, app, limit: int = MAX_BODY_BYTES):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        for k, v in scope.get("headers") or ():
            if k == b"content-length":
                try:
                    too_big = int(v) > self.limit
                except ValueError:
                    too_big = True
                if too_big:
                    return await JSONResponse({"detail": "request body too large"}, status_code=413)(
                        scope, receive, send)
        seen = 0

        async def limited():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.limit:
                    raise _TooLarge()
            return msg

        started = False

        async def tracking_send(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, limited, tracking_send)
        except _TooLarge:
            if not started:
                await JSONResponse({"detail": "request body too large"}, status_code=413)(scope, receive, send)


class _TooLarge(HTTPException):
    def __init__(self):
        super().__init__(413, "request body too large")


TAURI_ORIGINS = ["tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"]
ADMIN_CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; media-src 'self'; "
             "form-action 'self'; frame-ancestors 'none'")
PLAYER_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
              "media-src 'self' blob:; connect-src 'self' ws: wss:; font-src 'self' data:; frame-ancestors 'none'; "
              "base-uri 'self'; form-action 'self'")


def create_app(cfg: Config, jobs: Jobs | None = None, context: AppContext | None = None) -> FastAPI:
    from nasmusic import web
    from nasmusic.app import (account_api, admin_api, api, automix_api, autotag_api, cast_api, collections_api,
                              connect_api, continuing, discover_api, family_api, insights_api, jam_api, logbuffer,
                              scrobble_api, smart_api, spoken_api, tags_api, uploads_api, voice_api)
    from nasmusic.web import spa

    install_token_redaction()
    logbuffer.install()
    app = FastAPI(title="nas-music", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = context or build_context(cfg, jobs)
    for r in (api.public, account_api.public, api.router, account_api.router, account_api.admin,
              collections_api.router, discover_api.router, insights_api.router, insights_api.admin, admin_api.router,
              tags_api.router, autotag_api.router, connect_api.router, cast_api.media_router, cast_api.router,
              family_api.router, family_api.admin, jam_api.public, jam_api.host, voice_api.public,
              voice_api.router, uploads_api.router, uploads_api.admin, smart_api.router, scrobble_api.router,
              spoken_api.router, automix_api.router, continuing.router):
        app.include_router(r, prefix="/api/v1")
    app.include_router(web.router)
    app.include_router(spa.router_for(cfg, web.render_join))  # catch-all: must stay last

    # Desktop app (Tauri) calls the API cross-origin with the bearer token; no cookies cross-origin.
    app.add_middleware(
        CORSMiddleware, allow_origins=TAURI_ORIGINS + list(cfg.cors_origins), allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Range", "X-Jam-Token", "CF-Access-Client-Id", "CF-Access-Client-Secret"],
        expose_headers=["Content-Range", "Content-Length", "Accept-Ranges", "Content-Disposition", "X-Stream-Format"], max_age=600,
    )

    app.add_middleware(BodyLimit)

    @app.exception_handler(web.LoginRequired)
    async def _login_required(request: Request, exc: web.LoginRequired):
        return RedirectResponse("/admin/login", status_code=303)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        # CSRF: a state-changing request that relies on the session cookie (no bearer header) must come from
        # one of this server's own pages.
        if (request.method not in SAFE_METHODS and SESSION_COOKIE in request.cookies
                and not request.headers.get("authorization", "").lower().startswith("bearer ")
                and not origin_allowed(request.headers, request.app.state.ctx.cfg)):
            return JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        resp = await call_next(request)
        path = request.url.path
        if not path.startswith("/api/"):
            resp.headers.setdefault("X-Content-Type-Options", "nosniff")
            resp.headers.setdefault("Referrer-Policy", "no-referrer")
            resp.headers.setdefault("X-Frame-Options", "DENY")
            admin_like = path.startswith("/admin") or (path.startswith("/join/") and spa.web_root(cfg) is None)
            resp.headers.setdefault("Content-Security-Policy", ADMIN_CSP if admin_like else PLAYER_CSP)
        return resp

    return app
