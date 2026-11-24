"""Search by lyrics: a full-text index (SQLite FTS5) over the lyrics everyone sees by default.

The scan job keeps it up to date (`refresh`). For each present track the indexed text is, in order: an admin's
lyrics edit, the file's own lyrics (embedded tags or a sidecar .lrc), or cached LRCLIB lyrics, with LRC
timestamps removed. Per-user lyrics edits are private and never indexed. Nothing here contacts LRCLIB: it only
reads what the lyrics job and the lyrics endpoint have already cached.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
import unicodedata
from pathlib import Path

from nasmusic.core import now_iso
from nasmusic.library import lyrics, overrides, queries

log = logging.getLogger(__name__)

BATCH = 200                   # rows per transaction while indexing
MAX_TEXT = 64 * 1024          # lyrics longer than this are cut (a pasted libretto, a broken tag)
_TOKEN = re.compile(r"\w+", re.UNICODE)


def plain_text(lyr: lyrics.Lyrics) -> str:
    """Lyrics as plain lines (no timestamps, no blank lines)."""
    return "\n".join(ln["text"].strip() for ln in lyr.lines if ln.get("text", "").strip())[:MAX_TEXT]


def best_for_track(conn: sqlite3.Connection, library_root: Path, track_id: str, path: str) -> tuple[str, str]:
    """(source, text) for one track; ("none", "") when it has no lyrics anyone sees by default."""
    shared = overrides.lyrics_override(conn, track_id)
    if shared:
        parsed = lyrics.from_raw(shared, "library")
        if parsed and plain_text(parsed):
            return "library", plain_text(parsed)
    src = library_root / path
    try:
        found = lyrics.read_all(src) if src.is_file() else {}
    except Exception as e:  # mutagen raises many types on odd files; one bad file must not stop the pass
        log.info("cannot read lyrics from %s: %s", path, e)
        found = {}
    if found:
        # Same preference as reading lyrics for playback: synced first, embedded before the sidecar.
        best = sorted(found.values(), key=lambda x: (not x.synced, x.source != "embedded"))[0]
        text = plain_text(best)
        if text:
            return best.source, text
    row = conn.execute("SELECT raw FROM lyrics_cache WHERE track_id = ? AND status = 'found'",
                       (track_id,)).fetchone()
    if row and row["raw"]:
        parsed = lyrics.from_raw(row["raw"], "lrclib")
        if parsed and plain_text(parsed):
            return "lrclib", plain_text(parsed)
    return "none", ""


def _stale_sql() -> str:
    return (
        "SELECT t.id, t.path, t.mtime FROM tracks t LEFT JOIN lyrics_index li ON li.track_id = t.id "
        "WHERE t.missing = 0 AND (li.track_id IS NULL OR li.mtime IS NULL OR li.mtime != t.mtime "
        # an admin edited (or reverted) the lyrics since
        " OR EXISTS (SELECT 1 FROM tag_overrides o WHERE o.track_id = t.id AND o.field = 'lyrics' "
        "            AND o.updated_at > li.indexed_at) "
        " OR (li.source = 'library' AND NOT EXISTS (SELECT 1 FROM tag_overrides o WHERE o.track_id = t.id "
        "            AND o.field = 'lyrics')) "
        # LRCLIB found lyrics for a track that had none (or changed its answer)
        " OR (li.source IN ('none', 'lrclib') AND EXISTS (SELECT 1 FROM lyrics_cache c WHERE c.track_id = t.id "
        "            AND c.fetched_at > li.indexed_at))) "
        "ORDER BY t.added_at DESC, t.id")


def _write(conn: sqlite3.Connection, track_id: str, source: str, text: str, mtime: float | None, now: str) -> None:
    conn.execute("DELETE FROM lyrics_fts WHERE track_id = ?", (track_id,))
    if text:
        conn.execute("INSERT INTO lyrics_fts (track_id, body) VALUES (?, ?)", (track_id, text))
    conn.execute(
        "INSERT INTO lyrics_index (track_id, source, mtime, indexed_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(track_id) DO UPDATE SET source = excluded.source, mtime = excluded.mtime, "
        "indexed_at = excluded.indexed_at", (track_id, source, mtime, now))


def index_track(conn: sqlite3.Connection, library_root: Path, track_id: str) -> str | None:
    """Re-index one track now (after an admin lyrics edit). Returns its source, or None if it isn't present."""
    r = conn.execute("SELECT id, path, mtime FROM tracks WHERE id = ? AND missing = 0", (track_id,)).fetchone()
    if r is None:
        return None
    source, text = best_for_track(conn, library_root, r["id"], r["path"])
    _write(conn, r["id"], source, text, r["mtime"], now_iso())
    return source


