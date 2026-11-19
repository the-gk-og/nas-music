"""Volume levelling (ReplayGain 2.0 values from EBU R128 loudness).

Tracks whose files carry REPLAYGAIN_* / R128_* tags get their gains from the scanner (rg_source 'tags').
Every other present track is measured here with ffmpeg's `ebur128` filter (one ffmpeg at a time, nice 19)
and stored with rg_source 'ebur128'. Album gain is then filled in for every album whose tracks all have a
track gain: the album's integrated loudness is approximated as the duration-weighted energy mean of its
tracks' loudness (exact for un-gated material, within a few tenths of a dB for real albums), and the
album peak is the largest track peak. Album gains read from tags are kept as they are.

Gain = REFERENCE_LUFS - loudness (ReplayGain 2.0 reference level, -18 LUFS).
"""
from __future__ import annotations

import logging
import math
import os
import re
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

REFERENCE_LUFS = -18.0
FFMPEG_TIMEOUT_S = 900
# Silence / near-silence: ebur128 reports -70 LUFS (the absolute gate). Cap the gain so a silent
# interlude isn't boosted by +50 dB.
MAX_GAIN_DB = 20.0
MIN_GAIN_DB = -30.0

_I = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+|-inf|nan)\s*LUFS", re.IGNORECASE)
_PEAK = re.compile(r"(?:Sample|True) peak:\s*\n\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS", re.IGNORECASE)


@dataclass
class Measurement:
    lufs: float
    peak: float  # linear sample peak

    @property
    def gain_db(self) -> float:
        return gain_for(self.lufs)


def gain_for(lufs: float) -> float:
    return round(max(MIN_GAIN_DB, min(MAX_GAIN_DB, REFERENCE_LUFS - lufs)), 2)


def parse_ebur128(stderr: str) -> Measurement | None:
    """Parse the summary block ffmpeg's ebur128 filter prints at the end of stderr."""
    tail = stderr[stderr.rfind("Summary:"):] if "Summary:" in stderr else stderr
    mi, mp = _I.search(tail), _PEAK.search(tail)
    if not mi:
        return None
    try:
        lufs = float(mi.group(1))
    except ValueError:
        return None
    if math.isnan(lufs) or math.isinf(lufs):
        lufs = -70.0
    peak = 1.0
    if mp:
        try:
            db = float(mp.group(1))
            peak = 0.0 if math.isinf(db) else round(10 ** (db / 20), 6)
        except ValueError:
            pass
    return Measurement(lufs=round(lufs, 2), peak=peak)


def _lower_priority() -> None:  # runs in the forked child before exec
    os.nice(19)


def measure(path: Path, ffmpeg: str = "ffmpeg") -> Measurement | None:
    # framelog=verbose (not "quiet", which ffmpeg < 6 rejects): per-frame lines stay below the default log level.
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-vn",
           "-filter:a", "ebur128=peak=sample:framelog=verbose", "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=False,
                              preexec_fn=_lower_priority)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("loudness: ffmpeg failed to run on %s: %s", path.name, e)
        return None
    if proc.returncode != 0:
        tail = proc.stderr.decode(errors="replace").strip().splitlines()[-2:]
        log.warning("loudness: ffmpeg exited %s on %s: %s", proc.returncode, path.name, " | ".join(tail))
        return None
    return parse_ebur128(proc.stderr.decode(errors="replace"))


def album_values(rows: list[tuple[float, float, float | None]]) -> tuple[float, float | None] | None:
    """[(loudness_lufs, duration_s, track_peak)] -> (album gain dB, album peak), or None if empty."""
    total = sum(max(d, 0.001) for _, d, _ in rows)
    if not rows or total <= 0:
        return None
    energy = sum(max(d, 0.001) * 10 ** (lufs / 10) for lufs, d, _ in rows) / total
    lufs = 10 * math.log10(energy) if energy > 0 else -70.0
    peaks = [p for _, _, p in rows if p is not None]
    return gain_for(lufs), (max(peaks) if peaks else None)


def fill_album_gains(conn: sqlite3.Connection) -> int:
    """Set rg_album_gain/peak on albums where every present track has a track gain and some track
    lacks an album gain. Measured tracks of such an album all get the new value; album gains read from
    tags are left alone. Returns the number of albums updated."""
    albums = [r[0] for r in conn.execute(
        "SELECT album_id FROM tracks WHERE missing = 0 GROUP BY album_id "
        "HAVING SUM(rg_track_gain IS NULL) = 0 AND SUM(rg_album_gain IS NULL) > 0")]
    n = 0
    for al in albums:
        rows = conn.execute(
            "SELECT COALESCE(loudness_lufs, ? - rg_track_gain), duration_s, rg_track_peak FROM tracks "
            "WHERE album_id = ? AND missing = 0", (REFERENCE_LUFS, al)).fetchall()
        vals = album_values([(r[0], r[1] or 0.0, r[2]) for r in rows])
        if vals is None:
            continue
        conn.execute("UPDATE tracks SET rg_album_gain = ?, rg_album_peak = ? "
                     "WHERE album_id = ? AND missing = 0 AND (rg_album_gain IS NULL OR rg_source = 'ebur128')",
                     (vals[0], vals[1], al))
        n += 1
    return n


def run(conn: sqlite3.Connection, library_root: Path, ffmpeg: str = "ffmpeg", limit: int | None = None,
        time_budget_s: float | None = None) -> dict:
    """Measure unlevelled tracks (oldest first), then fill album gains. Stops after `limit` tracks or
    `time_budget_s` seconds; the rest is picked up next run."""
    t0 = time.monotonic()
    rows = conn.execute("SELECT id, path, mtime FROM tracks WHERE missing = 0 AND rg_source IS NULL "
                        "ORDER BY added_at, id").fetchall()
    measured = failed = 0
    for r in rows:
        if limit is not None and measured + failed >= limit:
            break
        if time_budget_s is not None and time.monotonic() - t0 > time_budget_s:
            break
        m = measure(library_root / r["path"], ffmpeg)
        if m is None:
            failed += 1
            # Mark it so a broken file isn't retried every run; a rescan of a changed file resets it.
            conn.execute("UPDATE tracks SET rg_source = 'failed' WHERE id = ? AND mtime = ?", (r["id"], r["mtime"]))
            continue
        conn.execute(
            "UPDATE tracks SET rg_track_gain = ?, rg_track_peak = ?, loudness_lufs = ?, rg_source = 'ebur128', "
            "rg_album_gain = NULL, rg_album_peak = NULL WHERE id = ? AND mtime = ?",
            (m.gain_db, m.peak, m.lufs, r["id"], r["mtime"]))
        measured += 1
    albums = fill_album_gains(conn)
    return {"pending": len(rows), "measured": measured, "failed": failed, "albums": albums,
            "remaining": max(0, len(rows) - measured - failed)}


def replay_gain(r) -> dict | None:
    """API shape (Track.replayGain) from a row with rg_* columns, or None when nothing is known."""
    try:
        tg = r["rg_track_gain"]
    except (IndexError, KeyError):
        return None
    if tg is None:
        return None
    return {"trackGain": tg, "trackPeak": r["rg_track_peak"], "albumGain": r["rg_album_gain"],
            "albumPeak": r["rg_album_peak"]}
