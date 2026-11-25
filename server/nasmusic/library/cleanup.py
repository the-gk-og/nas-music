"""Library cleanup for the admin console (schema v11 `library_removed`): reports of songs worth pruning, removal
into a trash, restore, and purge.

Removing a track sets tracks.missing = 3: it disappears from every listing at once (like a hidden duplicate)
but playlists, favourites and history are left alone, so a restore puts everything back. After `trash_days`
(or when an admin empties the trash early) the nightly cleanup job purges it:
- uploaded files (under upload_root, always writable) are deleted;
- library files are deleted only when config `library_delete_files` is on (the owner's opt-in; the music
  folder must then be mounted read-write). This is the only code that ever writes to the music library, and
  it deletes exactly the file the track row points at, after checking it is a regular file inside the library.
- a file that is deleted (or already gone) takes its database rows with it; one that is kept (deletion off,
  read-only mount, permission error) stays hidden for good, leaves playlists/queues/mixes, and is listed in the
  delete list (a shell script the admin can run on the NAS).
"""
from __future__ import annotations

import logging
import os
import shlex
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nasmusic.config import UPLOADS_PREFIX, Config
from nasmusic.core import iso

log = logging.getLogger(__name__)

LOSSLESS = ("FLAC", "ALAC", "WAV", "AIFF", "APE", "WAVPACK")
# Sidecar files deleted together with a song (same name, different extension).
SIDECARS = (".lrc",)
MAX_REMOVE = 50_000


class CleanupError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _chunks(ids: list[str], n: int = 500):
    for i in range(0, len(ids), n):
        yield ids[i:i + n]


# ---------- reports ----------

# id: (title, description, kind, default threshold, unit)
REPORTS: dict[str, tuple[str, str, str, int | None, str | None]] = {
    "never_played": ("Never played", "Songs nobody has played, added more than this many days ago.", "track", 90,
                     "days"),
    "unplayed_albums": ("Unplayed albums", "Albums nobody has played a single song from, biggest first.", "album",
                        90, "days"),
    "disliked": ("Disliked and skipped", "Songs someone gave a thumbs-down, or that get skipped far more than "
                 "played (at least this many skips).", "track", 3, "skips"),
    "low_quality": ("Low bitrate", "Lossy files (MP3, AAC, ...) below this bitrate.", "track", 128, "kbps"),
    "fake_lossless": ("Fake lossless", "Lossless files whose spectrum shows they were made from an MP3/AAC "
                      "(or upsampled from CD). Checked nightly.", "track", None, None),
    "short": ("Very short", "Songs shorter than this: intros, skits, silence, ringtones.", "track", 30, "seconds"),
}

_STATS = """LEFT JOIN (SELECT track_id, SUM(play_count) AS plays, SUM(skip_count) AS skips, SUM(blocked) AS blocked,
                  MAX(last_played) AS last_played FROM user_tracks GROUP BY track_id) s ON s.track_id = t.id"""
_TRACK_COLS = """t.id, t.title, t.path, t.album_id, t.flac_bytes, t.duration_s, t.codec, t.bitrate, t.sample_rate,
       t.bits_per_sample, t.added_at, ar.name AS artist, al.title AS album,
       COALESCE(s.plays, 0) AS plays, COALESCE(s.skips, 0) AS skips, COALESCE(s.blocked, 0) AS blocked,
       s.last_played"""
_TRACK_FROM = f"""FROM tracks t JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id
{_STATS}"""


def _track_item(r: sqlite3.Row, note: str | None = None) -> dict:
    return {"kind": "track", "id": r["id"], "title": r["title"], "artist": r["artist"], "album": r["album"],
            "albumId": r["album_id"], "path": r["path"], "bytes": r["flac_bytes"], "durationS": r["duration_s"],
            "codec": r["codec"], "bitrate": r["bitrate"], "sampleRate": r["sample_rate"],
            "bitsPerSample": r["bits_per_sample"], "plays": r["plays"], "skips": r["skips"],
            "blockedBy": r["blocked"], "lastPlayed": r["last_played"], "addedAt": r["added_at"], "note": note,
            "trackCount": 1, "trackIds": [r["id"]]}