def refresh(conn: sqlite3.Connection, library_root: Path, full: bool = False,
            time_budget_s: float | None = None) -> dict:
    """Index new and changed tracks, and drop rows of tracks that are gone. `full` marks every track stale first
    (a sidecar .lrc can change without the audio file's mtime changing). Resumable: stops after
    `time_budget_s`; the next run carries on with what is still stale."""
    t0 = time.monotonic()
    if full:
        conn.execute("UPDATE lyrics_index SET mtime = NULL")
    rows = conn.execute(_stale_sql()).fetchall()
    out = {"pending": len(rows), "indexed": 0, "withLyrics": 0, "removed": 0, "complete": True}
    for start in range(0, len(rows), BATCH):
        if time_budget_s is not None and time.monotonic() - t0 > time_budget_s:
            out["complete"] = False
            break
        chunk = rows[start:start + BATCH]
        found = [(r, *best_for_track(conn, library_root, r["id"], r["path"])) for r in chunk]  # file I/O first
        now = now_iso()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for r, source, text in found:
                _write(conn, r["id"], source, text, r["mtime"], now)
                out["indexed"] += 1
                out["withLyrics"] += source != "none"
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    gone = [r[0] for r in conn.execute(
        "SELECT li.track_id FROM lyrics_index li JOIN tracks t ON t.id = li.track_id WHERE t.missing != 0")]
    if gone:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for tid in gone:
                conn.execute("DELETE FROM lyrics_fts WHERE track_id = ?", (tid,))
                conn.execute("DELETE FROM lyrics_index WHERE track_id = ?", (tid,))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    out["removed"] = len(gone)
    out["pending"] -= out["indexed"]
    return out


def stats(conn: sqlite3.Connection) -> dict:
    rows = dict(conn.execute("SELECT source, COUNT(*) FROM lyrics_index GROUP BY source").fetchall())
    present = conn.execute("SELECT COUNT(*) FROM tracks WHERE missing = 0").fetchone()[0]
    return {"indexed": sum(rows.values()), "withLyrics": sum(v for k, v in rows.items() if k != "none"),
            "tracks": present, "bySource": rows}


# ---------- search ----------


def _fold(s: str) -> str:
    """Case- and accent-insensitive form, like the index's tokenizer (remove_diacritics)."""
    return "".join(c for c in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(c))


def tokens(q: str) -> list[str]:
    return _TOKEN.findall(q)[:12]


def fts_query(q: str) -> str | None:
    """Every word must appear; the last one may be a prefix (so results come while typing)."""
    words = tokens(q)
    if not words:
        return None
    parts = [f'"{w}"' for w in words]
    if len(words[-1]) >= 3:  # "a*" would match nearly every song
        parts[-1] += "*"
    return " ".join(parts)


def matching_line(text: str, q: str) -> str:
    """The lyric line that best matches the query: the whole phrase if a line has it, else most words."""
    words = [_fold(w) for w in tokens(q)]
    phrase = " ".join(words)
    best, best_score = "", -1.0
    for line in text.splitlines():
        folded = _fold(line)
        line_words = _TOKEN.findall(folded)
        score = sum(1 for w in words if any(lw.startswith(w) for lw in line_words))
        if phrase and phrase in " ".join(line_words):
            score += len(words) + 1
        if score > best_score:
            best, best_score = line.strip(), score
    return best


def search(conn: sqlite3.Connection, uid: str, q: str, limit: int) -> list[dict]:
    """Tracks whose lyrics contain every word of `q`, best first: [{track, line}]. Family mode applies."""
    match = fts_query(q)
    if match is None:
        return []
    f = queries.fam(conn, uid)
    try:
        hits = conn.execute(
            "SELECT track_id, body FROM lyrics_fts WHERE lyrics_fts MATCH ? ORDER BY bm25(lyrics_fts) LIMIT ?",
            (match, max(limit * 3, 30))).fetchall()
    except sqlite3.OperationalError as e:  # a query FTS can't parse: no results rather than a 500
        log.info("lyrics search %r failed: %s", q, e)
        return []
    if not hits:
        return []
    ids = [h["track_id"] for h in hits]
    rows = {r["id"]: r for r in conn.execute(
        queries.TRACK_SELECT + f" WHERE t.missing = 0{f} AND t.id IN ({','.join('?' * len(ids))})",
        (uid, *ids))}
    out = []
    for h in hits:
        r = rows.get(h["track_id"])
        if r is None:
            continue
        out.append({"track": queries.track_dict(r), "line": matching_line(h["body"], q)})
        if len(out) >= limit:
            break
    return out
