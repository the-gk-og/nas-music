"""Serve the web player (web/dist) at `/` with history-API fallback to index.html.

Where the build is looked for: config `web_root`, else /opt/nasmusic/web (Docker image), else the repo's
web/dist next to server/. Without a build, `/` shows a placeholder and `/join/<code>` a small
server-rendered registration form, so invites still work."""
from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse

from nasmusic.config import Config

DOCKER_WEB = Path("/opt/nasmusic/web")
REPO_WEB = Path(__file__).resolve().parents[3] / "web" / "dist"
RESERVED = ("api/", "admin", "favicon.ico")


def web_root(cfg: Config) -> Path | None:
    for cand in (cfg.web_root, DOCKER_WEB, REPO_WEB):
        if cand and (cand / "index.html").is_file():
            return cand.resolve()
    return None


def _file(root: Path, rel: str) -> Path | None:
    p = (root / rel).resolve()
    if root not in p.parents and p != root:
        return None  # path traversal
    return p if p.is_file() else None


def router_for(cfg: Config, render_join) -> APIRouter:
    router = APIRouter()

    @router.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request):
        if path.startswith(RESERVED):
            raise HTTPException(404, "not found")
        root = web_root(cfg)
        if root is None:
            if path.startswith("join/"):
                return render_join(request, path.removeprefix("join/").strip("/"))
            return HTMLResponse(PLACEHOLDER, headers={"Cache-Control": "no-store"})
        f = _file(root, path) if path else None
        if f is not None:
            immutable = path.startswith("assets/")
            media = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            return FileResponse(f, media_type=media, headers={
                "Cache-Control": "public, max-age=31536000, immutable" if immutable else "no-cache"})
        if "." in path.rsplit("/", 1)[-1] and not path.startswith("join/"):
            raise HTTPException(404, "not found")  # a missing asset, not an app route
        return FileResponse(root / "index.html", media_type="text/html", headers={"Cache-Control": "no-cache"})

    return router


PLACEHOLDER = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="light dark">
<title>nas-music</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1rem}
a{color:#2f6f5e}@media(prefers-color-scheme:dark){body{background:#000;color:#eee}a{color:#4ade80}}</style></head>
<body><h1>&#9835; nas-music</h1><p>The web player isn't installed on this server yet.</p>
<p>Use the Android or desktop app, or the <a href="/admin">admin pages</a>.</p></body></html>"""