def _cutoff(days: int) -> str:
    return iso(datetime.now(UTC) - timedelta(days=days))


def report(conn: sqlite3.Connection, rid: str, threshold: int | None = None) -> list[dict]:
    """All items of one report (present tracks only)."""
    if rid not in REPORTS:
        raise CleanupError(404, "unknown report")
    th = REPORTS[rid][3] if threshold is None else threshold
    base = f"SELECT {_TRACK_COLS} {_TRACK_FROM} WHERE t.missing = 0"
    if rid == "never_played":
        rows = conn.execute(base + " AND COALESCE(s.plays, 0) = 0 AND t.added_at < ? ORDER BY ar.name COLLATE "
                            "NOCASE, al.title COLLATE NOCASE, t.disc_no, t.track_no", (_cutoff(th),))
        return [_track_item(r) for r in rows]
    if rid == "disliked":
        rows = conn.execute(base + " AND (COALESCE(s.blocked, 0) > 0 OR (COALESCE(s.skips, 0) >= ? AND "
                            "COALESCE(s.skips, 0) > 2 * COALESCE(s.plays, 0))) ORDER BY COALESCE(s.blocked, 0) DESC, "
                            "COALESCE(s.skips, 0) - COALESCE(s.plays, 0) DESC", (th,))
        return [_track_item(r, ", ".join(x for x in (
            f"thumbs-down from {r['blocked']}" if r["blocked"] else "",
            f"skipped {r['skips']}× / played {r['plays']}×") if x)) for r in rows]
    if rid == "low_quality":
        ph = ",".join("?" * len(LOSSLESS))
        rows = conn.execute(base + f" AND UPPER(COALESCE(t.codec, '')) NOT IN ({ph}) AND t.bitrate IS NOT NULL "
                            "AND t.bitrate > 0 AND t.bitrate < ? ORDER BY t.bitrate, ar.name COLLATE NOCASE",
                            (*LOSSLESS, th))
        return [_track_item(r) for r in rows]
    if rid == "short":
        rows = conn.execute(base + " AND t.duration_s IS NOT NULL AND t.duration_s < ? ORDER BY t.duration_s", (th,))
        return [_track_item(r) for r in rows]
    if rid == "fake_lossless":
        rows = conn.execute(
            f"SELECT {_TRACK_COLS}, c.verdict, c.source_guess, c.cutoff_hz {_TRACK_FROM} "
            "JOIN lossless_checks c ON c.track_id = t.id AND c.mtime = t.mtime "
            "WHERE t.missing = 0 AND c.dismissed = 0 AND c.verdict IN ('lossy_source', 'suspect', 'upsampled') "
            "ORDER BY c.verdict, COALESCE(c.cutoff_hz, 0), ar.name COLLATE NOCASE")
        out = []
        for r in rows:
            it = _track_item(r, lossless_note(r["verdict"], r["source_guess"], r["cutoff_hz"]))
            it["verdict"] = r["verdict"]
            out.append(it)
        return out
    # unplayed_albums
    rows = conn.execute(
        "SELECT al.id, al.title, ar.name AS artist, COUNT(*) AS n, SUM(COALESCE(t.flac_bytes, 0)) AS bytes, "
        "SUM(COALESCE(t.duration_s, 0)) AS dur, MIN(t.added_at) AS added, MIN(t.folder) AS folder, "
        "GROUP_CONCAT(t.id) AS ids FROM tracks t JOIN albums al ON al.id = t.album_id "
        f"JOIN artists ar ON ar.id = al.artist_id {_STATS} WHERE t.missing = 0 GROUP BY al.id "
        "HAVING SUM(COALESCE(s.plays, 0)) = 0 AND MIN(t.added_at) < ? ORDER BY bytes DESC", (_cutoff(th),))
    return [{"kind": "album", "id": r["id"], "title": r["title"], "artist": r["artist"], "album": r["title"],
             "albumId": r["id"], "path": r["folder"], "bytes": r["bytes"], "durationS": r["dur"], "codec": None,
             "bitrate": None, "sampleRate": None, "bitsPerSample": None, "plays": 0, "skips": 0, "blockedBy": 0,
             "lastPlayed": None, "addedAt": r["added"], "note": f"{r['n']} songs", "trackCount": r["n"],
             "trackIds": r["ids"].split(",")} for r in rows]


