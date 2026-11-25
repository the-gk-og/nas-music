"""Fake-lossless checker (schema v11 `lossless_checks`), filled by the resumable `lossless` job.

A lossy encoder throws away the top of the spectrum: LAME's MP3 at 128 kbps cuts everything above ~16 kHz,
192 kbps ~19 kHz, 320 kbps / V0 ~19.5-20.5 kHz. Converting that MP3 to FLAC keeps the hole, so a "lossless"
file with a sharp cliff in its average spectrum well below the Nyquist frequency was almost certainly made
from a lossy file. Likewise a 96 kHz "hi-res" file with nothing above ~22 kHz was upsampled from CD, and a
24-bit file whose low 8 bits are always zero is 16-bit padded.

Three ~10 s excerpts (at 20%, 50% and 80% of the song) are decoded with ffmpeg (nice 19) to mono float, their
power spectra averaged, and the cliff searched for in 100 Hz bands. Old or quiet recordings can roll off
naturally, so a verdict is a strong hint, not proof; an admin can dismiss it. The music files are only read.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from nasmusic.core import now_iso

log = logging.getLogger(__name__)

LOSSLESS = ("FLAC", "ALAC", "WAV", "AIFF", "APE", "WAVPACK")
VERDICTS = ("ok", "lossy_source", "suspect", "upsampled", "unknown", "error")
FLAGGED = ("lossy_source", "suspect", "upsampled")
EXCERPT_S = 10.0
MAX_RATE = 96_000
FFMPEG_TIMEOUT_S = 120
BAND_HZ = 100
MIN_DROP_DB = 24.0          # a lowpass falls this far within ~1 kHz; natural roll-off doesn't
LOSSY_BELOW_HZ = 20_000     # cliff below this in a 44.1/48 kHz file: made from a lossy file
SUSPECT_BELOW_HZ = 21_000   # 20-21 kHz: MP3 320 / AAC 256 territory, but some masters are filtered there too


@dataclass
class Result:
    verdict: str
    cutoff_hz: int | None = None
    source_guess: str | None = None
    detail: str | None = None


def _lower_priority() -> None:  # runs in the forked child before exec
    os.nice(19)


def _guess(cutoff: float) -> str:
    if cutoff < 12_000:
        return "a low-bitrate MP3 (≤ 64 kbps)"
    if cutoff < 15_000:
        return "an MP3 ~96 kbps or AAC"
    if cutoff < 16_800:
        return "an MP3 ~128 kbps"
    if cutoff < 18_000:
        return "an MP3 ~160 kbps"
    if cutoff < 19_300:
        return "an MP3 ~192 kbps"
    if cutoff < 20_000:
        return "an MP3 V0 / ~256 kbps"
    return "an MP3 320 kbps or AAC 256 kbps"


def band_spectrum(samples: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray] | None:
    """(band centre Hz, band level dB) of the average power spectrum, 100 Hz bands; None if (near) silent."""
    n = 8192 if rate > 48_000 else 4096
    if samples.size < n * 4:
        return None
    hop = n // 2
    frames = (samples.size - n) // hop + 1
    idx = np.arange(n)[None, :] + hop * np.arange(frames)[:, None]
    x = samples[idx] * np.hanning(n)[None, :]
    rms = np.sqrt(np.mean(x * x, axis=1))
    loud = rms > max(1e-5, float(np.percentile(rms, 25)) * 0.5)  # skip silent gaps between excerpts
    if loud.sum() < 4:
        return None
    p = np.mean(np.abs(np.fft.rfft(x[loud], axis=1)) ** 2, axis=0)
    freqs = np.fft.rfftfreq(n, 1.0 / rate)
    edges = np.arange(0, rate / 2 + BAND_HZ, BAND_HZ)
    which = np.digitize(freqs, edges) - 1
    sums = np.bincount(which, weights=p, minlength=len(edges))[:len(edges) - 1]
    cnt = np.bincount(which, minlength=len(edges))[:len(edges) - 1]
    level = 10 * np.log10(np.where(cnt > 0, sums / np.maximum(cnt, 1), 0) + 1e-20)
    return edges[:-1] + BAND_HZ / 2, level


def find_cutoff(centres: np.ndarray, level: np.ndarray, rate: int, lo_hz: float = 10_000) -> tuple[float, float] | None:
    """(cutoff Hz, drop dB) of the sharpest cliff above `lo_hz` followed by nothing up to Nyquist, or None."""
    nyq = rate / 2
    ref = float(np.median(level[(centres > 1000) & (centres < 6000)]))
    best: tuple[float, float] | None = None
    top = nyq - 300
    for i, f in enumerate(centres):
        if f < lo_hz or f > top - 400:
            continue
        below = level[(centres >= f - 1000) & (centres < f)]
        above = level[(centres > f + 150) & (centres <= min(f + 2000, top))]
        rest = level[(centres > f + 150) & (centres <= top)]
        if below.size < 5 or above.size < 3:
            continue
        drop = float(np.mean(below) - np.mean(above))
        # The cliff must lead to a floor that stays low all the way up, well under the music.
        if drop >= MIN_DROP_DB and float(np.max(rest)) < float(np.mean(below)) - MIN_DROP_DB / 2 \
                and float(np.mean(below)) > ref - 70:
            if best is None or drop > best[1] + 3:
                best = (float(f), drop)
    if best is None:
        return None
    # Walk down to where the fall starts (the first band within 6 dB of the shelf below it).
    f0 = best[0]
    shelf = float(np.mean(level[(centres >= f0 - 1500) & (centres < f0 - 500)]))
    j = int(np.searchsorted(centres, f0))
    while j > 0 and centres[j] > f0 - 1000 and level[j] < shelf - 6:
        j -= 1
    return float(centres[j]) + BAND_HZ / 2, best[1]


def classify(samples: np.ndarray, rate: int) -> Result:
    spec = band_spectrum(samples.astype(np.float64), rate)
    if spec is None:
        return Result("unknown", detail="too short or too quiet to tell")
    centres, level = spec
    cut = find_cutoff(centres, level, rate)
    if cut is None:
        return Result("ok", detail=f"content up to {rate / 2000:.1f} kHz")
    hz, drop = cut
    c = int(round(hz))
    d = f"{drop:.0f} dB cliff at {hz / 1000:.1f} kHz"
    if hz < LOSSY_BELOW_HZ:
        return Result("lossy_source", c, _guess(hz), d)
    if rate > 48_000:
        return Result("upsampled", c, "CD (44.1 kHz)" if hz < 22_600 else "48 kHz", d)
    if hz < SUSPECT_BELOW_HZ:
        return Result("suspect", c, _guess(hz), d)
    return Result("ok", c, None, d)


def _decode(path: Path, start: float, length: float, rate: int, ffmpeg: str, fmt: str = "f32le",
            mono: bool = True) -> np.ndarray | None:
    cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, start):.2f}",
           "-t", f"{length:.2f}", "-i", str(path), "-map", "0:a:0", "-vn"]
    if mono:
        cmd += ["-ac", "1"]
    cmd += ["-ar", str(rate), "-f", fmt, "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=False,
                              preexec_fn=_lower_priority if os.name == "posix" else None)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("lossless: ffmpeg failed to run on %s: %s", path.name, e)
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    return np.frombuffer(proc.stdout, dtype=np.float32 if fmt == "f32le" else np.int32)


def padded_24bit(path: Path, duration: float, rate: int, ffmpeg: str = "ffmpeg") -> bool | None:
    """True if a 24-bit file's samples never use the low 8 bits (16-bit audio padded to 24)."""
    x = _decode(path, duration * 0.5, 5.0, rate, ffmpeg, fmt="s32le", mono=False)
    if x is None or x.size < 1000:
        return None
    nz = x[x != 0]
    if nz.size < 1000:
        return None
    return bool(np.all((nz & 0xFFFF) == 0))


