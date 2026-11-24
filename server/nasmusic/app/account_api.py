"""Accounts under /api/v1: server info, login/register/logout, QR pairing, /me/*, and admin user management."""
from __future__ import annotations

import json
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from nasmusic import auth
from nasmusic.app import collections as coll
from nasmusic.app import playlists, suggestions, userdata
from nasmusic.app.context import SESSION_COOKIE, AppContext, admin_device, client_ip, ctx, db, device
from nasmusic.auth import Device, qr, users

public = APIRouter()
router = APIRouter(dependencies=[Depends(device)])
admin = APIRouter(prefix="/admin", dependencies=[Depends(admin_device)])

Conn = sqlite3.Connection
Kind = Literal["android", "web", "desktop", "watch", "other"]
COOKIE_MAX_AGE = 365 * 24 * 3600


def _err(e: users.UserError):
    raise HTTPException(e.status, e.detail)


def secure_request(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


def set_session_cookie(resp: Response, request: Request, token: str) -> None:
    resp.set_cookie(SESSION_COOKIE, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax",
                    secure=secure_request(request), path="/")


def session_response(request: Request, u: users.User, device_id: str, token: str, cookie: bool) -> JSONResponse:
    resp = JSONResponse({"userId": u.id, "username": u.username, "isAdmin": u.is_admin, "deviceId": device_id,
                         "deviceToken": token})
    if cookie:
        set_session_cookie(resp, request, token)
    return resp


# ---------- public ----------


@public.get("/server-info")
def server_info(c: AppContext = Depends(ctx)):
    return {"serverId": c.server_id, "name": c.cfg.server_name, "version": c.version, "lanUrl": c.cfg.lan_url,
            "tunnelUrl": c.cfg.tunnel_url, "certSha256": c.cert_sha256, "registration": "invite"}


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)
    deviceName: str = Field(min_length=1, max_length=200)
    deviceKind: Kind = "other"
    appVersion: str | None = Field(default=None, max_length=50)
    cookie: bool = False


class RegisterBody(LoginBody):
    inviteCode: str = Field(min_length=1, max_length=64)


def _limited(request: Request, c: AppContext) -> str:
    ip = client_ip(request)
    if c.login_limiter.blocked(ip):
        raise HTTPException(429, "too many attempts; try again in a minute")
    return ip


