"""Piper text-to-speech for the radio DJ, and the cache of spoken clips.

Piper (https://github.com/OHF-Voice/piper1-gpl, GPL-3.0-or-later; the `djvoice` extra) runs on the CPU through
onnxruntime; a two-sentence intro takes around a second (more on an older CPU such as the i5-3470, which is
why clients ask ~25 s ahead). The voice model
(an .onnx file + .onnx.json) is NOT part of the repo or the image: it is downloaded on first use from the
rhasspy/piper-voices collection into <data_dir>/dj-voices/, or read from `dj_voice_model` in config.toml.
Voices carry their own licences (see each voice's MODEL_CARD); the default `en_GB-cori-medium` was trained
on a public-domain LibriVox recording.

Clips are Ogg Opus (mono, loudness-normalised with ffmpeg) under <data_dir>/dj/clips/<clipId>.ogg, where
clipId is a hash of the voice and the text, so the same words are only ever synthesised once."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import logging
import os
import re
import subprocess
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

import httpx

from nasmusic.config import Config

log = logging.getLogger(__name__)

VOICE_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/{family}/{lang}/{name}/{quality}/"
             "{lang}-{name}-{quality}{ext}?download=true")
VOICE_RE = re.compile(r"^(?P<lang>(?P<family>[a-z]{2,3})_[A-Z]{2})-(?P<name>[a-z0-9_]+)-(?P<quality>x_low|low|medium|high)$")
CLIP_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_MODEL_BYTES = 200 * 1024 * 1024
CACHE_MAX_BYTES = 300 * 1024 * 1024
CACHE_MAX_FILES = 5000
DOWNLOAD_RETRY_S = 3600
SYNTH_RETRY_S = 300

# A synthesiser turns text into a mono 16-bit WAV (bytes). Tests inject a fake one.
Synth = Callable[[str], bytes]


def piper_installed() -> bool:
    return importlib.util.find_spec("piper") is not None


def voice_parts(voice: str) -> dict | None:
    m = VOICE_RE.match(voice or "")
    return m.groupdict() if m else None


def clip_id(voice: str, text: str) -> str:
    return hashlib.sha1(f"{voice}\n{text}".encode()).hexdigest()[:32]


def wav_duration_ms(wav: bytes) -> int:
    with wave.open(io.BytesIO(wav), "rb") as w:
        return int(w.getnframes() * 1000 / max(1, w.getframerate()))


def encode_opus(wav: bytes, out: Path) -> None:
    """WAV -> Ogg Opus 48 kbit/s mono, normalised to about the loudness the players level music to, with a
    short lead-in so the first syllable isn't clipped by an output that is still waking up."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "wav", "-i", "pipe:0",
           "-af", "adelay=120,loudnorm=I=-16:TP=-1.5:LRA=11", "-ac", "1", "-ar", "48000",
           "-c:a", "libopus", "-b:a", "48k", "-application", "voip", "-f", "ogg", str(tmp)]
    try:
        r = subprocess.run(cmd, input=wav, capture_output=True, timeout=60, check=False)
        if r.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg failed: {r.stderr.decode(errors='replace')[-300:]}")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)