def lossless_note(verdict: str | None, guess: str | None, cutoff: int | None) -> str | None:
    if verdict == "lossy_source":
        return f"cut off at {cutoff / 1000:.1f} kHz: made from {guess or 'a lossy file'}" if cutoff else guess
    if verdict == "suspect":
        return f"cut off at {cutoff / 1000:.1f} kHz: possibly made from {guess or 'a lossy file'}" if cutoff else guess
    if verdict == "upsampled":
        return f"nothing above {cutoff / 1000:.1f} kHz: upsampled from {guess or 'CD'}" if cutoff else guess
    return None


def reports_summary(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for rid, (title, desc, kind, th, unit) in REPORTS.items():
        items = report(conn, rid)
        out.append({"id": rid, "title": title, "description": desc, "kind": kind, "threshold": th, "unit": unit,
                    "count": len(items), "bytes": sum(i["bytes"] or 0 for i in items)})
    return out


# ---------- search (to remove anything by name) ----------


def search(conn: sqlite3.Connection, q: str, limit: int = 30) -> dict:
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    artists = [{"id": r["id"], "name": r["name"], "trackCount": r["n"], "bytes": r["b"]} for r in conn.execute(
        "SELECT ar.id, ar.name, COUNT(t.id) AS n, SUM(COALESCE(t.flac_bytes, 0)) AS b FROM artists ar "
        "JOIN tracks t ON t.artist_id = ar.id OR t.album_id IN (SELECT id FROM albums WHERE artist_id = ar.id) "
        "WHERE ar.name LIKE ? ESCAPE '\\' AND t.missing = 0 GROUP BY ar.id ORDER BY n DESC LIMIT ?", (like, limit))]
    albums = [{"id": r["id"], "title": r["title"], "artist": r["artist"], "year": r["year"], "trackCount": r["n"],
               "bytes": r["b"], "path": r["folder"]} for r in conn.execute(
        "SELECT al.id, al.title, al.year, ar.name AS artist, COUNT(t.id) AS n, SUM(COALESCE(t.flac_bytes, 0)) AS b, "
        "MIN(t.folder) AS folder FROM albums al JOIN artists ar ON ar.id = al.artist_id "
        "JOIN tracks t ON t.album_id = al.id WHERE (al.title LIKE ? ESCAPE '\\' OR ar.name LIKE ? ESCAPE '\\') "
        "AND t.missing = 0 GROUP BY al.id ORDER BY al.title COLLATE NOCASE LIMIT ?", (like, like, limit))]
    tracks = [_track_item(r) for r in conn.execute(
        f"SELECT {_TRACK_COLS} {_TRACK_FROM} WHERE t.missing = 0 AND (t.title LIKE ? ESCAPE '\\' OR "
        "t.path LIKE ? ESCAPE '\\') ORDER BY t.title COLLATE NOCASE LIMIT ?", (like, like, limit))]
    return {"artists": artists, "albums": albums, "tracks": tracks}


# ---------- remove / trash / restore ----------


def expand(conn: sqlite3.Connection, track_ids: list[str] | None = None, album_ids: list[str] | None = None,
           artist_ids: list[str] | None = None) -> list[str]:
    """Track ids (not yet removed) for the given tracks, albums and artists (an artist's albums too)."""
    out: dict[str, None] = {}
    for chunk in _chunks(list(dict.fromkeys(track_ids or []))):
        ph = ",".join("?" * len(chunk))
        out.update((r[0], None) for r in conn.execute(
            f"SELECT id FROM tracks WHERE id IN ({ph}) AND missing <> 3", chunk))
    for chunk in _chunks(list(dict.fromkeys(album_ids or []))):
        ph = ",".join("?" * len(chunk))
        out.update((r[0], None) for r in conn.execute(
            f"SELECT id FROM tracks WHERE album_id IN ({ph}) AND missing <> 3 ORDER BY disc_no, track_no", chunk))
    for chunk in _chunks(list(dict.fromkeys(artist_ids or []))):
        ph = ",".join("?" * len(chunk))
        out.update((r[0], None) for r in conn.execute(
            f"SELECT id FROM tracks WHERE missing <> 3 AND (artist_id IN ({ph}) OR album_id IN "
            f"(SELECT id FROM albums WHERE artist_id IN ({ph})))", [*chunk, *chunk]))
    return list(out)


def remove(conn: sqlite3.Connection, cfg: Config, user_id: str | None, track_ids: list[str] | None = None,
           album_ids: list[str] | None = None, artist_ids: list[str] | None = None,
           reason: str | None = None, now: datetime | None = None) -> dict:
    """Move tracks to the trash (missing = 3). Nothing is deleted until the trash is purged."""
    ids = expand(conn, track_ids, album_ids, artist_ids)
    if not ids:
        raise CleanupError(404, "nothing to remove (unknown or already removed)")
    if len(ids) > MAX_REMOVE:
        raise CleanupError(422, f"at most {MAX_REMOVE} songs at once")
    now = now or datetime.now(UTC)
    batch = uuid.uuid4().hex
    purge_after = iso(now + timedelta(days=cfg.trash_days))
    size = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for chunk in _chunks(ids):
            ph = ",".join("?" * len(chunk))
            size += conn.execute(f"SELECT COALESCE(SUM(flac_bytes), 0) FROM tracks WHERE id IN ({ph})",
                                 chunk).fetchone()[0]
            conn.executemany(
                "INSERT INTO library_removed (track_id, batch_id, reason, removed_by, removed_at, purge_after) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(track_id) DO UPDATE SET batch_id = excluded.batch_id, "
                "reason = excluded.reason, removed_by = excluded.removed_by, removed_at = excluded.removed_at, "
                "purge_after = excluded.purge_after, purged_at = NULL, purge_error = NULL",
                [(t, batch, (reason or "").strip()[:200] or None, user_id, iso(now), purge_after) for t in chunk])
            conn.execute(f"UPDATE tracks SET missing = 3 WHERE id IN ({ph})", chunk)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    log.info("cleanup: %d songs moved to the trash (batch %s, %s)", len(ids), batch, reason or "no reason")
    return {"removed": len(ids), "batchId": batch, "purgeAfter": purge_after, "bytes": size}


def restore(conn: sqlite3.Connection, batch_ids: list[str] | None = None, track_ids: list[str] | None = None) -> dict:
    """Take tracks out of the trash. A hidden duplicate goes back to hidden; a file that is gone is marked
    missing by the next scan. Purged rows whose file was kept can be restored too."""
    ids: list[str] = []
    for chunk in _chunks(list(dict.fromkeys(batch_ids or []))):
        ph = ",".join("?" * len(chunk))
        ids += [r[0] for r in conn.execute(f"SELECT track_id FROM library_removed WHERE batch_id IN ({ph})", chunk)]
    for chunk in _chunks(list(dict.fromkeys(track_ids or []))):
        ph = ",".join("?" * len(chunk))
        ids += [r[0] for r in conn.execute(f"SELECT track_id FROM library_removed WHERE track_id IN ({ph})", chunk)]
    ids = list(dict.fromkeys(ids))
    conn.execute("BEGIN IMMEDIATE")
    try:
        for chunk in _chunks(ids):
            ph = ",".join("?" * len(chunk))
            conn.execute(f"DELETE FROM library_removed WHERE track_id IN ({ph})", chunk)
            conn.execute(f"UPDATE tracks SET missing = CASE WHEN id IN (SELECT track_id FROM duplicate_hidden) "
                         f"THEN 2 ELSE 0 END WHERE missing = 3 AND id IN ({ph})", chunk)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"restored": len(ids)}


