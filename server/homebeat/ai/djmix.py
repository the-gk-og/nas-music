"""DJ automix analysis: beat grid, downbeats, Camelot key, intro/outro and mix points, energy curve.

One `track_dj` row per track (schema v7). Runs as the `automix` background job: resumable (work is chosen
from the DB each run: tracks without a row, rows older than the file, failures older than RETRY_DAYS),
time-boxed, in one spawn-based worker process at low priority, so streaming stays responsive on the NAS.

Extraction:
* With Essentia (the `analysis` extra, CPU algorithms only, no TensorFlow): RhythmExtractor2013 (method
  'degara', the cheap one) for beat ticks, KeyExtractor for key/scale/strength. Version 'dj-essentia-1'.
* Without it: a small numpy beat tracker (spectral-flux onset envelope, autocorrelation tempo, comb phase,
  local peak refinement) and chroma key profiles. Version 'dj-basic-1'. Good enough for tests and steady
  electronic/pop; less so for anything else.

Both then share the pure post-processing below (grid fit, downbeat phase from bass energy, sections, mix points),
which is what the tests cover. Vocal in/out is not detected (no cheap CPU detector): the columns stay NULL.
"""
from __future__ import annotations

import json
import logging
import math
import multiprocessing as mp
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from . import audiohash
from .common import iso, transaction

log = logging.getLogger("homebeat.ai.djmix")

ESSENTIA_VERSION = "dj-essentia-1"
BASIC_VERSION = "dj-basic-1"
RETRY_DAYS = 14
MAX_CURVE_POINTS = 256
SR = 44100

# ---------------------------------------------------------------- Camelot wheel

