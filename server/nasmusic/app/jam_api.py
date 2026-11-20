"""Jam (listen together) under /api/v1 (docs/jam.md, api/openapi.yaml tag "jam").

- /jams...          the host (normal session auth): start, settings, link, approve/reject, remove, kick, end
- /jam/join/{code}  public: preview a join link, join with a display name (or as yourself when signed in)
- /jam/...          guests (header `X-Jam-Token`, from joining): view, search, request songs, vote, react, leave
- /jam/ws           live updates (guest token, or the host's session token + ?jam=<id>)

Guest tokens never work anywhere else (they aren't device tokens), and nothing here streams audio.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from nasmusic import auth, jam
from nasmusic.app import collections as coll
from nasmusic.app.context import AppContext, client_ip, ctx, db, device, request_token
from nasmusic.auth import Device
from nasmusic.auth.users import RateLimiter
from nasmusic.connect import state_from_json
from nasmusic.core import open_db
from nasmusic.jam.hub import JamHub, Sock
from nasmusic.library import art, queries

host = APIRouter(dependencies=[Depends(device)])
public = APIRouter()
Conn = sqlite3.Connection
TOKEN_HEADER = "x-jam-token"


class Limits:
    """Per-key sliding windows. Keys: client IP for joining, guest id for everything a guest does."""

    def __init__(self):
        self.join = RateLimiter(10, 60.0)
        self.preview = RateLimiter(60, 60.0)
        self.search = RateLimiter(20, 60.0)
        self.add = RateLimiter(10, 60.0)
        self.vote = RateLimiter(60, 60.0)
        self.react = RateLimiter(10, 30.0)
        self.art = RateLimiter(300, 60.0)
        self.host_link = RateLimiter(20, 60.0)

    def clear(self) -> None:
        for v in vars(self).values():
            v.clear()


def make_jam_hub(c: AppContext) -> JamHub:
    def view(jam_id: str, guest_id: str | None) -> dict | None:
        conn = open_db(c.cfg)
        try:
            row = jam.jam_by_id(conn, jam_id)
            if row is None or row["ended_at"] or row["expires_at"] <= jam.now_iso():
                return None
            if guest_id is None:
                v = jam.Viewer(row)
            else:
                g = conn.execute("SELECT * FROM jam_guests WHERE id = ? AND left_at IS NULL", (guest_id,)).fetchone()
                if g is None:
                    return None
                v = jam.Viewer(row, g)
            return jam.snapshot(conn, v, _state(c, conn, row["host_user_id"]), *_bases(c, None))
        finally:
            conn.close()

    h = JamHub(view)
    h.limits = Limits()  # type: ignore[attr-defined]
    if c.hub is not None:
        c.hub.listeners.append(h.on_host_event)
    return h


def _hub(c: AppContext) -> JamHub:
    h = getattr(c, "jam_hub", None)
    if h is None:
        h = c.jam_hub = make_jam_hub(c)
    return h


def _limit(c: AppContext, name: str, key: str) -> None:
    lim: RateLimiter = getattr(_hub(c).limits, name)  # type: ignore[attr-defined]
    if lim.blocked(key):
        raise HTTPException(429, "slow down a little", headers={"Retry-After": str(int(lim.window))})
    lim.fail(key)


def _run(fn, *a, **k):
    try:
        return fn(*a, **k)
    except jam.JamError as e:
        raise HTTPException(e.status, str(e)) from e
    except coll.NotFound as e:
        raise HTTPException(404, str(e)) from e
    except (coll.BadRequest, coll.Forbidden, coll.Conflict) as e:
        raise HTTPException(409, str(e)) from e


def _state(c: AppContext, conn: Conn, uid: str) -> dict | None:
    """The host's Connect PlaybackState: live from the hub, else the last persisted one (not playing)."""
    st = c.hub.snapshot(uid) if c.hub is not None else None
    if st:
        return st
    row = conn.execute("SELECT state FROM playback_state WHERE user_id = ?", (uid,)).fetchone()
    st = state_from_json(row[0]) if row else None
    if st:
        st["playing"] = False
    return st


