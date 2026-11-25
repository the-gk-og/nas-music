"""Blends: membership and consent (server core), tracks from the AI (homebeat.ai.blend via ai_adapter).

Rules
* Only existing, enabled users can be invited, and only if they haven't switched off invites
  (user setting `blends.allowInvites` = false).
* Nobody's listening is used until they accept. Invitees see the name and members, never the tracks.
* Any member can leave at any time; the blend is rebuilt straight away without them. A blend with nobody
  joined or invited left is deleted.
* At most MAX_MEMBERS people (joined + invited) per blend and MAX_BLENDS active blends per user.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid

from homebeat.app import ai_adapter, userdata
from homebeat.config import Config
from homebeat.core import ai_config, now_iso
from homebeat.library import queries

log = logging.getLogger(__name__)

MAX_MEMBERS = 8
MAX_BLENDS = 20
ALLOW_KEY = "blends.allowInvites"


class BlendError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _users(conn: sqlite3.Connection, ids: list[str]) -> dict[str, str]:
    if not ids:
        return {}
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT id, username FROM users WHERE id IN ({','.join('?' * len(ids))})", ids)}


def allows_invites(conn: sqlite3.Connection, uid: str) -> bool:
    return userdata.get_settings(conn, uid).get(ALLOW_KEY, True) is not False


def candidates(conn: sqlite3.Connection, uid: str) -> list[dict]:
    """Other enabled users who accept blend invites."""
    return [{"userId": r[0], "username": r[1]} for r in conn.execute(
        "SELECT id, username FROM users WHERE disabled = 0 AND id != ? AND password_hash != '!' "
        "ORDER BY username COLLATE NOCASE", (uid,)) if allows_invites(conn, r[0])]


def _status(conn: sqlite3.Connection, bid: str, uid: str) -> str | None:
    r = conn.execute("SELECT status FROM blend_members WHERE blend_id = ? AND user_id = ?", (bid, uid)).fetchone()
    return r[0] if r else None


def _count_active(conn: sqlite3.Connection, uid: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM blend_members WHERE user_id = ? AND status IN ('joined', 'invited')",
                        (uid,)).fetchone()[0]


def _resolve_invitees(conn: sqlite3.Connection, uid: str, usernames: list[str]) -> list[str]:
    out = []
    for name in dict.fromkeys(n.strip() for n in usernames if n and n.strip()):
        r = conn.execute("SELECT id, disabled FROM users WHERE username = ? COLLATE NOCASE", (name,)).fetchone()
        if r is None or r["disabled"]:
            raise BlendError(404, f"no user called {name}")
        if r["id"] == uid:
            raise BlendError(422, "you can't invite yourself")
        if not allows_invites(conn, r["id"]):
            raise BlendError(403, f"{name} isn't accepting blend invites")
        if _count_active(conn, r["id"]) >= MAX_BLENDS:
            raise BlendError(409, f"{name} is in too many blends already")
        out.append(r["id"])
    return out


def _rebuild(conn: sqlite3.Connection, cfg: Config, bid: str) -> None:
    try:
        ai_adapter.build_blend(conn, ai_config(cfg), bid)
    except NotImplementedError:
        pass
    except Exception:  # the nightly job will try again; membership changes must not fail on this
        log.exception("blend %s rebuild failed", bid)


def create(conn: sqlite3.Connection, cfg: Config, uid: str, name: str | None, usernames: list[str]) -> dict:
    if _count_active(conn, uid) >= MAX_BLENDS:
        raise BlendError(409, f"you're in {MAX_BLENDS} blends already")
    invitees = _resolve_invitees(conn, uid, usernames)
    if not invitees:
        raise BlendError(422, "invite at least one person")
    if len(invitees) + 1 > MAX_MEMBERS:
        raise BlendError(422, f"a blend has at most {MAX_MEMBERS} people")
    names = _users(conn, [uid, *invitees])
    title = (name or "").strip()[:80] or " + ".join(names[u] for u in [uid, *invitees][:3]) + (
        " + more" if len(invitees) > 2 else "")
    bid, now = uuid.uuid4().hex, now_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO blends (id, name, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                     (bid, title, uid, now, now))
        conn.execute("INSERT INTO blend_members (blend_id, user_id, status, invited_by, invited_at, joined_at) "
                     "VALUES (?, ?, 'joined', ?, ?, ?)", (bid, uid, uid, now, now))
        conn.executemany("INSERT INTO blend_members (blend_id, user_id, status, invited_by, invited_at) "
                         "VALUES (?, ?, 'invited', ?, ?)", [(bid, u, uid, now) for u in invitees])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return get(conn, uid, bid)


def invite(conn: sqlite3.Connection, uid: str, bid: str, usernames: list[str]) -> dict:
    if _status(conn, bid, uid) != "joined":
        raise BlendError(404, "no such blend")
    invitees = _resolve_invitees(conn, uid, usernames)
    current = {r[0]: r[1] for r in conn.execute("SELECT user_id, status FROM blend_members WHERE blend_id = ?", (bid,))}
    new = [u for u in invitees if current.get(u) not in ("joined", "invited")]
    active = sum(1 for s in current.values() if s in ("joined", "invited"))
    if active + len(new) > MAX_MEMBERS:
        raise BlendError(422, f"a blend has at most {MAX_MEMBERS} people")
    now = now_iso()
    for u in new:  # someone who left or declined is asked again; their consent starts from scratch
        conn.execute("INSERT INTO blend_members (blend_id, user_id, status, invited_by, invited_at) "
                     "VALUES (?, ?, 'invited', ?, ?) ON CONFLICT(blend_id, user_id) DO UPDATE SET status = 'invited', "
                     "invited_by = excluded.invited_by, invited_at = excluded.invited_at, joined_at = NULL, "
                     "left_at = NULL", (bid, u, uid, now))
    conn.execute("UPDATE blends SET updated_at = ? WHERE id = ?", (now, bid))
    return get(conn, uid, bid)


def respond(conn: sqlite3.Connection, cfg: Config, uid: str, bid: str, accept: bool) -> dict | None:
    if _status(conn, bid, uid) != "invited":
        raise BlendError(404, "no invite to this blend")
    now = now_iso()
    if accept:
        conn.execute("UPDATE blend_members SET status = 'joined', joined_at = ? WHERE blend_id = ? AND user_id = ?",
                     (now, bid, uid))
        _rebuild(conn, cfg, bid)
        return get(conn, uid, bid)
    conn.execute("UPDATE blend_members SET status = 'declined', left_at = ? WHERE blend_id = ? AND user_id = ?",
                 (now, bid, uid))
    _cleanup(conn, cfg, bid)
    return None


def leave(conn: sqlite3.Connection, cfg: Config, uid: str, bid: str) -> None:
    if _status(conn, bid, uid) not in ("joined", "invited"):
        raise BlendError(404, "no such blend")
    conn.execute("UPDATE blend_members SET status = 'left', left_at = ? WHERE blend_id = ? AND user_id = ?",
                 (now_iso(), bid, uid))
    _cleanup(conn, cfg, bid)


def _cleanup(conn: sqlite3.Connection, cfg: Config, bid: str) -> None:
    left = conn.execute("SELECT COUNT(*) FROM blend_members WHERE blend_id = ? AND status IN ('joined', 'invited')",
                        (bid,)).fetchone()[0]
    if left == 0:
        conn.execute("DELETE FROM blends WHERE id = ?", (bid,))
        return
    _rebuild(conn, cfg, bid)  # drops the leaver's tracks and attribution now, not tomorrow night


def rename(conn: sqlite3.Connection, uid: str, bid: str, name: str) -> dict:
    if _status(conn, bid, uid) != "joined":
        raise BlendError(404, "no such blend")
    conn.execute("UPDATE blends SET name = ?, updated_at = ? WHERE id = ?", (name.strip()[:80], now_iso(), bid))
    return get(conn, uid, bid)


def refresh(conn: sqlite3.Connection, cfg: Config, uid: str, bid: str) -> dict:
    if _status(conn, bid, uid) != "joined":
        raise BlendError(404, "no such blend")
    _rebuild(conn, cfg, bid)
    return get(conn, uid, bid)


def _match(raw: str | None, names: dict[str, str]) -> tuple[int | None, list[dict]]:
    try:
        m = json.loads(raw) if raw else None
    except ValueError:
        m = None
    if not isinstance(m, dict):
        return None, []
    pairs = [{"a": p.get("a"), "b": p.get("b"), "aName": names.get(p.get("a")), "bName": names.get(p.get("b")),
              "pct": p.get("pct")} for p in m.get("pairs", []) if isinstance(p, dict)]
    return m.get("pct"), pairs


def _summary(conn: sqlite3.Connection, uid: str, b: sqlite3.Row) -> dict:
    mem = conn.execute("SELECT user_id, status, invited_by, joined_at FROM blend_members WHERE blend_id = ? "
                       "ORDER BY joined_at IS NULL, joined_at, invited_at, user_id", (b["id"],)).fetchall()
    names = _users(conn, [m["user_id"] for m in mem] + [b["created_by"]])
    me = next((m for m in mem if m["user_id"] == uid), None)
    joined = [m["user_id"] for m in mem if m["status"] == "joined"]
    pct, pairs = _match(b["match"], names) if me and me["status"] == "joined" else (None, [])
    pairs = [p for p in pairs if p["a"] in joined and p["b"] in joined]
    n = conn.execute("SELECT COUNT(*) FROM blend_tracks WHERE blend_id = ?", (b["id"],)).fetchone()[0] \
        if me and me["status"] == "joined" else 0
    return {"id": b["id"], "name": b["name"], "createdBy": {"userId": b["created_by"], "username": names.get(b["created_by"])},
            "myStatus": me["status"] if me else None,
            "invitedBy": names.get(me["invited_by"]) if me and me["status"] == "invited" else None,
            "members": [{"userId": m["user_id"], "username": names.get(m["user_id"]), "status": m["status"],
                         "joinedAt": m["joined_at"]} for m in mem if m["status"] in ("joined", "invited")],
            "matchPct": pct, "pairs": pairs, "trackCount": n, "createdAt": b["created_at"],
            "updatedAt": b["built_at"] or b["updated_at"]}


def list_for(conn: sqlite3.Connection, uid: str) -> list[dict]:
    rows = conn.execute(
        "SELECT b.* FROM blends b JOIN blend_members m ON m.blend_id = b.id AND m.user_id = ? "
        "WHERE m.status IN ('joined', 'invited') ORDER BY m.status = 'invited' DESC, b.updated_at DESC", (uid,)).fetchall()
    return [_summary(conn, uid, b) for b in rows]


def get(conn: sqlite3.Connection, uid: str, bid: str) -> dict:
    b = conn.execute("SELECT * FROM blends WHERE id = ?", (bid,)).fetchone()
    if b is None or _status(conn, bid, uid) not in ("joined", "invited"):
        raise BlendError(404, "no such blend")
    out = _summary(conn, uid, b)
    out["tracks"] = []
    if out["myStatus"] == "joined":
        rows = conn.execute("SELECT track_id, for_users, reason FROM blend_tracks WHERE blend_id = ? ORDER BY position",
                            (bid,)).fetchall()
        tracks = queries.tracks_by_ids(conn, uid, [r[0] for r in rows])
        joined = {m["userId"] for m in out["members"] if m["status"] == "joined"}
        for r in rows:
            if r[0] not in tracks:
                continue
            try:
                users = [u for u in json.loads(r[1]) if u in joined]
            except ValueError:
                users = []
            out["tracks"].append({"track": tracks[r[0]], "forUsers": users, "reason": r[2]})
    return out


def shelf_items(conn: sqlite3.Connection, data_dir, uid: str) -> list[dict]:
    from homebeat.app.discover import _art_for_tracks

    items = []
    for b in list_for(conn, uid):
        others = [m["username"] for m in b["members"] if m["userId"] != uid]
        if b["myStatus"] == "invited":
            sub = f"Invite from {b['invitedBy'] or b['createdBy']['username']}"
            art = None
        else:
            sub = (f"{b['matchPct']}% match" if b["matchPct"] is not None else "Waiting for others to join") \
                + (f" · with {', '.join(others[:2])}" if others else "")
            ids = [r[0] for r in conn.execute("SELECT track_id FROM blend_tracks WHERE blend_id = ? ORDER BY position "
                                              "LIMIT 10", (b["id"],))]
            art = _art_for_tracks(conn, data_dir, ids) if ids else None
        items.append({"type": "blend", "id": b["id"], "title": b["name"], "subtitle": sub, "artUrl": art,
                      "blend": {"status": b["myStatus"], "matchPct": b["matchPct"], "trackCount": b["trackCount"],
                                "members": [m["username"] for m in b["members"]]}})
    return items