_PC = {"C": 0, "B#": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "FB": 4, "F": 5, "E#": 5,
       "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10, "BB": 10, "B": 11, "CB": 11}
# pitch class -> Camelot number. Majors are 'B', minors 'A'. C major = 8B, A minor = 8A.
_MAJOR_NUM = {0: 8, 7: 9, 2: 10, 9: 11, 4: 12, 11: 1, 6: 2, 1: 3, 8: 4, 3: 5, 10: 6, 5: 7}
_MINOR_NUM = {9: 8, 4: 9, 11: 10, 6: 11, 1: 12, 8: 1, 3: 2, 10: 3, 5: 4, 0: 5, 7: 6, 2: 7}


def camelot(key: str | None, scale: str | None = None) -> str | None:
    """'A', 'minor' -> '8A'; also accepts 'A minor', 'Am', 'Bb major', 'F#'. None if unparseable."""
    if not key:
        return None
    k = key.strip()
    if scale is None:
        parts = k.split()
        if len(parts) == 2:
            k, scale = parts
        elif k.endswith("m") and len(k) >= 2 and not k.lower().endswith("dim"):
            k, scale = k[:-1], "minor"
        else:
            scale = "major"
    pc = _PC.get(k.upper())
    if pc is None:
        return None
    minor = scale.lower().startswith("min")
    return f"{(_MINOR_NUM if minor else _MAJOR_NUM)[pc]}{'A' if minor else 'B'}"


def parse_camelot(code: str | None) -> tuple[int, str] | None:
    if not code or len(code) < 2 or code[-1].upper() not in "AB":
        return None
    try:
        n = int(code[:-1])
    except ValueError:
        return None
    return (n, code[-1].upper()) if 1 <= n <= 12 else None


def key_distance(a: str | None, b: str | None) -> int | None:
    """Harmonic-mixing distance on the Camelot wheel: 0 same key, 1 adjacent (±1 same letter, or the
    relative major/minor), 2 two steps (incl. the +2 'energy boost'), up to 7. None if either is unknown."""
    pa, pb = parse_camelot(a), parse_camelot(b)
    if pa is None or pb is None:
        return None
    d = abs(pa[0] - pb[0]) % 12
    d = min(d, 12 - d)
    return d + (pa[1] != pb[1])


# ---------------------------------------------------------------- beat grid

@dataclass
class Grid:
    bpm: float
    beat_s: float          # first beat >= 0
    confidence: float      # share of ticks within 6 % of a beat of the fitted grid
    stable: bool

    @property
    def period(self) -> float:
        return 60.0 / self.bpm


def fit_grid(ticks: np.ndarray | list[float], bpm_hint: float | None = None) -> Grid | None:
    """Least-squares constant-tempo grid through beat ticks (seconds). Robust to a few missed or extra ticks.
    `stable` = ≥ 85 % of ticks within 6 % of a beat of the grid and the tempo is in 60..200 BPM."""
    t = np.asarray(ticks, dtype=np.float64)
    t = t[np.isfinite(t)]
    if t.size < 8:
        return None
    t.sort()
    d = np.diff(t)
    d = d[(d > 0.25) & (d < 1.5)]
    if d.size < 4:
        return None
    period = float(np.median(d))
    if bpm_hint and bpm_hint > 0:  # trust the extractor's tempo when it agrees with the median interval
        p2 = 60.0 / bpm_hint
        if abs(p2 - period) / period < 0.04:
            period = p2
    for _ in range(3):  # assign beat numbers, refit, repeat
        k = np.round((t - t[0]) / period)
        A = np.vstack([k, np.ones_like(k)]).T
        (p_new, t0), *_ = np.linalg.lstsq(A, t, rcond=None)
        resid = np.abs(t - (t0 + k * p_new))
        inl = resid < 0.08 * p_new
        if inl.sum() >= 8 and inl.sum() < t.size:  # refit on inliers only
            (p_new, t0), *_ = np.linalg.lstsq(A[inl], t[inl], rcond=None)
        period = float(p_new)
    k = np.round((t - t0) / period)
    resid = np.abs(t - (t0 + k * period))
    conf = float(np.mean(resid < 0.06 * period))
    bpm = 60.0 / period
    first = float(t0 - math.floor(t0 / period) * period)  # earliest grid beat >= 0
    stable = conf >= 0.85 and 60 <= bpm <= 200
    return Grid(bpm=round(bpm, 3), beat_s=round(first, 4), confidence=round(conf, 3), stable=stable)


def beat_times(grid: Grid, until_s: float) -> np.ndarray:
    n = max(0, int((until_s - grid.beat_s) / grid.period) + 1)
    return grid.beat_s + np.arange(n) * grid.period


def downbeat_phase(grid: Grid, bass_env: np.ndarray, env_rate: float, beats_per_bar: int = 4) -> int:
    """Which beat (0..bpb-1) of the grid starts the bar: the phase whose beats carry the most low-end onset
    energy (kick on beat 1). `bass_env` = low-band onset strength at `env_rate` frames/s."""
    if bass_env.size == 0:
        return 0
    beats = beat_times(grid, bass_env.size / env_rate)
    if beats.size < beats_per_bar * 2:
        return 0
    idx = np.clip(np.round(beats * env_rate).astype(int), 0, bass_env.size - 1)
    # a little window around each beat, so a slightly early/late kick still counts
    w = max(1, int(0.03 * env_rate))
    vals = np.array([bass_env[max(0, i - w):i + w + 1].max() for i in idx])
    scores = [float(vals[p::beats_per_bar].mean()) for p in range(beats_per_bar)]
    return int(np.argmax(scores))


# ---------------------------------------------------------------- sections and mix points

@dataclass
class Sections:
    content_start_s: float
    content_end_s: float
    intro_end_s: float
    outro_start_s: float


def energy_db_curve(audio: np.ndarray, sr: int, step_s: float) -> np.ndarray:
    """RMS level in dBFS per `step_s` window."""
    n = max(1, int(step_s * sr))
    m = audio.size // n
    if m == 0:
        return np.array([20 * math.log10(max(float(np.sqrt(np.mean(audio ** 2))), 1e-6))])
    frames = audio[: m * n].reshape(m, n)
    rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-6))