def _bases(c: AppContext, request: Request | None) -> tuple[str | None, str | None]:
    """(public base URL, LAN base URL) for join links."""
    pub = c.cfg.tunnel_url
    if not pub and request is not None:
        pub = str(request.base_url).rstrip("/")
    return pub, c.cfg.lan_url


def _notify(c: AppContext, jam_id: str, host_uid: str | None = None, qid: str | None = None) -> None:
    if host_uid and qid and c.hub is not None:
        c.hub.notify_queue(host_uid, qid)   # host devices refetch; Jam guests hear it via the listener
    _hub(c).changed(jam_id)


def _host_snapshot(c: AppContext, conn: Conn, row, request: Request) -> dict:
    return jam.snapshot(conn, jam.Viewer(row), _state(c, conn, row["host_user_id"]), *_bases(c, request))


# ---------- host ----------


class JamSettings(BaseModel):
    approval: bool | None = None
    allowPlayNext: bool | None = None
    maxPerGuest: int | None = Field(default=None, ge=1, le=jam.MAX_PER_GUEST_CAP)


class StartJam(JamSettings):
    linkTtlMinutes: int | None = Field(default=None, ge=5, le=jam.LINK_TTL_MAX_MIN)


@host.post("/jams")
def start_jam(request: Request, body: StartJam | None = None, me: Device = Depends(device), conn: Conn = Depends(db),
              c: AppContext = Depends(ctx)):
    body = body or StartJam()
    st = _state(c, conn, me.user_id) or {}
    settings = body.model_dump(exclude_unset=True, exclude={"linkTtlMinutes"})
    row = _run(jam.start, conn, me.user_id, st.get("queueId"), settings, body.linkTtlMinutes)
    _hub(c)
    return _host_snapshot(c, conn, row, request)


@host.get("/jams/current")
def current_jam(request: Request, me: Device = Depends(device), conn: Conn = Depends(db),
                c: AppContext = Depends(ctx)):
    row = jam.active_for_host(conn, me.user_id)
    if row is None:
        return Response(status_code=204)
    return _host_snapshot(c, conn, row, request)


@host.get("/jams/{jid}")
def get_jam(jid: str, request: Request, me: Device = Depends(device), conn: Conn = Depends(db),
            c: AppContext = Depends(ctx)):
    row = _run(jam.host_jam, conn, me.user_id, jid)
    return _host_snapshot(c, conn, row, request)


@host.patch("/jams/{jid}")
def patch_jam(jid: str, body: JamSettings, request: Request, me: Device = Depends(device), conn: Conn = Depends(db),
              c: AppContext = Depends(ctx)):
    row = _run(jam.host_jam, conn, me.user_id, jid)
    row = _run(jam.update_settings, conn, row, body.model_dump(exclude_unset=True))
    _notify(c, jid)
    return _host_snapshot(c, conn, row, request)


