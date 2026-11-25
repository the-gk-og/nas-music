"""v3 phase-1 endpoints under /api/v1: /home, /foryou, /pins, /search, /catalog/*, /toget, /radio."""
from __future__ import annotations

import logging
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from homebeat import catalog
from homebeat.app import collections as coll
from homebeat.app import discover, suggestions, toget
from homebeat.app import playlists as playlists_mod
from homebeat.app.context import AppContext, ctx, db, device
from homebeat.auth import Device
from homebeat.library import art, lyrics_index, queries

log = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(device)])
Conn = sqlite3.Connection
LYRICS_IN_ALL = 5  # lyric matches shown alongside the other groups in an ordinary search
PinType = Literal["album", "artist", "playlist", "mix", "queue", "smart_list", "smart_playlist", "genre", "folder"]


# ---------- Home / For You / pins ----------


@router.get("/home")
def home(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return discover.home(conn, c.cfg, me.user_id, me.id, c.hub)


@router.get("/foryou")
def foryou(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return discover.foryou(conn, c.cfg, me.user_id)


@router.get("/pins")
def pins(me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return discover.list_pins(conn, c.data_dir, me.user_id)


class PinBody(BaseModel):
    type: PinType
    id: str = Field(min_length=1, max_length=4096)
    position: int | None = Field(default=None, ge=0)


@router.post("/pins")
def add_pin(body: PinBody, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    try:
        discover.pin(conn, c.data_dir, me.user_id, body.type, body.id, body.position)
    except discover.NotFound as e:
        raise HTTPException(404, str(e)) from e
    return discover.list_pins(conn, c.data_dir, me.user_id)


@router.delete("/pins/{type}/{item_id:path}", status_code=204)
def delete_pin(type: PinType, item_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    discover.unpin(conn, me.user_id, type, item_id)
    return Response(status_code=204)


# ---------- search ----------


def _top(q: str, res: dict, cat: list[dict], data_dir) -> dict | None:
    """Exact (normalised) name match: artist > album > track > playlist; else the first library hit;
    else the first catalogue hit."""
    n = catalog.norm(q)
    for a in res["artists"]:
        if catalog.norm(a["name"]) == n:
            return {"type": "artist", "id": a["id"], "title": a["name"], "subtitle": "Artist", "artUrl": None,
                    "artist": a}
    for a in res["albums"]:
        if catalog.norm(a["title"]) == n:
            return {"type": "album", "id": a["id"], "title": a["title"], "subtitle": a["artist"],
                    "artUrl": f"art/{a['id']}" if a["hasArt"] else None, "album": a}
    for t in res["tracks"]:
        if catalog.norm(t["title"]) == n:
            return discover.track_item(t)
    for p in res["playlists"]:
        if catalog.norm(p["name"]) == n:
            return {"type": "playlist", "id": p["id"], "title": p["name"], "subtitle": "Playlist", "artUrl": None,
                    "playlist": p}
    if res["tracks"]:
        return discover.track_item(res["tracks"][0])
    if res["albums"]:
        a = res["albums"][0]
        return {"type": "album", "id": a["id"], "title": a["title"], "subtitle": a["artist"],
                "artUrl": f"art/{a['id']}" if a["hasArt"] else None, "album": a}
    if cat:
        i = cat[0]
        return {"type": "catalog", "id": i["id"], "title": i["title"], "subtitle": i.get("artist"),
                "artUrl": i["artUrl"], "catalog": i}
    return None


@router.get("/search")
def search(q: str = Query(..., min_length=1, max_length=200),
           scope: Literal["all", "library", "catalog", "lyrics"] = "all",
           limit: int = Query(20, ge=1, le=100), me: Device = Depends(device), conn: Conn = Depends(db),
           c: AppContext = Depends(ctx)):
    uid = me.user_id
    if scope == "lyrics":  # the "Lyrics" filter: only songs whose words match
        return {"query": q, "top": None, "tracks": [], "albums": [], "artists": [], "playlists": [], "queues": [],
                "catalog": [], "catalogError": None, "lyrics": lyrics_index.search(conn, uid, q, limit)}
    empty = {"tracks": [], "albums": [], "artists": []}
    lib = queries.search(conn, uid, q, limit, c.data_dir) if scope != "catalog" else empty
    words = [w.casefold() for w in q.split()]
    match = lambda name: all(w in name.casefold() for w in words)  # noqa: E731
    playlists = [p for p in playlists_mod.list_playlists(conn, uid) if match(p["name"])][:limit] if scope != "catalog" else []
    queues = [x for x in coll.list_queues(conn, uid) if match(x["name"])][:limit] if scope != "catalog" else []
    cat_items: list[dict] = []
    cat_error = None
    if scope != "library":
        try:
            raw, cat_error = c.catalog.search(q)
        except catalog.CatalogError as e:
            raw, cat_error = [], str(e)
        if raw:
            catalog.store(conn, raw)
            index = catalog.LibraryIndex(conn)
            marks = toget.status_for(conn, uid)
            for i in raw:
                owned = index.owned(i)
                if owned and scope == "all":
                    continue  # "Not in your library" only
                cat_items.append(catalog.api_item(i, owned, marks.get((i["type"], catalog.match_key(
                    i.get("artist"), i["title"])))))
            cat_items = cat_items[:limit * 3]
    res = {**lib, "playlists": playlists, "queues": queues}
    # A few lyric matches under the usual groups ("In lyrics"); scope=lyrics lists them all.
    in_lyrics = lyrics_index.search(conn, uid, q, min(limit, LYRICS_IN_ALL)) if scope != "catalog" else []
    return {"query": q, "top": _top(q, res, cat_items, c.data_dir), **res, "catalog": cat_items,
            "catalogError": "online catalogue unavailable" if cat_error and not cat_items else None,
            "lyrics": in_lyrics}


# ---------- catalogue samples + covers ----------


def _catalog_item(conn: Conn, c: AppContext, uid: str, catalog_id: str) -> dict:
    if catalog_id.startswith("sg:"):  # a suggestion's own catalogue row: only its owner may use it
        if not conn.execute("SELECT 1 FROM suggestions WHERE id = ? AND user_id = ?",
                            (catalog_id[3:], uid)).fetchone():
            raise HTTPException(404, "not found")
    item = c.catalog.lookup(conn, catalog_id)
    if item is None:
        raise HTTPException(404, "not found")
    return item


@router.get("/catalog/{catalog_id}/preview")
async def catalog_preview(catalog_id: str, request: Request, me: Device = Depends(device), conn: Conn = Depends(db),
                          c: AppContext = Depends(ctx)):
    item = _catalog_item(conn, c, me.user_id, catalog_id)
    return await suggestions.stream_preview(c, item.get("preview_ref"), request.headers.get("range"),
                                            f"catalog {catalog_id}")


@router.get("/catalog/{catalog_id}/cover")
def catalog_cover(catalog_id: str, size: int = Query(512, ge=16, le=4096), me: Device = Depends(device),
                  conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    item = _catalog_item(conn, c, me.user_id, catalog_id)
    out = suggestions.cached_cover(c, art.art_dir(c.data_dir) / "catalog", catalog_id, item.get("cover_url"), size)
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=604800"})


# ---------- To get ----------


def _toget(fn, *a, **k):
    try:
        return fn(*a, **k)
    except toget.NotFound as e:
        raise HTTPException(404, str(e)) from e


@router.get("/toget")
def list_toget(status: Literal["want", "got", "dismissed", "all"] = "want", me: Device = Depends(device),
               conn: Conn = Depends(db)):
    return toget.list_items(conn, me.user_id, status)


class ToGetBody(BaseModel):
    catalogId: str | None = Field(default=None, max_length=200)
    type: Literal["album", "track"] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=300)
    artist: str | None = Field(default=None, min_length=1, max_length=300)
    year: int | None = None
    mbid: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=1000)


@router.post("/toget")
def add_toget(body: ToGetBody, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    if body.catalogId:
        item = _catalog_item(conn, c, me.user_id, body.catalogId)
        if item["type"] == "artist":
            raise HTTPException(422, "add an album or a song, not an artist")
        return toget.add(conn, me.user_id, type=item["type"], title=item["title"], artist=item.get("artist") or "",
                         year=item.get("year"), mbid=item.get("mbid"), catalog_id=item["id"], note=body.note,
                         source="search")
    if not (body.type and body.title and body.artist):
        raise HTTPException(422, "give catalogId, or type + title + artist")
    return toget.add(conn, me.user_id, type=body.type, title=body.title, artist=body.artist, year=body.year,
                     mbid=body.mbid, note=body.note, source="manual")


class ToGetPatch(BaseModel):
    status: Literal["want", "got", "dismissed"] | None = None
    note: str | None = Field(default=None, max_length=1000)


@router.patch("/toget/{item_id}")
def patch_toget(item_id: str, body: ToGetPatch, me: Device = Depends(device), conn: Conn = Depends(db)):
    return _toget(toget.patch, conn, me.user_id, item_id, body.status, body.note,
                  clear_note="note" in body.model_fields_set and body.note is None)


@router.delete("/toget/{item_id}", status_code=204)
def delete_toget(item_id: str, me: Device = Depends(device), conn: Conn = Depends(db)):
    _toget(toget.delete, conn, me.user_id, item_id)
    return Response(status_code=204)


# ---------- radio ----------


class Seed(BaseModel):
    type: Literal["random", "track", "artist", "album", "playlist", "smart_playlist", "moment", "vibe"]
    id: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=200)            # vibe
    constraints: list[dict] | None = Field(default=None, max_length=30)  # vibe chips (from /radio/vibe/parse)


class RadioContext(BaseModel):
    """Hints for the smart shuffle; all optional (the server knows the time and the device kind)."""
    localHour: int | None = Field(default=None, ge=0, le=23)
    weekday: int | None = Field(default=None, ge=1, le=7)
    deviceKind: Literal["phone", "car", "speaker", "desktop", "watch", "web", "tv"] | None = None
    output: str | None = Field(default=None, max_length=100)
    motion: str | None = Field(default=None, max_length=20)
    workout: str | None = Field(default=None, max_length=40)


class RadioBody(BaseModel):
    seed: Seed
    context: RadioContext | None = None
    automix: bool = False  # v7: order the batch for DJ flow (tempo, key, energy)


def _radio_errors(fn, *a):
    try:
        return fn(*a)
    except NotImplementedError as e:
        raise HTTPException(503, "radio isn't available yet (AI engine update pending)") from e
    except ValueError as e:  # malformed seed / steer (the AI's signal)
        raise HTTPException(400, f"bad request: {e}") from e
    except (discover.NotFound, KeyError) as e:
        raise HTTPException(404, "nothing to build this radio from yet") from e
    except coll.NotFound as e:
        raise HTTPException(404, str(e)) from e


@router.post("/radio")
def start_radio(body: RadioBody, me: Device = Depends(device), conn: Conn = Depends(db),
                c: AppContext = Depends(ctx)):
    playing = (c.hub.snapshot(me.user_id) or {}).get("queueId") if c.hub else None
    q = _radio_errors(discover.start_radio, conn, c.cfg, me.user_id, body.seed.model_dump(), playing,
                      body.context.model_dump(exclude_none=True) if body.context else None, me.kind, body.automix)
    if c.hub:
        c.hub.notify_queue(me.user_id, q["id"])
    return q


class MoreBody(BaseModel):
    count: int = Field(default=25, ge=1, le=100)
    automix: bool = False  # v7: order the new batch for DJ flow, continuing from the queue's last track


@router.post("/radio/{queue_id}/more")
def more_radio(queue_id: str, body: MoreBody | None = None, me: Device = Depends(device), conn: Conn = Depends(db),
               c: AppContext = Depends(ctx)):
    b = body or MoreBody()
    q = _radio_errors(discover.more_radio, conn, c.cfg, me.user_id, queue_id, b.count, b.automix)
    if c.hub:
        c.hub.notify_queue(me.user_id, queue_id)
    return q


# ---------- AI radio (v6): vibe parsing, station state, steering ----------


class VibeBody(BaseModel):
    text: str = Field(max_length=200)


@router.post("/radio/vibe/parse")
def parse_vibe(body: VibeBody, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _radio_errors(discover.parse_vibe, conn, c.cfg, me.user_id, body.text)


@router.get("/radio/{queue_id}/station")
def radio_station(queue_id: str, me: Device = Depends(device), conn: Conn = Depends(db), c: AppContext = Depends(ctx)):
    return _radio_errors(discover.radio_station, conn, c.cfg, me.user_id, queue_id)


class SteerBody(BaseModel):
    action: Literal["more_like", "less_like", "more_energy", "calmer", "more_variety", "more_familiar",
                    "never_here", "remove", "reset"]
    trackId: str | None = Field(default=None, max_length=200)
    scope: Literal["track", "artist"] = "track"
    refId: str | None = Field(default=None, max_length=200)
    currentIndex: int | None = Field(default=None, ge=0)
    refresh: bool = True
    context: dict | None = None


@router.post("/radio/{queue_id}/steer")
def steer_radio(queue_id: str, body: SteerBody, me: Device = Depends(device), conn: Conn = Depends(db),
                c: AppContext = Depends(ctx)):
    res = _radio_errors(discover.steer_radio, conn, c.cfg, me.user_id, queue_id, body.model_dump())
    if c.hub:
        c.hub.notify_queue(me.user_id, queue_id)
    return res
