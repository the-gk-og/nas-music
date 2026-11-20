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

MIN_BITRATE, MAX_BITRATE = 24, 256


def media_type(path: str) -> str:
    return MEDIA_TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")


def clamp_bitrate(kbps: int) -> int:
    return max(MIN_BITRATE, min(MAX_BITRATE, int(kbps)))
