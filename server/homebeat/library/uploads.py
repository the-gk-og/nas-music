"""Uploading songs into the library (schema v7: upload_access, uploads, upload_sessions).

Files never go near the read-only music library: they live under config `upload_root`, a second, writable
library root that the scanner indexes with paths '@uploads/<path>' (see homebeat.config.LibraryRoot).

Protocol (resumable, chunk + offset, a small subset of tus):
  1. create(): checks permission, format (by name), size limit and quota; reserves a session.
  2. append(offset, bytes) any number of times; `offset` must equal what the server has (else Conflict with
     the server's offset, so a client that lost a response just resumes from there). The first chunk is
     sniffed: not audio -> rejected straight away.
  3. When the last byte arrives the file is sniffed again and parsed with mutagen (must be audio with a
     duration), then moved to <upload_root>/<username>/<Artist>/<Album>/<NN Title>.<ext> from its tags
     (or <username>/Unsorted/<original name>.<ext>) and recorded in `uploads`. The caller then triggers an
     uploads-only scan, after which analysis, loudness, explicit and AcoustID jobs pick the track up.
Partial files are upload_root/.incoming/<session>.part (same filesystem, so the final move is a rename;
dot-folders are never scanned).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from homebeat.config import UPLOADS_PREFIX, Config
from homebeat.core import iso, now_iso

log = logging.getLogger(__name__)

MAX_CHUNK = 16 * 1024 * 1024          # per request; clients send 4-8 MiB (Cloudflare/relay friendly)
RECOMMENDED_CHUNK = 4 * 1024 * 1024
INCOMING = ".incoming"
STALE_SESSION = timedelta(days=7)     # unfinished uploads are dropped after a week without progress
FINISHED_SESSION = timedelta(days=1)  # done/failed session rows are kept a day (for clients that lost the reply)
MAX_SESSIONS_PER_USER = 50

# Sniffed format -> stored extension. Everything is kept as uploaded (no transcoding).
EXTENSIONS = {"flac": ".flac", "mp3": ".mp3", "m4a": ".m4a", "aac": ".aac", "ogg": ".ogg", "opus": ".opus",
              "wav": ".wav"}
# File names a client may announce (checked again by content at the end).
ALLOWED_NAME_EXTS = {".flac", ".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".wav"}
FORMATS = ("flac", "mp3", "m4a", "aac", "ogg", "opus", "wav")

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_place_lock = threading.Lock()  # choosing a free destination name and moving there must be one step


class UploadError(Exception):
    """`status` is the HTTP status the API answers with."""

    def __init__(self, status: int, detail: str, offset: int | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.offset = offset


def _lock(session_id: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(session_id, threading.Lock())


# ---------------------------------------------------------------- permissions and quota


@dataclass
class Access:
    allowed: bool
    quota_bytes: int          # 0 = unlimited
    used_bytes: int           # finished uploads + bytes reserved by unfinished ones
    explicit: bool            # an admin set `allowed` (False = default: admins only)

    def as_api(self, cfg: Config) -> dict:
        return {"canUpload": self.allowed and cfg.upload_root is not None, "enabled": cfg.upload_root is not None,
                "quotaBytes": self.quota_bytes or None, "usedBytes": self.used_bytes,
                "maxFileBytes": max_file_bytes(cfg), "chunkBytes": RECOMMENDED_CHUNK, "maxChunkBytes": MAX_CHUNK,
                "formats": list(FORMATS)}


def max_file_bytes(cfg: Config) -> int:
    return int(cfg.upload_max_file_mb) * 1024 * 1024


def default_quota(cfg: Config) -> int:
    return int(cfg.upload_quota_gb * 1024**3)


def used_bytes(conn: sqlite3.Connection, user_id: str) -> int:
    done = conn.execute("SELECT COALESCE(SUM(bytes), 0) FROM uploads WHERE user_id = ?", (user_id,)).fetchone()[0]
    pending = conn.execute("SELECT COALESCE(SUM(bytes), 0) FROM upload_sessions WHERE user_id = ? "
                           "AND status = 'uploading'", (user_id,)).fetchone()[0]
    return int(done) + int(pending)


def access(conn: sqlite3.Connection, cfg: Config, user_id: str) -> Access:
    u = conn.execute("SELECT is_admin, disabled FROM users WHERE id = ?", (user_id,)).fetchone()
    r = conn.execute("SELECT allowed, quota_bytes FROM upload_access WHERE user_id = ?", (user_id,)).fetchone()
    is_admin = bool(u and u["is_admin"])
    allowed = bool(r["allowed"]) if r is not None and r["allowed"] is not None else is_admin
    if u is None or u["disabled"]:
        allowed = False
    quota = r["quota_bytes"] if r is not None and r["quota_bytes"] is not None else default_quota(cfg)
    return Access(allowed=allowed, quota_bytes=int(quota), used_bytes=used_bytes(conn, user_id),
                  explicit=r is not None and r["allowed"] is not None)


def set_access(conn: sqlite3.Connection, user_id: str, allowed: bool | None | type(...) = ...,
               quota_bytes: int | None | type(...) = ...) -> None:
    """`...` = leave as is; None = back to the default."""
    r = conn.execute("SELECT allowed, quota_bytes FROM upload_access WHERE user_id = ?", (user_id,)).fetchone()
    a = (r["allowed"] if r else None) if allowed is ... else (None if allowed is None else int(allowed))
    q = (r["quota_bytes"] if r else None) if quota_bytes is ... else quota_bytes
    conn.execute("INSERT INTO upload_access (user_id, allowed, quota_bytes, updated_at) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(user_id) DO UPDATE SET allowed = excluded.allowed, quota_bytes = excluded.quota_bytes, "
                 "updated_at = excluded.updated_at", (user_id, a, q, now_iso()))


def admin_overview(conn: sqlite3.Connection, cfg: Config) -> list[dict]:
    out = []
    for u in conn.execute("SELECT id, username, is_admin, disabled FROM users ORDER BY username COLLATE NOCASE"):
        a = access(conn, cfg, u["id"])
        files = conn.execute("SELECT COUNT(*) FROM uploads WHERE user_id = ?", (u["id"],)).fetchone()[0]
        out.append({"userId": u["id"], "username": u["username"], "isAdmin": bool(u["is_admin"]),
                    "canUpload": a.allowed, "explicit": a.explicit, "quotaBytes": a.quota_bytes or None,
                    "usedBytes": a.used_bytes, "files": files})
    return out


# ---------------------------------------------------------------- sniffing


def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def sniff(head: bytes) -> str | None:
    """Audio format from the first bytes of a file (FLAC, MP3, MP4/M4A, ADTS AAC, Ogg Vorbis/Opus, WAV), or
    None if it is not one of them. An ID3v2 tag in front is skipped when `head` reaches past it; when it
    doesn't, "mp3" is assumed (checked properly by mutagen once the whole file is there)."""
    if len(head) < 4:
        return None
    if head.startswith(b"ID3") and len(head) >= 10:
        end = 10 + _syncsafe(head[6:10]) + (10 if head[5] & 0x10 else 0)
        if len(head) >= end + 4:
            inner = sniff(head[end:])
            return inner if inner in ("flac", "mp3", "aac") else None
        return "mp3"
    if head.startswith(b"fLaC"):
        return "flac"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "m4a"
    if head.startswith(b"OggS"):
        if b"OpusHead" in head[:128]:
            return "opus"
        return "ogg"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[0] == 0xFF and len(head) >= 2:
        if (head[1] & 0xF6) == 0xF0:          # ADTS AAC: layer bits 00
            return "aac"
        if (head[1] & 0xE0) == 0xE0 and (head[1] & 0x06) != 0 and (head[2] & 0xF0) != 0xF0:
            return "mp3"                      # MPEG audio frame (layer set, bitrate index not 'bad')
    return None


