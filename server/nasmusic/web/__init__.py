"""Admin web UI (Jinja2, no JS): login, dashboard, pairing QR, suggestions, settings."""
from __future__ import annotations

import hashlib
import hmac
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from nasmusic import auth
from nasmusic.ai.api import AIConfig
from nasmusic.app import api as api_mod
from nasmusic.app.context import AppContext, ctx, db
from nasmusic.app.scheduler import JOB_NAMES
from nasmusic.auth import qr
from nasmusic.core import get_setting
from nasmusic.library import queries

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
router = APIRouter()

COOKIE = "nm_admin"
SESSION_S = 30 * 24 * 3600
MAX_FAILS, FAIL_WINDOW_S = 5, 300

_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


class LoginRequired(Exception):
    pass


# ---------- sessions (HMAC-signed cookie bound to the current password hash) ----------


def _sig(c: AppContext, exp: int) -> str:
    msg = f"admin.{exp}.{c.cfg.admin_password_hash or ''}".encode()
    return hmac.new(c.web_secret, msg, hashlib.sha256).hexdigest()


def make_session(c: AppContext) -> str:
    exp = int(time.time()) + SESSION_S
    return f"{exp}.{_sig(c, exp)}"


def session_ok(c: AppContext, value: str | None) -> bool:
    if not value or not c.cfg.admin_password_hash:
        return False
    exp_s, _, sig = value.partition(".")
    try:
        exp = int(exp_s)
    except ValueError:
        return False
    return exp > time.time() and hmac.compare_digest(sig, _sig(c, exp))


def admin(request: Request, c: AppContext = Depends(ctx)) -> None:
    if not session_ok(c, request.cookies.get(COOKIE)):
        raise LoginRequired()


def _rate_limited(ip: str) -> bool:
    now = time.time()
    with _fails_lock:
        recent = [t for t in _fails.get(ip, []) if now - t < FAIL_WINDOW_S]
        _fails[ip] = recent
        return len(recent) >= MAX_FAILS


def _record_fail(ip: str) -> None:
    with _fails_lock:
        _fails.setdefault(ip, []).append(time.time())


def _client_ip(request: Request) -> str:
    # Behind cloudflared every request comes from the tunnel container; Cloudflare adds the real IP.
    # Only trust that header on the internal plain-HTTP port, which only cloudflared can reach.
    c: AppContext = request.app.state.ctx
    server = request.scope.get("server") or (None, None)
    if server[1] == c.cfg.http_port and request.headers.get("cf-connecting-ip"):
        return request.headers["cf-connecting-ip"]
    return request.client.host if request.client else "?"


def render(request: Request, name: str, **kw) -> HTMLResponse:
    c: AppContext = request.app.state.ctx
    return templates.TemplateResponse(request, name, {"server_name": c.cfg.server_name, **kw})


# ---------- routes ----------


@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/admin", status_code=303)


@router.get("/admin/login", response_class=HTMLResponse)
def login_form(request: Request, c: AppContext = Depends(ctx)):
    return render(request, "login.html", error=None, configured=bool(c.cfg.admin_password_hash))


@router.post("/admin/login")
def login(request: Request, password: str = Form(...), c: AppContext = Depends(ctx)):
    ip = _client_ip(request)
    if _rate_limited(ip):
        return render(request, "login.html", error="Too many attempts. Wait a few minutes.",
                      configured=True)
    if not auth.verify_password(password, c.cfg.admin_password_hash):
        _record_fail(ip)
        return render(request, "login.html", error="Wrong password.", configured=bool(c.cfg.admin_password_hash))
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(COOKIE, make_session(c), max_age=SESSION_S, httponly=True, samesite="strict",
                    secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https",
                    path="/")
    return resp


@router.post("/admin/logout")
def logout():
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie(COOKIE, path="/")
    return resp


