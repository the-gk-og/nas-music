"""Fake-lossless checker (schema v11): the spectral cliff of a lossy source, upsampled hi-res, padded 24-bit,
and the resumable job."""
import shutil
import subprocess

import numpy as np
import pytest

from nasmusic.library import lossless
from tests.conftest import track_ids


def _noise(rate=44_100, seconds=12, cutoff=None, seed=1):
    x = np.random.default_rng(seed).normal(0, 0.1, rate * seconds)
    if cutoff:
        f = np.fft.rfft(x)
        f[np.fft.rfftfreq(x.size, 1 / rate) > cutoff] = 0
        x = np.fft.irfft(f, x.size) + np.random.default_rng(seed + 1).normal(0, 1e-6, x.size)
    return x


def _pink(rate=44_100, seconds=12, seed=3):
    w = np.fft.rfft(np.random.default_rng(seed).normal(0, 1, rate * seconds))
    fr = np.fft.rfftfreq(rate * seconds, 1 / rate)
    w[1:] /= np.sqrt(fr[1:])
    x = np.fft.irfft(w, rate * seconds)
    return x / np.max(np.abs(x)) * 0.5


@pytest.mark.parametrize("cut,guess", [(16_000, "128"), (19_000, "192"), (11_000, "64")])
def test_lowpassed_noise_is_a_lossy_source(cut, guess):
    r = lossless.classify(_noise(cutoff=cut), 44_100)
    assert r.verdict == "lossy_source"
    assert abs(r.cutoff_hz - cut) <= 400
    assert guess in r.source_guess


def test_full_band_audio_is_ok():
    assert lossless.classify(_noise(), 44_100).verdict == "ok"
    assert lossless.classify(_pink(), 44_100).verdict == "ok"
    assert lossless.classify(_noise(rate=96_000, seconds=6), 96_000).verdict == "ok"


def test_320k_territory_is_only_suspect():
    r = lossless.classify(_noise(cutoff=20_500), 44_100)
    assert r.verdict == "suspect"


def test_hi_res_with_nothing_above_cd_is_upsampled():
    r = lossless.classify(_noise(rate=96_000, seconds=6, cutoff=22_050), 96_000)
    assert r.verdict == "upsampled" and "CD" in r.source_guess


def test_silence_is_unknown():
    assert lossless.classify(np.zeros(44_100 * 5), 44_100).verdict == "unknown"
    assert lossless.classify(np.zeros(100), 44_100).verdict == "unknown"


@pytest.fixture
def fakes(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")

    def ff(*a):
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *a], check=True)

    ff("-f", "lavfi", "-i", "anoisesrc=d=35:c=pink:r=44100:a=0.3", "-ac", "2", "-sample_fmt", "s16",
       str(tmp_path / "real.flac"))
    ff("-i", str(tmp_path / "real.flac"), "-b:a", "128k", str(tmp_path / "m.mp3"))
    ff("-i", str(tmp_path / "m.mp3"), "-sample_fmt", "s16", str(tmp_path / "fake.flac"))
    ff("-i", str(tmp_path / "real.flac"), "-sample_fmt", "s32", str(tmp_path / "pad24.flac"))
    return tmp_path


def test_check_real_files(fakes):
    real = lossless.check(fakes / "real.flac", 35, 44_100, 16)
    fake = lossless.check(fakes / "fake.flac", 35, 44_100, 16)
    pad = lossless.check(fakes / "pad24.flac", 35, 44_100, 24)
    assert real.verdict == "ok", real
    assert fake.verdict == "lossy_source" and 15_500 < fake.cutoff_hz < 18_000, fake
    assert pad.verdict == "upsampled" and "padded" in pad.source_guess
    assert lossless.check(fakes / "nope.flac", 35, 44_100, 16).verdict == "error"


def test_job_is_resumable_and_rechecks_changed_files(cfg, conn, scanned):
    r = lossless.run(conn, cfg.library_root, limit=2)
    assert r == {"pending": 5, "checked": 2, "flagged": r["flagged"], "remaining": 3}
    r = lossless.run(conn, cfg.library_root)
    assert r["checked"] == 3 and r["remaining"] == 0
    assert lossless.run(conn, cfg.library_root)["pending"] == 0
    ids = track_ids(conn)
    conn.execute("UPDATE tracks SET mtime = mtime + 1 WHERE id = ?", (ids["Song 1"],))
    assert lossless.run(conn, cfg.library_root)["checked"] == 1
    c = lossless.counts(conn)
    assert c["lossless"] == 5 and c["checked"] == 5