def find_sections(db: np.ndarray, step_s: float, duration_s: float) -> Sections:
    """Leading/trailing silence and intro/outro from a dB-per-step curve. The intro ends where the level
    first reaches the body (within 4 dB of the median of the middle 60 %, smoothed); the outro starts after
    the last point at body level. Intro is capped at 40 % of the track, the outro starts after 55 %."""
    if db.size < 4:
        return Sections(0.0, duration_s, 0.0, duration_s)
    peak = float(db.max())
    audible = np.flatnonzero(db > peak - 45)
    start = float(audible[0] * step_s) if audible.size else 0.0
    end = float(min(duration_s, (audible[-1] + 1) * step_s)) if audible.size else duration_s
    k = max(1, int(round(4.0 / step_s)))  # ~4 s moving average
    sm = np.convolve(db, np.ones(k) / k, mode="same")
    lo, hi = int(db.size * 0.2), max(int(db.size * 0.8), int(db.size * 0.2) + 1)
    body = float(np.median(sm[lo:hi]))
    at_body = np.flatnonzero(sm >= body - 4)
    if at_body.size == 0:
        return Sections(start, end, start, end)
    intro_end = min(float(at_body[0] * step_s), duration_s * 0.4)
    outro_start = max(float((at_body[-1] + 1) * step_s), duration_s * 0.55)
    intro_end = max(intro_end, start)
    outro_start = min(outro_start, end)
    return Sections(round(start, 3), round(end, 3), round(intro_end, 3), round(outro_start, 3))


def snap_to_bar(t: float, downbeat_s: float, bar_s: float, mode: str = "nearest") -> float:
    """Snap a time to the bar grid (downbeat_s + n*bar_s); mode nearest | floor | ceil. Never below downbeat_s."""
    x = (t - downbeat_s) / bar_s
    if mode == "floor":
        n = math.floor(x + 1e-6)
    elif mode == "ceil":
        n = math.ceil(x - 1e-6)
    else:
        n = round(x)
    return downbeat_s + max(0, n) * bar_s


@dataclass
class MixPoints:
    mix_in_s: float
    mix_out_s: float
    last_beat_s: float


def mix_points(sec: Sections, duration_s: float, grid: Grid | None, downbeat_s: float | None,
               beats_per_bar: int = 4, min_out_beats: int = 16) -> MixPoints:
    """Mix-in = the first bar where the music has started (after leading silence). Mix-out = the bar where
    the outro starts, snapped to a 4-bar phrase from the mix-in when that's close, and at least
    `min_out_beats` before the last beat. Without a usable grid: the same in seconds, unsnapped."""
    if grid is None or downbeat_s is None or not grid.stable:
        last = sec.content_end_s
        out = sec.outro_start_s
        if last - out < 8.0:
            out = max(duration_s * 0.5, last - 10.0)
        return MixPoints(round(sec.content_start_s, 3), round(out, 3), round(last, 3))
    p = grid.period
    bar = p * beats_per_bar
    beats = beat_times(grid, duration_s)
    in_range = beats[beats <= sec.content_end_s + 0.05]
    last_beat = float(in_range[-1]) if in_range.size else sec.content_end_s
    mix_in = snap_to_bar(sec.content_start_s, downbeat_s, bar, "ceil")
    if mix_in - sec.content_start_s > bar * 0.75 and mix_in - bar >= downbeat_s:
        mix_in -= bar  # the music starts just before a bar line: take that bar, its pickup is quiet anyway
    out = snap_to_bar(sec.outro_start_s, downbeat_s, bar, "nearest")
    phrase = bar * 4
    to_phrase = snap_to_bar(out, mix_in, phrase, "nearest")
    if abs(to_phrase - out) <= bar * 1.01:
        out = to_phrase
    latest = last_beat - min_out_beats * p
    if out > latest:
        out = snap_to_bar(latest, downbeat_s, bar, "floor")
    out = max(out, mix_in + bar)
    return MixPoints(round(mix_in, 4), round(out, 4), round(last_beat, 4))