def check(path: Path, duration: float | None, sample_rate: int | None, bits: int | None,
          ffmpeg: str = "ffmpeg") -> Result:
    rate = min(int(sample_rate or 44_100), MAX_RATE)
    dur = float(duration or 0)
    starts = [dur * f for f in (0.2, 0.5, 0.8)] if dur > EXCERPT_S * 3 else [0.0]
    length = EXCERPT_S if dur > EXCERPT_S * 3 else max(dur, EXCERPT_S)
    parts = [x for s in starts if (x := _decode(path, s, length, rate, ffmpeg)) is not None and x.size]
    if not parts:
        return Result("error", detail="ffmpeg could not decode the file")
    res = classify(np.concatenate(parts), rate)
    if res.verdict in ("ok", "unknown") and bits and bits >= 24 and padded_24bit(path, dur, rate, ffmpeg):
        return Result("upsampled", None, "16-bit audio padded to 24-bit", "the low 8 bits are always zero")
    return res


def run(conn: sqlite3.Connection, library_root: Path, ffmpeg: str = "ffmpeg", limit: int | None = None,
        time_budget_s: float | None = None) -> dict:
    """Check lossless tracks without a verdict for their current file (newest first)."""
    t0 = time.monotonic()
    ph = ",".join("?" * len(LOSSLESS))
    rows = conn.execute(
        "SELECT t.id, t.path, t.mtime, t.duration_s, t.sample_rate, t.bits_per_sample FROM tracks t "
        "LEFT JOIN lossless_checks c ON c.track_id = t.id "
        f"WHERE t.missing = 0 AND UPPER(COALESCE(t.codec, '')) IN ({ph}) AND (c.track_id IS NULL OR c.mtime <> t.mtime) "
        "ORDER BY t.added_at DESC, t.id", LOSSLESS).fetchall()
    done = flagged = 0
    for r in rows:
        if limit is not None and done >= limit:
            break
        if time_budget_s is not None and time.monotonic() - t0 > time_budget_s:
            break
        res = check(library_root / r["path"], r["duration_s"], r["sample_rate"], r["bits_per_sample"], ffmpeg)
        conn.execute(
            "INSERT INTO lossless_checks (track_id, mtime, verdict, cutoff_hz, source_guess, detail, checked_at, "
            "dismissed) VALUES (?, ?, ?, ?, ?, ?, ?, 0) ON CONFLICT(track_id) DO UPDATE SET mtime = excluded.mtime, "
            "verdict = excluded.verdict, cutoff_hz = excluded.cutoff_hz, source_guess = excluded.source_guess, "
            "detail = excluded.detail, checked_at = excluded.checked_at, dismissed = 0",
            (r["id"], r["mtime"], res.verdict, res.cutoff_hz, res.source_guess, res.detail, now_iso()))
        done += 1
        flagged += res.verdict in FLAGGED
    return {"pending": len(rows), "checked": done, "flagged": flagged, "remaining": max(0, len(rows) - done)}


