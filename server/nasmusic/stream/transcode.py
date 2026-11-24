"""Opus transcode cache.

Files live at <data_dir>/opus/<trackId>_<kbps>_<mtime>.opus, so a re-tagged/replaced source gets a new
entry. One lock per (track, bitrate) stops concurrent requests transcoding the same file twice.
The cache is trimmed least-recently-used (file mtime is bumped on use) to `max_bytes`.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

FFMPEG_TIMEOUT_S = 900
TOUCH_RESOLUTION_S = 3600


def _lower_priority() -> None:  # runs in the forked child before exec
    os.nice(19)


class TranscodeError(Exception):
    pass


class OpusCache:
    EXT = "opus"

    def __init__(self, directory: Path, max_bytes: int, ffmpeg: str = "ffmpeg"):
        self.dir = directory
        self.max_bytes = max_bytes
        self.ffmpeg = ffmpeg
        self._guard = threading.Lock()
        self._locks: dict[tuple[str, int], threading.Lock] = {}
        self._evict_lock = threading.Lock()
        self.transcodes = 0  # counter, for tests/metrics

    def path_for(self, track_id: str, kbps: int, mtime: float) -> Path:
        return self.dir / f"{track_id}_{kbps}_{int(mtime)}.{self.EXT}"

    def _lock_for(self, key: tuple[str, int]) -> threading.Lock:
        with self._guard:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            return lock

    def get(self, src: Path, track_id: str, kbps: int, mtime: float, low_priority: bool = False) -> Path:
        """Return the cached Opus file for this source, transcoding it first if needed (blocking).
        `low_priority` runs ffmpeg at nice 19 (background pre-transcoding)."""
        out = self.path_for(track_id, kbps, mtime)
        if self._hit(out):
            return out
        key = (track_id, kbps)
        lock = self._lock_for(key)
        try:
            with lock:
                if self._hit(out):
                    return out
                self._transcode(src, out, kbps, low_priority)
                for old in self.dir.glob(f"{track_id}_{kbps}_*.{self.EXT}"):
                    if old != out:
                        old.unlink(missing_ok=True)
        finally:
            with self._guard:  # don't keep a lock per (track, bitrate) ever played; drop it once nobody waits
                if self._locks.get(key) is lock and not lock.locked():
                    del self._locks[key]
        self.evict()
        return out

    def _hit(self, out: Path) -> bool:
        try:
            st = out.stat()
        except OSError:
            return False
        now = time.time()
        if now - st.st_mtime > TOUCH_RESOLUTION_S:
            try:
                os.utime(out, (now, now))
            except OSError:
                pass
        return True

    def _codec_args(self, kbps: int) -> list[str]:
        return ["-c:a", "libopus", "-b:a", f"{kbps}k", "-vbr", "on", "-application", "audio",
                "-map_metadata", "0", "-f", "opus"]

    def _transcode(self, src: Path, out: Path, kbps: int, low_priority: bool = False) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = out.with_name(f".{out.name}.{threading.get_ident()}.tmp")
        cmd = [
            self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(src), "-map", "0:a:0", "-vn", "-ac", "2",
            *self._codec_args(kbps), str(tmp),
        ]
        t0 = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=False,
                                  preexec_fn=_lower_priority if low_priority else None)
        except (OSError, subprocess.TimeoutExpired) as e:
            tmp.unlink(missing_ok=True)
            raise TranscodeError(f"ffmpeg failed to run: {e}") from e
        if proc.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            tmp.unlink(missing_ok=True)
            raise TranscodeError(f"ffmpeg exited {proc.returncode}: {proc.stderr.decode(errors='replace')[-500:]}")
        os.replace(tmp, out)
        self.transcodes += 1
        log.info("transcoded %s -> %s kbps in %.1fs", src.name, kbps, time.monotonic() - t0)

    def evict(self) -> int:
        """Delete least-recently-used files until the cache is under its limit. Returns files removed."""
        if not self._evict_lock.acquire(blocking=False):
            return 0
        try:
            entries = []
            total = 0
            try:
                with os.scandir(self.dir) as it:
                    for e in it:
                        if e.is_file() and e.name.endswith(f".{self.EXT}") and not e.name.startswith("."):
                            st = e.stat()
                            entries.append((st.st_mtime, st.st_size, e.path))
                            total += st.st_size
            except FileNotFoundError:
                return 0
            if total <= self.max_bytes:
                return 0
            target = int(self.max_bytes * 0.9)
            removed = 0
            for _, size, path in sorted(entries):
                if total <= target:
                    break
                try:
                    os.unlink(path)
                    total -= size
                    removed += 1
                except OSError:
                    pass
            return removed
        finally:
            self._evict_lock.release()

    def size_bytes(self) -> int:
        try:
            return sum(e.stat().st_size for e in os.scandir(self.dir) if e.name.endswith(f".{self.EXT}"))
        except FileNotFoundError:
            return 0


class Mp3Cache(OpusCache):
    """Same cache, MP3 output: for DLNA renderers / speakers that can't play the original or Opus."""

    EXT = "mp3"

    def _codec_args(self, kbps: int) -> list[str]:
        return ["-c:a", "libmp3lame", "-b:a", f"{kbps}k", "-map_metadata", "0", "-id3v2_version", "3",
                "-f", "mp3"]
