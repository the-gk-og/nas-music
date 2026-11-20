"""Voice tokens and per-user device aliases (schema v7: voice_tokens, voice_devices).

A voice token is a long-lived bearer token that authenticates ONLY `POST /api/v1/voice/command` as its user.
It is not a device token (it can't stream, browse or change settings), so pasting it into Home Assistant
doesn't hand HA the user's account. Only its sha256 is stored; it is shown once, at creation.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from nasmusic.auth import token_hash
from nasmusic.core import iso, now_iso

TOKEN_PREFIX = "nmv_"
MAX_TOKENS = 20
MAX_ALIASES = 10
LAST_USED_RESOLUTION_S = 60


@dataclass(frozen=True)
class VoiceCaller:
    token_id: str
    token_name: str
    user_id: str
    username: str


def create_token(conn: sqlite3.Connection, user_id: str, name: str) -> tuple[dict, str]:
    n = conn.execute("SELECT COUNT(*) FROM voice_tokens WHERE user_id = ? AND revoked = 0", (user_id,)).fetchone()[0]
    if n >= MAX_TOKENS:
        raise ValueError(f"at most {MAX_TOKENS} voice tokens; revoke one first")
    tid = uuid.uuid4().hex
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = now_iso()
    name = (name or "").strip()[:100] or "Home Assistant"
    conn.execute("INSERT INTO voice_tokens (id, user_id, name, token_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                 (tid, user_id, name, token_hash(token), now))
    return {"id": tid, "name": name, "createdAt": now, "lastUsed": None}, token


def list_tokens(conn: sqlite3.Connection, user_id: str) -> list[dict]:
    rows = conn.execute("SELECT id, name, created_at, last_used FROM voice_tokens WHERE user_id = ? AND revoked = 0 "
                        "ORDER BY created_at DESC", (user_id,))
    return [{"id": r["id"], "name": r["name"], "createdAt": r["created_at"], "lastUsed": r["last_used"]} for r in rows]


def revoke_token(conn: sqlite3.Connection, user_id: str, token_id: str) -> bool:
    return conn.execute("UPDATE voice_tokens SET revoked = 1 WHERE id = ? AND user_id = ? AND revoked = 0",
                        (token_id, user_id)).rowcount == 1


def caller_for_token(conn: sqlite3.Connection, token: str) -> VoiceCaller | None:
    if not token.startswith(TOKEN_PREFIX):
        return None
    r = conn.execute(
        "SELECT v.id, v.name, v.last_used, u.id AS user_id, u.username FROM voice_tokens v "
        "JOIN users u ON u.id = v.user_id WHERE v.token_hash = ? AND v.revoked = 0 AND u.disabled = 0",
        (token_hash(token),)).fetchone()
    if r is None:
        return None
    now = datetime.now(UTC)
    if r["last_used"] is None or r["last_used"] < iso(now - timedelta(seconds=LAST_USED_RESOLUTION_S)):
        conn.execute("UPDATE voice_tokens SET last_used = ? WHERE id = ?", (iso(now), r["id"]))
    return VoiceCaller(r["id"], r["name"], r["user_id"], r["username"])


# ---------- device aliases ----------


def clean_aliases(aliases: list[str]) -> list[str]:
    out: list[str] = []
    for a in aliases:
        a = " ".join(str(a).split())[:60]
        if a and a.casefold() not in (x.casefold() for x in out):
            out.append(a)
    return out[:MAX_ALIASES]


def device_prefs(conn: sqlite3.Connection, user_id: str) -> dict[str, dict]:
    """device_id -> {"aliases": [...], "isDefault": bool}"""
    out = {}
    for r in conn.execute("SELECT device_id, aliases, is_default FROM voice_devices WHERE user_id = ?", (user_id,)):
        try:
            al = json.loads(r["aliases"])
        except ValueError:
            al = []
        out[r["device_id"]] = {"aliases": [a for a in al if isinstance(a, str)], "isDefault": bool(r["is_default"])}
    return out


def set_device_prefs(conn: sqlite3.Connection, user_id: str, device_id: str, aliases: list[str] | None,
                     is_default: bool | None) -> dict:
    cur = device_prefs(conn, user_id).get(device_id, {"aliases": [], "isDefault": False})
    al = clean_aliases(aliases) if aliases is not None else cur["aliases"]
    dflt = cur["isDefault"] if is_default is None else bool(is_default)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if dflt:
            conn.execute("UPDATE voice_devices SET is_default = 0 WHERE user_id = ? AND device_id != ?",
                         (user_id, device_id))
        if not al and not dflt:
            conn.execute("DELETE FROM voice_devices WHERE user_id = ? AND device_id = ?", (user_id, device_id))
        else:
            conn.execute(
                "INSERT INTO voice_devices (user_id, device_id, aliases, is_default, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id, device_id) DO UPDATE SET aliases = excluded.aliases, "
                "is_default = excluded.is_default, updated_at = excluded.updated_at",
                (user_id, device_id, json.dumps(al), int(dflt), now_iso()))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"aliases": al, "isDefault": dflt}
