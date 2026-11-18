"""FastAPI application factory: the /api/v1 API and the admin web UI on one app."""
from __future__ import annotations

import secrets
from importlib.metadata import PackageNotFoundError, version

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse

from nasmusic.app.context import AppContext
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
    init_db(cfg)
    files, _ = tls.ensure_cert(cfg.data_dir, cfg.lan_host)
    conn = open_db(cfg)
    try:
        sid = server_id(conn)
        secret = _web_secret(conn)
        set_setting(conn, "server_version", VERSION)
    finally:
        conn.close()
    return AppContext(
        cfg=cfg,
        server_id=sid,
        cert_sha256=tls.fingerprint(files.cert),
        opus=jobs.opus if jobs else OpusCache(cfg.data_dir / "opus", int(cfg.opus_cache_gb * 1024**3)),
        version=VERSION,
        web_secret=secret,
        jobs=jobs,
    )


def create_app(cfg: Config, jobs: Jobs | None = None, context: AppContext | None = None) -> FastAPI:
    from nasmusic import web
    from nasmusic.app import api

    app = FastAPI(title="nas-music", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.ctx = context or build_context(cfg, jobs)
    app.include_router(api.public, prefix="/api/v1")
    app.include_router(api.router, prefix="/api/v1")
    app.include_router(web.router)

    @app.exception_handler(web.LoginRequired)
    async def _login_required(request: Request, exc: web.LoginRequired):
        return RedirectResponse("/admin/login", status_code=303)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        resp = await call_next(request)
        if not request.url.path.startswith("/api/"):
            resp.headers.setdefault("X-Frame-Options", "DENY")
            resp.headers.setdefault("X-Content-Type-Options", "nosniff")
            resp.headers.setdefault("Referrer-Policy", "no-referrer")
            resp.headers.setdefault(
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; "
                "media-src 'self'; form-action 'self'; frame-ancestors 'none'",
            )
        return resp

    return app