# ---------------------------------------------------------------- signal helpers (numpy)

def _stft_mag(audio: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    """|STFT| frames x bins, computed in chunks to bound memory."""
    if audio.size < n_fft:
        audio = np.pad(audio, (0, n_fft - audio.size))
    n = 1 + (audio.size - n_fft) // hop
    win = np.hanning(n_fft).astype(np.float32)
    out = np.empty((n, n_fft // 2 + 1), dtype=np.float32)
    chunk = 2048
    for s in range(0, n, chunk):
        e = min(n, s + chunk)
        idx = (np.arange(s, e)[:, None] * hop) + np.arange(n_fft)[None, :]
        out[s:e] = np.abs(np.fft.rfft(audio[idx] * win, axis=1)).astype(np.float32)
    return out


def onset_envelopes(audio: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray, float, np.ndarray]:
    """(full-band spectral flux, low-band (<150 Hz) flux, frames per second, chroma 12-vector).
    ~170 frames/s (5.8 ms), so beat positions are resolved well below the ~10 ms that is audible as flamming."""
    hop = 256 if sr >= 32000 else 128
    n_fft = 2048 if sr >= 32000 else 1024
    # A frame's flux peaks when an onset reaches its centre, so frame i is about the time (i*hop + n_fft/2)/sr.
    # Pad the front by that much so that frame index / rate is the onset time.
    audio = np.concatenate([np.zeros(n_fft // 2 - hop, dtype=audio.dtype), audio])
    mag = _stft_mag(audio, n_fft, hop)
    logm = np.log1p(10 * mag)
    flux = np.maximum(np.diff(logm, axis=0, prepend=logm[:1]), 0)
    freqs = np.fft.rfftfreq(n_fft, 1 / sr)
    low = freqs < 150
    full = flux.sum(axis=1)
    bass = flux[:, low].sum(axis=1)
    # chroma (for the numpy key fallback): 60..2000 Hz bins folded to pitch classes
    band = (freqs > 60) & (freqs < 2000)
    pcs = np.round(12 * np.log2(freqs[band] / 440.0) + 9).astype(int) % 12
    energy = (mag[:, band] ** 2).sum(axis=0)
    chroma = np.bincount(pcs, weights=energy, minlength=12)
    return full.astype(np.float64), bass.astype(np.float64), sr / hop, chroma


def _normalise_env(env: np.ndarray, rate: float) -> np.ndarray:
    k = max(1, int(rate * 1.0))
    local = np.convolve(env, np.ones(k) / k, mode="same")
    e = np.maximum(env - local, 0)
    s = e.std()
    return e / s if s > 0 else e


def track_beats(env: np.ndarray, rate: float) -> tuple[float, np.ndarray]:
    """Numpy beat tracker -> (bpm, beat ticks in seconds)."""
    e = _normalise_env(env, rate)
    n = e.size
    if n < rate * 8:
        return 0.0, np.array([])
    lo, hi = int(rate * 60 / 200), int(rate * 60 / 60)
    ac = np.correlate(e, e, mode="full")[n - 1: n - 1 + hi + 2]
    lags = np.arange(lo, hi + 1)
    bpms = 60 * rate / lags
    prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 0.9) ** 2)
    score = ac[lo:hi + 1] * prior
    # reward tempos whose double period also correlates (stabilises octave choice)
    dbl = np.clip(lags * 2, 0, ac.size - 1)
    score = score + 0.5 * ac[dbl] * prior
    i = int(np.argmax(score))
    lag = float(lags[i])
    if 0 < i < score.size - 1:  # parabolic refinement
        a, b, c = score[i - 1], score[i], score[i + 1]
        den = a - 2 * b + c
        if den != 0:
            lag += 0.5 * (a - c) / den
    period = lag  # frames
    # phase: comb sum over a slightly smoothed envelope (peaks are ~1 frame wide; the period is fractional)
    sm = np.convolve(e, np.array([0.25, 0.5, 1.0, 0.5, 0.25]), mode="same")
    # Only over ~16 s from where the music starts: a tempo estimate a fraction of a BPM off would smear a
    # whole-track comb; the tracking below follows the real peaks from there on.
    loud = np.flatnonzero(e > 1.0)
    s0 = int(loud[0]) if loud.size else 0
    s1 = min(n, s0 + int(16 * rate))
    best_phase, best = float(s0), -1.0
    for ph in np.arange(s0, s0 + period, 0.5):
        pos = np.arange(ph, s1, period)
        v = float(sm[np.clip(np.round(pos).astype(int), 0, n - 1)].sum())
        if v > best:
            best, best_phase = v, float(ph)
    best_phase -= math.floor(best_phase / period) * period  # back to the first beat position >= 0
    # refine: snap each predicted beat to the strongest frame within ±12 % of a period
    w = max(1, int(period * 0.12))
    ticks = []
    pos = best_phase
    while pos < n:
        c = int(round(pos))
        s0, s1 = max(0, c - w), min(n, c + w + 1)
        j = s0 + int(np.argmax(e[s0:s1])) if s1 > s0 else c
        if e[j] > 0.3:
            jf = float(j)
            if 0 < j < n - 1:  # sub-frame peak
                a, b, cc = e[j - 1], e[j], e[j + 1]
                den = a - 2 * b + cc
                if den < 0:
                    jf += 0.5 * (a - cc) / den
            ticks.append(jf / rate)
            pos = jf + period
        else:
            ticks.append(c / rate)
            pos += period
    return 60 * rate / period, np.asarray(ticks)


# ---------------------------------------------------------------- extraction

@dataclass
class DjAnalysis:
    version: str
    bpm: float | None = None
    bpm_confidence: float | None = None
    beat_s: float | None = None
    downbeat_s: float | None = None
    beats_per_bar: int | None = None
    grid_stable: bool = False
    key: str | None = None
    camelot: str | None = None
    key_strength: float | None = None
    mix_in_s: float | None = None
    mix_out_s: float | None = None
    intro_end_s: float | None = None
    outro_start_s: float | None = None
    last_beat_s: float | None = None
    energy: float | None = None
    energy_curve: list[float] = field(default_factory=list)
    curve_step_s: float | None = None
    duration_s: float | None = None


def analyse_signal(audio: np.ndarray, sr: int, *, version: str, ticks: np.ndarray | None = None,
                   bpm_hint: float | None = None, key: str | None = None, scale: str | None = None,
                   key_strength: float | None = None) -> DjAnalysis:
    """Shared post-processing. Pass Essentia's ticks/key when you have them; otherwise the numpy fallbacks run."""
    duration = audio.size / sr
    full, bass, rate, chroma = onset_envelopes(audio, sr)
    if ticks is None or len(ticks) < 8:
        bpm_hint, ticks = track_beats(full, rate)
    grid = fit_grid(ticks, bpm_hint)
    if key is None:
        from .extractors import key_from_chroma
        k = key_from_chroma(chroma)
        if k:
            key, scale = k.split()
    downbeat = None
    bpb = 4
    if grid is not None:
        ph = downbeat_phase(grid, _normalise_env(bass, rate), rate, bpb)
        downbeat = grid.beat_s + ph * grid.period
    # energy curve: one value per bar when the grid is good, else 2 s; at most MAX_CURVE_POINTS
    step = grid.period * bpb if grid is not None and grid.stable else 2.0
    step = max(step, duration / MAX_CURVE_POINTS)
    db = energy_db_curve(audio, sr, step)
    sec = find_sections(db, step, duration)
    mp_ = mix_points(sec, duration, grid, downbeat, bpb)
    peak = float(db.max()) if db.size else 0.0
    curve = np.clip((db - (peak - 30)) / 30, 0, 1)
    # whole-track energy: loudness of the body plus onset density, 0..1 (comparable across tracks)
    body = float(np.median(db[int(db.size * 0.2): max(int(db.size * 0.8), 1)])) if db.size else -60
    onset_rate = float((_normalise_env(full, rate) > 1.5).sum() / max(duration, 1))
    energy = 0.5 * float(np.clip((body + 30) / 22, 0, 1)) + 0.5 * float(np.clip(onset_rate / 6, 0, 1))
    return DjAnalysis(
        version=version,
        bpm=grid.bpm if grid else (round(bpm_hint, 3) if bpm_hint else None),
        bpm_confidence=grid.confidence if grid else None,
        beat_s=grid.beat_s if grid else None,
        downbeat_s=round(downbeat, 4) if downbeat is not None else None,
        beats_per_bar=bpb if grid else None,
        grid_stable=bool(grid and grid.stable),
        key=f"{key} {scale}" if key and scale else key,
        camelot=camelot(key, scale) if key else None,
        key_strength=round(float(key_strength), 3) if key_strength is not None else None,
        mix_in_s=mp_.mix_in_s, mix_out_s=mp_.mix_out_s,
        intro_end_s=sec.intro_end_s, outro_start_s=sec.outro_start_s, last_beat_s=mp_.last_beat_s,
        energy=round(energy, 3),
        energy_curve=[round(float(x), 3) for x in curve],
        curve_step_s=round(step, 4),
        duration_s=round(duration, 3),
    )


def essentia_available() -> bool:
    try:
        import essentia.standard  # noqa: F401
        return True
    except Exception:  # noqa: BLE001 (ImportError, or SIGILL-free failures on odd CPUs)
        return False


def extract_file(path: Path, use_essentia: bool | None = None) -> DjAnalysis:
    from .extractors import decode

    audio = decode(path, SR, timeout=600)
    if use_essentia is None:
        use_essentia = essentia_available()
    if use_essentia:
        import essentia.standard as es

        bpm, ticks, _conf, _est, _int = es.RhythmExtractor2013(method="degara")(audio)
        key, scale, strength = es.KeyExtractor(profileType="bgate")(audio)
        return analyse_signal(audio, SR, version=ESSENTIA_VERSION, ticks=np.asarray(ticks), bpm_hint=float(bpm),
                              key=key, scale=scale, key_strength=float(strength))
    return analyse_signal(audio, SR, version=BASIC_VERSION)


# ---------------------------------------------------------------- DB

COLUMNS = ("version", "bpm", "bpm_confidence", "beat_s", "downbeat_s", "beats_per_bar", "grid_stable", "key",
           "camelot", "key_strength", "mix_in_s", "mix_out_s", "intro_end_s", "outro_start_s", "last_beat_s",
           "energy", "energy_curve", "curve_step_s", "duration_s")


def write_row(conn: sqlite3.Connection, track_id: str, a: DjAnalysis | dict, audio_hash: str | None = None) -> None:
    d = asdict(a) if isinstance(a, DjAnalysis) else dict(a)
    vals = [d.get(c) for c in COLUMNS]
    vals[COLUMNS.index("grid_stable")] = int(bool(d.get("grid_stable")))
    vals[COLUMNS.index("energy_curve")] = json.dumps(d.get("energy_curve") or [])
    with transaction(conn):
        conn.execute(
            f"INSERT OR REPLACE INTO track_dj (track_id, status, error, {', '.join(COLUMNS)}, analysed_at, "
            f"audio_hash) VALUES (?, 'done', NULL, {', '.join('?' * len(COLUMNS))}, ?, ?)",
            (track_id, *vals, iso(), audio_hash))


def reuse_row(conn: sqlite3.Connection, track_id: str, version: str, audio_hash: str | None) -> bool:
    """Satisfy `track_id` from a finished `track_dj` row with the same audio (schema v13, see `audiohash`):
    its own row after a retag is re-stamped, another track's (a move, rename or copy) is copied. A row of
    `version`, or any version when only the basic analyser is available. False if there is none."""
    if not audio_hash:
        return False
    src = conn.execute(
        "SELECT track_id FROM track_dj WHERE audio_hash = ? AND status = 'done' AND (version = ? OR ? = ?) "
        "ORDER BY track_id = ? DESC, version = ? DESC, analysed_at DESC LIMIT 1",
        (audio_hash, version, version, BASIC_VERSION, track_id, ESSENTIA_VERSION)).fetchone()
    if src is None:
        return False
    with transaction(conn):
        if src[0] == track_id:
            conn.execute("UPDATE track_dj SET analysed_at = ? WHERE track_id = ?", (iso(), track_id))
        else:
            cols = ", ".join(COLUMNS)
            conn.execute(
                f"INSERT OR REPLACE INTO track_dj (track_id, status, error, {cols}, analysed_at, audio_hash) "
                f"SELECT ?, status, error, {cols}, ?, audio_hash FROM track_dj WHERE track_id = ?",
                (track_id, iso(), src[0]))
    return True


def write_failure(conn: sqlite3.Connection, track_id: str, version: str, error: str) -> None:
    with transaction(conn):
        conn.execute("INSERT OR REPLACE INTO track_dj (track_id, version, status, error, analysed_at) "
                     "VALUES (?, ?, 'failed', ?, ?)", (track_id, version, error[:500], iso()))


def pending(conn: sqlite3.Connection, version: str, limit: int | None = None) -> list[sqlite3.Row]:
    """Tracks to (re)analyse: no row, the file changed since, an older/basic version once Essentia is here,
    or a failure older than RETRY_DAYS."""
    sql = """
        SELECT t.id, t.path, t.duration_s, t.mtime, t.flac_bytes FROM tracks t LEFT JOIN track_dj d ON d.track_id = t.id
        WHERE t.missing = 0 AND (
            d.track_id IS NULL
            OR d.analysed_at < strftime('%Y-%m-%dT%H:%M:%SZ', t.mtime, 'unixepoch')
            OR (d.status = 'done' AND d.version != :v AND :v = 'dj-essentia-1')
            OR (d.status = 'failed' AND d.analysed_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', :retry)))
        ORDER BY d.track_id IS NOT NULL, t.added_at DESC, t.id"""
    params: dict[str, Any] = {"v": version, "retry": f"-{RETRY_DAYS} days"}
    if limit is not None:
        sql += " LIMIT :lim"
        params["lim"] = int(limit)
    return conn.execute(sql, params).fetchall()


def _init_worker() -> None:
    import os
    try:
        os.nice(15)
    except OSError:
        pass


def _work(path: str, use_essentia: bool) -> dict:
    return asdict(extract_file(Path(path), use_essentia))


def run(conn: sqlite3.Connection, library_root: Path, time_budget_s: float = 50 * 60, limit: int | None = None,
        workers: int = 1, in_process: bool = False) -> dict[str, Any]:
    """The `automix` job body. Returns {analysed, reused, failed, left, version}; `analysed` includes the
    `reused` tracks, satisfied from a row with the same audio hash without decoding. Stops starting new tracks
    once the time budget is used; whatever is unfinished is picked up by the next run. Leftover budget goes to
    hashing rows analysed before schema v13 (`audiohash.backfill`)."""
    use_es = essentia_available()
    version = ESSENTIA_VERSION if use_es else BASIC_VERSION
    todo = pending(conn, version, limit)
    res: dict[str, Any] = {"version": version, "analysed": 0, "reused": 0, "failed": 0, "left": len(todo)}
    t0 = time.monotonic()
    root = Path(library_root)
    if not todo:
        audiohash.backfill(conn, root, "track_dj", time_budget_s=time_budget_s)
        return res
    hashes: dict[str, str | None] = {}

    def reused(row) -> bool:
        h = hashes[row["id"]] = audiohash.current(conn, root, row["id"], row["path"], row["mtime"],
                                                  row["flac_bytes"])
        if reuse_row(conn, row["id"], version, h):
            res["analysed"] += 1
            res["reused"] += 1
            return True
        return False

    def done(row, fut_result=None, err: Exception | None = None):
        if err is None:
            write_row(conn, row["id"], fut_result, hashes.get(row["id"]))
            res["analysed"] += 1
        else:
            log.warning("automix analysis failed for %s: %s", row["path"], err)
            write_failure(conn, row["id"], version, f"{type(err).__name__}: {err}")
            res["failed"] += 1

    if in_process:  # tests / CLI
        for row in todo:
            if time.monotonic() - t0 > time_budget_s:
                break
            if reused(row):
                continue
            try:
                done(row, _work(str(root / row["path"]), use_es))
            except Exception as e:  # noqa: BLE001
                done(row, err=e)
    else:
        pool = ProcessPoolExecutor(max_workers=max(1, workers), mp_context=mp.get_context("spawn"),
                                   initializer=_init_worker)
        queue = list(todo)
        flying: dict[Future, sqlite3.Row] = {}
        try:
            while queue or flying:
                while queue and len(flying) < workers and time.monotonic() - t0 < time_budget_s:
                    row = queue.pop(0)
                    if reused(row):
                        continue
                    flying[pool.submit(_work, str(root / row["path"]), use_es)] = row
                if not flying:
                    break
                fin, _ = wait(flying, return_when=FIRST_COMPLETED)
                for f in fin:
                    row = flying.pop(f)
                    try:
                        done(row, f.result())
                    except Exception as e:
                        done(row, err=e)
                        if "BrokenProcessPool" in type(e).__name__:
                            raise
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
    res["left"] = len(todo) - res["analysed"] - res["failed"]
    if not res["left"]:
        audiohash.backfill(conn, root, "track_dj", time_budget_s=max(0.0, time_budget_s - (time.monotonic() - t0)))
    log.info("automix analysis: %s", res)
    return res


# ---------------------------------------------------------------- reading (for the API)

def row_to_mix(r: sqlite3.Row | dict | None) -> dict | None:
    """Compact Track.mix (openapi TrackMix) from a track_dj row (or a TRACK_SELECT row with dj_* columns)."""
    if r is None:
        return None
    g = (lambda k: r[k]) if isinstance(r, dict) else (lambda k: r[k] if k in r.keys() else None)
    if g("status") != "done" or g("bpm") is None and g("mix_out_s") is None:
        return None
    out = {
        "bpm": g("bpm"), "beatS": g("beat_s"), "downbeatS": g("downbeat_s"), "beatsPerBar": g("beats_per_bar"),
        "gridStable": bool(g("grid_stable")), "key": g("key"), "camelot": g("camelot"),
        "mixInS": g("mix_in_s"), "mixOutS": g("mix_out_s"), "introEndS": g("intro_end_s"),
        "outroStartS": g("outro_start_s"), "lastBeatS": g("last_beat_s"), "energy": g("energy"),
    }
    return {k: v for k, v in out.items() if v is not None}


def detail(conn: sqlite3.Connection, track_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM track_dj WHERE track_id = ?", (track_id,)).fetchone()
    if r is None:
        return None
    if r["status"] != "done":
        return {"trackId": track_id, "status": "failed", "version": r["version"], "error": r["error"],
                "analysedAt": r["analysed_at"]}
    return {"trackId": track_id, "status": "done", "version": r["version"], "analysedAt": r["analysed_at"],
            "bpmConfidence": r["bpm_confidence"], "keyStrength": r["key_strength"],
            "energyCurve": json.loads(r["energy_curve"] or "[]"), "curveStepS": r["curve_step_s"],
            "durationS": r["duration_s"], "mix": row_to_mix(r)}
