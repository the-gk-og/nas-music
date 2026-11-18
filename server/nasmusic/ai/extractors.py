"""Audio feature extractors, one per `model_version`.

* `basic-1`: decode with ffmpeg, numpy spectral / MFCC-style statistics. No ML dependencies; used
  for dev, tests, and whenever Essentia is not importable.
* `effnet-discogs-1`: Essentia Discogs-EffNet embeddings (mean-pooled, 1280-d) plus genre, mood,
  danceability and voice heads, BPM, key and energy. Needs the `analysis` extra and the model files
  (see `models.py`).

An extractor turns a file (or an in-memory waveform, for discovery previews) into a `Features`
dict. Extractors run inside analyser worker processes, so construction may be slow (model load)
but happens once per worker.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .common import BASIC_VERSION

log = logging.getLogger("nasmusic.ai.extractors")

EFFNET_VERSION = "effnet-discogs-1"


class ExtractionError(RuntimeError):
    pass


@dataclass
class Features:
    embedding: np.ndarray
    bpm: float | None = None
    key: str | None = None
    energy: float | None = None
    danceability: float | None = None
    tags: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------- decoding

def ffmpeg_path() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ExtractionError("ffmpeg not found on PATH")
    return exe


def decode(path: Path | None, sr: int, *, data: bytes | None = None, offset_s: float = 0.0,
           max_seconds: float | None = None, timeout: float = 300) -> np.ndarray:
    """Decode a file (or bytes, via stdin) to mono float32 at `sr` Hz. Never writes to disk."""
    cmd = [ffmpeg_path(), "-nostdin", "-v", "error"]
    if offset_s > 0:
        cmd += ["-ss", f"{offset_s:.2f}"]
    cmd += ["-i", "pipe:0" if data is not None else str(path)]
    if max_seconds:
        cmd += ["-t", f"{max_seconds:.2f}"]
    cmd += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"]
    try:
        proc = subprocess.run(cmd, input=data, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise ExtractionError("ffmpeg timed out") from e
    if proc.returncode != 0:
        raise ExtractionError(f"ffmpeg failed: {proc.stderr.decode(errors='replace')[-300:]}")
    audio = np.frombuffer(proc.stdout, dtype="<f4").astype(np.float32)
    if audio.size < sr:  # under a second of audio
        raise ExtractionError("decoded audio is empty or shorter than 1 s")
    return audio


def segments(duration_s: float | None, max_seconds: float, excerpts: int = 0,
             excerpt_seconds: float = 40.0) -> list[tuple[float, float | None]]:
    """Which parts of a track to analyse, as (offset, length) pairs.

    With `excerpts` > 0 and a long enough track: that many excerpts spread over the track (at 20 %,
    50 %, 80 % for three), which keeps EffNet time per track roughly constant. Otherwise the whole
    track, centre-cropped to `max_seconds`.
    """
    if excerpts > 0 and duration_s and duration_s > excerpts * excerpt_seconds * 1.5:
        if excerpts == 1:
            centres = [0.5]
        else:
            centres = [0.2 + 0.6 * k / (excerpts - 1) for k in range(excerpts)]
        return [(max(0.0, c * duration_s - excerpt_seconds / 2), excerpt_seconds) for c in centres]
    if not max_seconds or not duration_s or duration_s <= max_seconds:
        return [(0.0, max_seconds or None)]
    return [((duration_s - max_seconds) / 2.0, max_seconds)]


def load_segments(path: Path, sr: int, duration_s: float | None, max_seconds: float, excerpts: int,
                  excerpt_seconds: float) -> np.ndarray:
    parts = [decode(path, sr, offset_s=off, max_seconds=length)
             for off, length in segments(duration_s, max_seconds, excerpts, excerpt_seconds)]
    return parts[0] if len(parts) == 1 else np.concatenate(parts)


# ---------------------------------------------------------------- key detection (shared)

_KEYS = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


def key_from_chroma(chroma: np.ndarray) -> str | None:
    if not np.any(chroma > 0):
        return None
    best, best_r = None, -2.0
    for i in range(12):
        for profile, mode in ((_MAJOR, "major"), (_MINOR, "minor")):
            r = np.corrcoef(chroma, np.roll(profile, i))[0, 1]
            if np.isfinite(r) and r > best_r:
                best, best_r = f"{_KEYS[i]} {mode}", r
    return best


# ---------------------------------------------------------------- basic-1

def _mel_filterbank(sr: int, n_fft: int, n_mels: int, fmin: float = 30.0, fmax: float | None = None):
    fmax = fmax or sr / 2
    mel = lambda f: 2595 * np.log10(1 + f / 700)
    imel = lambda m: 700 * (10 ** (m / 2595) - 1)
    pts = imel(np.linspace(mel(fmin), mel(fmax), n_mels + 2))
    bins = np.fft.rfftfreq(n_fft, 1 / sr)
    fb = np.zeros((n_mels, bins.size), dtype=np.float32)
    for m in range(n_mels):
        lo, c, hi = pts[m], pts[m + 1], pts[m + 2]
        up = (bins - lo) / max(c - lo, 1e-9)
        down = (hi - bins) / max(hi - c, 1e-9)
        fb[m] = np.maximum(0, np.minimum(up, down))
    return fb


def _dct_matrix(n_in: int, n_out: int) -> np.ndarray:
    k = np.arange(n_out)[:, None]
    n = np.arange(n_in)[None, :]
    return np.cos(np.pi / n_in * (n + 0.5) * k).astype(np.float32)


class BasicExtractor:
    """Pure numpy features. Embedding layout (107 dims):
    40 log-mel means, 13 MFCC means, 13 MFCC stds, 12 chroma, 29 summary stats."""

    version = BASIC_VERSION
    sr = 22050
    n_fft = 2048
    hop = 1024

    def __init__(self, data_dir: Path | None = None, max_seconds: float = 480, excerpts: int = 0,
                 excerpt_seconds: float = 40.0):
        self.max_seconds = max_seconds
        self.excerpts, self.excerpt_seconds = excerpts, excerpt_seconds
        self.window = np.hanning(self.n_fft).astype(np.float32)
        self.mel = _mel_filterbank(self.sr, self.n_fft, 40)
        self.dct = _dct_matrix(40, 13)
        freqs = np.fft.rfftfreq(self.n_fft, 1 / self.sr)
        self.freqs = freqs
        pitch = np.full(freqs.shape, -1)
        ok = (freqs >= 55) & (freqs <= 2000)
        pitch[ok] = np.round(12 * np.log2(freqs[ok] / 440.0) + 9).astype(int) % 12  # 0 = C
        self.pitch = pitch

    def extract_file(self, path: Path, duration_s: float | None = None) -> Features:
        return self.extract_audio(load_segments(path, self.sr, duration_s, self.max_seconds, self.excerpts,
                                                self.excerpt_seconds))

    def extract_audio(self, audio: np.ndarray, sr: int | None = None) -> Features:
        if sr and sr != self.sr:
            idx = np.linspace(0, audio.size - 1, int(audio.size * self.sr / sr))
            audio = np.interp(idx, np.arange(audio.size), audio).astype(np.float32)
        if audio.size < self.n_fft * 4:
            raise ExtractionError("audio too short")
        n = 1 + (audio.size - self.n_fft) // self.hop
        frames = np.lib.stride_tricks.as_strided(
            audio, shape=(n, self.n_fft), strides=(audio.strides[0] * self.hop, audio.strides[0]))
        spec = np.abs(np.fft.rfft(frames * self.window, axis=1)).astype(np.float32)  # (n, bins)
        power = spec ** 2
        melspec = np.log10(power @ self.mel.T + 1e-10)  # (n, 40)
        mfcc = melspec @ self.dct.T  # (n, 13)
        rms = np.sqrt(np.mean(frames ** 2, axis=1) + 1e-12)
        rms_db = 20 * np.log10(rms + 1e-9)
        total = power.sum(axis=1) + 1e-12
        centroid = (power @ self.freqs) / total
        cum = np.cumsum(power, axis=1)
        rolloff = self.freqs[np.argmax(cum >= 0.85 * cum[:, -1:], axis=1)]
        flatness = np.exp(np.mean(np.log(power + 1e-12), axis=1)) / (np.mean(power, axis=1) + 1e-12)
        signs = np.signbit(frames)
        zcr = np.mean(signs[:, 1:] != signs[:, :-1], axis=1)
        flux = np.r_[0, np.sqrt(np.sum(np.diff(np.log1p(spec), axis=0).clip(min=0) ** 2, axis=1))]
        chroma = np.zeros(12)
        mean_power = power.mean(axis=0)
        for p in range(12):
            chroma[p] = mean_power[self.pitch == p].sum()
        chroma = chroma / (chroma.sum() + 1e-12)

        bpm, beat_strength = self._tempo(flux)
        loud = float(np.mean(rms_db))
        stats = np.array([
            loud / 20, np.std(rms_db) / 10, np.mean(centroid) / 2000, np.std(centroid) / 2000,
            np.mean(rolloff) / 4000, np.std(rolloff) / 4000, np.mean(flatness) * 10, np.std(flatness) * 10,
            np.mean(zcr) * 10, np.std(zcr) * 10, np.mean(flux), np.std(flux), bpm / 100, beat_strength,
            *np.percentile(rms_db, [10, 50, 90]) / 20,
            *np.mean(melspec[:, :8], axis=0) / 5,          # low-end detail
            np.mean(np.abs(np.diff(rms_db))) / 5,
            np.max(rms_db) / 20, np.min(rms_db) / 20, np.median(flux),
        ], dtype=np.float32)
        emb = np.concatenate([
            melspec.mean(axis=0) / 5, mfcc.mean(axis=0) / 20, mfcc.std(axis=0) / 10, chroma * 4, stats,
        ]).astype(np.float32)
        emb = np.nan_to_num(emb, nan=0.0, posinf=0.0, neginf=0.0)
        loud01 = float(np.clip((loud + 45) / 40, 0, 1))
        bright01 = float(np.clip(np.mean(centroid) / 4000, 0, 1))
        onset01 = float(np.clip(np.mean(flux) / (np.mean(flux) + np.std(flux) + 1e-9), 0, 1))
        energy = float(np.clip(0.6 * loud01 + 0.25 * bright01 + 0.15 * onset01, 0, 1))
        return Features(
            embedding=emb, bpm=round(bpm, 1) if bpm else None, key=key_from_chroma(chroma),
            energy=round(energy, 4), danceability=round(float(np.clip(beat_strength, 0, 1)), 4), tags={},
        )

    def _tempo(self, flux: np.ndarray) -> tuple[float, float]:
        """Autocorrelation tempo from spectral flux. Returns (bpm, strength 0..1)."""
        fps = self.sr / self.hop
        x = flux - flux.mean()
        if x.size < 64 or not np.any(x):
            return 0.0, 0.0
        ac = np.correlate(x, x, mode="full")[x.size - 1:]
        ac = ac / (ac[0] + 1e-12)
        lags = np.arange(ac.size)
        lo, hi = int(fps * 60 / 200), int(fps * 60 / 60)
        if hi >= ac.size or lo < 1:
            return 0.0, 0.0
        seg = ac[lo:hi + 1]
        best = int(np.argmax(seg)) + lo
        bpm = 60 * fps / lags[best]
        return float(bpm), float(max(seg.max(), 0.0))


# ---------------------------------------------------------------- effnet-discogs-1

class EssentiaExtractor:
    """Discogs-EffNet embeddings + classifier heads, via essentia-tensorflow."""

    version = EFFNET_VERSION
    sr = 16000

    def __init__(self, data_dir: Path, max_seconds: float = 480, excerpts: int = 3,
                 excerpt_seconds: float = 40.0):
        import essentia
        import essentia.standard as es

        from . import models

        essentia.log.infoActive = False
        essentia.log.warningActive = False
        self.es = es
        self.max_seconds = max_seconds
        self.excerpts, self.excerpt_seconds = excerpts, excerpt_seconds
        mdir = models.ensure_models(Path(data_dir), self.version)
        spec = models.MODEL_SETS[self.version]
        self.embedder = es.TensorflowPredictEffnetDiscogs(
            graphFilename=str(mdir / spec.embedding.filename), output="PartitionedCall:1")
        self.heads = []
        for head in spec.heads:
            algo = es.TensorflowPredict2D(
                graphFilename=str(mdir / head.filename), input=head.input, output=head.output)
            self.heads.append((head, algo, models.head_classes(Path(data_dir), self.version, head)))
        self.bpm_algo = es.PercivalBpmEstimator(sampleRate=self.sr)
        self.key_algo = es.KeyExtractor(sampleRate=self.sr, profileType="bgate")

    def extract_file(self, path: Path, duration_s: float | None = None) -> Features:
        audio = load_segments(path, self.sr, duration_s, self.max_seconds, self.excerpts, self.excerpt_seconds)
        return self.extract_audio(audio, self.sr)

    def extract_audio(self, audio: np.ndarray, sr: int | None = None) -> Features:
        if sr and sr != self.sr:
            audio = self.es.Resample(inputSampleRate=sr, outputSampleRate=self.sr)(audio)
        audio = np.ascontiguousarray(audio, dtype=np.float32)
        patches = self.embedder(audio)  # (n_patches, 1280)
        if patches.size == 0:
            raise ExtractionError("no embedding patches (audio too short?)")
        emb = patches.mean(axis=0).astype(np.float32)
        tags: dict[str, float] = {}
        dance = None
        moods: dict[str, float] = {}
        for head, algo, classes in self.heads:
            preds = np.asarray(algo(patches)).mean(axis=0)
            if head.kind == "multilabel":
                top = np.argsort(preds)[::-1][: head.top_n]
                for i in top:
                    if preds[i] >= head.min_prob:
                        tags[f"{head.tag_prefix}{classes[i]}"] = round(float(preds[i]), 3)
            else:  # binary softmax: probability of the named positive class
                p = float(preds[classes.index(head.positive)])
                if head.name == "danceability":
                    dance = p
                else:
                    moods[head.name] = p
                    tags[f"{head.tag_prefix}{head.positive}"] = round(p, 3)
        try:
            bpm = float(self.bpm_algo(audio))
        except Exception:  # noqa: BLE001
            bpm = None
        try:
            k, scale, _strength = self.key_algo(audio)
            key = f"{k} {scale}"
        except Exception:  # noqa: BLE001
            key = None
        rms_db = 20 * np.log10(np.sqrt(np.mean(audio ** 2)) + 1e-9)
        loud01 = float(np.clip((rms_db + 40) / 32, 0, 1))
        energy = 0.5 * loud01 + 0.2 * moods.get("mood_aggressive", 0.5) + 0.2 * moods.get("mood_party", 0.5) \
            + 0.1 * (1 - moods.get("mood_relaxed", 0.5))
        return Features(embedding=emb, bpm=round(bpm, 1) if bpm else None, key=key,
                        energy=round(float(np.clip(energy, 0, 1)), 4),
                        danceability=round(dance, 4) if dance is not None else None, tags=tags)


# ---------------------------------------------------------------- selection

def essentia_available() -> bool:
    try:
        import essentia.standard  # noqa: F401
    except Exception:  # noqa: BLE001  (ImportError, or a crashing native lib)
        return False
    return True


def effective_version(requested: str) -> str:
    """The version that will actually be produced: falls back to basic-1 without Essentia."""
    if requested == BASIC_VERSION:
        return requested
    if requested == EFFNET_VERSION and essentia_available():
        return requested
    log.warning("model_version %r unavailable (Essentia not importable or unknown); using %s",
                requested, BASIC_VERSION)
    return BASIC_VERSION


def make_extractor(version: str, data_dir: Path, max_seconds: float = 480, excerpts: int = 3,
                   excerpt_seconds: float = 40.0):
    if version == EFFNET_VERSION:
        return EssentiaExtractor(data_dir, max_seconds=max_seconds, excerpts=excerpts,
                                 excerpt_seconds=excerpt_seconds)
    if version == BASIC_VERSION:  # cheap enough to look at the whole track
        return BasicExtractor(data_dir, max_seconds=max_seconds)
    raise ValueError(f"unknown model_version {version!r}")
