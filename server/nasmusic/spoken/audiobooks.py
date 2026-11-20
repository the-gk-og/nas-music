"""Audiobooks from a separate read-only folder (config `audiobooks_root`). Never written to.

A folder that directly holds audio files is one book; its files (natural sort) are the parts. An audio file
lying directly in the root is a book of its own. Chapters come from the files (ffprobe: m4b/mp4 chapters,
ID3 CHAP, Vorbis CHAPTERxx); a multi-part book without embedded chapters gets one chapter per part. Book
positions are book-wide seconds; `audiobook_files.start_s` maps them to (part, offset)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
from pathlib import Path

from mutagen.mp4 import MP4

from nasmusic.core import now_iso
from nasmusic.library import art
from nasmusic.library import tags as tagmod
from nasmusic.spoken import covers

log = logging.getLogger(__name__)
AUDIO_EXTS = {".m4b", ".m4a", ".mp3", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".flac", ".wav"}
MEDIA_TYPES = {".m4b": "audio/mp4", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".mp3": "audio/mpeg",
               ".aac": "audio/aac", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
               ".flac": "audio/flac", ".wav": "audio/wav"}
_DIGITS = re.compile(r"(\d+)")


def natural_key(s: str):
    return [int(p) if p.isdigit() else p.lower() for p in _DIGITS.split(s)]


def media_type(path: str) -> str:
    return MEDIA_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def probe_chapters(path: Path) -> list[tuple[float, str]]:
    """Embedded chapters as (start seconds, title) via ffprobe; [] when none or ffprobe is missing."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_chapters", str(path)],
                             capture_output=True, timeout=60, check=False)
        data = json.loads(out.stdout or b"{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    res = []
    for i, ch in enumerate(data.get("chapters") or []):
        try:
            start = float(ch.get("start_time"))
        except (TypeError, ValueError):
            continue
        title = ((ch.get("tags") or {}).get("title") or "").strip() or f"Chapter {i + 1}"
        res.append((max(0.0, start), title[:500]))
    res.sort()
    return res


def book_id(folder: str) -> str:
    return hashlib.sha1(folder.encode()).hexdigest()[:16]


def image_key(bid: str) -> str:
    return f"book_{bid}"


def _find_books(root: Path) -> dict[str, list[Path]]:
    """folder (relative; a loose file's own relative path) -> its audio files."""
    books: dict[str, list[Path]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        audio = sorted((f for f in filenames if not f.startswith(".") and
                        os.path.splitext(f)[1].lower() in AUDIO_EXTS), key=natural_key)
        if not audio:
            continue
        d = Path(dirpath)
        if d == root:
            for f in audio:
                books[f] = [d / f]
        else:
            books[str(d.relative_to(root))] = [d / f for f in audio]
    return books


def _signature(files: list[Path]) -> str:
    h = hashlib.sha1()
    for f in files:
        st = f.stat()
        h.update(f"{f.name}\x1f{st.st_size}\x1f{int(st.st_mtime)}\n".encode())
    d = files[0].parent
    try:
        names = os.listdir(d)
    except OSError:
        names = []
    h.update((art.pick_folder_image(names) or "").encode())
    return h.hexdigest()


def _mp4_text(f, key: str) -> str | None:
    if isinstance(f, MP4) and f.tags is not None and f.tags.get(key):
        v = f.tags.get(key)[0]
        return str(v).strip() or None
    return None


def _id3_text(f, key: str) -> str | None:
    tags = getattr(f, "tags", None)
    if tags is None or not hasattr(tags, "getall"):
        return None
    for fr in tags.getall(key):
        if getattr(fr, "text", None):
            return str(fr.text[0]).strip() or None
    return None


def _read_book(root: Path, folder: str, files: list[Path]) -> dict:
    parts = []
    first_tags, first_f = None, None
    t = 0.0
    for i, p in enumerate(files):
        try:
            tg, f = tagmod.read(p)
        except Exception as e:  # unreadable part: keep it with zero length
            log.warning("audiobook part %s unreadable: %s", p, e)
            tg, f = tagmod.TrackTags(), None
        if i == 0:
            first_tags, first_f = tg, f
        parts.append({"path": str(p.relative_to(root)), "duration": tg.duration_s, "start": t,
                      "bytes": p.stat().st_size, "title": tg.title})
        t += tg.duration_s
    single = len(files) == 1
    name = Path(folder).stem if single else Path(folder).name
    tg = first_tags or tagmod.TrackTags()
    title = (tg.album or (tg.title if single else None) or name).strip()
    author = tg.albumartist or tg.artist
    narrator = (_mp4_text(first_f, "\xa9nrt") or _mp4_text(first_f, "\xa9nar") or _id3_text(first_f, "TXXX:NARRATOR")
                or tg.composer)
    desc = _mp4_text(first_f, "desc") or _mp4_text(first_f, "ldes") or tg.comment
    # chapters: embedded, offset by each part's start; else one per part
    chapters: list[tuple[float, str]] = []
    for prt in parts:
        for s, ttl in probe_chapters(root / prt["path"]):
            chapters.append((prt["start"] + s, ttl))
    if not chapters:
        if single:
            chapters = [(0.0, title)]
        else:
            chapters = [(prt["start"], prt["title"] or Path(prt["path"]).stem) for prt in parts]
    return {"title": title, "author": author, "narrator": narrator, "description": desc, "year": tg.year,
            "duration": t, "parts": parts, "chapters": chapters, "first": first_f}


def scan(conn: sqlite3.Connection, root: Path | None, data_dir: Path) -> dict:
    """The `audiobooks` job: index new/changed books, mark vanished ones missing (rows are kept so positions
    and bookmarks survive a temporarily unmounted share)."""
    if root is None:
        return {"skipped": "audiobooks_root is not set"}
    if not root.is_dir():
        return {"skipped": f"audiobooks_root {root} is not a readable folder"}
    found = _find_books(root)
    known = {r["folder"]: r for r in conn.execute("SELECT id, folder, signature, missing FROM audiobooks")}
    added = updated = unchanged = 0
    for folder, files in found.items():
        try:
            sig = _signature(files)
        except OSError as e:
            log.warning("audiobook %s: %s", folder, e)
            continue
        row = known.get(folder)
        if row is not None and row["signature"] == sig and not row["missing"]:
            unchanged += 1
            continue
        bid = book_id(folder)
        info = _read_book(root, folder, files)
        names = os.listdir(files[0].parent)
        img = art.pick_folder_image(names) if len(files) > 1 or files[0].parent != root else None
        cover_rel = str((files[0].parent / img).relative_to(root)) if img else None
        if cover_rel is None:
            pic = tagmod.embedded_picture(info["first"])
            if pic:
                covers.store(data_dir, image_key(bid), pic)
        else:
            covers.source(data_dir, image_key(bid)).unlink(missing_ok=True)
            art.forget_resized(data_dir, image_key(bid))
        now = now_iso()
        conn.execute("BEGIN")
        try:
            conn.execute(
                "INSERT INTO audiobooks (id, folder, title, author, narrator, description, year, duration_s, cover_path, "
                "signature, missing, added_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET title = excluded.title, author = excluded.author, "
                "narrator = excluded.narrator, description = excluded.description, year = excluded.year, "
                "duration_s = excluded.duration_s, cover_path = excluded.cover_path, signature = excluded.signature, "
                "missing = 0, updated_at = excluded.updated_at",
                (bid, folder, info["title"], info["author"], info["narrator"], info["description"], info["year"],
                 info["duration"], cover_rel, sig, now, now))
            conn.execute("DELETE FROM audiobook_files WHERE book_id = ?", (bid,))
            conn.execute("DELETE FROM audiobook_chapters WHERE book_id = ?", (bid,))
            conn.executemany("INSERT INTO audiobook_files (book_id, idx, path, duration_s, start_s, bytes) "
                             "VALUES (?, ?, ?, ?, ?, ?)",
                             [(bid, i, p["path"], p["duration"], p["start"], p["bytes"])
                              for i, p in enumerate(info["parts"])])
            conn.executemany("INSERT INTO audiobook_chapters (book_id, idx, title, start_s) VALUES (?, ?, ?, ?)",
                             [(bid, i, ttl, s) for i, (s, ttl) in enumerate(info["chapters"])])
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if row is None:
            added += 1
        else:
            updated += 1
    gone = [f for f, r in known.items() if f not in found and not r["missing"]]
    for f in gone:
        conn.execute("UPDATE audiobooks SET missing = 1, updated_at = ? WHERE folder = ?", (now_iso(), f))
    return {"books": len(found), "added": added, "updated": updated, "unchanged": unchanged, "missing": len(gone)}


# ---------- API shapes ----------


def book_api(r, prog: dict | None = None) -> dict:
    return {"id": r["id"], "title": r["title"], "author": r["author"], "narrator": r["narrator"],
            "year": r["year"], "durationS": r["duration_s"], "addedAt": r["added_at"], "progress": prog,
            "summary": (r["description"] or "")[:300] or None}


def detail(conn: sqlite3.Connection, uid: str, bid: str, prog: dict | None) -> dict | None:
    r = conn.execute("SELECT * FROM audiobooks WHERE id = ? AND missing = 0", (bid,)).fetchone()
    if r is None:
        return None
    out = book_api(r, prog)
    out["description"] = r["description"]
    out["parts"] = [{"index": f["idx"], "durationS": f["duration_s"], "startS": f["start_s"], "sizeBytes": f["bytes"],
                     "contentType": media_type(f["path"]), "name": Path(f["path"]).name}
                    for f in conn.execute("SELECT * FROM audiobook_files WHERE book_id = ? ORDER BY idx", (bid,))]
    out["chapters"] = [{"index": c["idx"], "title": c["title"], "startS": c["start_s"]}
                       for c in conn.execute("SELECT * FROM audiobook_chapters WHERE book_id = ? ORDER BY idx", (bid,))]
    out["bookmarks"] = bookmarks(conn, uid, bid)
    return out


def bookmarks(conn: sqlite3.Connection, uid: str, bid: str) -> list[dict]:
    return [{"id": b["id"], "positionS": b["position_s"], "note": b["note"], "createdAt": b["created_at"]}
            for b in conn.execute("SELECT * FROM audiobook_bookmarks WHERE user_id = ? AND book_id = ? "
                                  "ORDER BY position_s", (uid, bid))]


def cover_source(root: Path | None, data_dir: Path, r) -> Path | None:
    if r["cover_path"] and root is not None:
        p = root / r["cover_path"]
        return p if p.is_file() else None
    p = covers.source(data_dir, image_key(r["id"]))
    return p if p.exists() else None
