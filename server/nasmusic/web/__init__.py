"""Admin web UI under /admin (Jinja2, no JS): login as an admin user, dashboard, pairing QR, suggestions,
settings, users and invites. `/` belongs to the web player (nasmusic.web.spa)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from nasmusic import auth
from nasmusic.app import suggestions as sugg
from nasmusic.app.account_api import set_session_cookie
from nasmusic.app.context import SESSION_COOKIE, AppContext, client_ip, ctx, db
from nasmusic.app.scheduler import JOB_NAMES
from nasmusic.auth import qr
from nasmusic.auth import users
from nasmusic.core import get_setting, now_iso
from nasmusic.web import spa
from nasmusic.library import queries

HERE = Path(__file__).parent
templates = Jinja2Templates(directory=str(HERE / "templates"))


def _localtime(value: str | None, tz: str = "UTC", fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Render a stored ISO-8601 UTC timestamp in the server's configured timezone."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(ZoneInfo(tz)).strftime(fmt)
    except (ValueError, KeyError):
        return value


templates.env.filters["localtime"] = _localtime
router = APIRouter()


class LoginRequired(Exception):
    pass


# ---------- sessions: the same device token as the web player (`nm_session` cookie), admins only ----------


def admin(request: Request, conn: sqlite3.Connection = Depends(db)) -> auth.Device:
    token = request.cookies.get(SESSION_COOKIE)
    dev = auth.device_for_token(conn, token) if token else None
    if dev is None or not dev.is_admin:
        raise LoginRequired()
    return dev


def render(request: Request, name: str, **kw) -> HTMLResponse:
    c: AppContext = request.app.state.ctx
    return templates.TemplateResponse(request, name, {"server_name": c.cfg.server_name, "tz": c.cfg.timezone, **kw})


def _has_password(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM users WHERE password_hash != '!' AND disabled = 0 LIMIT 1").fetchone() is not None


# ---------- routes ----------


@router.get("/admin/login", response_class=HTMLResponse)
def login_form(request: Request, conn: sqlite3.Connection = Depends(db)):
    return render(request, "login.html", error=None, configured=_has_password(conn), username="")


@router.post("/admin/login")
def login(request: Request, username: str = Form(""), password: str = Form(...),
          conn: sqlite3.Connection = Depends(db), c: AppContext = Depends(ctx)):
    ip = client_ip(request)
    username = username.strip() or c.cfg.admin_username
    if c.login_limiter.blocked(ip):
        return render(request, "login.html", error="Too many attempts. Wait a minute.", configured=True,
                      username=username)
    try:
        u = users.authenticate(conn, username, password)
    except users.UserError as e:
        c.login_limiter.fail(ip)
        msg = "Account disabled." if e.status == 403 else "Wrong username or password."
        return render(request, "login.html", error=msg, configured=_has_password(conn), username=username)
    if not u.is_admin:
        return render(request, "login.html", error="These pages are for admins. Use the player instead.",
                      configured=True, username=username)
    _, token = auth.create_device(conn, u.id, "Admin pages (browser)", "web")
    resp = RedirectResponse("/admin", status_code=303)
    set_session_cookie(resp, request, token)
    return resp


@router.post("/admin/logout")
def logout(request: Request, conn: sqlite3.Connection = Depends(db)):
    token = request.cookies.get(SESSION_COOKIE)
    dev = auth.device_for_token(conn, token) if token else None
    if dev:
        auth.revoke_device(conn, dev.id)
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


def _analysis_progress(conn: sqlite3.Connection, configured: str) -> dict:
    """Progress for the model version the AI is actually writing: the version of the most recently
    analysed features row (e.g. 'basic-1' when Essentia is unavailable), else the configured one."""
    status: dict = {}
    srow = conn.execute("SELECT value FROM settings WHERE key = 'ai.status.model_version'").fetchone()
    if srow:
        try:
            status = json.loads(srow[0]) or {}
        except (ValueError, TypeError):
            status = {}
    mv = status.get("analysing")
    if not mv:
        row = conn.execute("SELECT model_version FROM features ORDER BY analysed_at DESC LIMIT 1").fetchone()
        mv = row[0] if row else configured
    counts = {r["status"]: r["n"] for r in conn.execute(
        "SELECT j.status, COUNT(*) AS n FROM analysis_jobs j JOIN tracks t ON t.id = j.track_id "
        "WHERE j.model_version = ? AND t.missing = 0 GROUP BY j.status", (mv,))}
    analysed = conn.execute(
        "SELECT COUNT(*) FROM features f JOIN tracks t ON t.id = f.track_id "
        "WHERE f.model_version = ? AND t.missing = 0", (mv,)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 0").fetchone()[0]
    return {
        "model": mv, "configured": configured, "analysed": analysed, "total": total, "failed": counts.get("failed", 0),
        "pending": counts.get("pending", 0),
        "pct": round(100 * analysed / total, 1) if total else 0.0,
        "fallback_reason": status.get("fallback_reason"),
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


@router.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request, me: auth.Device = Depends(admin), conn: sqlite3.Connection = Depends(db),
              c: AppContext = Depends(ctx)):
    online = set()
    if c.hub:
        for uid in list(c.hub.users):
            online |= c.hub.online_device_ids(uid)
    return render(
        request, "dashboard.html", me=me, online=online,
        stats=queries.stats(conn, me.user_id), analysis=_analysis_progress(conn, c.cfg.model_version), jobs=_jobs(conn, c),
        devices=auth.list_devices(conn), cache_gb=round(c.opus.size_bytes() / 1e9, 2),
        cache_limit=c.cfg.opus_cache_gb, cert=c.cert_sha256,
        mixes=conn.execute("SELECT kind, title, created_at FROM mixes WHERE active = 1 AND user_id = ? "
                           "ORDER BY created_at DESC", (me.user_id,)).fetchall(),
    )


@router.post("/admin/devices/{device_id}/revoke", dependencies=[Depends(admin)])
def revoke(device_id: str, conn: sqlite3.Connection = Depends(db), c: AppContext = Depends(ctx)):
    if not auth.revoke_device(conn, device_id):
        raise HTTPException(404, "no such device")
    if c.hub:
        c.hub.disconnect_device(device_id)
    return RedirectResponse("/admin", status_code=303)


@router.get("/admin/pair", response_class=HTMLResponse)
def pair_page(request: Request, me: auth.Device = Depends(admin), conn: sqlite3.Connection = Depends(db),
              c: AppContext = Depends(ctx)):
    code, expires = auth.create_pair_code(conn, me.user_id)
    p = qr.payload(c.cfg, c.server_id, c.cert_sha256, code)
    data = qr.payload_json(p)
    resp = render(request, "pair.html", username=me.username, code=code, expires=expires, qr=qr.png_data_uri(data), payload=p)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/admin/suggestions", response_class=HTMLResponse)
def suggestions_page(request: Request, me: auth.Device = Depends(admin), conn: sqlite3.Connection = Depends(db)):
    return render(request, "suggestions.html", suggestions=sugg.list_all(conn, me.user_id))


@router.post("/admin/suggestions/{suggestion_id}")
def answer(suggestion_id: str, status: str = Form(...), me: auth.Device = Depends(admin),
           conn: sqlite3.Connection = Depends(db)):
    if status not in ("want", "not_interested", "have", "new"):
        raise HTTPException(400, "bad status")
    if not sugg.answer(conn, me.user_id, suggestion_id, status):
        raise HTTPException(404, "no such suggestion")
    return RedirectResponse(f"/admin/suggestions#s-{suggestion_id}", status_code=303)


@router.get("/admin/suggestions/{suggestion_id}/preview")
async def admin_preview(suggestion_id: str, request: Request, me: auth.Device = Depends(admin),
                        conn: sqlite3.Connection = Depends(db), c: AppContext = Depends(ctx)):
    return await sugg.preview(c, conn, me.user_id, suggestion_id, request.headers.get("range"))


@router.get("/admin/suggestions/{suggestion_id}/cover")
def admin_cover(suggestion_id: str, size: int = 256, me: auth.Device = Depends(admin),
                conn: sqlite3.Connection = Depends(db), c: AppContext = Depends(ctx)):
    return FileResponse(sugg.cover(c, conn, me.user_id, suggestion_id, size), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=604800"})


@router.get("/admin/settings", response_class=HTMLResponse, dependencies=[Depends(admin)])
def settings_page(request: Request, conn: sqlite3.Connection = Depends(db), c: AppContext = Depends(ctx),
                  started: str | None = None):
    cfg = c.cfg
    secret = lambda v: "set" if v else "not set"  # noqa: E731
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
        ("First admin user", cfg.admin_username),
        ("Web player", str(spa.web_root(cfg) or "not installed")),
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


# ---------- users + invites ----------


@router.get("/admin/users", response_class=HTMLResponse)
def users_page(request: Request, me: auth.Device = Depends(admin), conn: sqlite3.Connection = Depends(db),
               c: AppContext = Depends(ctx), error: str | None = None, invite: str | None = None):
    invites = [{"code": r["code"], "url": f"{c.cfg.tunnel_url or c.cfg.lan_url}/join/{r['code']}",
                "note": r["note"], "created_at": r["created_at"], "expires_at": r["expires_at"],
                "created_by": r["created_by_name"], "used_by": r["used_by_name"], "used_at": r["used_at"],
                "expired": r["expires_at"] <= now_iso()} for r in users.list_invites(conn)]
    return render(request, "users.html", me=me, users=users.list_all(conn), invites=invites, error=error,
                  new_invite=invite)


@router.post("/admin/invites")
def create_invite(note: str = Form(""), days: int = Form(7), me: auth.Device = Depends(admin),
                  conn: sqlite3.Connection = Depends(db)):
    inv = users.create_invite(conn, me.user_id, note.strip() or None, max(1, min(days, 90)))
    return RedirectResponse(f"/admin/users?invite={inv['code']}", status_code=303)


@router.post("/admin/invites/{code}/delete", dependencies=[Depends(admin)])
def delete_invite(code: str, conn: sqlite3.Connection = Depends(db)):
    users.delete_invite(conn, code)
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}")
def edit_user(user_id: str, action: str = Form(...), password: str = Form(""), me: auth.Device = Depends(admin),
              conn: sqlite3.Connection = Depends(db), c: AppContext = Depends(ctx)):
    try:
        revoked: list[str] = []
        if action in ("disable", "enable"):
            revoked = users.update(conn, user_id, disabled=action == "disable")
        elif action in ("make_admin", "remove_admin"):
            users.update(conn, user_id, is_admin=action == "make_admin")
        elif action == "reset_password":
            if users.get(conn, user_id) is None:
                raise users.UserError(404, "no such user")
            users.set_password(conn, user_id, password)
            revoked = auth.revoke_user_devices(conn, user_id)
        else:
            raise users.UserError(400, "unknown action")
    except users.UserError as e:
        return RedirectResponse(f"/admin/users?error={quote(e.detail)}", status_code=303)
    for d in revoked:
        if c.hub:
            c.hub.disconnect_device(d)
    return RedirectResponse("/admin/users", status_code=303)


# ---------- /join/<code> without the web player: minimal registration form ----------


def render_join(request: Request, code: str, error: str | None = None, done: str | None = None) -> HTMLResponse:
    return render(request, "join.html", code=code, error=error, done=done)


@router.post("/join/{code}", response_class=HTMLResponse, include_in_schema=False)
def join(request: Request, code: str, username: str = Form(...), password: str = Form(...),
         conn: sqlite3.Connection = Depends(db), c: AppContext = Depends(ctx)):
    ip = client_ip(request)
    if c.login_limiter.blocked(ip):
        return render_join(request, code, "Too many attempts. Wait a minute.")
    try:
        u = users.register(conn, code, username.strip(), password)
    except users.UserError as e:
        c.login_limiter.fail(ip)
        return render_join(request, code, e.detail)
    return render_join(request, code, done=u.username)


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)