def _read_head(path: Path) -> bytes:
    with open(path, "rb") as f:
        head = f.read(10)
        if head.startswith(b"ID3") and len(head) == 10:
            size = 10 + _syncsafe(head[6:10]) + (10 if head[5] & 0x10 else 0)
            f.seek(0)
            return f.read(min(size + 4096, 64 * 1024 * 1024))
        return head + f.read(4096)


# ---------------------------------------------------------------- safe names

_BAD_CHARS = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')


def safe_component(s: str | None, fallback: str, max_bytes: int = 120) -> str:
    """One path component: no separators, control characters, Windows-reserved characters, leading dots
    (hidden / '..') or trailing dots and spaces, at most `max_bytes` of UTF-8."""
    s = unicodedata.normalize("NFC", s or "")
    s = _BAD_CHARS.sub("_", s)
    s = " ".join(s.split()).strip(" .")
    while len(s.encode("utf-8")) > max_bytes:
        s = s[:-1]
    s = s.strip(" .")
    return s or fallback


def original_stem(filename: str) -> str:
    base = re.split(r"[/\\]", filename or "")[-1]
    stem, _ = os.path.splitext(base)
    return safe_component(stem, "upload")


def target_relpath(username: str, user_id: str, t, filename: str, ext: str) -> str:
    """<username>/<Artist>/<Album>/<NN Title><ext> from tags, or <username>/Unsorted/<original name><ext>."""
    user_dir = safe_component(username, user_id)
    artist = t.albumartist or t.artist
    if artist and t.title:
        album = safe_component(t.album, "Unknown Album")
        prefix = ""
        if t.track_no:
            prefix = f"{t.track_no:02d} "
            if t.disc_no and t.disc_no > 1:
                prefix = f"{t.disc_no}-{prefix}"
        name = safe_component(prefix + t.title, "Untitled", max_bytes=150)
        return f"{user_dir}/{safe_component(artist, 'Unknown Artist')}/{album}/{name}{ext}"
    return f"{user_dir}/Unsorted/{original_stem(filename)}{ext}"