@public.post("/auth/login")
def login(body: LoginBody, request: Request, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    ip = _limited(request, c)
    try:
        u = users.authenticate(conn, body.username, body.password)
    except users.UserError as e:
        c.login_limiter.fail(ip)
        _err(e)
    device_id, token = auth.create_device(conn, u.id, body.deviceName, body.deviceKind, body.appVersion)
    return session_response(request, u, device_id, token, body.cookie)


@public.post("/auth/register")
def register(body: RegisterBody, request: Request, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    ip = _limited(request, c)
    try:
        u = users.register(conn, body.inviteCode, body.username, body.password)
    except users.UserError as e:
        c.login_limiter.fail(ip)
        _err(e)
    conn.execute("UPDATE users SET last_login = created_at WHERE id = ?", (u.id,))
    device_id, token = auth.create_device(conn, u.id, body.deviceName, body.deviceKind, body.appVersion)
    return session_response(request, u, device_id, token, body.cookie)


class PairBody(BaseModel):
    pairCode: str = Field(min_length=1, max_length=64)
    deviceName: str = Field(min_length=1, max_length=200)
    deviceKind: Kind = "android"
    appVersion: str | None = Field(default=None, max_length=50)


@public.post("/pair")
def pair(body: PairBody, request: Request, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    ip = _limited(request, c)
    res = auth.pair_device(conn, body.pairCode, body.deviceName, body.deviceKind, body.appVersion)
    if res is None:
        c.login_limiter.fail(ip)
        raise HTTPException(403, "pair code invalid or expired")
    user_id, device_id, token = res
    return session_response(request, users.get(conn, user_id), device_id, token, False)


# ---------- signed in ----------


@router.post("/auth/logout", status_code=204)
def logout(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    auth.revoke_device(conn, me.id)
    if c.hub:
        c.hub.disconnect_device(me.id)
    resp = Response(status_code=204)
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@router.get("/me")
def get_me(me: Device = Depends(device), conn: Conn = Depends(db)):
    return users.get(conn, me.user_id).as_api()


class PasswordBody(BaseModel):
    current: str = Field(max_length=1024)
    new: str = Field(max_length=1024)


@router.put("/me/password", status_code=204)
def change_password(body: PasswordBody, me: Device = Depends(device), conn: Conn = Depends(db),
                    c: AppContext = Depends(ctx)):
    """Changing your password signs out every other device (this one stays signed in)."""
    try:
        users.change_password(conn, me.user_id, body.current, body.new)
    except users.UserError as e:
        _err(e)
    for d in auth.revoke_user_devices(conn, me.user_id, except_device=me.id):
        if c.hub:
            c.hub.disconnect_device(d)
    return Response(status_code=204)


def device_list(conn: Conn, c: AppContext, uid: str, current: str | None) -> list[dict]:
    online = c.hub.online_device_ids(uid) if c.hub else set()
    return [{"id": r["id"], "name": r["name"], "kind": r["kind"], "appVersion": r["app_version"],
             "createdAt": r["created_at"], "lastSeen": r["last_seen"], "online": r["id"] in online,
             "current": r["id"] == current} for r in auth.list_devices(conn, uid)]


@router.get("/me/devices")
def my_devices(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return device_list(conn, c, me.user_id, me.id)


@router.delete("/me/devices/{device_id}", status_code=204)
def revoke_my_device(device_id: str, me: Device = Depends(device), conn: Conn = Depends(db),
                     c: AppContext = Depends(ctx)):
    if not auth.revoke_device(conn, device_id, me.user_id):
        raise HTTPException(404, "not one of your devices")
    if c.hub:
        c.hub.disconnect_device(device_id)
    return Response(status_code=204)


@router.post("/me/pair-code")
def my_pair_code(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    code, expires = auth.create_pair_code(conn, me.user_id)
    return {"pairCode": code, "expiresAt": expires,
            "qrPayload": qr.payload(c.cfg, c.server_id, c.cert_sha256, code)}


class BugReportIn(BaseModel):
    description: str | None = Field(default=None, max_length=5000)
    appVersion: str | None = Field(default=None, max_length=50)
    platform: str | None = Field(default=None, max_length=100)
    logs: str
    crash: str | None = None
    extra: dict | None = None


@router.post("/me/bug-report")
async def bug_report(request: Request, me: Device = Depends(device), c: AppContext = Depends(ctx)):
    from starlette.concurrency import run_in_threadpool

    from nasmusic.app import bugreports
    from nasmusic.core import open_db

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > bugreports.MAX_BYTES:
        raise HTTPException(413, "bug report too large (max 2 MB)")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > bugreports.MAX_BYTES:
            raise HTTPException(413, "bug report too large (max 2 MB)")
    try:
        body = BugReportIn.model_validate_json(bytes(buf))
    except ValueError as e:
        raise HTTPException(422, "invalid bug report") from e

    def store() -> str:
        conn = open_db(c.cfg)
        try:
            return bugreports.create(conn, me.user_id, me.id, body.model_dump(), len(buf))
        finally:
            conn.close()

    try:
        rid = await run_in_threadpool(store)
    except bugreports.TooMany as e:
        raise HTTPException(429, "too many bug reports today") from e
    return {"id": rid}


@router.get("/me/settings")
def get_settings(me: Device = Depends(device), conn: Conn = Depends(db)):
    return {"settings": userdata.get_settings(conn, me.user_id)}


class SettingsBody(BaseModel):
    settings: dict


@router.put("/me/settings")
def put_settings(body: SettingsBody, me: Device = Depends(device), conn: Conn = Depends(db)):
    try:
        return {"settings": userdata.put_settings(conn, me.user_id, body.settings)}
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/me/data")
def data_summary(me: Device = Depends(device), conn: Conn = Depends(db)):
    return userdata.summary(conn, me.user_id)


@router.get("/me/data/export")
def data_export(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    uid = me.user_id
    doc = userdata.export(
        conn, uid, users.get(conn, uid).as_api(), device_list(conn, c, uid, me.id),
        [playlists.get_playlist(conn, uid, p["id"]) for p in playlists.list_playlists(conn, uid)
         if p["ownerId"] == uid],
        [coll.get_queue(conn, uid, q["id"]) for q in coll.list_queues(conn, uid)],
        suggestions.list_all(conn, uid),
    )
    return Response(json.dumps(doc, ensure_ascii=False, indent=1), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="nas-music-{me.username}.json"',
                             "Cache-Control": "no-store"})


@router.delete("/me/data")
def data_delete(signal: Literal["listening", "output", "device", "motion", "location", "weather", "search", "time",
                                "health", "all"] = Query(...),
                confirm: bool = False, me: Device = Depends(device), conn: Conn = Depends(db)):
    if signal == "all" and not confirm:
        raise HTTPException(400, "signal=all erases your whole listening history; repeat with confirm=true")
    return userdata.erase(conn, me.user_id, signal)


# ---------- admin ----------


def _invite_url(c: AppContext, code: str) -> str:
    return f"{c.cfg.tunnel_url or c.cfg.lan_url}/join/{code}"


@admin.get("/invites")
def list_invites(conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return [{"code": r["code"], "url": _invite_url(c, r["code"]), "note": r["note"], "createdAt": r["created_at"],
             "expiresAt": r["expires_at"], "createdBy": r["created_by_name"], "usedBy": r["used_by_name"],
             "usedAt": r["used_at"]} for r in users.list_invites(conn)]


class InviteBody(BaseModel):
    note: str | None = Field(default=None, max_length=200)
    expiresInDays: int = Field(default=7, ge=1, le=90)


@admin.post("/invites")
def create_invite(body: InviteBody | None = None, me: Device = Depends(device), conn: Conn = Depends(db),
                  c: AppContext = Depends(ctx)):
    body = body or InviteBody()
    inv = users.create_invite(conn, me.user_id, body.note, body.expiresInDays)
    return {**inv, "url": _invite_url(c, inv["code"])}


@admin.delete("/invites/{code}", status_code=204)
def delete_invite(code: str, conn: Conn = Depends(db)):
    if not users.delete_invite(conn, code):
        raise HTTPException(404, "no such invite")
    return Response(status_code=204)


@admin.get("/users")
def list_users(conn: Conn = Depends(db)):
    return users.list_all(conn)


class UserPatch(BaseModel):
    disabled: bool | None = None
    isAdmin: bool | None = None


@admin.patch("/users/{user_id}")
def patch_user(user_id: str, body: UserPatch, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    try:
        revoked = users.update(conn, user_id, disabled=body.disabled, is_admin=body.isAdmin)
    except users.UserError as e:
        _err(e)
    for d in revoked:
        if c.hub:
            c.hub.disconnect_device(d)
    return users.admin_view(conn, user_id)


class ResetBody(BaseModel):
    new: str = Field(max_length=1024)


@admin.post("/users/{user_id}/reset-password", status_code=204)
def reset_password(user_id: str, body: ResetBody, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    if users.get(conn, user_id) is None:
        raise HTTPException(404, "no such user")
    try:
        users.set_password(conn, user_id, body.new)
    except users.UserError as e:
        _err(e)
    for d in auth.revoke_user_devices(conn, user_id):
        if c.hub:
            c.hub.disconnect_device(d)
    return Response(status_code=204)
