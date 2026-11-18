"""User accounts and invites (docs/v2.md §2, §4). Invite-only registration; admins manage users."""
from __future__ import annotations

import functools
import re
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from nasmusic.auth import hash_password, verify_password
from nasmusic.core import iso, now_iso

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
MIN_PASSWORD = 8
NO_PASSWORD = "!"
INVITE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


class UserError(Exception):
    """Raised with an HTTP-ish status for API mapping."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


@dataclass(frozen=True)
class User:
    id: str
    username: str
    is_admin: bool
    disabled: bool
    created_at: str
    last_login: str | None

    def as_api(self) -> dict:
        return {"id": self.id, "username": self.username, "isAdmin": self.is_admin, "createdAt": self.created_at,
                "lastLogin": self.last_login}


def _user(row: sqlite3.Row | None) -> User | None:
    if row is None:
        return None
    return User(id=row["id"], username=row["username"], is_admin=bool(row["is_admin"]),
                disabled=bool(row["disabled"]), created_at=row["created_at"], last_login=row["last_login"])


def get(conn: sqlite3.Connection, user_id: str) -> User | None:
    return _user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def by_username(conn: sqlite3.Connection, username: str) -> User | None:
    return _user(conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone())


def check_new_credentials(username: str | None, password: str) -> None:
    if username is not None and not USERNAME_RE.match(username):
        raise UserError(422, "username must be 3-32 characters: letters, digits, _ . -")
    if len(password) < MIN_PASSWORD:
        raise UserError(422, f"password must be at least {MIN_PASSWORD} characters")


def create(conn: sqlite3.Connection, username: str, password: str | None, is_admin: bool = False,
           password_hash: str | None = None) -> User:
    if password is not None:
        check_new_credentials(username, password)
        password_hash = hash_password(password)
    elif not USERNAME_RE.match(username):
        raise UserError(422, "invalid username")
    uid = uuid.uuid4().hex
    try:
        conn.execute(
            "INSERT INTO users (id, username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?, ?)",
            (uid, username, password_hash or NO_PASSWORD, int(is_admin), now_iso()),
        )
    except sqlite3.IntegrityError as e:
        raise UserError(409, "username taken") from e
    return get(conn, uid)


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> User:
    """Check credentials. Raises UserError(401) for wrong ones, (403) for a disabled account."""
    row = conn.execute("SELECT * FROM users WHERE username = ? COLLATE NOCASE", (username,)).fetchone()
    if row is None:
        verify_password(password, _dummy_hash())  # same cost whether or not the user exists
        raise UserError(401, "wrong username or password")
    if not verify_password(password, row["password_hash"]):
        raise UserError(401, "wrong username or password")
    if row["disabled"]:
        raise UserError(403, "account disabled")
    conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now_iso(), row["id"]))
    return get(conn, row["id"])


@functools.cache
def _dummy_hash() -> str:
    return hash_password(secrets.token_hex(8))


def set_password(conn: sqlite3.Connection, user_id: str, new: str) -> None:
    check_new_credentials(None, new)
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(new), user_id))


def change_password(conn: sqlite3.Connection, user_id: str, current: str, new: str) -> None:
    row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None or not verify_password(current, row[0]):
        raise UserError(403, "current password is wrong")
    set_password(conn, user_id, new)


def ensure_admin(conn: sqlite3.Connection, username: str, password_hash: str | None) -> User | None:
    """Fresh install: create the first admin from config. Existing admin with no password ('!', e.g. migrated
    before a hash was configured): fill it in. Never overwrites a real password."""
    if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
        return create(conn, username, None, is_admin=True, password_hash=password_hash)
    if password_hash:
        conn.execute("UPDATE users SET password_hash = ? WHERE username = ? COLLATE NOCASE AND password_hash = ?",
                      (password_hash, username, NO_PASSWORD))
    return by_username(conn, username)


def list_all(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT u.*, (SELECT COUNT(*) FROM devices d WHERE d.user_id = u.id AND d.revoked = 0) AS devices "
        "FROM users u ORDER BY u.username COLLATE NOCASE"
    ).fetchall()
    return [{**_user(r).as_api(), "disabled": bool(r["disabled"]), "deviceCount": r["devices"]} for r in rows]


def admin_view(conn: sqlite3.Connection, user_id: str) -> dict | None:
    return next((u for u in list_all(conn) if u["id"] == user_id), None)


def update(conn: sqlite3.Connection, user_id: str, disabled: bool | None = None,
           is_admin: bool | None = None) -> list[str]:
    """Admin edit. Refuses (409) to leave no active admin. Disabling revokes the user's devices.
    Returns the revoked device ids (for closing their Connect sockets)."""
    from nasmusic.auth import revoke_user_devices

    conn.execute("BEGIN IMMEDIATE")
    try:
        u = get(conn, user_id)
        if u is None:
            raise UserError(404, "no such user")
        new_admin = u.is_admin if is_admin is None else is_admin
        new_disabled = u.disabled if disabled is None else disabled
        if u.is_admin and not u.disabled and (not new_admin or new_disabled):
            others = conn.execute("SELECT COUNT(*) FROM users WHERE is_admin = 1 AND disabled = 0 AND id != ?",
                                  (user_id,)).fetchone()[0]
            if others == 0:
                raise UserError(409, "can't remove the last admin")
        conn.execute("UPDATE users SET is_admin = ?, disabled = ? WHERE id = ?",
                     (int(new_admin), int(new_disabled), user_id))
        revoked = revoke_user_devices(conn, user_id) if new_disabled and not u.disabled else []
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return revoked


# ---------- invites ----------


def create_invite(conn: sqlite3.Connection, created_by: str, note: str | None = None, days: int = 7) -> dict:
    code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(10))
    now = datetime.now(UTC)
    expires = iso(now + timedelta(days=days))
    conn.execute("INSERT INTO invites (code, created_by, created_at, expires_at, note) VALUES (?, ?, ?, ?, ?)",
                 (code, created_by, iso(now), expires, (note or None) and note[:200]))
    return {"code": code, "expiresAt": expires}


def list_invites(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT i.*, c.username AS created_by_name, u.username AS used_by_name FROM invites i "
        "JOIN users c ON c.id = i.created_by LEFT JOIN users u ON u.id = i.used_by ORDER BY i.created_at DESC"
    ).fetchall()


def delete_invite(conn: sqlite3.Connection, code: str) -> bool:
    return conn.execute("DELETE FROM invites WHERE code = ?", (code,)).rowcount == 1


def register(conn: sqlite3.Connection, invite_code: str, username: str, password: str) -> User:
    """Create a user from an unused, unexpired invite (atomically consumes it)."""
    check_new_credentials(username, password)
    pw_hash = hash_password(password)  # slow: do it before taking the write lock
    conn.execute("BEGIN IMMEDIATE")
    try:
        inv = conn.execute("SELECT * FROM invites WHERE code = ?", (invite_code.strip().lower(),)).fetchone()
        if inv is None or inv["used_by"] is not None or inv["expires_at"] <= now_iso():
            raise UserError(403, "invite code unknown, used or expired")
        u = create(conn, username, None, password_hash=pw_hash)
        conn.execute("UPDATE invites SET used_by = ?, used_at = ? WHERE code = ?", (u.id, now_iso(), inv["code"]))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return u


# ---------- rate limiting ----------


class RateLimiter:
    """At most `limit` failures per `window_s` per key (client IP)."""

    def __init__(self, limit: int = 5, window_s: float = 60.0):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._hits.get(key, []) if now - t < self.window]
            if recent:
                self._hits[key] = recent
            else:
                self._hits.pop(key, None)
            return len(recent) >= self.limit

    def fail(self, key: str) -> None:
        with self._lock:
            self._hits.setdefault(key, []).append(time.monotonic())

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()
