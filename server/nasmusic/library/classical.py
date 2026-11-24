"""Classical music (schema v10): which work and movement a track is.

A track's work comes from its WORK tag (Vorbis WORK, ID3 TXXX:WORK or TIT1 next to a movement tag, MP4 \\xa9wrk)
and its movement from MOVEMENTNAME / MOVEMENT (ID3 MVNM / MVIN, MP4 \\xa9mvn / \\xa9mvi). Files without them
often carry the work in the title instead ("Symphony No. 5 in C minor, Op. 67: I. Allegro con brio"); that is
split when the track has a composer, so pop titles with a colon are left alone.

tracks.work is NULL until a track is read under v10 and '' when it is not part of a work. `backfill` reads the
work tags of tracks scanned before v10 (read-only), a time-boxed batch after each library scan.

Works are not stored: a work is the tracks of one album that share `work`. Its id is
`<album id>.<12 hex of sha1(work)>`, so it is stable across scans and needs no table."""
from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
import time
from dataclasses import dataclass

from nasmusic.config import Config

log = logging.getLogger(__name__)

_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
# "<work>: <number>. <movement>" with a roman or arabic movement number.
_TITLE = re.compile(r"^(?P<work>.{3,}?)\s*:\s+(?P<num>[IVXLC]{1,7}|\d{1,3})[.):]\s+(?P<name>\S.*)$")
# A movement name that still starts with its number ("II. Andante").
_NUMBERED = re.compile(r"^(?P<num>[IVXLC]{1,7}|\d{1,3})[.):]\s+(?P<name>\S.*)$")


@dataclass
class Movement:
    work: str                 # '' = not part of a work
    movement: str | None = None
    movement_no: int | None = None


def roman(s: str) -> int | None:
    s = s.upper()
    if not s or any(ch not in _ROMAN for ch in s):
        return None
    total = 0
    for i, ch in enumerate(s):
        v = _ROMAN[ch]
        total += -v if i + 1 < len(s) and _ROMAN[s[i + 1]] > v else v
    return total if 0 < total < 200 else None


def _num(s: str) -> int | None:
    return int(s) if s.isdigit() else roman(s)


def _clean(s: str | None) -> str | None:
    s = " ".join(s.split()) if s else None
    return s or None


def parse(title: str | None, composer: str | None, work: str | None = None, movement: str | None = None,
          movement_no: int | None = None) -> Movement:
    """Work and movement of a track from its tags, falling back to its title."""
    title, work, movement = _clean(title), _clean(work), _clean(movement)
    if work:
        if not movement and title:
            # the title often repeats the work: "Work: II. Andante" -> "II. Andante"
            m = _TITLE.match(title)
            if title.casefold().startswith(work.casefold()):
                movement = title[len(work):].lstrip(" :,-–") or title
            elif m and _num(m.group("num")) is not None:  # "Sym. No. 9: IV. Presto" under a longer WORK tag
                movement = title[m.start("num"):]
            else:
                movement = title
        if movement:
            m = _NUMBERED.match(movement)
            if m:
                movement_no = movement_no or _num(m.group("num"))
                movement = m.group("name")
        return Movement(work, movement, movement_no)
    if title and composer:
        m = _TITLE.match(title)
        if m:
            n = _num(m.group("num"))
            if n is not None:
                return Movement(m.group("work").rstrip(" ,"), m.group("name"), movement_no or n)
    return Movement("")


def work_id(album_id: str, work: str) -> str:
    return f"{album_id}.{hashlib.sha1(work.encode()).hexdigest()[:12]}"


def split_work_id(wid: str) -> tuple[str, str] | None:
    album_id, _, h = wid.rpartition(".")
    if not album_id or not re.fullmatch(r"[0-9a-f]{12}", h):
        return None
    return album_id, h


# ---------- backfill of tracks scanned before v10 ----------


def backfill(cfg: Config, conn: sqlite3.Connection, budget_s: float = 60.0, batch: int = 200) -> int:
    """Read the work tags of tracks whose `work` is still NULL, until done or `budget_s` passes. Returns how
    many tracks were updated. Unreadable files get '' so they are not retried every scan."""
    from nasmusic.library import tags  # mutagen import kept out of module import time

    t0, done = time.monotonic(), 0
    while time.monotonic() - t0 < budget_s:
        rows = conn.execute("SELECT id, path, title, composer FROM tracks WHERE work IS NULL AND missing != 1 "
                            "LIMIT ?", (batch,)).fetchall()
        if not rows:
            break
        updates = []
        for r in rows:
            try:
                t, _ = tags.read(cfg.library_root / r["path"])
                mv = parse(r["title"], r["composer"], t.work, t.movement, t.movement_no)
            except Exception as e:  # missing/corrupt file: classify from the stored title alone
                log.debug("classical backfill: cannot read %s: %s", r["path"], e)
                mv = parse(r["title"], r["composer"])
            updates.append((mv.work, mv.movement, mv.movement_no, r["id"]))
            if time.monotonic() - t0 >= budget_s:
                break
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.executemany("UPDATE tracks SET work = ?, movement = ?, movement_no = ? WHERE id = ?", updates)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        done += len(updates)
    if done:
        log.info("classical backfill: read work tags of %d tracks", done)
    return done
