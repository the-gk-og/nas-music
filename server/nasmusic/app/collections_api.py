"""Playlists and queues under /api/v1 (per user). Queue changes are pushed to the user's other devices
over Connect as `{"type": "queues", "queueId": ...}`."""
from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from nasmusic.app import collections as coll
from nasmusic.app import playlists as pl
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
    except coll.Forbidden as e:
        raise HTTPException(403, str(e)) from e
    except coll.Conflict as e:
        raise HTTPException(409, str(e)) from e


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


# ---------- playlists (owner, editors, viewers; see nasmusic.app.playlists) ----------


def _pl_notify(c: AppContext, conn: Conn, pid: str, before: set[str]) -> None:
    """Push {"type": "playlist"} to everyone who had or has access; ones who lost it get deleted=True."""
    if not c.hub:
        return
    row = conn.execute("SELECT rev FROM playlists WHERE id = ?", (pid,)).fetchone()
    now = pl.audience(conn, pid) if row else set()
    if now:
        c.hub.notify_playlist(now, pid, row[0])
    if before - now:
        c.hub.notify_playlist(before - now, pid, row[0] if row else None, deleted=True)


def _pl_change(c: AppContext, conn: Conn, pid: str, fn, *a, **k):
    before = pl.audience(conn, pid)
    out = _run(fn, conn, *a, **k)
    _pl_notify(c, conn, pid, before)
    return out


class AddEntries(AddTracks):
    """Insert after `afterEntryId` ("" = at the top), else at index `position`, else append."""
    afterEntryId: str | None = Field(default=None, max_length=64)


class MoveEntry(BaseModel):
    afterEntryId: str | None = Field(default=None, max_length=64)   # null = to the top


class RemoveEntries(BaseModel):
    entryIds: list[str] = Field(min_length=1, max_length=10_000)


class NewMember(BaseModel):
    userId: str | None = Field(default=None, max_length=64)
    username: str | None = Field(default=None, max_length=200)
    role: Literal["editor", "viewer"] = "editor"


class MemberPatch(BaseModel):
    role: Literal["editor", "viewer"]


@router.get("/playlists")
def playlists(me: Device = Depends(device), conn: Conn = Depends(db)):
    return pl.list_playlists(conn, me.user_id)


