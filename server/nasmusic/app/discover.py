"""Home and For You shelf feeds, pins, and radio (docs/v3-ux.md §3.1, §3.2, §5).

Every shelf is {id, kind, title, subtitle, layout, items}; every item is a generic ShelfItem
{type, id, title, subtitle, artUrl, + one detail object}. artUrl is relative to the API base."""
from __future__ import annotations

import logging
import sqlite3

from nasmusic.app import ai_adapter, blends, insights, mixes, playlists, suggestions, toget, userdata
from nasmusic.app import collections as coll
from nasmusic.config import Config
from nasmusic.db import family
from nasmusic.core import ai_config
from nasmusic.library import art, queries

HOME_KINDS = ("start_radio", "devices", "recently_played", "continue_listening", "pinned")
FORYOU_KINDS = ("mixes", "radio", "because_you_played", "release_radar", "suggestions", "to_get", "blends", "wrapped")
PIN_TYPES = ("album", "artist", "playlist", "mix", "queue", "smart_list", "genre", "folder")
SMART_TITLES = {"favourites": "Favourites", "recently_added": "Recently added", "recently_played": "Recently played",
                "most_played": "Most played", "not_played": "Not played"}
CONTINUE_DAYS = 30
log = logging.getLogger(__name__)


class NotFound(Exception):
    pass


def shelf(id_: str, kind: str, title: str, items: list[dict], layout: str = "row",
          subtitle: str | None = None) -> dict:
    return {"id": id_, "kind": kind, "title": title, "subtitle": subtitle, "layout": layout, "items": items}


# ---------- item builders ----------


def _album_rows(conn: sqlite3.Connection, ids: list[str], uid: str | None = None) -> dict[str, sqlite3.Row]:
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
        return {}
    q = queries.album_select(queries.fam(conn, uid)) + f" WHERE al.id IN ({','.join('?' * len(ids))})"
    return {r["id"]: r for r in conn.execute(q, ids)}


def _art_for_album(data_dir, row: sqlite3.Row | None) -> str | None:
    if row is None:
        return None
    return f"art/{row['id']}" if art.has_art(data_dir, row["art_path"], row["id"]) else None


def _art_for_tracks(conn: sqlite3.Connection, data_dir, track_ids: list[str]) -> str | None:
    """Art of the first of these tracks whose album has art."""
    for tid in track_ids[:10]:
        r = conn.execute("SELECT al.id, al.art_path FROM tracks t JOIN albums al ON al.id = t.album_id "
                         "WHERE t.id = ?", (tid,)).fetchone()
        if r and art.has_art(data_dir, r["art_path"], r["id"]):
            return f"art/{r['id']}"
    return None


def album_item(data_dir, row: sqlite3.Row) -> dict:
    a = queries.album_dict(row, data_dir)
    return {"type": "album", "id": a["id"], "title": a["title"], "subtitle": a["artist"],
            "artUrl": f"art/{a['id']}" if a["hasArt"] else None, "album": a}


def track_item(t: dict) -> dict:
    return {"type": "track", "id": t["id"], "title": t["title"], "subtitle": t["artist"],
            "artUrl": f"art/{t['albumId']}", "track": t}


def _artist_item(conn: sqlite3.Connection, data_dir, artist_id: str, uid: str | None = None) -> dict | None:
    r = conn.execute(queries.artist_select(queries.fam(conn, uid)) + " AND ar.id = ? GROUP BY ar.id", (artist_id,)).fetchone()
    if r is None:
        return None
    a = queries.artist_dict(r)
    rows = conn.execute("SELECT id, art_path FROM albums WHERE artist_id = ? ORDER BY year DESC", (artist_id,))
    url = next((f"art/{x['id']}" for x in rows if art.has_art(data_dir, x["art_path"], x["id"])), None)
    return {"type": "artist", "id": a["id"], "title": a["name"],
            "subtitle": f"{a['albumCount']} album{'s' if a['albumCount'] != 1 else ''}", "artUrl": url, "artist": a}


def _playlist_item(conn: sqlite3.Connection, data_dir, uid: str, pid: str) -> dict | None:
    p = playlists.summary_for(conn, uid, pid)
    if p is None:
        return None
    ids = playlists._playlist_ids(conn, pid)
    return {"type": "playlist", "id": pid, "title": p["name"], "subtitle": f"{p['trackCount']} songs",
            "artUrl": _art_for_tracks(conn, data_dir, ids), "playlist": p}


