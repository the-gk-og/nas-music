"""Incremental library scanner.

Walks library_root (and upload_root, the writable second root: its files get paths '@uploads/...'),
re-reads tags only for files whose (mtime, size) changed, groups tracks into
albums by album artist, marks vanished files `missing`, and keeps album art in sync.
IDs (see schema.sql): track = sha1(relative path); artist = 'mb:'+MBID or sha1(normalised name);
album = sha1(album artist id + normalised title).
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from homebeat.config import UPLOADS_PREFIX, Config
from homebeat.core import now_iso
from homebeat.library import art, classical, overrides, tags

log = logging.getLogger(__name__)

_scan_lock = threading.Lock()

UNKNOWN_ARTIST = "Unknown Artist"
UNKNOWN_ALBUM = "Unknown Album"
UPLOADS_DIR = UPLOADS_PREFIX.rstrip("/")


@dataclass
class ScanResult:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    missing: int = 0
    restored: int = 0
    errors: int = 0
    duration_s: float = 0.0
    error_paths: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["error_paths"] = d["error_paths"][:20]
        return d


def normalise(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", s).casefold().split())


def sha1(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def track_id(relpath: str) -> str:
    return sha1(relpath)


def artist_id(name: str, mbid: str | None) -> str:
    return f"mb:{mbid.lower()}" if mbid else sha1(normalise(name))


def album_id(album_artist_id: str, title: str) -> str:
    return sha1(f"{album_artist_id}\x00{normalise(title)}")


def sort_name(name: str) -> str:
    n = name.strip()
    low = n.lower()
    for article in ("the ", "a "):
        if low.startswith(article) and len(n) > len(article):
            return n[len(article):].strip()
    return n


@dataclass
class _File:
    rel: str
    abs: Path
    mtime: float
    size: int


def _walk(root: Path, prefix_dir: str = "") -> tuple[list[_File], dict[str, list[str]]]:
    """Return audio files and, per directory (relative posix path), the image file names in it.
    `prefix_dir` ("@uploads" for upload_root) is put in front of every relative path. In the main library a
    top-level folder named like that prefix is skipped, so its ids can never collide with uploads."""
    files: list[_File] = []
    images: dict[str, list[str]] = {}
    root = Path(os.fspath(root))
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError as e:
            log.warning("cannot list %s: %s", d, e)
            continue
        rel_dir = PurePosixPath(Path(d).relative_to(root)).as_posix() if d != root else ""
        if prefix_dir:
            rel_dir = f"{prefix_dir}/{rel_dir}" if rel_dir else prefix_dir
        with it:
            for entry in it:
                name = entry.name
                if name.startswith("."):
                    continue
                if not prefix_dir and d == root and name == UPLOADS_DIR:
                    log.warning("skipping %s: that folder name is reserved for uploads", entry.path)
                    continue
                try:
                    if entry.is_dir(follow_symlinks=True):
                        stack.append(Path(entry.path))
                        continue
                    ext = os.path.splitext(name)[1].lower()
                    if ext in tags.AUDIO_EXTS:
                        st = entry.stat(follow_symlinks=True)
                        rel = f"{rel_dir}/{name}" if rel_dir else name
                        try:
                            rel.encode("utf-8")
                        except UnicodeEncodeError:
                            log.warning("skipping non-UTF-8 path %r", entry.path)
                            continue
                        files.append(_File(rel, Path(entry.path), st.st_mtime, st.st_size))
                    elif ext in art.IMAGE_EXTS:
                        images.setdefault(rel_dir, []).append(name)
                except OSError as e:
                    log.warning("cannot stat %s: %s", entry.path, e)
    return files, images


def _folder_art(rel_dir: str, images: dict[str, list[str]]) -> str | None:
    """Folder image for a track directory; for multi-disc layouts (Album/CD1) also try the parent."""
    candidates = [rel_dir]
    if rel_dir:
        parent = PurePosixPath(rel_dir).parent.as_posix()
        candidates.append("" if parent == "." else parent)
    for d in candidates:
        pick = art.pick_folder_image(images.get(d, []))
        if pick:
            return f"{d}/{pick}" if d else pick
    return None


def upsert_names(conn: sqlite3.Connection, rel: str, t: tags.TrackTags, now: str) -> tuple[str, str]:
    """Create/update the artist and album rows the (effective) tags `t` of file `rel` belong to.
    Returns (track artist id, album id)."""
    rel_dir = PurePosixPath(rel).parent.as_posix()
    track_artist = t.artist or t.albumartist or UNKNOWN_ARTIST
    album_artist = t.albumartist or t.artist or UNKNOWN_ARTIST
    # MBIDs only apply when they belong to the name we use.
    aa_mbid = t.mb_albumartist or (t.mb_artist if not t.albumartist else None)
    aa_id = artist_id(album_artist, aa_mbid)
    ta_id = artist_id(track_artist, t.mb_artist if t.artist else aa_mbid)
    album_title = t.album or (PurePosixPath(rel_dir).name if rel_dir not in ("", ".") else UNKNOWN_ALBUM)
    al_id = album_id(aa_id, album_title)
    for aid, name, mbid in ((aa_id, album_artist, aa_mbid), (ta_id, track_artist, None)):
        conn.execute(
            "INSERT INTO artists (id, name, sort_name, mbid) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = excluded.name, sort_name = excluded.sort_name, "
            "mbid = COALESCE(excluded.mbid, artists.mbid)",
            (aid, name, sort_name(name), mbid or (aid[3:] if aid.startswith("mb:") else None)),
        )
    conn.execute(
        "INSERT INTO albums (id, artist_id, title, year, mbid, art_path, added_at) "
        "VALUES (?, ?, ?, ?, ?, NULL, ?) "
        "ON CONFLICT(id) DO UPDATE SET title = excluded.title, "
        "mbid = COALESCE(excluded.mbid, albums.mbid)",
        (al_id, aa_id, album_title, t.year, t.mb_releasegroup, now),
    )
    return ta_id, al_id


def scan(cfg: Config, conn: sqlite3.Connection, full: bool = False, uploads_only: bool = False) -> ScanResult:
    """Scan the library. `full` re-reads tags of every file. `uploads_only` walks just upload_root (fast; run
    after an upload finishes). Serialised by a process-wide lock."""
    with _scan_lock:
        return _scan(cfg, conn, full, uploads_only)


def _scan(cfg: Config, conn: sqlite3.Connection, full: bool, uploads_only: bool = False) -> ScanResult:
    t0 = time.monotonic()
    res = ScanResult()
    files: list[_File] = []
    images: dict[str, list[str]] = {}
    if not uploads_only:
        root = cfg.library_root
        if not root.is_dir():
            raise FileNotFoundError(f"library_root is not a directory: {root}")
        files, images = _walk(root)
    up = cfg.upload_root
    uploads_mounted = up is not None and up.is_dir()
    if up is not None and not uploads_mounted:
        log.warning("upload_root %s is not a directory; uploaded tracks left as they are", up)
    if uploads_mounted:
        f2, im2 = _walk(up, UPLOADS_DIR)
        files += f2
        images.update(im2)

    def in_scope(path: str) -> bool:
        """Rows this scan may mark missing: uploads only when their root was walked (or none is configured)."""
        if path.startswith(UPLOADS_PREFIX):
            return uploads_mounted or up is None
        return not uploads_only

    existing = {
        r["path"]: r for r in conn.execute("SELECT id, path, mtime, flac_bytes, missing, album_id FROM tracks")
    }
    now = now_iso()
    seen: set[str] = set()
    changed: list[tuple[_File, tags.TrackTags, object]] = []
    restore_ids: list[str] = []
    for f in files:
        seen.add(f.rel)
        row = existing.get(f.rel)
        if row is not None and not full and row["mtime"] == f.mtime and row["flac_bytes"] == f.size:
            res.unchanged += 1
            if row["missing"] == 1:  # 2 = hidden duplicate, 3 = removed by an admin: stay hidden
                restore_ids.append(row["id"])
            continue
        try:
            t, mf = tags.read(f.abs)
            if mf is None:
                raise ValueError("unrecognised audio file")
        except Exception as e:  # mutagen raises many types on corrupt files
            res.errors += 1
            res.error_paths.append(f.rel)
            log.warning("cannot read tags from %s: %s", f.rel, e)
            continue
        changed.append((f, t, mf))

    touched_albums: set[str] = set()
    embedded_candidates: dict[str, bytes | None] = {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        all_overrides = overrides.load_all(conn)
        for f, t, mf in changed:
            rel_dir = PurePosixPath(f.rel).parent.as_posix()
            tid = track_id(f.rel)
            file_tags = overrides.file_tags_json(t)
            t = overrides.apply(t, all_overrides.get(tid))
            ta_id, al_id = upsert_names(conn, f.rel, t, now)
            mv = classical.parse(t.title, t.composer, t.work, t.movement, t.movement_no)
            prev = existing.get(f.rel)
            conn.execute(
                "INSERT INTO tracks (id, path, album_id, artist_id, title, track_no, disc_no, year, genre, "
                "duration_s, flac_bytes, mtime, mbid, added_at, missing, composer, lyricist, comment, album_artist, "
                "codec, bitrate, sample_rate, bits_per_sample, channels, folder, file_tags, "
                "rg_track_gain, rg_track_peak, rg_album_gain, rg_album_peak, rg_source, loudness_lufs, "
                "work, movement, movement_no) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET album_id = excluded.album_id, artist_id = excluded.artist_id, "
                "title = excluded.title, track_no = excluded.track_no, disc_no = excluded.disc_no, "
                "year = excluded.year, genre = excluded.genre, duration_s = excluded.duration_s, "
                "flac_bytes = excluded.flac_bytes, mtime = excluded.mtime, mbid = excluded.mbid, "
                "missing = CASE WHEN tracks.missing IN (2, 3) THEN tracks.missing ELSE 0 END, "
                "composer = excluded.composer, lyricist = excluded.lyricist, comment = excluded.comment, "
                "album_artist = excluded.album_artist, codec = excluded.codec, bitrate = excluded.bitrate, "
                "sample_rate = excluded.sample_rate, bits_per_sample = excluded.bits_per_sample, "
                "channels = excluded.channels, folder = excluded.folder, file_tags = excluded.file_tags, "
                # A changed file is re-levelled: gain tags if it has them, else measured again by the loudness job.
                "rg_track_gain = excluded.rg_track_gain, rg_track_peak = excluded.rg_track_peak, "
                "rg_album_gain = excluded.rg_album_gain, rg_album_peak = excluded.rg_album_peak, "
                "rg_source = excluded.rg_source, loudness_lufs = excluded.loudness_lufs, "
                "work = excluded.work, movement = excluded.movement, movement_no = excluded.movement_no",
                (tid, f.rel, al_id, ta_id, t.title or Path(f.rel).stem, t.track_no, t.disc_no, t.year,
                 t.genre, t.duration_s, f.size, f.mtime, t.mb_track, now, t.composer, t.lyricist, t.comment,
                 t.albumartist, t.codec, t.bitrate, t.sample_rate, t.bits_per_sample, t.channels,
                 "" if rel_dir in ("", ".") else rel_dir, file_tags, *_gain_values(t),
                 mv.work, mv.movement, mv.movement_no),
            )
            if prev is None:
                res.added += 1
            else:
                res.updated += 1
                if prev["album_id"] != al_id:
                    touched_albums.add(prev["album_id"])
            touched_albums.add(al_id)
            if al_id not in embedded_candidates or embedded_candidates[al_id] is None:
                embedded_candidates[al_id] = tags.embedded_picture(mf)

        for tid in restore_ids:
            conn.execute("UPDATE tracks SET missing = 0 WHERE id = ?", (tid,))
        res.restored = len(restore_ids)

        gone = [r["id"] for p, r in existing.items() if p not in seen and not r["missing"] and in_scope(p)]
        for tid in gone:
            conn.execute("UPDATE tracks SET missing = 1 WHERE id = ?", (tid,))
        res.missing = len(gone)

        _sync_albums(cfg, conn, images, touched_albums, embedded_candidates, uploads_only)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    res.duration_s = round(time.monotonic() - t0, 3)
    return res


def _gain_values(t: tags.TrackTags) -> tuple:
    """(track gain, track peak, album gain, album peak, source, loudness) from tags, or all None to measure."""
    if t.rg_track_gain is None:
        return (None, None, None, None, None, None)
    from homebeat.library.loudness import REFERENCE_LUFS

    return (t.rg_track_gain, t.rg_track_peak, t.rg_album_gain, t.rg_album_peak, "tags",
            round(REFERENCE_LUFS - t.rg_track_gain, 2))


def _sync_albums(cfg: Config, conn: sqlite3.Connection, images: dict[str, list[str]],
                 touched: set[str], embedded: dict[str, bytes | None], uploads_only: bool = False) -> None:
    # Drop albums/artists nothing references any more (never-seen-again tag typos etc.).
    conn.execute("DELETE FROM albums WHERE id NOT IN (SELECT DISTINCT album_id FROM tracks)")
    conn.execute(
        "DELETE FROM artists WHERE id NOT IN (SELECT DISTINCT artist_id FROM albums) "
        "AND id NOT IN (SELECT DISTINCT artist_id FROM tracks)"
    )
    conn.execute(
        "UPDATE albums SET year = (SELECT MIN(year) FROM tracks t WHERE t.album_id = albums.id AND t.missing = 0) "
        "WHERE id IN (SELECT DISTINCT album_id FROM tracks WHERE missing = 0)"
    )
    # Album art: folder image from the album's first track directory, else embedded.
    first_dir: dict[str, str] = {}
    for r in conn.execute(
        "SELECT album_id, path FROM tracks WHERE missing = 0 ORDER BY album_id, disc_no, track_no, path"
    ):
        first_dir.setdefault(r["album_id"], PurePosixPath(r["path"]).parent.as_posix())
    current = {r["id"]: r["art_path"] for r in conn.execute("SELECT id, art_path FROM albums")}
    covers = overrides.album_covers(conn)
    for al_id, d in first_dir.items():
        if uploads_only and not (d == UPLOADS_DIR or d.startswith(UPLOADS_PREFIX)):
            continue  # `images` only knows the upload folders in this scan: leave library albums alone
        want = covers.get(al_id) or _folder_art("" if d == "." else d, images)
        if want != current.get(al_id):
            conn.execute("UPDATE albums SET art_path = ? WHERE id = ?", (want, al_id))
            if current.get(al_id) and current[al_id].startswith(art.UPLOAD_PREFIX):
                art.forget_resized(cfg.data_dir, al_id)  # back from an uploaded cover to an older file
        if want:
            continue
        if al_id in touched or not art.embedded_state_known(cfg.data_dir, al_id):
            data = embedded.get(al_id)
            if data is None and al_id not in embedded:
                data = _embedded_from_first_track(cfg, conn, al_id)
            art.store_embedded(cfg.data_dir, al_id, data)


def _embedded_from_first_track(cfg: Config, conn: sqlite3.Connection, al_id: str) -> bytes | None:
    row = conn.execute(
        "SELECT path FROM tracks WHERE album_id = ? AND missing = 0 ORDER BY disc_no, track_no LIMIT 1", (al_id,)
    ).fetchone()
    if row is None:
        return None
    try:
        _, mf = tags.read(cfg.library_root / row["path"])
    except Exception:
        return None
    return tags.embedded_picture(mf)