def _inside(root: Path, p: Path) -> bool:
    root = root.resolve()
    try:
        p.resolve().relative_to(root)
    except ValueError:
        return False
    return p.resolve() != root


def upload_track_id(rel: str) -> str:
    return hashlib.sha1(f"{UPLOADS_PREFIX}{rel}".encode()).hexdigest()


# ---------------------------------------------------------------- sessions


def _root(cfg: Config) -> Path:
    if cfg.upload_root is None:
        raise UploadError(503, "uploads are not set up on this server (upload_root)")
    root = Path(cfg.upload_root)
    if not root.is_dir():
        raise UploadError(503, "the upload folder is not mounted")
    return root


def part_path(cfg: Config, session_id: str) -> Path:
    return _root(cfg) / INCOMING / f"{session_id}.part"


def session_api(row: sqlite3.Row, conn: sqlite3.Connection | None = None) -> dict:
    out = {"id": row["id"], "filename": row["filename"], "bytes": row["bytes"], "offset": row["received"],
           "visibility": row["visibility"], "status": row["status"], "error": row["error"],
           "createdAt": row["created_at"], "updatedAt": row["updated_at"], "upload": None}
    if conn is not None and row["upload_id"]:
        u = conn.execute("SELECT * FROM uploads WHERE id = ?", (row["upload_id"],)).fetchone()
        out["upload"] = upload_api(conn, u) if u else None
    return out


