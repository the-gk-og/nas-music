"""Audio delivery: original files and an on-disk Opus transcode cache (Range served by Starlette)."""
from __future__ import annotations

import os

MEDIA_TYPES = {
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
}

# The Opus bitrates /stream transcodes to. Requests are snapped to the nearest one, so the transcode cache holds
# at most this many variants per track (and a client sending an odd value still gets a sensible, cached file).
OPUS_BITRATES = (64, 96, 128, 160, 192, 256)
DEFAULT_BITRATE = 160
MIN_BITRATE, MAX_BITRATE = OPUS_BITRATES[0], OPUS_BITRATES[-1]


def media_type(path: str) -> str:
    return MEDIA_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def snap_bitrate(kbps: int) -> int:
    """Nearest supported Opus bitrate; a tie goes to the higher one."""
    k = int(kbps)
    return min(OPUS_BITRATES, key=lambda b: (abs(b - k), -b))


def clamp_bitrate(kbps: int) -> int:  # kept for callers outside /stream
    return snap_bitrate(kbps)


def _khz(sr: int) -> str:
    return f"{sr // 1000}" if sr % 1000 == 0 else f"{sr / 1000:.1f}"


def original_label(codec: str | None, bits: int | None, sample_rate: int | None, kbps: int | None) -> str:
    """What an untranscoded file is, short: "FLAC 16/44.1", "FLAC 24/96", "MP3 320", "AAC 256"."""
    name = (codec or "").strip().upper() or "Original"
    name = {"OPUS": "Opus", "VORBIS": "Vorbis"}.get(name, name)
    lossless = name in {"FLAC", "ALAC", "WAV", "PCM", "AIFF", "APE", "WAVPACK"}
    if lossless and bits and sample_rate:
        return f"{name} {bits}/{_khz(sample_rate)}"
    if lossless and sample_rate:
        return f"{name} {_khz(sample_rate)} kHz"
    if not lossless and kbps:
        return f"{name} {kbps}"
    return name


def opus_label(kbps: int) -> str:
    return f"Opus {kbps}"
