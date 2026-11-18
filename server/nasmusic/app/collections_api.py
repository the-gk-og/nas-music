"""Playlists and queues under /api/v1 (per user). Queue changes are pushed to the user's other devices
over Connect as `{"type": "queues", "queueId": ...}`."""
from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from nasmusic.app import collections as coll
from nasmusic.app.context import AppContext, ctx, db, device
from nasmusic.auth import Device

router = APIRouter(dependencies=[Depends(device)])
Conn = sqlite3.Connection


def _run(fn, *a, **k):
    try:
        return fn(*a, **k)
    except coll.NotFound as e:
        raise HTTPException(404, str(e)) from e
    except coll.BadRequest as e:
        raise HTTPException(422, str(e)) from e


class Name(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class NewPlaylist(Name):
    trackIds: list[str] = Field(default_factory=list, max_length=10_000)


class AddTracks(BaseModel):
    trackIds: list[str] = Field(min_length=1, max_length=10_000)
    position: int | None = Field(default=None, ge=0)


class Move(BaseModel):
    from_: int = Field(alias="from", ge=0)
    to: int = Field(ge=0)


# ---------- playlists ----------


@router.get("/playlists")
def playlists(me: Device = Depends(device), conn: Conn = Depends(db)):
    return coll.list_playlists(conn, me.user_id)


@router.post("/playlists")
def create_playlist(body: NewPlaylist, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _run(coll.create_playlist, conn, me.user_id, body.name, body.trackIds)


@router.get("/playlists/{pid}")
def get_playlist(pid: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _run(coll.get_playlist, conn, me.user_id, pid)


class PlaylistPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)


@router.patch("/playlists/{pid}")
def patch_playlist(pid: str, body: PlaylistPatch, me: Device = Depends(device), conn: Conn = Depends(db)):
    if body.name is None:
        return _run(coll.get_playlist, conn, me.user_id, pid)
    return _run(coll.rename_playlist, conn, me.user_id, pid, body.name)


@router.delete("/playlists/{pid}", status_code=204)
def delete_playlist(pid: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    _run(coll.delete_playlist, conn, me.user_id, pid)
    return Response(status_code=204)


@router.post("/playlists/{pid}/tracks")
def add_playlist_tracks(pid: str, body: AddTracks, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _run(coll.edit_playlist, conn, me.user_id, pid, add=body.trackIds, position=body.position)


@router.delete("/playlists/{pid}/tracks/{position}")
def remove_playlist_track(pid: str, position: int, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _run(coll.edit_playlist, conn, me.user_id, pid, remove=position)


@router.post("/playlists/{pid}/move")
def move_playlist_track(pid: str, body: Move, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _run(coll.edit_playlist, conn, me.user_id, pid, move=(body.from_, body.to))


# ---------- queues ----------


def _notify(c: AppContext, uid: str, qid: str) -> None:
    if c.hub:
        c.hub.notify_queue(uid, qid)


class NewQueue(Name):
    trackIds: list[str] = Field(max_length=10_000)
    startIndex: int = Field(default=0, ge=0)


@router.get("/queues")
def queues(me: Device = Depends(device), conn: Conn = Depends(db)):
    return coll.list_queues(conn, me.user_id)


@router.post("/queues")
def create_queue(body: NewQueue, me: Device = Depends(device), conn: Conn = Depends(db),
                 c: AppContext = Depends(ctx)):
    playing = (c.hub.snapshot(me.user_id) or {}).get("queueId") if c.hub else None
    q = _run(coll.create_queue, conn, me.user_id, body.name, body.trackIds, body.startIndex, protect=playing)
    _notify(c, me.user_id, q["id"])
    return q


@router.get("/queues/{qid}")
def get_queue(qid: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _run(coll.get_queue, conn, me.user_id, qid)


class QueuePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    currentIndex: int | None = Field(default=None, ge=0)
    positionMs: int | None = Field(default=None, ge=0)


@router.patch("/queues/{qid}")
def patch_queue(qid: str, body: QueuePatch, me: Device = Depends(device), conn: Conn = Depends(db),
                c: AppContext = Depends(ctx)):
    q = _run(coll.patch_queue, conn, me.user_id, qid, body.name, body.currentIndex, body.positionMs)
    if body.name is not None or body.currentIndex is not None:  # position-only saves are too chatty to push
        _notify(c, me.user_id, qid)
    return q


@router.delete("/queues/{qid}", status_code=204)
def delete_queue(qid: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _run(coll.delete_queue, conn, me.user_id, qid)
    _notify(c, me.user_id, qid)
    return Response(status_code=204)


class AddQueueTracks(BaseModel):
    trackIds: list[str] = Field(min_length=1, max_length=10_000)
    position: int | Literal["next", "end"] = "end"


@router.post("/queues/{qid}/tracks")
def add_queue_tracks(qid: str, body: AddQueueTracks, me: Device = Depends(device), conn: Conn = Depends(db),
                     c: AppContext = Depends(ctx)):
    if isinstance(body.position, int) and body.position < 0:
        raise HTTPException(422, "position must be >= 0")
    q = _run(coll.edit_queue, conn, me.user_id, qid, add=body.trackIds, position=body.position)
    _notify(c, me.user_id, qid)
    return q


@router.delete("/queues/{qid}/tracks/{position}")
def remove_queue_track(qid: str, position: int, me: Device = Depends(device), conn: Conn = Depends(db),
                       c: AppContext = Depends(ctx)):
    q = _run(coll.edit_queue, conn, me.user_id, qid, remove=position)
    _notify(c, me.user_id, qid)
    return q


@router.post("/queues/{qid}/move")
def move_queue_track(qid: str, body: Move, me: Device = Depends(device), conn: Conn = Depends(db),
                     c: AppContext = Depends(ctx)):
    q = _run(coll.edit_queue, conn, me.user_id, qid, move=(body.from_, body.to))
    _notify(c, me.user_id, qid)
    return q


@router.post("/queues/{qid}/save-as-playlist")
def save_queue(qid: str, body: Name, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _run(coll.queue_to_playlist, conn, me.user_id, qid, body.name)
