"""DJ automix analysis: Camelot, grid fitting from (Essentia-style) ticks, downbeats, sections, mix points,
the numpy fallback on a synthetic track, and the resumable job + API exposure."""

import numpy as np
import pytest

from nasmusic.ai import djmix

SR = 22050


# ---------------------------------------------------------------- Camelot

@pytest.mark.parametrize("key,scale,code", [
    ("C", "major", "8B"), ("A", "minor", "8A"), ("G", "major", "9B"), ("E", "minor", "9A"),
    ("F", "major", "7B"), ("D", "minor", "7A"), ("Bb", "major", "6B"), ("A#", "major", "6B"),
    ("Eb", "minor", "2A"), ("D#", "minor", "2A"), ("F#", "major", "2B"), ("Gb", "major", "2B"),
    ("B", "major", "1B"), ("G#", "minor", "1A"), ("Ab", "minor", "1A"), ("C#", "minor", "12A"),
])
def test_camelot(key, scale, code):
    assert djmix.camelot(key, scale) == code


def test_camelot_strings_and_distance():
    assert djmix.camelot("A minor") == "8A"
    assert djmix.camelot("Am") == "8A"
    assert djmix.camelot("F#") == "2B"
    assert djmix.camelot("H minor") is None and djmix.camelot(None) is None
    assert djmix.key_distance("8A", "8A") == 0
    assert djmix.key_distance("8A", "9A") == 1 and djmix.key_distance("12A", "1A") == 1
    assert djmix.key_distance("8A", "8B") == 1          # relative major
    assert djmix.key_distance("8A", "10A") == 2         # energy boost
    assert djmix.key_distance("8A", "2A") == 6          # opposite side of the wheel
    assert djmix.key_distance("8A", "x") is None


# ---------------------------------------------------------------- grid

def test_fit_grid_from_ticks_with_noise_and_misses():
    rng = np.random.default_rng(1)
    period = 60 / 126.0
    t = 0.37 + np.arange(400) * period + rng.normal(0, 0.008, 400)
    t = np.delete(t, [10, 11, 50, 200])                 # missed beats
    t = np.sort(np.append(t, [5.123, 99.9]))            # spurious ticks
    g = djmix.fit_grid(t, bpm_hint=125.9)
    assert g is not None and g.stable
    assert abs(g.bpm - 126.0) < 0.05
    assert abs(g.beat_s - 0.37) < 0.01
    assert g.confidence > 0.9


def test_fit_grid_first_beat_is_earliest_at_or_after_zero():
    period = 0.5
    g = djmix.fit_grid(3.2 + np.arange(100) * period)
    assert abs(g.beat_s - 0.2) < 1e-3                   # extrapolated back to the first beat >= 0


def test_fit_grid_unstable_tempo_drift():
    # rubato: tempo drifting 100 -> 130 BPM
    periods = 60 / np.linspace(100, 130, 300)
    g = djmix.fit_grid(np.cumsum(periods))
    assert g is not None and not g.stable


def test_fit_grid_too_few():
    assert djmix.fit_grid([0.5, 1.0, 1.5]) is None


def test_downbeat_phase_from_bass():
    g = djmix.Grid(bpm=120.0, beat_s=0.25, confidence=1.0, stable=True)
    rate = 100.0
    env = np.zeros(int(60 * rate))
    for k, b in enumerate(djmix.beat_times(g, 59)):
        env[int(round(b * rate))] = 3.0 if k % 4 == 2 else 1.0   # kick-heavy beat is the third grid beat
    assert djmix.downbeat_phase(g, env, rate) == 2


# ---------------------------------------------------------------- sections and mix points

def _curve(step, duration, intro, outro, lead=1.0, tail=2.0):
    t = np.arange(0, duration, step)
    db = np.full(t.size, -12.0)
    db[t < intro] = -24
    db[t >= outro] = -24
    db[t < lead] = -90
    db[t >= duration - tail] = -90
    return db


def test_find_sections():
    db = _curve(0.5, 240, intro=30, outro=210)
    s = djmix.find_sections(db, 0.5, 240)
    assert s.content_start_s == pytest.approx(1.0, abs=0.6)
    assert s.content_end_s == pytest.approx(238, abs=0.6)
    assert 27 <= s.intro_end_s <= 31
    assert 209 <= s.outro_start_s <= 213