def _analysis_progress(conn: sqlite3.Connection) -> dict:
    mv = AIConfig.__dataclass_fields__["model_version"].default
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT j.status, COUNT(*) AS n FROM analysis_jobs j JOIN tracks t ON t.id = j.track_id "
        "WHERE j.model_version = ? AND t.missing = 0 GROUP BY j.status", (mv,))}
    analysed = conn.execute(
        "SELECT COUNT(*) FROM features f JOIN tracks t ON t.id = f.track_id "
        "WHERE f.model_version = ? AND t.missing = 0", (mv,)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 0").fetchone()[0]
    return {
        "model": mv, "analysed": analysed, "total": total, "failed": counts.get("failed", 0),
        "pending": counts.get("pending", 0),
        "pct": round(100 * analysed / total, 1) if total else 0.0,
    }


def _jobs(conn: sqlite3.Connection, c: AppContext) -> list[dict]:
    nxt = c.jobs.next_runs() if c.jobs else {}
    out = []
    for name in JOB_NAMES:
        rec = get_setting(conn, f"job.{name}")
        if rec is None and name == "full_scan":
            continue
        out.append({"name": name, "rec": rec, "running": bool(c.jobs and c.jobs.is_running(name)),
                    "next": nxt.get(name)})
    return out


@router.get("/admin", response_class=HTMLResponse, dependencies=[Depends(admin)])
def dashboard(request: Request, conn: sqlite3.Connection = Depends(db), c: AppContext = Depends(ctx)):
    return render(
        request, "dashboard.html",
        stats=queries.stats(conn), analysis=_analysis_progress(conn), jobs=_jobs(conn, c),
        devices=auth.list_devices(conn), cache_gb=round(c.opus.size_bytes() / 1e9, 2),
        cache_limit=c.cfg.opus_cache_gb, cert=c.cert_sha256,
        mixes=conn.execute("SELECT kind, title, created_at FROM mixes WHERE active = 1 ORDER BY created_at DESC")
        .fetchall(),
    )


@router.post("/admin/devices/{device_id}/revoke", dependencies=[Depends(admin)])
def revoke(device_id: str, conn: sqlite3.Connection = Depends(db)):
    if not auth.revoke_device(conn, device_id):
        raise HTTPException(404, "no such device")
    return RedirectResponse("/admin", status_code=303)


@router.get("/admin/pair", response_class=HTMLResponse, dependencies=[Depends(admin)])
def pair_page(request: Request, conn: sqlite3.Connection = Depends(db), c: AppContext = Depends(ctx)):
    code, expires = auth.create_pair_code(conn)
    p = qr.payload(c.cfg, c.server_id, c.cert_sha256, code)
    data = qr.payload_json(p)
    resp = render(request, "pair.html", code=code, expires=expires, qr=qr.png_data_uri(data), payload=p)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/admin/suggestions", response_class=HTMLResponse, dependencies=[Depends(admin)])
def suggestions_page(request: Request, conn: sqlite3.Connection = Depends(db)):
    return render(request, "suggestions.html", suggestions=api_mod.list_suggestions(conn))


@router.post("/admin/suggestions/{suggestion_id}", dependencies=[Depends(admin)])
def answer(suggestion_id: str, status: str = Form(...), conn: sqlite3.Connection = Depends(db)):
    if status not in ("want", "not_interested", "have", "new"):
        raise HTTPException(400, "bad status")
    if not api_mod.answer_suggestion(conn, suggestion_id, status):
        raise HTTPException(404, "no such suggestion")
    return RedirectResponse(f"/admin/suggestions#s-{suggestion_id}", status_code=303)


@router.get("/admin/settings", response_class=HTMLResponse, dependencies=[Depends(admin)])
def settings_page(request: Request, conn: sqlite3.Connection = Depends(db), c: AppContext = Depends(ctx),
                  started: str | None = None):
    cfg = c.cfg
    secret = lambda v: "set" if v else "not set"
    rows = [
        ("Config file", str(cfg.source_path or "-")),
        ("Library", str(cfg.library_root)),
        ("Data directory", str(cfg.data_dir)),
        ("LAN URL", cfg.lan_url),
        ("Tunnel URL", cfg.tunnel_url or "not set"),
        ("CF Access client id", secret(cfg.cf_access_client_id)),
        ("CF Access client secret", secret(cfg.cf_access_client_secret)),
        ("Last.fm API key", secret(cfg.lastfm_api_key)),
        ("Timezone", cfg.timezone),
        ("Opus cache limit", f"{cfg.opus_cache_gb:g} GB"),
        ("Scan interval", f"{cfg.scan_interval_minutes} min"),
        ("Plain HTTP port (cloudflared)", str(cfg.http_port)),
        ("Cert SHA-256", c.cert_sha256 or "-"),
        ("Server id", c.server_id),
        ("Version", c.version),
    ]
    return render(request, "settings.html", rows=rows, jobs=_jobs(conn, c), started=started,
                  can_run=c.jobs is not None)


@router.post("/admin/jobs/{name}", dependencies=[Depends(admin)])
def run_job(name: str, c: AppContext = Depends(ctx)):
    if name not in JOB_NAMES or c.jobs is None:
        raise HTTPException(404, "no such job")
    c.jobs.trigger(name)
    return RedirectResponse(f"/admin/settings?started={name}", status_code=303)


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
