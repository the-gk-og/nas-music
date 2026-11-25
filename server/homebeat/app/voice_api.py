"""Voice control (Home Assistant Assist): POST /voice/command with a voice token, and the settings endpoints
(/me/voice/...) a signed-in device uses to create/revoke voice tokens and name devices for voice."""
from __future__ import annotations

import asyncio
import logging
import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, StringConstraints

from homebeat.app.context import AppContext, ctx, db, device
from homebeat.auth import Device
from homebeat.connect import RemoteConn
from homebeat.core import open_db
from homebeat.voice import service, store

log = logging.getLogger(__name__)
router = APIRouter()   # device-token routes (settings)
public = APIRouter()   # voice-token route
Conn = sqlite3.Connection


def voice_caller(request: Request) -> store.VoiceCaller:
    header = request.headers.get("authorization", "")
    scheme, _, tok = header.partition(" ")
    tok = tok.strip() if scheme.lower() == "bearer" else ""
    if not tok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing voice token", headers={"WWW-Authenticate": "Bearer"})
    conn = open_db(request.app.state.ctx.cfg)
    try:
        caller = store.caller_for_token(conn, tok)
    finally:
        conn.close()
    if caller is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked voice token",
                            headers={"WWW-Authenticate": "Bearer"})
    return caller


Short = Annotated[str, StringConstraints(max_length=200)]


class VoiceCommand(BaseModel):
    text: str | None = Field(default=None, max_length=500)
    intent: str | None = Field(default=None, max_length=60)
    slots: dict[str, Any] | None = None
    area: str | None = Field(default=None, max_length=100)
    areaDevices: list[Short] | None = Field(default=None, max_length=30)
    language: str | None = Field(default=None, max_length=20)
    trackId: str | None = Field(default=None, max_length=100)
    dryRun: bool = False


def _notify_playlist(c: AppContext, conn: sqlite3.Connection, p: service.Plan, dry: bool) -> None:
    """A voice "add this to Gym" changed a playlist: tell everyone who can see it (as the playlists API does)."""
    if p.intent != "add_to_playlist" or not p.ok or dry or not p.item or not c.hub:
        return
    from homebeat.app import playlists as pl

    pid = p.item["id"]
    row = conn.execute("SELECT rev FROM playlists WHERE id = ?", (pid,)).fetchone()
    if row:
        c.hub.notify_playlist(pl.audience(conn, pid), pid, row[0])


async def _command(c: AppContext, uid: str, body: VoiceCommand, requester: RemoteConn,
                   caller: dict | None = None) -> service.Plan:
    if not (body.text or body.intent):
        raise HTTPException(400, "text or intent required")
    if body.slots and len(body.slots) > 20:
        raise HTTPException(400, "too many slots")
    hub = c.hub
    await hub.ensure_speakers()
    live = await hub.devices_now(uid)
    state = await hub.state_now(uid)
    payload = body.model_dump()
    if payload.get("slots"):
        payload["slots"] = {str(k)[:40]: v if isinstance(v, (int, float)) else str(v)[:200]
                            for k, v in payload["slots"].items()}

    def work():
        conn = open_db(c.cfg)
        try:
            p = service.plan(conn, c.cfg, uid, payload, live, state, caller)
            _notify_playlist(c, conn, p, body.dryRun)
            return p
        finally:
            conn.close()

    p = await asyncio.to_thread(work)
    if not body.dryRun:
        p = await service.run(hub, p, uid, requester)
    return p


@public.post("/voice/command")
async def voice_command(body: VoiceCommand, request: Request, caller: store.VoiceCaller = Depends(voice_caller)):
    c: AppContext = request.app.state.ctx
    p = await _command(c, caller.user_id, body, RemoteConn(f"voice:{caller.token_id}", caller.token_name))
    log.info("voice %s (%s): %r -> %s ok=%s", caller.username, caller.token_name, body.text or body.intent, p.intent,
             p.ok)
    return p.response()


@router.post("/me/voice/command")
async def app_voice_command(body: VoiceCommand, request: Request, me: Device = Depends(device)):
    """The app's own mic button (speech recognised on the phone): the same commands, with this device as
    "here". Answers `local` when the app should do it itself (play the returned queue, pause, skip...)."""
    c: AppContext = request.app.state.ctx
    caller = {"id": me.id, "name": me.name, "kind": me.kind}
    p = await _command(c, me.user_id, body, RemoteConn(f"voice-app:{me.id}", f"{me.name} (voice)"), caller)
    log.info("app voice %s (%s): %r -> %s ok=%s local=%s", me.username, me.name, body.text or body.intent, p.intent,
             p.ok, p.local)
    return p.response()


# ---------- settings ----------


class NewToken(BaseModel):
    name: str = Field(default="Home Assistant", max_length=100)


@router.get("/me/voice/tokens")
def list_tokens(me: Device = Depends(device), conn: Conn = Depends(db)):
    return {"tokens": store.list_tokens(conn, me.user_id)}


@router.post("/me/voice/tokens", status_code=201)
def create_token(body: NewToken | None = None, me: Device = Depends(device), conn: Conn = Depends(db)):
    try:
        info, token = store.create_token(conn, me.user_id, (body or NewToken()).name)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {**info, "token": token}


@router.delete("/me/voice/tokens/{token_id}", status_code=204)
def revoke_token(token_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    if not store.revoke_token(conn, me.user_id, token_id):
        raise HTTPException(404, "no such voice token")


def _device_out(d: dict) -> dict:
    out = {k: d.get(k) for k in ("id", "name", "kind", "online", "aliases", "isDefault")}
    if d.get("castType"):
        out["castType"] = d["castType"]
    return out


@router.get("/me/voice/devices")
async def voice_devices(me: Device = Depends(device), c: AppContext = Depends(ctx)):
    await c.hub.ensure_speakers()
    live = await c.hub.devices_now(me.user_id)

    def work():
        conn = open_db(c.cfg)
        try:
            return service.all_devices(conn, me.user_id, live)
        finally:
            conn.close()

    devs = await asyncio.to_thread(work)
    return {"devices": [_device_out(d) for d in devs if d.get("canPlay", True)]}


class DevicePrefs(BaseModel):
    aliases: list[Annotated[str, StringConstraints(max_length=60)]] | None = Field(default=None, max_length=10)
    isDefault: bool | None = None


@router.put("/me/voice/devices/{device_id:path}")
def put_voice_device(device_id: str, body: DevicePrefs, me: Device = Depends(device), conn: Conn = Depends(db)):
    if not device_id or len(device_id) > 300:
        raise HTTPException(400, "bad device id")
    return {"id": device_id, **store.set_device_prefs(conn, me.user_id, device_id, body.aliases, body.isDefault)}