def test_mix_points_snap_to_bars_and_phrases():
    g = djmix.Grid(bpm=120.0, beat_s=0.1, confidence=1, stable=True)
    sec = djmix.Sections(content_start_s=1.0, content_end_s=238.0, intro_end_s=30.0, outro_start_s=210.3)
    m = djmix.mix_points(sec, 240.0, g, downbeat_s=0.6)
    bar = 2.0
    assert ((m.mix_in_s - 0.6) / bar) == pytest.approx(round((m.mix_in_s - 0.6) / bar))
    assert m.mix_in_s == pytest.approx(0.6)               # music starts 0.4 s after a bar line -> that bar
    assert ((m.mix_out_s - m.mix_in_s) / (bar * 4)) == pytest.approx(round((m.mix_out_s - m.mix_in_s) / (bar * 4)))
    assert 207 <= m.mix_out_s <= 213
    assert m.last_beat_s <= 238.05 and m.last_beat_s > 237


def test_mix_out_leaves_room_before_last_beat():
    g = djmix.Grid(bpm=128.0, beat_s=0.0, confidence=1, stable=True)
    sec = djmix.Sections(0.0, 200.0, 10.0, 199.0)       # no real outro: ends at full energy
    m = djmix.mix_points(sec, 200.0, g, downbeat_s=0.0)
    assert m.last_beat_s - m.mix_out_s >= 16 * 60 / 128 - 1e-6


def test_mix_points_without_grid_are_seconds():
    sec = djmix.Sections(2.0, 180.0, 20.0, 176.0)
    m = djmix.mix_points(sec, 182.0, None, None)
    assert m.mix_in_s == 2.0 and m.last_beat_s == 180.0 and m.mix_out_s <= 172.0


# ---------------------------------------------------------------- numpy fallback on a synthetic track

def synth(bpm=124.0, bars=96, lead=1.3, tail=2.0, intro_bars=16, outro_bars=16, sr=SR, key_hz=220.0):
    """Hi-hat on every beat, kick on beat 1 and 3 in the body plus a sustained A-minor pad; intro and outro are
    hats + quiet kick on beat 1 only. Returns (audio, first_downbeat_s)."""
    p = 60 / bpm
    n = int((lead + bars * 4 * p + tail) * sr)
    a = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(0)
    hat = (rng.normal(0, 1, int(0.03 * sr)) * np.exp(-np.linspace(0, 8, int(0.03 * sr)))).astype(np.float32)
    kt = np.arange(int(0.15 * sr)) / sr
    kick = (np.sin(2 * np.pi * 55 * kt) * np.exp(-kt * 25)).astype(np.float32)
    for b in range(bars * 4):
        t = lead + b * p
        i = int(t * sr)
        bar = b // 4
        body = intro_bars <= bar < bars - outro_bars
        a[i:i + hat.size] += 0.15 * hat[: n - i]
        if b % 4 == 0 or (body and b % 4 == 2):
            a[i:i + kick.size] += (0.9 if body else 0.5) * kick[: n - i]
    s0, s1 = int((lead + intro_bars * 4 * p) * sr), int((lead + (bars - outro_bars) * 4 * p) * sr)
    tt = np.arange(s1 - s0) / sr
    pad = sum(np.sin(2 * np.pi * f * tt) for f in (key_hz, key_hz * 1.2, key_hz * 1.5))  # A C E
    a[s0:s1] += (0.12 * pad).astype(np.float32)
    return a, lead


def test_numpy_analysis_of_synthetic_track():
    audio, lead = synth()
    a = djmix.analyse_signal(audio, SR, version=djmix.BASIC_VERSION)
    p = 60 / 124
    assert a.grid_stable
    assert abs(a.bpm - 124) < 0.3
    # downbeat on the kick (beat 1 of a bar), within 30 ms, modulo one bar
    off = ((a.downbeat_s - lead) / (4 * p)) % 1
    assert min(off, 1 - off) * 4 * p < 0.03
    assert a.mix_in_s == pytest.approx(lead, abs=0.05)
    # mix-out is a bar line in the outro region (last 16 bars), at least 16 beats before the last beat
    outro = lead + 80 * 4 * p
    assert outro - 8 * p <= a.mix_out_s <= a.last_beat_s - 16 * p + 1e-6
    assert abs(((a.mix_out_s - a.downbeat_s) / (4 * p)) - round((a.mix_out_s - a.downbeat_s) / (4 * p))) < 0.02
    assert a.camelot == "8A"   # A minor pad
    assert 0 <= a.energy <= 1 and len(a.energy_curve) > 50 and max(a.energy_curve) == 1.0
