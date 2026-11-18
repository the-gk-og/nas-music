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


def create_pair_code(conn: sqlite3.Connection, now: datetime | None = None) -> tuple[str, str]:
    """Create a single-use code. Returns (code, expires_at)."""
    now = now or datetime.now(UTC)
    code = "".join(secrets.choice(PAIR_ALPHABET) for _ in range(PAIR_CODE_LEN))
    expires = iso(now + PAIR_CODE_TTL)
    conn.execute("DELETE FROM pair_codes WHERE expires_at < ? OR used = 1", (iso(now - timedelta(days=1)),))
    conn.execute("INSERT INTO pair_codes (code, expires_at) VALUES (?, ?)", (code, expires))
    return code, expires


def normalise_code(code: str) -> str:
    return "".join(code.split()).replace("-", "").upper()


def consume_pair_code(conn: sqlite3.Connection, code: str) -> bool:
    cur = conn.execute(
        "UPDATE pair_codes SET used = 1 WHERE code = ? AND used = 0 AND expires_at > ?",
        (normalise_code(code), now_iso()),
    )
    return cur.rowcount == 1


# ---------- devices ----------


@dataclass(frozen=True)
class Device:
    id: str
    name: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def pair_device(conn: sqlite3.Connection, code: str, device_name: str) -> tuple[str, str] | None:
    """Consume `code` and register a device. Returns (device_id, token) or None if the code is bad."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not consume_pair_code(conn, code):
            conn.execute("ROLLBACK")
            return None
        device_id = uuid.uuid4().hex
        token = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO devices (id, name, token_hash, created_at) VALUES (?, ?, ?, ?)",
            (device_id, (device_name.strip() or "Device")[:100], token_hash(token), now_iso()),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return device_id, token


def device_for_token(conn: sqlite3.Connection, token: str) -> Device | None:
    row = conn.execute(
        "SELECT id, name, last_seen FROM devices WHERE token_hash = ? AND revoked = 0",
        (token_hash(token),),
    ).fetchone()
    if row is None:
        return None
    now = datetime.now(UTC)
    last = row["last_seen"]
    if last is None or last < iso(now - timedelta(seconds=LAST_SEEN_RESOLUTION_S)):
        conn.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (iso(now), row["id"]))
    return Device(id=row["id"], name=row["name"])


def revoke_device(conn: sqlite3.Connection, device_id: str) -> bool:
    return conn.execute("UPDATE devices SET revoked = 1 WHERE id = ?", (device_id,)).rowcount == 1


def list_devices(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, name, created_at, last_seen, revoked FROM devices ORDER BY revoked, created_at DESC"
    ).fetchall()