def create(conn: sqlite3.Connection, cfg: Config, user_id: str, filename: str, size: int,
           visibility: str = "everyone") -> dict:
    root = _root(cfg)
    acc = access(conn, cfg, user_id)
    if not acc.allowed:
        raise UploadError(403, "you are not allowed to upload; ask an admin")
    ext = os.path.splitext(re.split(r"[/\\]", filename or "")[-1])[1].lower()
    if ext not in ALLOWED_NAME_EXTS:
        raise UploadError(415, f"unsupported file type {ext or '(none)'}; allowed: FLAC, MP3, M4A/AAC, Ogg/Opus, WAV")
    if size <= 0:
        raise UploadError(422, "empty file")
    if size > max_file_bytes(cfg):
        raise UploadError(413, f"file is larger than the {cfg.upload_max_file_mb} MB limit")
    if acc.quota_bytes and acc.used_bytes + size > acc.quota_bytes:
        raise UploadError(413, "upload quota exceeded")
    n = conn.execute("SELECT COUNT(*) FROM upload_sessions WHERE user_id = ? AND status = 'uploading'",
                     (user_id,)).fetchone()[0]
    if n >= MAX_SESSIONS_PER_USER:
        raise UploadError(429, "too many unfinished uploads; finish or cancel some first")
    if visibility not in ("everyone", "private"):
        raise UploadError(422, "visibility must be everyone or private")
    sid = uuid.uuid4().hex
    (root / INCOMING).mkdir(exist_ok=True)
    part_path(cfg, sid).touch()
    now = now_iso()
    conn.execute("INSERT INTO upload_sessions (id, user_id, filename, bytes, received, visibility, status, "
                 "created_at, updated_at) VALUES (?, ?, ?, ?, 0, ?, 'uploading', ?, ?)",
                 (sid, user_id, safe_component(re.split(r"[/\\]", filename)[-1], "upload", 255), size, visibility,
                  now, now))
    return get_session(conn, user_id, sid)


def _session(conn: sqlite3.Connection, user_id: str, session_id: str, is_admin: bool = False) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM upload_sessions WHERE id = ?", (session_id,)).fetchone()
    if r is None or (r["user_id"] != user_id and not is_admin):
        raise UploadError(404, "no such upload")
    return r


def get_session(conn: sqlite3.Connection, user_id: str, session_id: str) -> dict:
    return session_api(_session(conn, user_id, session_id), conn)