class Engine:
    """One per server process. Thread-safe; synthesis runs one clip at a time (it uses every core anyway)."""

    def __init__(self, cfg: Config, synth: Synth | None = None, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self._synth = synth
        self._transport = transport
        self._voice_obj = None
        self._lock = threading.Lock()
        self._dl_lock = threading.Lock()
        self._downloading = False
        self._download_failed_at = 0.0
        self._error: str | None = None
        self._synth_failed_at = 0.0
        self._writes = 0

    # ---- configuration ----

    @property
    def voice(self) -> str:
        if self.cfg.dj_voice_model:
            return Path(self.cfg.dj_voice_model).stem
        return self.cfg.dj_voice

    @property
    def clips_dir(self) -> Path:
        return self.cfg.data_dir / "dj" / "clips"

    def model_path(self) -> Path:
        if self.cfg.dj_voice_model:
            return Path(self.cfg.dj_voice_model)
        return self.cfg.data_dir / "dj-voices" / f"{self.cfg.dj_voice}.onnx"

    def status(self) -> dict:
        """{state, voice, detail}: state is ready | downloading | unavailable | disabled | error."""
        voice = self.voice
        if not self.cfg.dj_voice_enabled:
            return {"state": "disabled", "voice": voice, "detail": "dj_voice_enabled is off in config.toml"}
        if self._synth is None and not piper_installed():
            return {"state": "unavailable", "voice": voice,
                    "detail": "Piper isn't installed (server built without the djvoice extra)"}
        if self._synth is None and not self._model_ready():
            if self._downloading:
                return {"state": "downloading", "voice": voice, "detail": "downloading the voice"}
            if self.cfg.dj_voice_model or not self.cfg.dj_voice_download:
                return {"state": "unavailable", "voice": voice, "detail": f"voice model not found at {self.model_path()}"}
            if self._error:
                return {"state": "error", "voice": voice, "detail": self._error}
            return {"state": "downloading", "voice": voice, "detail": "the voice downloads on first use"}
        if self._error and time.time() - self._synth_failed_at < SYNTH_RETRY_S:
            return {"state": "error", "voice": voice, "detail": self._error}
        return {"state": "ready", "voice": voice, "detail": None}

    def _model_ready(self) -> bool:
        p = self.model_path()
        return p.is_file() and Path(str(p) + ".json").is_file()

    # ---- voice download (background, once) ----

    def ensure_voice(self) -> None:
        """Start downloading the configured voice if it is missing. Returns at once."""
        if self._synth is not None or self._model_ready() or self.cfg.dj_voice_model or not self.cfg.dj_voice_download:
            return
        if not self.cfg.dj_voice_enabled or not piper_installed():
            return
        with self._dl_lock:
            if self._downloading or time.time() - self._download_failed_at < DOWNLOAD_RETRY_S:
                return
            self._downloading = True
        threading.Thread(target=self._download, name="dj-voice-download", daemon=True).start()

    def download(self) -> None:
        """Download the configured voice now (CLI / tests). Raises on failure."""
        parts = voice_parts(self.cfg.dj_voice)
        if parts is None:
            raise ValueError(f"dj_voice {self.cfg.dj_voice!r} isn't a Piper voice name like en_GB-cori-medium")
        dest = self.model_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(transport=self._transport, timeout=httpx.Timeout(60.0, connect=10.0),
                          follow_redirects=True, headers={"User-Agent": "nas-music"}) as c:
            # The .json last: its presence marks the voice as complete.
            for ext, target in ((".onnx", dest), (".onnx.json", Path(str(dest) + ".json"))):
                url = VOICE_URL.format(ext=ext, **parts)
                tmp = target.with_name(target.name + ".part")
                size = 0
                with c.stream("GET", url) as r:
                    if r.status_code != 200:
                        raise RuntimeError(f"voice download failed: HTTP {r.status_code} for {target.name}")
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_bytes(1 << 16):
                            size += len(chunk)
                            if size > MAX_MODEL_BYTES:
                                raise RuntimeError("voice download too large")
                            f.write(chunk)
                tmp.replace(target)
        log.info("DJ voice %s downloaded to %s", self.cfg.dj_voice, dest)

    def _download(self) -> None:
        try:
            self.download()
            self._error = None
        except Exception as e:  # noqa: BLE001 - reported through status()
            log.warning("DJ voice download failed: %s", e)
            self._error = f"couldn't download the voice: {e}"
            self._download_failed_at = time.time()
            for p in self.model_path().parent.glob("*.part"):
                p.unlink(missing_ok=True)
        finally:
            self._downloading = False

    # ---- synthesis ----

    def _piper_synth(self, text: str) -> bytes:
        from piper import PiperVoice, SynthesisConfig  # optional extra

        if self._voice_obj is None:
            self._voice_obj = PiperVoice.load(str(self.model_path()))
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            # A touch slower than the default reads better over music.
            self._voice_obj.synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=1.08))
        return buf.getvalue()

    def clip_path(self, cid: str) -> Path | None:
        if not CLIP_ID_RE.match(cid or ""):
            return None
        p = self.clips_dir / f"{cid}.ogg"
        return p if p.is_file() else None

    def speak(self, text: str) -> tuple[str, int] | None:
        """(clipId, durationMs) for `text`, synthesising it unless cached. None when the voice isn't ready."""
        cid = clip_id(self.voice, text)
        out = self.clips_dir / f"{cid}.ogg"
        meta = out.with_suffix(".ms")
        if out.is_file() and meta.is_file():
            try:
                os.utime(out)  # LRU for pruning
                return cid, int(meta.read_text())
            except (OSError, ValueError):
                pass
        if self.status()["state"] != "ready":
            self.ensure_voice()
            return None
        with self._lock:
            if out.is_file() and meta.is_file():
                return cid, int(meta.read_text())
            try:
                wav = (self._synth or self._piper_synth)(text)
                ms = wav_duration_ms(wav) + 120
                encode_opus(wav, out)
                meta.write_text(str(ms))
                self._error = None
            except Exception as e:  # noqa: BLE001 - a DJ that can't talk stays quiet
                log.warning("DJ voice synthesis failed: %s", e)
                self._error = f"synthesis failed: {e}"
                self._synth_failed_at = time.time()
                self._voice_obj = None
                return None
            self._writes += 1
            if self._writes % 50 == 1:
                self.prune()
        return cid, ms

    def prune(self, max_bytes: int = CACHE_MAX_BYTES, max_files: int = CACHE_MAX_FILES) -> int:
        """Delete the least recently used clips beyond the cache limits. Returns how many went."""
        try:
            files = [(p, p.stat()) for p in self.clips_dir.glob("*.ogg")]
        except OSError:
            return 0
        files.sort(key=lambda x: x[1].st_mtime, reverse=True)
        total, removed = 0, 0
        for i, (p, st) in enumerate(files):
            total += st.st_size
            if i >= max_files or total > max_bytes:
                p.unlink(missing_ok=True)
                p.with_suffix(".ms").unlink(missing_ok=True)
                removed += 1
        return removed