def trash(conn: sqlite3.Connection) -> list[dict]:
    """Batches in the trash (purged ones that kept their file included, flagged), newest first."""
    rows = conn.execute(
        "SELECT r.batch_id, MIN(r.removed_at) AS removed_at, MIN(r.purge_after) AS purge_after, "
        "MAX(r.reason) AS reason, MAX(u.username) AS removed_by, COUNT(*) AS n, "
        "SUM(COALESCE(t.flac_bytes, 0)) AS bytes, SUM(r.purged_at IS NOT NULL) AS purged, "
        "MAX(r.purge_error) AS purge_error FROM library_removed r JOIN tracks t ON t.id = r.track_id "
        "LEFT JOIN users u ON u.id = r.removed_by GROUP BY r.batch_id ORDER BY removed_at DESC").fetchall()
    out = []
    for r in rows:
        sample = conn.execute(
            "SELECT t.id, t.title, t.path, ar.name AS artist, al.title AS album FROM library_removed r "
            "JOIN tracks t ON t.id = r.track_id JOIN artists ar ON ar.id = t.artist_id "
            "JOIN albums al ON al.id = t.album_id WHERE r.batch_id = ? ORDER BY t.path LIMIT 5",
            (r["batch_id"],)).fetchall()
        out.append({"batchId": r["batch_id"], "removedAt": r["removed_at"], "removedBy": r["removed_by"],
                    "reason": r["reason"], "purgeAfter": r["purge_after"], "count": r["n"], "bytes": r["bytes"],
                    "purgedCount": r["purged"], "purgeError": r["purge_error"],
                    "sample": [{"id": s["id"], "title": s["title"], "artist": s["artist"], "album": s["album"],
                                "path": s["path"]} for s in sample]})
    return out