def _fail(conn: sqlite3.Connection, cfg: Config, row: sqlite3.Row, msg: str) -> None:
    try:
        part_path(cfg, row["id"]).unlink(missing_ok=True)
    except UploadError:
        pass
    conn.execute("UPDATE upload_sessions SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                 (msg, now_iso(), row["id"]))


def append(conn: sqlite3.Connection, cfg: Config, user_id: str, session_id: str, offset: int, data: bytes,
           username: str) -> dict:
    """Write `data` at `offset`. Returns the session (status 'done' with its upload once complete)."""
    _root(cfg)
    with _lock(session_id):
        row = _session(conn, user_id, session_id)
        if row["status"] != "uploading":
            if row["status"] == "done" and offset == row["bytes"] and not data:
                return session_api(row, conn)  # a client re-asking after a lost reply
            raise UploadError(409, f"upload is {row['status']}", offset=row["received"])
        part = part_path(cfg, session_id)
        try:
            have = part.stat().st_size
        except FileNotFoundError:
            have = 0
            part.touch()
        if have != row["received"]:  # a write that died half way: the file is the truth
            conn.execute("UPDATE upload_sessions SET received = ? WHERE id = ?", (have, session_id))
        if offset != have:
            raise UploadError(409, "offset mismatch", offset=have)
        if len(data) > MAX_CHUNK:
            raise UploadError(413, f"chunk larger than {MAX_CHUNK} bytes")
        if offset + len(data) > row["bytes"]:
            raise UploadError(413, "more data than the announced size", offset=have)
        if offset == 0 and (len(data) >= 16 or len(data) == row["bytes"]) and data:
            if sniff(data[:64 * 1024]) is None:
                _fail(conn, cfg, row, "not an audio file")
                raise UploadError(415, "not an audio file (FLAC, MP3, M4A/AAC, Ogg/Opus or WAV)")
        if data:
            with open(part, "r+b") as f:
                f.seek(offset)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        received = offset + len(data)
        conn.execute("UPDATE upload_sessions SET received = ?, updated_at = ? WHERE id = ?",
                     (received, now_iso(), session_id))
        if received == row["bytes"]:
            _finish(conn, cfg, _session(conn, user_id, session_id), username)
        return get_session(conn, user_id, session_id)


def _place(conn: sqlite3.Connection, cfg: Config, root: Path, probe: Path, row: sqlite3.Row, t, username: str,
           ext: str) -> Path:
    """Move the finished file to a free name under the uploader's folder (never over an existing file)."""
    rel = target_relpath(username, row["user_id"], t, row["filename"], ext)
    user_dir = root / rel.split("/", 1)[0]
    dest = root / rel
    stem, n = dest.stem, 2
    while dest.exists() or conn.execute("SELECT 1 FROM uploads WHERE path = ?",
                                        (dest.relative_to(root).as_posix(),)).fetchone():
        dest = dest.with_name(f"{stem} ({n}){ext}")
        n += 1
    if not _inside(user_dir, dest) or not _inside(root, dest):
        probe.unlink(missing_ok=True)
        _fail(conn, cfg, row, "unsafe file name")
        raise UploadError(422, "unsafe file name")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not _inside(root, dest.parent / "x"):  # a symlinked folder pointing elsewhere
        probe.unlink(missing_ok=True)
        _fail(conn, cfg, row, "unsafe file name")
        raise UploadError(422, "unsafe file name")
    os.replace(probe, dest)
    try:
        os.chmod(dest, 0o664)
    except OSError:
        pass
    return dest


def _finish(conn: sqlite3.Connection, cfg: Config, row: sqlite3.Row, username: str) -> None:
    from homebeat.library import tags

    root = _root(cfg)
    part = part_path(cfg, row["id"])
    fmt = sniff(_read_head(part))
    if fmt is None:
        _fail(conn, cfg, row, "not an audio file")
        raise UploadError(415, "not an audio file (FLAC, MP3, M4A/AAC, Ogg/Opus or WAV)")
    ext = EXTENSIONS[fmt]
    # mutagen picks its parser from content and name: give it the sniffed extension.
    probe = part.with_suffix(ext)
    os.replace(part, probe)
    try:
        t, mf = tags.read(probe)
        if mf is None or not t.duration_s or t.duration_s <= 0:
            raise ValueError("no audio stream")
    except Exception as e:  # mutagen raises many types on junk
        probe.unlink(missing_ok=True)
        _fail(conn, cfg, row, f"not a readable {fmt.upper()} file")
        raise UploadError(415, f"not a readable {fmt.upper()} file: {e}") from e
    with _place_lock:
        dest = _place(conn, cfg, root, probe, row, t, username, ext)
    rel = dest.relative_to(root).as_posix()
    uid = uuid.uuid4().hex
    conn.execute("INSERT INTO uploads (id, user_id, path, track_id, bytes, original_name, format, visibility, "
                 "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (uid, row["user_id"], rel, upload_track_id(rel), row["bytes"], row["filename"], fmt,
                  row["visibility"], now_iso()))
    conn.execute("UPDATE upload_sessions SET status = 'done', upload_id = ?, updated_at = ? WHERE id = ?",
                 (uid, now_iso(), row["id"]))
    log.info("upload %s stored as %s", row["filename"], rel)


def cancel(conn: sqlite3.Connection, cfg: Config, user_id: str, session_id: str, is_admin: bool = False) -> None:
    with _lock(session_id):
        row = _session(conn, user_id, session_id, is_admin)
        if cfg.upload_root is not None:
            (Path(cfg.upload_root) / INCOMING / f"{row['id']}.part").unlink(missing_ok=True)
        conn.execute("DELETE FROM upload_sessions WHERE id = ?", (session_id,))


def cleanup(conn: sqlite3.Connection, cfg: Config, now: datetime | None = None) -> dict:
    """Drop unfinished uploads without progress for a week (and their partial files) and old done/failed rows."""
    now = now or datetime.now(UTC)
    stale = iso(now - STALE_SESSION)
    finished = iso(now - FINISHED_SESSION)
    rows = conn.execute("SELECT id FROM upload_sessions WHERE (status = 'uploading' AND updated_at < ?) "
                        "OR (status != 'uploading' AND updated_at < ?)", (stale, finished)).fetchall()
    for r in rows:
        if cfg.upload_root is not None:
            (Path(cfg.upload_root) / INCOMING / f"{r['id']}.part").unlink(missing_ok=True)
        conn.execute("DELETE FROM upload_sessions WHERE id = ?", (r["id"],))
    return {"uploadSessionsDropped": len(rows)}