def _mix_item(conn: sqlite3.Connection, data_dir, uid: str, mix_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM mixes WHERE id = ? AND user_id = ?", (mix_id, uid)).fetchone()
    if row is None:
        return None
    m = mixes.mix_dict(conn, uid, row)
    ids = [x["track"]["id"] for x in m["tracks"]]
    names = list(dict.fromkeys(x["track"]["artist"] for x in m["tracks"]))[:3]
    return {"type": "mix", "id": m["id"], "title": m["title"], "subtitle": ", ".join(names) or None,
            "artUrl": _art_for_tracks(conn, data_dir, ids),
            "mix": {"kind": m["kind"], "trackCount": len(ids), "durationS": m["durationS"]}}


def _queue_item(conn: sqlite3.Connection, data_dir, uid: str, qid: str, progress: bool = False) -> dict | None:
    try:
        row = coll.queue_row(conn, uid, qid)
    except coll.NotFound:
        return None
    q = coll._queue_summary(conn, row)
    ids = coll._queue_ids(conn, qid)
    cur = ids[q["currentIndex"]:] + ids[:q["currentIndex"]]
    item = {"type": "queue", "id": qid, "title": q["name"], "subtitle": f"{q['trackCount']} songs",
            "artUrl": _art_for_tracks(conn, data_dir, cur), "queue": q}
    if q["radio"]:
        item["seed"] = q["radio"]
    if progress:
        item["progress"] = {"index": q["currentIndex"], "count": q["trackCount"], "positionMs": q["positionMs"],
                            "queueId": qid}
        item["subtitle"] = f"{q['currentIndex'] + 1} of {q['trackCount']}"
    return item


def resolve(conn: sqlite3.Connection, data_dir, uid: str, type_: str, id_: str) -> dict | None:
    """Any pinnable / recent item as a ShelfItem, or None if it doesn't exist (for this user)."""
    if type_ == "album":
        row = _album_rows(conn, [id_], uid).get(id_)
        return album_item(data_dir, row) if row else None
    if type_ == "artist":
        return _artist_item(conn, data_dir, id_, uid)
    if type_ == "playlist":
        return _playlist_item(conn, data_dir, uid, id_)
    if type_ == "mix":
        return _mix_item(conn, data_dir, uid, id_)
    if type_ == "queue":
        return _queue_item(conn, data_dir, uid, id_)
    if type_ == "smart_list":
        return ({"type": "smart_list", "id": id_, "title": SMART_TITLES[id_], "subtitle": None, "artUrl": None}
                if id_ in SMART_TITLES else None)
    if type_ == "genre":
        g = next((g for g in queries.genres(conn, uid) if g["name"].casefold() == id_.casefold()), None)
        return ({"type": "genre", "id": g["name"], "title": g["name"], "subtitle": f"{g['trackCount']} songs",
                 "artUrl": None} if g else None)
    if type_ == "folder":
        f = queries.folders(conn, uid, id_)
        if f is None:
            return None
        name = id_.rsplit("/", 1)[-1] or "Music"
        return {"type": "folder", "id": f["path"], "title": name, "subtitle": f["parent"] or None,
                "artUrl": _art_for_tracks(conn, data_dir, [t["id"] for t in f["tracks"]])}
    return None


# ---------- pins ----------


def list_pins(conn: sqlite3.Connection, data_dir, uid: str) -> list[dict]:
    out = []
    for r in conn.execute("SELECT type, item_id FROM pins WHERE user_id = ? ORDER BY position, created_at",
                          (uid,)).fetchall():
        item = resolve(conn, data_dir, uid, r["type"], r["item_id"])
        if item:  # pins to things that are gone (deleted playlist, removed album) are skipped
            out.append(item)
    return out


def pin(conn: sqlite3.Connection, data_dir, uid: str, type_: str, id_: str, position: int | None) -> None:
    item = resolve(conn, data_dir, uid, type_, id_)
    if item is None:
        raise NotFound("no such item")
    id_ = item["id"]  # canonical (e.g. genre spelling)
    from nasmusic.core import now_iso

    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = [(r["type"], r["item_id"]) for r in conn.execute(
            "SELECT type, item_id FROM pins WHERE user_id = ? ORDER BY position, created_at", (uid,))]
        cur = [p for p in cur if p != (type_, id_)]
        at = len(cur) if position is None else max(0, min(position, len(cur)))
        cur.insert(at, (type_, id_))
        created = {(r["type"], r["item_id"]): r["created_at"] for r in conn.execute(
            "SELECT type, item_id, created_at FROM pins WHERE user_id = ?", (uid,))}
        conn.execute("DELETE FROM pins WHERE user_id = ?", (uid,))
        now = now_iso()
        conn.executemany("INSERT INTO pins (user_id, type, item_id, position, created_at) VALUES (?, ?, ?, ?, ?)",
                         [(uid, t, i, n, created.get((t, i), now)) for n, (t, i) in enumerate(cur)])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def unpin(conn: sqlite3.Connection, uid: str, type_: str, id_: str) -> None:
    conn.execute("DELETE FROM pins WHERE user_id = ? AND type = ? AND item_id = ? COLLATE NOCASE", (uid, type_, id_))


# ---------- Home ----------


def _ordered(shelves: list[dict], settings: dict, prefix: str) -> list[dict]:
    order = settings.get(f"{prefix}.shelves")
    hidden = set(settings.get(f"{prefix}.hidden") or [])
    shelves = [s for s in shelves if s["id"] not in hidden and s["kind"] not in hidden]
    if isinstance(order, list):
        rank = {k: n for n, k in enumerate(order)}
        shelves.sort(key=lambda s: rank.get(s["id"], rank.get(s["kind"], len(rank))))
    return shelves


def _devices_shelf(conn: sqlite3.Connection, uid: str, me_device: str | None, hub) -> list[dict]:
    if hub is None:
        return []
    state = hub.snapshot(uid) or {}
    items = []
    for d in hub.devices_for(uid):
        if d["id"] == me_device or not d["online"]:
            continue
        now_playing = None
        if d["isActive"] and state.get("trackId"):
            now_playing = queries.track(conn, uid, state["trackId"])
        playing = bool(d["isActive"] and state.get("playing"))
        items.append({"type": "device", "id": d["id"], "title": d["name"],
                      "subtitle": (f"Playing {now_playing['title']}" if now_playing and playing
                                   else (now_playing["title"] if now_playing else d["kind"].capitalize())),
                      "artUrl": f"art/{now_playing['albumId']}" if now_playing else None,
                      "device": {"kind": d["kind"], "canPlay": d["canPlay"], "isActive": d["isActive"],
                                 "playing": playing, "nowPlaying": now_playing}})
    return items


def _recently_played(conn: sqlite3.Connection, data_dir, uid: str, limit: int = 8) -> list[dict]:
    """Albums, playlists, mixes, radios and other started queues, most recent first, de-duplicated."""
    stamps: dict[tuple[str, str], str] = {}

    def seen(key: tuple[str, str], at: str) -> None:
        if at and (key not in stamps or at > stamps[key]):
            stamps[key] = at

    for r in conn.execute("SELECT id, source_type, source_id, radio_seed, updated_at FROM queues WHERE user_id = ? "
                          "AND source_type IS NOT NULL ORDER BY updated_at DESC LIMIT 40", (uid,)):
        if r["radio_seed"] or r["source_type"] in ("radio", "search", "other") or not r["source_id"]:
            seen(("queue", r["id"]), r["updated_at"])
        elif r["source_type"] in ("album", "artist", "playlist", "mix", "genre", "folder", "smart_list"):
            seen((r["source_type"], r["source_id"]), r["updated_at"])
    for r in conn.execute(
        "SELECT e.at, e.mix_id, t.album_id FROM events e JOIN tracks t ON t.id = e.track_id "
        "WHERE e.user_id = ? AND e.type = 'play' ORDER BY e.at DESC LIMIT 300", (uid,)
    ):
        if r["mix_id"]:
            seen(("mix", r["mix_id"]), r["at"])
        else:
            seen(("album", r["album_id"]), r["at"])
    out = []
    for (t, i), at in sorted(stamps.items(), key=lambda kv: kv[1], reverse=True):
        item = resolve(conn, data_dir, uid, t, i)
        if item:
            item["playedAt"] = at
            out.append(item)
        if len(out) >= limit:
            break
    return out


def _continue_listening(conn: sqlite3.Connection, data_dir, uid: str, playing_queue: str | None) -> list[dict]:
    """Started but unfinished non-radio queues from the last 30 days (where you left off)."""
    from datetime import UTC, datetime, timedelta

    from nasmusic.core import iso

    since = iso(datetime.now(UTC) - timedelta(days=CONTINUE_DAYS))
    out = []
    for r in conn.execute(
        "SELECT q.id, q.current_index, q.position_ms, (SELECT COUNT(*) FROM queue_tracks x WHERE x.queue_id = q.id) n "
        "FROM queues q WHERE q.user_id = ? AND q.radio_seed IS NULL AND q.updated_at >= ? "
        "ORDER BY q.updated_at DESC LIMIT 30", (uid, since)
    ):
        started = r["current_index"] > 0 or r["position_ms"] > 0
        if not started or r["n"] < 2 or r["current_index"] >= r["n"] - 1 or r["id"] == playing_queue:
            continue
        item = _queue_item(conn, data_dir, uid, r["id"], progress=True)
        if item:
            out.append(item)
        if len(out) >= 10:
            break
    return out


def home(conn: sqlite3.Connection, cfg: Config, uid: str, me_device: str | None, hub) -> dict:
    data_dir = cfg.data_dir
    playing_queue = ((hub.snapshot(uid) or {}).get("queueId") if hub else None)
    shelves = [
        shelf("start_radio", "start_radio", "Start radio",
              [{"type": "radio", "id": "random", "title": "Start a random radio", "subtitle": "Picked from your taste",
                "artUrl": None, "seed": {"type": "random", "id": None, "title": "Random"}}], layout="button"),
        shelf("devices", "devices", "Your devices", _devices_shelf(conn, uid, me_device, hub)),
        shelf("recently_played", "recently_played", "Recently played", _recently_played(conn, data_dir, uid),
              layout="grid2"),
        shelf("continue_listening", "continue_listening", "Continue listening",
              _continue_listening(conn, data_dir, uid, playing_queue)),
        shelf("pinned", "pinned", "Pinned", list_pins(conn, data_dir, uid)),
    ]
    shelves = [s for s in shelves if s["items"]]
    return {"shelves": _ordered(shelves, userdata.get_settings(conn, uid), "home")}


# ---------- For You ----------


def _radio_seeds(conn: sqlite3.Connection, data_dir, uid: str) -> list[dict]:
    """"<Artist> radio" for the most-played / favourite artists, "<Song> radio" for recent favourites."""
    items = []
    artists = conn.execute(
        "SELECT t.artist_id, ar.name, SUM(COALESCE(ut.play_count, 0)) + 5 * SUM(ut.starred_at IS NOT NULL) score, "
        "MIN(t.album_id) album FROM user_tracks ut JOIN tracks t ON t.id = ut.track_id "
        "JOIN artists ar ON ar.id = t.artist_id WHERE ut.user_id = ? AND t.missing = 0 AND ut.blocked = 0 "
        "GROUP BY t.artist_id HAVING score > 0 ORDER BY score DESC LIMIT 4", (uid,)).fetchall()
    rows = _album_rows(conn, [a["album"] for a in artists], uid)
    for a in artists:
        items.append({"type": "radio", "id": f"artist:{a['artist_id']}", "title": f"{a['name']} radio",
                      "subtitle": "Artist radio", "artUrl": _art_for_album(data_dir, rows.get(a["album"])),
                      "seed": {"type": "artist", "id": a["artist_id"], "title": a["name"]}})
    for t in queries.smart_list(conn, uid, "favourites", 0, 2):
        items.append({"type": "radio", "id": f"track:{t['id']}", "title": f"{t['title']} radio",
                      "subtitle": t["artist"], "artUrl": f"art/{t['albumId']}",
                      "seed": {"type": "track", "id": t["id"], "title": t["title"]}})
    return items


def _because_you_played(conn: sqlite3.Connection, cfg: Config, uid: str) -> list[dict]:
    """1-3 "Because you played <track>" shelves from the AI (seed is always a track)."""
    out = []
    for n, s in enumerate(ai_adapter.because_you_played(conn, ai_config(cfg), uid, 3)):
        ids = [i for i in s.get("track_ids", []) if isinstance(i, str)]
        tracks = queries.tracks_by_ids(conn, uid, ids)
        items = [track_item(tracks[i]) for i in ids if i in tracks]
        if items:
            seed = s.get("seed_track_id")
            out.append(shelf(f"byp:track:{seed or n}", "because_you_played",
                             s.get("title") or "Because you played this", items,
                             subtitle=None))
    return out


def _optional(fn, *a) -> list[dict]:
    """Phase-2 shelves never break For You: an engine without them (or any failure) just hides the shelf."""
    try:
        return fn(*a)
    except NotImplementedError:
        return []
    except Exception:
        log.exception("For You shelf %s failed", getattr(fn, "__name__", fn))
        return []


def _blends_shelf(conn: sqlite3.Connection, cfg: Config, uid: str) -> list[dict]:
    """Your blends and invites, plus "Start a blend" when there's someone to blend with."""
    items = blends.shelf_items(conn, cfg.data_dir, uid)
    if blends.candidates(conn, uid):
        items.append({"type": "blend", "id": "new", "title": "Start a blend",
                      "subtitle": "Mix your taste with someone else's", "artUrl": None, "blend": {"status": "new"}})
    if not items:
        return []
    return [shelf("blends", "blends", "Blends", items, subtitle="Shared playlists with people on this server")]


def foryou(conn: sqlite3.Connection, cfg: Config, uid: str) -> dict:
    data_dir = cfg.data_dir
    mix_items = [i for i in (_mix_item(conn, data_dir, uid, m["id"]) for m in mixes.today(conn, uid)) if i]
    sug = [s for s in suggestions.list_all(conn, uid) if s["status"] in ("new", "want")]
    sug_items = [{"type": "suggestion", "id": s["id"], "title": s["album"], "subtitle": s["artist"],
                  "artUrl": f"suggestions/{s['id']}/cover" if s["hasCover"] else None, "suggestion": s}
                 for s in sug[:20]]
    tg = toget.list_items(conn, uid, "want")[:20]
    tg_items = [{"type": "toget", "id": t["id"], "title": t["title"], "subtitle": t["artist"],
                 "artUrl": t["artUrl"], "toget": t} for t in tg]
    shelves = [shelf("mixes", "mixes", "Your mixes", mix_items),
               shelf("radio", "radio", "Radio", _radio_seeds(conn, data_dir, uid))]
    shelves += _because_you_played(conn, cfg, uid)
    shelves += _optional(insights.release_radar_shelves, conn, cfg, uid)
    shelves += [shelf("suggestions", "suggestions", "Suggestions for you", sug_items),
                shelf("to_get", "to_get", "To get", tg_items)]
    shelves += _optional(_blends_shelf, conn, cfg, uid)
    shelves += _optional(insights.wrapped_shelf, conn, cfg, uid)
    shelves = [s for s in shelves if s["items"]]
    return {"shelves": _ordered(shelves, userdata.get_settings(conn, uid), "foryou")}


# ---------- radio ----------


def seed_title(conn: sqlite3.Connection, uid: str, seed: dict) -> str:
    """Validate the seed and return its display title. Raises NotFound."""
    t, i = seed.get("type"), seed.get("id")
    if t == "random":
        return "Random"
    if not i:
        raise NotFound("seed id required")
    if t == "track":
        r = queries.track(conn, uid, i)
        title = r and r["title"]
    elif t == "album":
        r = conn.execute("SELECT title FROM albums WHERE id = ?", (i,)).fetchone()
        title = r and r[0]
    elif t == "artist":
        r = conn.execute("SELECT name FROM artists WHERE id = ?", (i,)).fetchone()
        title = r and r[0]
    elif t == "playlist":
        p = playlists.summary_for(conn, uid, i)
        title = p and p["name"]
    else:
        title = None
    if not title:
        raise NotFound("seed not found")
    return title


def start_radio(conn: sqlite3.Connection, cfg: Config, uid: str, seed: dict, protect: str | None) -> dict:
    title = seed_title(conn, uid, seed)  # validates the seed (404) and gives the seed a display title
    seed = {"type": seed["type"], "id": seed.get("id"), "title": title}
    radio_id, ids = ai_adapter.radio_start(conn, ai_config(cfg), uid, seed)
    ids = [i for i in dict.fromkeys(ids) if queries.track_file(conn, i) is not None]
    ids = family.filter_ids(conn, uid, ids)  # family mode: never radio an explicit track
    if not ids:
        raise NotFound("nothing to play for this radio yet")
    row = conn.execute("SELECT title FROM radio_sessions WHERE id = ?", (radio_id,)).fetchone()
    name = (row[0] if row and row[0] else ("Random radio" if seed["type"] == "random" else f"{title} radio"))
    return coll.create_queue(conn, uid, name, ids, 0, protect=protect, replace_same_name=True,
                             source=("radio", seed.get("id")), radio_seed=seed, radio_id=radio_id)


def more_radio(conn: sqlite3.Connection, cfg: Config, uid: str, qid: str, n: int) -> dict:
    try:
        row = coll.queue_row(conn, uid, qid)
    except coll.NotFound as e:
        raise NotFound("no such queue") from e
    if not row["radio_seed"] or not row["radio_id"]:
        raise NotFound("not a radio queue")
    have = coll.queue_track_ids(conn, uid, qid)
    new = ai_adapter.radio_more(conn, ai_config(cfg), uid, row["radio_id"], have, n)
    fresh = [i for i in dict.fromkeys(new) if i not in set(have) and queries.track_file(conn, i) is not None]
    fresh = family.filter_ids(conn, uid, fresh)
    if not fresh:  # the library ran out: the queue stays as it is
        return coll.get_queue(conn, uid, qid)
    return coll.edit_queue(conn, uid, qid, add=fresh, position="end")