def trash_tracks(conn: sqlite3.Connection, batch_id: str) -> list[dict]:
    return [{"id": r["id"], "title": r["title"], "artist": r["artist"], "album": r["album"], "path": r["path"],
             "bytes": r["flac_bytes"], "purgedAt": r["purged_at"], "purgeError": r["purge_error"]}
            for r in conn.execute(
                "SELECT t.id, t.title, t.path, t.flac_bytes, ar.name AS artist, al.title AS album, r.purged_at, "
                "r.purge_error FROM library_removed r JOIN tracks t ON t.id = r.track_id "
                "JOIN artists ar ON ar.id = t.artist_id JOIN albums al ON al.id = t.album_id "
                "WHERE r.batch_id = ? ORDER BY t.path", (batch_id,))]


# ---------- purge ----------


def _library_file(cfg: Config, rel: str) -> Path | None:
    """The file a track row points at, if it is safely inside its root (library or uploads)."""
    if rel.startswith(UPLOADS_PREFIX):
        if cfg.upload_root is None:
            return None
        root = Path(cfg.upload_root)
        rest = rel[len(UPLOADS_PREFIX):]
    else:
        root = Path(os.fspath(cfg.library_root))
        rest = rel
    parts = Path(rest).parts
    if not rest or Path(rest).is_absolute() or ".." in parts:
        return None
    target = root / rest
    try:
        real_root = root.resolve(strict=True)
        # The parent must resolve inside the root; the file itself must not be a symlink.
        target.parent.resolve(strict=False).relative_to(real_root)
    except (OSError, ValueError):
        return None
    return target


def _delete_file(cfg: Config, rel: str) -> tuple[bool, str | None]:
    """(gone, error). gone = the file no longer exists (deleted now or already)."""
    target = _library_file(cfg, rel)
    if target is None:
        return False, "unsafe path"
    if not os.path.lexists(target):
        return True, None
    if target.is_symlink() or not target.is_file():
        return False, "not a regular file"
    try:
        target.unlink()
    except OSError as e:
        return False, f"{type(e).__name__}: {e.strerror or e}"
    for ext in SIDECARS:
        side = target.with_suffix(ext)
        if side.is_file() and not side.is_symlink():
            try:
                side.unlink()
            except OSError:
                pass
    root = Path(cfg.upload_root if rel.startswith(UPLOADS_PREFIX) else os.fspath(cfg.library_root))
    d = target.parent
    while d != root and root in d.parents:  # remove folders left empty, never the root itself
        try:
            d.rmdir()
        except OSError:
            break
        d = d.parent
    return True, None