@host.delete("/jams/{jid}", status_code=204)
def end_jam(jid: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    row = _run(jam.host_jam, conn, me.user_id, jid)
    jam.end(conn, row)
    _hub(c).end(jid)


class LinkBody(BaseModel):
    ttlMinutes: int | None = Field(default=None, ge=5, le=jam.LINK_TTL_MAX_MIN)
    close: bool = False


@host.post("/jams/{jid}/link")
def regenerate_link(jid: str, request: Request, body: LinkBody | None = None, me: Device = Depends(device),
                    conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _limit(c, "host_link", me.user_id)
    body = body or LinkBody()
    row = _run(jam.host_jam, conn, me.user_id, jid)
    row = _run(jam.new_link, conn, row, body.ttlMinutes, body.close)
    _notify(c, jid)
    return _host_snapshot(c, conn, row, request)


@host.get("/jams/{jid}/qr")
def join_qr(jid: str, request: Request, me: Device = Depends(device), conn: Conn = Depends(db),
            c: AppContext = Depends(ctx)):
    """QR codes (PNG data URIs) for the public and LAN join links."""
    from nasmusic.auth import qr

    row = _run(jam.host_jam, conn, me.user_id, jid)
    urls = jam.join_urls(row, *_bases(c, request))
    return {"joinUrl": urls["joinUrl"], "lanJoinUrl": urls["lanJoinUrl"],
            "qr": qr.png_data_uri(urls["joinUrl"]) if urls["joinUrl"] else None,
            "lanQr": qr.png_data_uri(urls["lanJoinUrl"]) if urls["lanJoinUrl"] else None}


class Decision(BaseModel):
    approve: bool


@host.post("/jams/{jid}/requests/{rid}")
def decide(jid: str, rid: str, body: Decision, request: Request, me: Device = Depends(device),
           conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    row = _run(jam.host_jam, conn, me.user_id, jid)
    res = _run(jam.decide, conn, row, rid, body.approve, _state(c, conn, me.user_id))
    _notify(c, jid, me.user_id, res["queueId"] if body.approve else None)
    return _host_snapshot(c, conn, row, request)


class HostAdd(BaseModel):
    trackId: str = Field(min_length=1, max_length=100)
    playNext: bool = False


@host.post("/jams/{jid}/queue")
def host_add(jid: str, body: HostAdd, request: Request, me: Device = Depends(device), conn: Conn = Depends(db),
             c: AppContext = Depends(ctx)):
    row = _run(jam.host_jam, conn, me.user_id, jid)
    res = _run(jam.request_add, conn, jam.Viewer(row), body.trackId, body.playNext, _state(c, conn, me.user_id))
    _notify(c, jid, me.user_id, res["queueId"])
    return _host_snapshot(c, conn, row, request)


@host.delete("/jams/{jid}/queue/{position}")
def remove_entry(jid: str, position: int, request: Request, trackId: str | None = Query(None, max_length=100),
                 me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    row = _run(jam.host_jam, conn, me.user_id, jid)
    qid = _run(jam.remove_entry, conn, row, position, _state(c, conn, me.user_id), trackId)
    _notify(c, jid, me.user_id, qid)
    return _host_snapshot(c, conn, row, request)


@host.delete("/jams/{jid}/guests/{gid}")
def kick_guest(jid: str, gid: str, request: Request, me: Device = Depends(device), conn: Conn = Depends(db),
               c: AppContext = Depends(ctx)):
    row = _run(jam.host_jam, conn, me.user_id, jid)
    _run(jam.kick, conn, row, gid)
    _hub(c).kick(jid, gid)
    return _host_snapshot(c, conn, row, request)


# ---------- joining (public) ----------


@public.get("/jam/join/{code}")
def preview(code: str, request: Request, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _limit(c, "preview", client_ip(request))
    out = _run(jam.preview, conn, code)
    me = _signed_in(request, conn)
    out["signedInAs"] = me.username if me else None
    return out


def _signed_in(request: Request, conn: Conn) -> Device | None:
    tok = request_token(request)
    return auth.device_for_token(conn, tok) if tok and not tok.startswith(jam.TOKEN_PREFIX) else None


class JoinBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    asMember: bool = False


@public.post("/jam/join/{code}")
def join(code: str, body: JoinBody, request: Request, conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _limit(c, "join", client_ip(request))
    uid = None
    if body.asMember:
        me = _signed_in(request, conn)
        if me is None:
            raise HTTPException(401, "sign in first to join as yourself")
        uid = me.user_id
    token, v = _run(jam.join, conn, code, body.name, uid)
    _notify(c, v.jam["id"])
    snap = jam.snapshot(conn, v, _state(c, conn, v.jam["host_user_id"]))
    return {"token": token, **snap}


# ---------- guests ----------


def guest(request: Request, conn: Conn = Depends(db)) -> jam.Viewer:
    tok = request.headers.get(TOKEN_HEADER)
    if tok is None and request.url.path.startswith("/api/v1/jam/art/"):
        tok = request.query_params.get("token")   # <img> can't send headers
    v = jam.guest_for_token(conn, tok)
    if v is None:
        raise HTTPException(401, "not in a Jam (it may have ended, or the host removed you)")
    return v


@public.get("/jam/me")
def guest_view(v: jam.Viewer = Depends(guest), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return jam.snapshot(conn, v, _state(c, conn, v.jam["host_user_id"]))


@public.delete("/jam/me", status_code=204)
def guest_leave(v: jam.Viewer = Depends(guest), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    jam.leave(conn, v)
    _hub(c).kick(v.jam["id"], v.guest["id"])


@public.get("/jam/search")
def guest_search(q: str = Query(..., min_length=1, max_length=100), v: jam.Viewer = Depends(guest),
                 conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _limit(c, "search", v.guest["id"])
    return {"tracks": jam.search(conn, q, v.jam["host_user_id"])}


class GuestAdd(BaseModel):
    trackId: str = Field(min_length=1, max_length=100)
    playNext: bool = False


@public.post("/jam/requests")
def guest_add(body: GuestAdd, v: jam.Viewer = Depends(guest), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _limit(c, "add", v.guest["id"])
    uid = v.jam["host_user_id"]
    res = _run(jam.request_add, conn, v, body.trackId, body.playNext, _state(c, conn, uid))
    _notify(c, v.jam["id"], uid, res["queueId"] if res["queued"] else None)
    return {"request": res["request"], "queued": res["queued"]}


@public.delete("/jam/requests/{rid}", status_code=204)
def guest_withdraw(rid: str, v: jam.Viewer = Depends(guest), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _run(jam.withdraw, conn, v, rid)
    _notify(c, v.jam["id"])


class VoteBody(BaseModel):
    trackId: str = Field(min_length=1, max_length=100)
    value: int = Field(ge=-1, le=1)


@public.post("/jam/votes", status_code=204)
def guest_vote(body: VoteBody, v: jam.Viewer = Depends(guest), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _limit(c, "vote", v.guest["id"])
    _run(jam.vote, conn, v, body.trackId, body.value)
    _notify(c, v.jam["id"])


class ReactBody(BaseModel):
    emoji: str = Field(min_length=1, max_length=16)


@public.post("/jam/reactions", status_code=204)
def guest_react(body: ReactBody, v: jam.Viewer = Depends(guest), c: AppContext = Depends(ctx)):
    if body.emoji not in jam.REACTIONS:
        raise HTTPException(422, "unsupported reaction")
    _limit(c, "react", v.guest["id"])
    _hub(c).react(v.jam["id"], body.emoji, v.guest["name"])


@public.get("/jam/art/{album_id}")
def guest_art(album_id: str, size: int = Query(256, ge=16, le=1024), v: jam.Viewer = Depends(guest),
              conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _limit(c, "art", v.guest["id"])
    row = queries.album_art_info(conn, album_id)
    out = art.resized(c.data_dir, c.cfg.library_root, album_id, row["art_path"], size) if row else None
    if out is None:
        raise HTTPException(404, "no art")
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


# ---------- live updates ----------


@public.websocket("/jam/ws")
async def jam_ws(ws: WebSocket):
    import asyncio

    c: AppContext = ws.app.state.ctx
    h = _hub(c)
    tok = ws.query_params.get("token")
    if not tok:
        header = ws.headers.get("authorization", "")
        scheme, _, t = header.partition(" ")
        tok = t.strip() if scheme.lower() == "bearer" and t.strip() else ws.cookies.get("nm_session")
    jam_id = ws.query_params.get("jam")

    def resolve() -> Sock | None:
        conn = open_db(c.cfg)
        try:
            if tok and tok.startswith(jam.TOKEN_PREFIX):
                v = jam.guest_for_token(conn, tok)
                if v is None:
                    return None
                exp = min(v.guest["expires_at"], v.jam["expires_at"])
                return Sock(ws, v.jam["id"], v.jam["host_user_id"], v.guest["id"], exp)
            dev = auth.device_for_token(conn, tok) if tok else None
            if dev is None or not jam_id:
                return None
            try:
                row = jam.host_jam(conn, dev.user_id, jam_id)
            except jam.JamError:
                return None
            return Sock(ws, row["id"], dev.user_id, None, row["expires_at"])
        finally:
            conn.close()

    sock = await asyncio.to_thread(resolve)
    await ws.accept()
    if sock is None:
        await ws.close(code=4401, reason="invalid or expired Jam token")
        return
    await h.handle(sock)