def counts(conn: sqlite3.Connection) -> dict:
    ph = ",".join("?" * len(LOSSLESS))
    total = conn.execute(f"SELECT COUNT(*) FROM tracks WHERE missing = 0 AND UPPER(COALESCE(codec, '')) IN ({ph})",
                         LOSSLESS).fetchone()[0]
    by = dict(conn.execute(
        "SELECT c.verdict, COUNT(*) FROM lossless_checks c JOIN tracks t ON t.id = c.track_id AND t.mtime = c.mtime "
        "WHERE t.missing = 0 AND c.dismissed = 0 GROUP BY c.verdict").fetchall())
    checked = conn.execute("SELECT COUNT(*) FROM lossless_checks c JOIN tracks t ON t.id = c.track_id "
                           "AND t.mtime = c.mtime WHERE t.missing = 0").fetchone()[0]
    return {"lossless": total, "checked": checked, "flagged": sum(by.get(v, 0) for v in FLAGGED),
            "byVerdict": by}


def dismiss(conn: sqlite3.Connection, track_ids: list[str], dismissed: bool = True) -> int:
    n = 0
    for i in range(0, len(track_ids), 500):
        chunk = track_ids[i:i + 500]
        n += conn.execute(f"UPDATE lossless_checks SET dismissed = ? WHERE track_id IN ({','.join('?' * len(chunk))})",
                          (int(dismissed), *chunk)).rowcount
    return n