def purge(conn: sqlite3.Connection, cfg: Config, batch_ids: list[str] | None = None,
          now: datetime | None = None) -> dict:
    """Purge expired trash (or the given batches now). See the module docstring for what happens to files."""
    now_s = iso(now or datetime.now(UTC))
    if batch_ids is not None:
        rows = []
        for chunk in _chunks(list(dict.fromkeys(batch_ids))):
            ph = ",".join("?" * len(chunk))
            rows += conn.execute(
                "SELECT r.track_id, r.purged_at, t.path, t.flac_bytes FROM library_removed r JOIN tracks t "
                f"ON t.id = r.track_id WHERE r.batch_id IN ({ph})", chunk).fetchall()
    else:
        rows = conn.execute(
            "SELECT r.track_id, r.purged_at, t.path, t.flac_bytes FROM library_removed r JOIN tracks t "
            "ON t.id = r.track_id WHERE r.purge_after <= ? OR r.purged_at IS NOT NULL", (now_s,)).fetchall()
    drop: list[str] = []
    kept: list[tuple[str, str | None]] = []
    deleted = freed = 0
    for r in rows:
        path = r["path"]
        upload = path.startswith(UPLOADS_PREFIX)
        if r["purged_at"] is not None and not upload and not cfg.library_delete_files:
            # Kept before: drop the rows once the admin deleted the file by hand.
            target = _library_file(cfg, path)
            if target is not None and not os.path.lexists(target) and Path(os.fspath(cfg.library_root)).is_dir():
                drop.append(r["track_id"])
            continue
        if upload or cfg.library_delete_files:
            existed = (t := _library_file(cfg, path)) is not None and os.path.lexists(t)
            gone, err = _delete_file(cfg, path)
            if upload:
                conn.execute("DELETE FROM uploads WHERE track_id = ?", (r["track_id"],))
            if gone:
                drop.append(r["track_id"])
                if existed:
                    deleted += 1
                    freed += r["flac_bytes"] or 0
                    log.warning("cleanup: deleted %s", path)
            else:
                log.warning("cleanup: could not delete %s: %s", path, err)
                kept.append((r["track_id"], err))
        else:
            kept.append((r["track_id"], None))
    conn.execute("BEGIN IMMEDIATE")
    try:
        from nasmusic.app import health

        newly_kept = [t for t, _ in kept]
        health.detach_tracks(conn, newly_kept)
        conn.executemany("UPDATE library_removed SET purged_at = COALESCE(purged_at, ?), purge_error = ? "
                         "WHERE track_id = ?", [(now_s, err, t) for t, err in kept])
        health.delete_track_rows(conn, drop)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"purged": len(drop) + len(kept), "filesDeleted": deleted, "freedBytes": freed,
            "kept": len(kept), "errors": sum(1 for _, e in kept if e)}


def delete_list(conn: sqlite3.Connection) -> list[dict]:
    """Purged library files that were kept (deletion off or failed): what the admin can delete by hand."""
    return [{"id": r["id"], "path": r["path"], "bytes": r["flac_bytes"], "error": r["purge_error"]}
            for r in conn.execute(
                "SELECT t.id, t.path, t.flac_bytes, r.purge_error FROM library_removed r JOIN tracks t "
                "ON t.id = r.track_id WHERE r.purged_at IS NOT NULL ORDER BY t.path")]


def delete_script(items: list[dict]) -> str:
    """A POSIX shell script that deletes the listed files, relative to a music folder given as $1."""
    lines = ["#!/bin/sh",
             f"# nas-music delete list: {len(items)} file(s) removed in the admin console and past the trash.",
             "# Run it on the NAS with your music folder, e.g.:  sh delete-list.sh /mnt/tank/music",
             "# The server drops these songs from its database once the files are gone.",
             "set -eu",
             'cd "${1:?usage: sh delete-list.sh /path/to/music}"']
    lines += [f"rm -fv -- {shlex.quote(i['path'])}" for i in items if not i["path"].startswith(UPLOADS_PREFIX)]
    lines.append("")
    return "\n".join(lines)


def counts(conn: sqlite3.Connection) -> dict:
    r = conn.execute("SELECT SUM(purged_at IS NULL), SUM(purged_at IS NOT NULL) FROM library_removed").fetchone()
    return {"inTrash": r[0] or 0, "deleteList": r[1] or 0}