@router.post("/playlists")
def create_playlist(body: NewPlaylist, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _run(pl.create_playlist, conn, me.user_id, body.name, body.trackIds)


@router.get("/playlists/{pid}")
def get_playlist(pid: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _run(pl.get_playlist, conn, me.user_id, pid)


class PlaylistPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)


@router.patch("/playlists/{pid}")
def patch_playlist(pid: str, body: PlaylistPatch, me: Device = Depends(device), conn: Conn = Depends(db),
                   c: AppContext = Depends(ctx)):
    if body.name is None:
        return _run(pl.get_playlist, conn, me.user_id, pid)
    return _pl_change(c, conn, pid, pl.rename_playlist, me.user_id, pid, body.name)


@router.delete("/playlists/{pid}", status_code=204)
def delete_playlist(pid: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _pl_change(c, conn, pid, pl.delete_playlist, me.user_id, pid)
    return Response(status_code=204)


@router.post("/playlists/{pid}/tracks")
def add_playlist_tracks(pid: str, body: AddEntries, me: Device = Depends(device), conn: Conn = Depends(db),
                        c: AppContext = Depends(ctx)):
    after = body.afterEntryId
    return _pl_change(c, conn, pid, pl.add_tracks, me.user_id, pid, body.trackIds, position=body.position,
                      after_entry=after or None, at_top=after == "")


@router.delete("/playlists/{pid}/tracks/{position}")
def remove_playlist_track(pid: str, position: int, me: Device = Depends(device), conn: Conn = Depends(db),
                          c: AppContext = Depends(ctx)):
    """Legacy (index-based); prefer DELETE /playlists/{pid}/entries/{entryId}."""
    return _pl_change(c, conn, pid, pl.edit_playlist, me.user_id, pid, remove=position)


@router.post("/playlists/{pid}/move")
def move_playlist_track(pid: str, body: Move, me: Device = Depends(device), conn: Conn = Depends(db),
                        c: AppContext = Depends(ctx)):
    """Legacy (index-based); prefer POST /playlists/{pid}/entries/{entryId}/move."""
    return _pl_change(c, conn, pid, pl.edit_playlist, me.user_id, pid, move=(body.from_, body.to))


@router.delete("/playlists/{pid}/entries/{entry_id}")
def remove_playlist_entry(pid: str, entry_id: str, me: Device = Depends(device), conn: Conn = Depends(db),
                          c: AppContext = Depends(ctx)):
    return _pl_change(c, conn, pid, pl.remove_entries, me.user_id, pid, [entry_id])


@router.post("/playlists/{pid}/entries/remove")
def remove_playlist_entries(pid: str, body: RemoveEntries, me: Device = Depends(device), conn: Conn = Depends(db),
                            c: AppContext = Depends(ctx)):
    return _pl_change(c, conn, pid, pl.remove_entries, me.user_id, pid, body.entryIds)


@router.post("/playlists/{pid}/entries/{entry_id}/move")
def move_playlist_entry(pid: str, entry_id: str, body: MoveEntry, me: Device = Depends(device),
                        conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _pl_change(c, conn, pid, pl.move_entry, me.user_id, pid, entry_id, body.afterEntryId or None)


@router.get("/playlists/{pid}/members")
def playlist_members(pid: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _run(pl.members, conn, me.user_id, pid)


@router.post("/playlists/{pid}/members")
def add_playlist_member(pid: str, body: NewMember, me: Device = Depends(device), conn: Conn = Depends(db),
                        c: AppContext = Depends(ctx)):
    if not body.userId and not body.username:
        raise HTTPException(422, "userId or username required")
    return _pl_change(c, conn, pid, pl.add_member, me.user_id, pid, body.role, user_id=body.userId,
                      username=body.username)


@router.patch("/playlists/{pid}/members/{user_id}")
def patch_playlist_member(pid: str, user_id: str, body: MemberPatch, me: Device = Depends(device),
                          conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    _run(pl.members, conn, me.user_id, pid)
    if not conn.execute("SELECT 1 FROM playlist_members WHERE playlist_id = ? AND user_id = ?",
                        (pid, user_id)).fetchone():
        raise HTTPException(404, "not a member")
    return _pl_change(c, conn, pid, pl.add_member, me.user_id, pid, body.role, user_id=user_id)


@router.delete("/playlists/{pid}/members/{user_id}", status_code=204)
def remove_playlist_member(pid: str, user_id: str, me: Device = Depends(device), conn: Conn = Depends(db),
                           c: AppContext = Depends(ctx)):
    """The owner removes a member; a member removes themself (leave)."""
    _pl_change(c, conn, pid, pl.remove_member, me.user_id, pid, user_id)
    return Response(status_code=204)


@router.get("/playlists/{pid}/activity")
def playlist_activity(pid: str, limit: int = Query(100, ge=1, le=500), before: int | None = Query(None, ge=1),
                      me: Device = Depends(device), conn: Conn = Depends(db)):
    return _run(pl.activity, conn, me.user_id, pid, limit, before)


@router.get("/users")
def user_directory(me: Device = Depends(device), conn: Conn = Depends(db)):
    """Other enabled users (id + username), for sharing a playlist."""
    return pl.users_directory(conn, me.user_id)


# ---------- queues ----------


def _notify(c: AppContext, uid: str, qid: str) -> None:
    if c.hub:
        c.hub.notify_queue(uid, qid)


class QueueSource(BaseModel):
    type: Literal["album", "artist", "playlist", "mix", "radio", "genre", "folder", "smart_list", "smart_playlist",
                  "search", "other"]
    id: str | None = Field(default=None, max_length=4096)


class NewQueue(Name):
    trackIds: list[str] = Field(max_length=10_000)
    startIndex: int = Field(default=0, ge=0)
    source: QueueSource | None = None


@router.get("/queues")
def queues(me: Device = Depends(device), conn: Conn = Depends(db)):
    return coll.list_queues(conn, me.user_id)


@router.post("/queues")
def create_queue(body: NewQueue, me: Device = Depends(device), conn: Conn = Depends(db),
                 c: AppContext = Depends(ctx)):
    playing = (c.hub.snapshot(me.user_id) or {}).get("queueId") if c.hub else None
    src = (body.source.type, body.source.id) if body.source else None
    q = _run(coll.create_queue, conn, me.user_id, body.name, body.trackIds, body.startIndex, protect=playing,
             source=src)
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


class ReplaceQueueTracks(BaseModel):
    trackIds: list[str] = Field(max_length=10_000)
    currentIndex: int | None = Field(default=None, ge=0)


@router.put("/queues/{qid}/tracks")
def replace_queue_tracks(qid: str, body: ReplaceQueueTracks, me: Device = Depends(device), conn: Conn = Depends(db),
                         c: AppContext = Depends(ctx)):
    q = _run(coll.replace_queue_tracks, conn, me.user_id, qid, body.trackIds, body.currentIndex)
    _notify(c, me.user_id, qid)
    return q


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