# ---------------------------------------------------------------- finished uploads


def upload_api(conn: sqlite3.Connection, r: sqlite3.Row) -> dict:
    t = conn.execute("SELECT t.title, t.missing, t.duration_s, ar.name AS artist, al.title AS album "
                     "FROM tracks t LEFT JOIN artists ar ON ar.id = t.artist_id "
                     "LEFT JOIN albums al ON al.id = t.album_id WHERE t.id = ?", (r["track_id"],)).fetchone()
    owner = conn.execute("SELECT username FROM users WHERE id = ?", (r["user_id"],)).fetchone() if r["user_id"] else None
    return {"id": r["id"], "userId": r["user_id"], "username": owner["username"] if owner else None,
            "path": r["path"], "trackId": r["track_id"], "bytes": r["bytes"], "originalName": r["original_name"],
            "format": r["format"], "visibility": r["visibility"], "createdAt": r["created_at"],
            "indexed": bool(t is not None and t["missing"] == 0),
            "title": t["title"] if t else None, "artist": t["artist"] if t else None,
            "album": t["album"] if t else None, "durationS": t["duration_s"] if t else None}


def list_mine(conn: sqlite3.Connection, cfg: Config, user_id: str, all_users: bool = False) -> dict:
    q = "SELECT * FROM uploads" + ("" if all_users else " WHERE user_id = ?") + " ORDER BY created_at DESC"
    files = [upload_api(conn, r) for r in conn.execute(q, () if all_users else (user_id,))]
    sessions = [session_api(r, conn) for r in conn.execute(
        "SELECT * FROM upload_sessions WHERE user_id = ? ORDER BY created_at DESC", (user_id,))]
    return {**access(conn, cfg, user_id).as_api(cfg), "files": files, "sessions": sessions}


def _upload_row(conn: sqlite3.Connection, user_id: str, upload_id: str, is_admin: bool) -> sqlite3.Row:
    r = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    if r is None or (r["user_id"] != user_id and not is_admin):
        raise UploadError(404, "no such upload")
    return r


def set_visibility(conn: sqlite3.Connection, user_id: str, upload_id: str, visibility: str,
                   is_admin: bool = False) -> dict:
    if visibility not in ("everyone", "private"):
        raise UploadError(422, "visibility must be everyone or private")
    r = _upload_row(conn, user_id, upload_id, is_admin)
    conn.execute("UPDATE uploads SET visibility = ? WHERE id = ?", (visibility, r["id"]))
    return upload_api(conn, _upload_row(conn, user_id, upload_id, is_admin))


def delete(conn: sqlite3.Connection, cfg: Config, user_id: str, upload_id: str, is_admin: bool = False) -> dict:
    """Delete an uploaded file: only ever a file under upload_root that `uploads` records, never the library."""
    r = _upload_row(conn, user_id, upload_id, is_admin)
    root = _root(cfg)
    rel = PurePosixPath(r["path"])
    if rel.is_absolute() or ".." in rel.parts:
        raise UploadError(422, "unsafe path")
    target = root / rel
    lib = Path(os.fspath(cfg.library_root)).resolve()
    if not _inside(root, target) or target.resolve() == lib or lib in target.resolve().parents:
        raise UploadError(422, "unsafe path")
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise UploadError(422, "not a regular file")
    target.unlink(missing_ok=True)
    # Remove folders left empty, up to (not including) upload_root itself.
    d = target.parent
    while d != root and _inside(root, d):
        try:
            d.rmdir()
        except OSError:
            break
        d = d.parent
    conn.execute("DELETE FROM uploads WHERE id = ?", (r["id"],))
    conn.execute("UPDATE tracks SET missing = 1 WHERE id = ? AND path = ?", (r["track_id"], UPLOADS_PREFIX + r["path"]))
    return {"deleted": r["id"], "trackId": r["track_id"]}


def upload_for_track(conn: sqlite3.Connection, track_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM uploads WHERE track_id = ?", (track_id,)).fetchone()
