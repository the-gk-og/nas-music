"""Jam: listen together (docs/jam.md).

A signed-in host starts a Jam; the server hands out a short-lived, revocable join link. Guests open it in any
browser, pick a display name and get a random guest token scoped to that one Jam. With it they can see what's
playing and coming up, search the library (read-only), ask for songs and vote. They never get streams,
downloads, account data or admin access: everything they do ends up as an edit of the HOST's queue, which plays
wherever the host is playing (Connect decides that).

This module is the database side (blocking; call it from a worker thread). homebeat.jam.hub pushes live
updates to the guests' WebSockets; homebeat.app.jam_api is the HTTP surface.

Who added what: `jam_requests` rows with status 'queued' are matched to the host queue's entries by track id,
in the order they were queued. Removing an entry through the Jam marks its request 'removed', so a later add
of the same song isn't credited to the wrong person.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from homebeat.app import collections as coll
from homebeat.db import family
from homebeat.library import queries

JAM_HOURS = 12                      # hard lifetime of a Jam; every guest token dies with it
LINK_TTL_DEFAULT_MIN = 60
LINK_TTL_MAX_MIN = 24 * 60
MAX_GUESTS = 50
MAX_PER_GUEST_CAP = 50
NAME_MAX = 32
UPCOMING_MAX = 100
SEARCH_LIMIT = 30
TOKEN_PREFIX = "jg_"
REACTIONS = ("🔥", "❤️", "😂", "👏", "🕺", "🙌", "😮", "👎")
DEFAULT_SETTINGS = {"approval": False, "allowPlayNext": False, "maxPerGuest": 5}
QUEUE_NAME = "Jam"


class JamError(Exception):
    status = 400


class NotFound(JamError):
    status = 404


class Forbidden(JamError):
    status = 403


class Gone(JamError):
    status = 410


class Conflict(JamError):
    status = 409


def _now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_iso() -> str:
    return iso(_now())


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_code() -> str:
    """12 URL-safe characters (~72 bits): unguessable, short enough for a QR code and to read out."""
    return secrets.token_urlsafe(9)


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def clean_name(raw: object) -> str:
    """A display name: NFC, no control / format characters (bidi overrides), whitespace collapsed, <= 32."""
    if not isinstance(raw, str):
        raise JamError("name is required")
    s = unicodedata.normalize("NFC", raw)
    s = "".join(ch for ch in s if unicodedata.category(ch) not in ("Cc", "Cf", "Co", "Cs"))
    s = " ".join(_CTRL.sub(" ", s).split())[:NAME_MAX].strip()
    if not s:
        raise JamError("name is required")
    return s


def clean_settings(base: dict, patch: dict) -> dict:
    out = {**DEFAULT_SETTINGS, **(base or {})}
    if "approval" in patch and patch["approval"] is not None:
        out["approval"] = bool(patch["approval"])
    if "allowPlayNext" in patch and patch["allowPlayNext"] is not None:
        out["allowPlayNext"] = bool(patch["allowPlayNext"])
    if "maxPerGuest" in patch:
        v = patch["maxPerGuest"]
        if v is None:
            out["maxPerGuest"] = None    # unlimited
        elif isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= MAX_PER_GUEST_CAP:
            out["maxPerGuest"] = v
        else:
            raise JamError(f"maxPerGuest must be 1..{MAX_PER_GUEST_CAP} or null")
    return out


@dataclass
class Viewer:
    """Who is looking at a Jam: the host (guest is None) or a guest / signed-in member."""
    jam: sqlite3.Row
    guest: sqlite3.Row | None = None

    @property
    def is_host(self) -> bool:
        return self.guest is None


# ---------- lookups ----------


def _expire(conn: sqlite3.Connection) -> None:
    """End Jams past their hard lifetime (lazily, on every lookup)."""
    now = now_iso()
    rows = conn.execute("SELECT id FROM jams WHERE ended_at IS NULL AND expires_at <= ?", (now,)).fetchall()
    for r in rows:
        _end(conn, r["id"], now)


def _end(conn: sqlite3.Connection, jid: str, now: str) -> None:
    conn.execute("UPDATE jams SET ended_at = ?, join_code = NULL WHERE id = ? AND ended_at IS NULL", (now, jid))
    conn.execute("UPDATE jam_guests SET left_at = ? WHERE jam_id = ? AND left_at IS NULL", (now, jid))
    conn.execute("UPDATE jam_requests SET status = 'withdrawn', decided_at = ? WHERE jam_id = ? AND status = 'pending'",
                 (now, jid))


def active_for_host(conn: sqlite3.Connection, uid: str) -> sqlite3.Row | None:
    _expire(conn)
    return conn.execute("SELECT * FROM jams WHERE host_user_id = ? AND ended_at IS NULL "
                        "ORDER BY created_at DESC LIMIT 1", (uid,)).fetchone()


def host_jam(conn: sqlite3.Connection, uid: str, jid: str) -> sqlite3.Row:
    _expire(conn)
    row = conn.execute("SELECT * FROM jams WHERE id = ? AND host_user_id = ?", (jid, uid)).fetchone()
    if row is None:
        raise NotFound("no such Jam")
    if row["ended_at"]:
        raise Gone("this Jam has ended")
    return row


def jam_by_id(conn: sqlite3.Connection, jid: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM jams WHERE id = ?", (jid,)).fetchone()


def guest_for_token(conn: sqlite3.Connection, token: str | None) -> Viewer | None:
    """The live guest behind a token, or None (unknown, kicked, left, expired, or the Jam ended)."""
    if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 100:
        return None
    _expire(conn)
    g = conn.execute("SELECT * FROM jam_guests WHERE token_hash = ?", (token_hash(token),)).fetchone()
    now = _now()
    if g is None or g["left_at"] or g["expires_at"] <= iso(now):
        return None
    jam = jam_by_id(conn, g["jam_id"])
    if jam is None or jam["ended_at"]:
        return None
    last = g["last_seen_at"]
    if last is None or last < iso(now - timedelta(seconds=60)):
        conn.execute("UPDATE jam_guests SET last_seen_at = ? WHERE id = ?", (iso(now), g["id"]))
    return Viewer(jam, g)


def settings_of(jam: sqlite3.Row) -> dict:
    try:
        s = json.loads(jam["settings"])
    except (TypeError, ValueError):
        s = {}
    return {**DEFAULT_SETTINGS, **(s if isinstance(s, dict) else {})}


def _username(conn: sqlite3.Connection, uid: str) -> str:
    r = conn.execute("SELECT username FROM users WHERE id = ?", (uid,)).fetchone()
    return r["username"] if r else "Host"


# ---------- host side ----------


def _queue_owned(conn: sqlite3.Connection, uid: str, qid: str | None) -> bool:
    return bool(qid) and conn.execute("SELECT 1 FROM queues WHERE id = ? AND user_id = ?",
                                      (qid, uid)).fetchone() is not None


def start(conn: sqlite3.Connection, uid: str, playing_queue_id: str | None, settings: dict | None = None,
          link_ttl_min: int | None = None) -> sqlite3.Row:
    """Start a Jam (or return the host's running one). Guests add to the queue the host is playing, else to a
    new empty "Jam" queue."""
    existing = active_for_host(conn, uid)
    if existing is not None:
        return existing
    qid = playing_queue_id if _queue_owned(conn, uid, playing_queue_id) else None
    if qid is None:
        qid = coll.create_queue(conn, uid, QUEUE_NAME, [], 0, replace_same_name=True)["id"]
    now = _now()
    ttl = _ttl(link_ttl_min)
    jid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO jams (id, host_user_id, queue_id, join_code, join_expires_at, settings, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (jid, uid, qid, new_code(), iso(min(now + timedelta(minutes=ttl), now + timedelta(hours=JAM_HOURS))),
         json.dumps(clean_settings({}, settings or {})), iso(now), iso(now + timedelta(hours=JAM_HOURS))))
    return jam_by_id(conn, jid)


def _ttl(minutes: int | None) -> int:
    if minutes is None:
        return LINK_TTL_DEFAULT_MIN
    if not isinstance(minutes, int) or not 5 <= minutes <= LINK_TTL_MAX_MIN:
        raise JamError(f"linkTtlMinutes must be 5..{LINK_TTL_MAX_MIN}")
    return minutes


def update_settings(conn: sqlite3.Connection, jam: sqlite3.Row, patch: dict) -> sqlite3.Row:
    s = clean_settings(settings_of(jam), patch)
    conn.execute("UPDATE jams SET settings = ? WHERE id = ?", (json.dumps(s), jam["id"]))
    return jam_by_id(conn, jam["id"])


def new_link(conn: sqlite3.Connection, jam: sqlite3.Row, ttl_min: int | None = None,
             close: bool = False) -> sqlite3.Row:
    """Regenerate the join link (the old one stops working; guests already in stay) or close it."""
    if close:
        conn.execute("UPDATE jams SET join_code = NULL, join_expires_at = NULL WHERE id = ?", (jam["id"],))
    else:
        now = _now()
        exp = min(now + timedelta(minutes=_ttl(ttl_min)), datetime.fromisoformat(jam["expires_at"]))
        conn.execute("UPDATE jams SET join_code = ?, join_expires_at = ? WHERE id = ?", (new_code(), iso(exp), jam["id"]))
    return jam_by_id(conn, jam["id"])


def end(conn: sqlite3.Connection, jam: sqlite3.Row) -> None:
    _end(conn, jam["id"], now_iso())


def kick(conn: sqlite3.Connection, jam: sqlite3.Row, gid: str) -> None:
    now = now_iso()
    if conn.execute("UPDATE jam_guests SET left_at = ?, kicked = 1 WHERE id = ? AND jam_id = ?",
                    (now, gid, jam["id"])).rowcount != 1:
        raise NotFound("no such guest")
    conn.execute("UPDATE jam_requests SET status = 'withdrawn', decided_at = ? WHERE guest_id = ? AND status = 'pending'",
                 (now, gid))


def leave(conn: sqlite3.Connection, v: Viewer) -> None:
    now = now_iso()
    conn.execute("UPDATE jam_guests SET left_at = ? WHERE id = ? AND left_at IS NULL", (now, v.guest["id"]))
    conn.execute("UPDATE jam_requests SET status = 'withdrawn', decided_at = ? WHERE guest_id = ? AND status = 'pending'",
                 (now, v.guest["id"]))


# ---------- the queue ----------


def queue_id(conn: sqlite3.Connection, jam: sqlite3.Row, playing_queue_id: str | None) -> str:
    """The host queue the Jam feeds: the one the host is playing now (the Jam follows it), else the last one;
    a fresh empty "Jam" queue if that was deleted."""
    uid = jam["host_user_id"]
    qid = jam["queue_id"]
    if playing_queue_id and playing_queue_id != qid and _queue_owned(conn, uid, playing_queue_id):
        qid = playing_queue_id
    elif not _queue_owned(conn, uid, qid):
        qid = coll.create_queue(conn, uid, QUEUE_NAME, [], 0, replace_same_name=True)["id"]
    if qid != jam["queue_id"]:
        conn.execute("UPDATE jams SET queue_id = ? WHERE id = ?", (qid, jam["id"]))
    return qid


def _attribution(conn: sqlite3.Connection, jam_id: str, ids: list[str]) -> list[sqlite3.Row | None]:
    """For each queue entry, the 'queued' request it came from (or None = the host's own)."""
    reqs = conn.execute("SELECT r.*, g.name AS guest_name, g.user_id AS guest_user FROM jam_requests r "
                        "LEFT JOIN jam_guests g ON g.id = r.guest_id "
                        "WHERE r.jam_id = ? AND r.status = 'queued' ORDER BY r.decided_at, r.created_at",
                        (jam_id,)).fetchall()
    by_track: dict[str, list[sqlite3.Row]] = {}
    for r in reqs:
        by_track.setdefault(r["track_id"], []).append(r)
    out: list[sqlite3.Row | None] = []
    for tid in ids:
        lst = by_track.get(tid)
        out.append(lst.pop(0) if lst else None)
    return out


def _current_index(conn: sqlite3.Connection, qid: str, state: dict | None) -> int:
    if state and state.get("queueId") == qid and isinstance(state.get("index"), int):
        return state["index"]
    r = conn.execute("SELECT current_index FROM queues WHERE id = ?", (qid,)).fetchone()
    return int(r["current_index"]) if r else 0


def _upcoming(conn: sqlite3.Connection, jam: sqlite3.Row, qid: str, state: dict | None):
    ids = coll.queue_track_ids(conn, jam["host_user_id"], qid)
    attr = _attribution(conn, jam["id"], ids)
    cur = _current_index(conn, qid, state)
    return ids, attr, cur


def _active_count(conn: sqlite3.Connection, jam: sqlite3.Row, gid: str, qid: str, state: dict | None) -> int:
    """A guest's songs still to come: pending requests + their queued entries after the current one."""
    _ids, attr, cur = _upcoming(conn, jam, qid, state)
    queued = sum(1 for i, r in enumerate(attr) if r is not None and r["guest_id"] == gid and i > cur)
    pending = conn.execute("SELECT COUNT(*) FROM jam_requests WHERE jam_id = ? AND guest_id = ? AND status = 'pending'",
                           (jam["id"], gid)).fetchone()[0]
    return queued + pending


def _track_exists(conn: sqlite3.Connection, tid: str) -> bool:
    return conn.execute("SELECT 1 FROM tracks WHERE id = ? AND missing = 0", (tid,)).fetchone() is not None


def request_add(conn: sqlite3.Connection, v: Viewer, track_id: str, play_next: bool, state: dict | None) -> dict:
    """A guest (or the host) asks for a song. Returns {"request": ..., "queued": bool}."""
    jam = v.jam
    s = settings_of(jam)
    if not isinstance(track_id, str) or not _track_exists(conn, track_id):
        raise NotFound("no such track")
    # The host's family mode applies to what guests can add (hidden songs look like they don't exist).
    if not family.allowed(conn, jam["host_user_id"], track_id):
        raise NotFound("no such track")
    if play_next and not v.is_host and not s["allowPlayNext"]:
        raise Forbidden("the host hasn't allowed 'play next'")
    qid = queue_id(conn, jam, (state or {}).get("queueId"))
    ids, _attr, cur = _upcoming(conn, jam, qid, state)
    if track_id in ids[cur + 1:] or (ids and 0 <= cur < len(ids) and ids[cur] == track_id):
        raise Conflict("that song is already playing or coming up")
    if conn.execute("SELECT 1 FROM jam_requests WHERE jam_id = ? AND track_id = ? AND status = 'pending'",
                    (jam["id"], track_id)).fetchone():
        raise Conflict("someone already asked for that song")
    gid = None if v.is_host else v.guest["id"]
    if gid is not None and s["maxPerGuest"] is not None and \
            _active_count(conn, jam, gid, qid, state) >= s["maxPerGuest"]:
        raise Forbidden(f"you already have {s['maxPerGuest']} songs waiting; wait for one to play")
    rid = uuid.uuid4().hex
    now = now_iso()
    pending = gid is not None and s["approval"]
    conn.execute("INSERT INTO jam_requests (id, jam_id, guest_id, track_id, mode, status, created_at, decided_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (rid, jam["id"], gid, track_id, "next" if play_next else "end",
                  "pending" if pending else "queued", now, None if pending else now))
    if not pending:
        try:
            coll.edit_queue(conn, jam["host_user_id"], qid, add=[track_id], position="next" if play_next else "end")
        except Exception:
            conn.execute("DELETE FROM jam_requests WHERE id = ?", (rid,))
            raise
    return {"request": request_dict(conn, rid), "queued": not pending, "queueId": qid}


def decide(conn: sqlite3.Connection, jam: sqlite3.Row, rid: str, approve: bool, state: dict | None) -> dict:
    r = conn.execute("SELECT * FROM jam_requests WHERE id = ? AND jam_id = ?", (rid, jam["id"])).fetchone()
    if r is None:
        raise NotFound("no such request")
    if r["status"] != "pending":
        raise Conflict("that request was already handled")
    now = now_iso()
    qid = queue_id(conn, jam, (state or {}).get("queueId"))
    if approve:
        if not _track_exists(conn, r["track_id"]):
            raise NotFound("that track is gone from the library")
        coll.edit_queue(conn, jam["host_user_id"], qid, add=[r["track_id"]],
                        position="next" if r["mode"] == "next" else "end")
    conn.execute("UPDATE jam_requests SET status = ?, decided_at = ? WHERE id = ?",
                 ("queued" if approve else "rejected", now, rid))
    return {"request": request_dict(conn, rid), "queueId": qid}


def withdraw(conn: sqlite3.Connection, v: Viewer, rid: str) -> None:
    if conn.execute("UPDATE jam_requests SET status = 'withdrawn', decided_at = ? "
                    "WHERE id = ? AND jam_id = ? AND guest_id = ? AND status = 'pending'",
                    (now_iso(), rid, v.jam["id"], v.guest["id"])).rowcount != 1:
        raise NotFound("no such pending request")


def remove_entry(conn: sqlite3.Connection, jam: sqlite3.Row, position: int, state: dict | None,
                 track_id: str | None = None) -> str:
    """Host removes a queue entry. `track_id` (optional) guards against a stale view. Returns the queue id."""
    qid = queue_id(conn, jam, (state or {}).get("queueId"))
    ids = coll.queue_track_ids(conn, jam["host_user_id"], qid)
    if not 0 <= position < len(ids):
        raise NotFound("no such queue entry")
    if track_id is not None and ids[position] != track_id:
        raise Conflict("the queue changed; refresh and try again")
    attr = _attribution(conn, jam["id"], ids)
    coll.edit_queue(conn, jam["host_user_id"], qid, remove=position)
    if attr[position] is not None:
        conn.execute("UPDATE jam_requests SET status = 'removed', decided_at = ? WHERE id = ?",
                     (now_iso(), attr[position]["id"]))
    return qid


def vote(conn: sqlite3.Connection, v: Viewer, track_id: str, value: int) -> None:
    if value not in (-1, 0, 1):
        raise JamError("value must be -1, 0 or 1")
    if not isinstance(track_id, str) or len(track_id) > 100:
        raise JamError("bad trackId")
    if value == 0:
        conn.execute("DELETE FROM jam_votes WHERE jam_id = ? AND guest_id = ? AND track_id = ?",
                     (v.jam["id"], v.guest["id"], track_id))
    else:
        conn.execute("INSERT INTO jam_votes (jam_id, guest_id, track_id, value) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(jam_id, guest_id, track_id) DO UPDATE SET value = excluded.value",
                     (v.jam["id"], v.guest["id"], track_id, value))


# ---------- guests joining ----------


def _by_code(conn: sqlite3.Connection, code: str) -> sqlite3.Row:
    _expire(conn)
    if not isinstance(code, str) or not 4 <= len(code) <= 64:
        raise NotFound("this Jam link isn't valid")
    jam = conn.execute("SELECT * FROM jams WHERE join_code = ?", (code,)).fetchone()
    if jam is None or jam["ended_at"]:
        raise NotFound("this Jam link isn't valid any more")
    if not jam["join_expires_at"] or jam["join_expires_at"] <= now_iso():
        raise Gone("this Jam link has expired; ask the host for a new one")
    return jam


def preview(conn: sqlite3.Connection, code: str) -> dict:
    jam = _by_code(conn, code)
    n = conn.execute("SELECT COUNT(*) FROM jam_guests WHERE jam_id = ? AND left_at IS NULL", (jam["id"],)).fetchone()[0]
    return {"hostName": _username(conn, jam["host_user_id"]), "guestCount": n, "linkExpiresAt": jam["join_expires_at"]}


def join(conn: sqlite3.Connection, code: str, name: object, user_id: str | None = None) -> tuple[str, Viewer]:
    """Returns (guest token, viewer). Signed-in users join as themselves (their username)."""
    jam = _by_code(conn, code)
    if user_id is not None:
        if user_id == jam["host_user_id"]:
            raise Conflict("you're hosting this Jam")
        display = _username(conn, user_id)
    else:
        display = clean_name(name)
    n = conn.execute("SELECT COUNT(*) FROM jam_guests WHERE jam_id = ? AND left_at IS NULL", (jam["id"],)).fetchone()[0]
    if n >= MAX_GUESTS:
        raise Forbidden("this Jam is full")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    gid = uuid.uuid4().hex
    now = now_iso()
    conn.execute("INSERT INTO jam_guests (id, jam_id, user_id, name, token_hash, created_at, expires_at, last_seen_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (gid, jam["id"], user_id, display, token_hash(token), now, jam["expires_at"], now))
    g = conn.execute("SELECT * FROM jam_guests WHERE id = ?", (gid,)).fetchone()
    return token, Viewer(jam, g)


# ---------- views ----------


def track_lite(t: dict) -> dict:
    """What guests may see of a track: no paths, sizes, ratings or other per-user data."""
    return {"id": t["id"], "title": t["title"], "artist": t["artist"], "album": t["album"],
            "albumId": t["albumId"], "durationS": t["durationS"]}


def search(conn: sqlite3.Connection, q: str, host_user_id: str | None = None) -> list[dict]:
    """Tracks only (what a guest can add), every word matching title, artist or album. Read-only. Tracks the
    host's family mode hides are left out."""
    words = [w for w in q[:100].split() if w][:6]
    if not words:
        return []
    parts, params = [], []
    for w in words:
        like = "%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        parts.append("(t.title LIKE ? ESCAPE '\\' OR ar.name LIKE ? ESCAPE '\\' OR al.title LIKE ? ESCAPE '\\')")
        params += [like] * 3
    rows = conn.execute(queries.TRACK_SELECT + " WHERE t.missing = 0 AND " + " AND ".join(parts) + family.where(conn, host_user_id, "t") +
                        " ORDER BY t.title COLLATE NOCASE LIMIT ?", ("", *params, SEARCH_LIMIT))
    return [track_lite(queries.track_dict(r)) for r in rows]


def request_dict(conn: sqlite3.Connection, rid: str) -> dict:
    r = conn.execute("SELECT r.*, g.name AS guest_name FROM jam_requests r LEFT JOIN jam_guests g ON g.id = r.guest_id "
                     "WHERE r.id = ?", (rid,)).fetchone()
    return _request_out(conn, r, None)


def _request_out(conn, r: sqlite3.Row, tracks: dict | None) -> dict:
    if tracks is None:
        tracks = queries.tracks_by_ids(conn, "", [r["track_id"]], include_missing=True)
    t = tracks.get(r["track_id"])
    return {"id": r["id"], "track": track_lite(t) if t else None, "trackId": r["track_id"],
            "playNext": r["mode"] == "next", "status": r["status"], "createdAt": r["created_at"],
            "by": {"guestId": r["guest_id"], "name": r["guest_name"]} if r["guest_id"] else None}


def _adder(r: sqlite3.Row | None, host_name: str) -> dict:
    if r is None or r["guest_id"] is None:
        return {"guestId": None, "name": host_name, "isHost": True}
    return {"guestId": r["guest_id"], "name": r["guest_name"], "isHost": False}


def join_urls(jam: sqlite3.Row, public_base: str | None, lan_base: str | None) -> dict:
    code = jam["join_code"]
    if not code:
        return {"joinCode": None, "joinUrl": None, "lanJoinUrl": None, "joinExpiresAt": None}
    return {"joinCode": code, "joinUrl": f"{public_base}/jam/{code}" if public_base else None,
            "lanJoinUrl": f"{lan_base}/jam/{code}" if lan_base else None, "joinExpiresAt": jam["join_expires_at"]}


def snapshot(conn: sqlite3.Connection, v: Viewer, state: dict | None, public_base: str | None = None,
             lan_base: str | None = None) -> dict:
    """Everything a Jam screen shows, for the host or one guest. `state` = the host's Connect PlaybackState."""
    jam = conn.execute("SELECT * FROM jams WHERE id = ?", (v.jam["id"],)).fetchone()
    s = settings_of(jam)
    host_name = _username(conn, jam["host_user_id"])
    qid = queue_id(conn, jam, (state or {}).get("queueId"))
    ids, attr, cur = _upcoming(conn, jam, qid, state)
    gid = None if v.is_host else v.guest["id"]
    window = list(range(cur + 1, min(len(ids), cur + 1 + UPCOMING_MAX)))
    pend_rows = conn.execute(
        "SELECT r.*, g.name AS guest_name FROM jam_requests r LEFT JOIN jam_guests g ON g.id = r.guest_id "
        "WHERE r.jam_id = ? AND r.status = 'pending'" + ("" if v.is_host else " AND r.guest_id = ?") +
        " ORDER BY r.created_at", (jam["id"],) if v.is_host else (jam["id"], gid)).fetchall()
    now_tid = (state or {}).get("trackId") if state and state.get("queueId") == qid else None
    if now_tid is None and 0 <= cur < len(ids):
        now_tid = ids[cur]
    want = [ids[i] for i in window] + [r["track_id"] for r in pend_rows] + ([now_tid] if now_tid else [])
    tracks = queries.tracks_by_ids(conn, "", want, include_missing=True)
    votes: dict[str, dict] = {}
    for r in conn.execute("SELECT track_id, value, guest_id FROM jam_votes WHERE jam_id = ?", (jam["id"],)):
        d = votes.setdefault(r["track_id"], {"up": 0, "down": 0, "mine": 0})
        d["up" if r["value"] > 0 else "down"] += 1
        if r["guest_id"] == gid:
            d["mine"] = r["value"]
    upcoming = []
    for i in window:
        t = tracks.get(ids[i])
        if t is None:
            continue
        upcoming.append({"position": i, "track": track_lite(t), "addedBy": _adder(attr[i], host_name),
                         "votes": votes.get(ids[i], {"up": 0, "down": 0, "mine": 0})})
    now_playing = None
    if now_tid and now_tid in tracks:
        st = state if state and state.get("queueId") == qid else {}
        now_playing = {
            "track": track_lite(tracks[now_tid]), "position": cur,
            "addedBy": _adder(attr[cur], host_name) if 0 <= cur < len(attr) else _adder(None, host_name),
            "positionMs": int(st.get("positionMs") or 0), "positionAt": st.get("positionAt") or now_iso(),
            "playing": bool(st.get("playing")), "speed": float(st.get("speed") or 1.0),
            "votes": votes.get(now_tid, {"up": 0, "down": 0, "mine": 0}),
        }
    guests = conn.execute("SELECT id, name, user_id FROM jam_guests WHERE jam_id = ? AND left_at IS NULL "
                          "ORDER BY created_at", (jam["id"],)).fetchall()
    out = {
        "jam": {"id": jam["id"], "hostName": host_name, "settings": s, "createdAt": jam["created_at"],
                "expiresAt": jam["expires_at"], "ended": bool(jam["ended_at"])},
        "serverTime": now_iso(),
        "nowPlaying": now_playing,
        "upcoming": upcoming,
        "pending": [_request_out(conn, r, tracks) for r in pend_rows],
        "guests": [{"id": g["id"], "name": g["name"], "isMember": g["user_id"] is not None} for g in guests],
    }
    if v.is_host:
        out["jam"].update(join_urls(jam, public_base, lan_base))
        out["jam"]["queueId"] = qid
        out["me"] = {"guestId": None, "name": host_name, "isHost": True}
    else:
        g = v.guest
        mine = sum(1 for i in window if attr[i] is not None and attr[i]["guest_id"] == gid) + len(pend_rows)
        cap = s["maxPerGuest"]
        out["me"] = {"guestId": gid, "name": g["name"], "isHost": False, "isMember": g["user_id"] is not None,
                     "waiting": mine, "remaining": None if cap is None else max(0, cap - mine),
                     "expiresAt": g["expires_at"]}
    return out
