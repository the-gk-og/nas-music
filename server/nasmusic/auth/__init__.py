"""Pairing codes, device tokens and the admin password.

Device tokens are random bearer tokens; only their sha256 is stored. Admin passwords use scrypt
(hashlib), encoded as `scrypt$n$r$p$<salt b64>$<hash b64>`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from nasmusic.core import iso, now_iso

PAIR_CODE_TTL = timedelta(minutes=10)
PAIR_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
PAIR_CODE_LEN = 8
LAST_SEEN_RESOLUTION_S = 60
DEVICE_IDLE_EXPIRY = timedelta(days=365)  # a device login unused this long stops working (sign in again)

# ---------- passwords ----------

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**15, 8, 1


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P,
                       maxmem=128 * 1024 * 1024, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(h)}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algo, n, r, p, salt_b64, hash_b64 = encoded.split("$")
        if algo != "scrypt":
            return False
        salt, want = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
        got = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p),
                             maxmem=128 * 1024 * 1024, dklen=len(want))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


# ---------- pair codes ----------


def create_pair_code(conn: sqlite3.Connection, user_id: str, now: datetime | None = None) -> tuple[str, str]:
    """Create a single-use code that logs a device in as `user_id`. Returns (code, expires_at)."""
    now = now or datetime.now(UTC)
    code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_CODE_LEN))
    expires = iso(now + PAIR_CODE_TTL)
    conn.execute("DELETE FROM pair_codes WHERE expires_at < ? OR used = 1", (iso(now - timedelta(days=1)),))
    conn.execute("INSERT INTO pair_codes (code, user_id, expires_at) VALUES (?, ?, ?)", (code, user_id, expires))
    return code, expires


def normalise_code(code: str) -> str:
    return "".join(code.split()).replace("-", "").upper()


def consume_pair_code(conn: sqlite3.Connection, code: str) -> str | None:
    """Mark the code used. Returns the user id it belongs to, or None if invalid/expired/used."""
    c = normalise_code(code)
    row = conn.execute(
        "SELECT p.user_id FROM pair_codes p JOIN users u ON u.id = p.user_id "
        "WHERE p.code = ? AND p.used = 0 AND p.expires_at > ? AND u.disabled = 0", (c, now_iso()),
    ).fetchone()
    if row is None:
        return None
    if conn.execute("UPDATE pair_codes SET used = 1 WHERE code = ? AND used = 0", (c,)).rowcount != 1:
        return None
    return row[0]


# ---------- devices ----------

DEVICE_KINDS = ("android", "web", "desktop", "watch", "other")


@dataclass(frozen=True)
class Device:
    """The authenticated caller: a device and the user it belongs to."""
    id: str
    name: str
    user_id: str
    username: str
    is_admin: bool
    kind: str = "other"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_device(conn: sqlite3.Connection, user_id: str, name: str, kind: str = "other",
                  app_version: str | None = None) -> tuple[str, str]:
    """Register a device for a user. Returns (device_id, token); only the token's sha256 is stored."""
    device_id = uuid.uuid4().hex
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO devices (id, user_id, name, kind, app_version, token_hash, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (device_id, user_id, (name.strip() or "Device")[:100], kind if kind in DEVICE_KINDS else "other",
         (app_version or None) and app_version[:50], token_hash(token), now_iso()),
    )
    return device_id, token


def pair_device(conn: sqlite3.Connection, code: str, device_name: str, kind: str = "android",
                app_version: str | None = None) -> tuple[str, str, str] | None:
    """Consume `code` and register a device for its user. Returns (user_id, device_id, token) or None."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        user_id = consume_pair_code(conn, code)
        if user_id is None:
            conn.execute("ROLLBACK")
            return None
        device_id, token = create_device(conn, user_id, device_name, kind, app_version)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return user_id, device_id, token


def device_for_token(conn: sqlite3.Connection, token: str) -> Device | None:
    """Resolve a bearer token. Revoked devices and disabled users get None."""
    row = conn.execute(
        "SELECT d.id, d.name, d.kind, d.last_seen, d.created_at, u.id AS user_id, u.username, u.is_admin "
        "FROM devices d JOIN users u ON u.id = d.user_id "
        "WHERE d.token_hash = ? AND d.revoked = 0 AND u.disabled = 0",
        (token_hash(token),),
    ).fetchone()
    if row is None:
        return None
    now = datetime.now(UTC)
    last = row["last_seen"]
    if (last or row["created_at"]) < iso(now - DEVICE_IDLE_EXPIRY):
        conn.execute("UPDATE devices SET revoked = 1 WHERE id = ?", (row["id"],))
        return None
    if last is None or last < iso(now - timedelta(seconds=LAST_SEEN_RESOLUTION_S)):
        conn.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (iso(now), row["id"]))
    return Device(id=row["id"], name=row["name"], user_id=row["user_id"], username=row["username"],
                  is_admin=bool(row["is_admin"]), kind=row["kind"])


def revoke_device(conn: sqlite3.Connection, device_id: str, user_id: str | None = None) -> bool:
    """Revoke a device (only if it belongs to `user_id`, when given)."""
    if user_id is None:
        return conn.execute("UPDATE devices SET revoked = 1 WHERE id = ? AND revoked = 0",
                            (device_id,)).rowcount == 1
    return conn.execute("UPDATE devices SET revoked = 1 WHERE id = ? AND user_id = ? AND revoked = 0",
                        (device_id, user_id)).rowcount == 1


def revoke_user_devices(conn: sqlite3.Connection, user_id: str, except_device: str | None = None) -> list[str]:
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM devices WHERE user_id = ? AND revoked = 0 AND id IS NOT ?", (user_id, except_device))]
    conn.execute("UPDATE devices SET revoked = 1 WHERE user_id = ? AND revoked = 0 AND id IS NOT ?",
                 (user_id, except_device))
    return ids


def list_devices(conn: sqlite3.Connection, user_id: str | None = None) -> list[sqlite3.Row]:
    q = ("SELECT d.id, d.name, d.kind, d.app_version, d.created_at, d.last_seen, d.revoked, d.user_id, "
         "u.username FROM devices d JOIN users u ON u.id = d.user_id")
    if user_id is None:
        return conn.execute(q + " ORDER BY d.revoked, u.username COLLATE NOCASE, d.created_at DESC").fetchall()
    return conn.execute(q + " WHERE d.user_id = ? AND d.revoked = 0 ORDER BY d.created_at DESC",
                        (user_id,)).fetchall()
