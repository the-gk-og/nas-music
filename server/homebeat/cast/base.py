"""What every speaker backend implements, and the data passed to it."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from pathlib import Path

# The formats a signed media URL can ask for (see homebeat.app.cast_api):
#   original - the file as stored (FLAC/MP3/AAC/...), Range supported
#   opus     - the cached Ogg Opus transcode, Range supported
#   mp3      - live MP3 320k transcode (for DLNA renderers that only take MP3), no Range
FORMATS = ("original", "opus", "mp3")


@dataclass
class Media:
    track_id: str
    title: str
    artist: str
    album: str
    duration_s: float
    url_for: object = field(repr=False)  # (fmt: str) -> signed URL
    art_url: str | None = None
    mime: str = "audio/flac"  # mime of the original file
    codec: str | None = None  # FLAC, MP3, AAC, ALAC, Opus, Vorbis (tracks.codec)
    path: Path | None = field(default=None, repr=False)  # local file, for backends that decode themselves

    def url(self, fmt: str = "original") -> str:
        return self.url_for(fmt)  # type: ignore[operator]


@dataclass
class Status:
    state: str = "idle"  # playing | paused | buffering | idle | finished | unavailable
    position_s: float = 0.0
    volume: float | None = None  # 0..1
    error: str | None = None


class Renderer(abc.ABC):
    """One output device. Methods are async and may raise; the session catches and reports errors."""

    kind: str = "other"  # chromecast | dlna | homeassistant | snapcast

    def __init__(self, uid: str, name: str):
        self.uid = uid  # stable id within its backend
        self.name = name

    @property
    def device_id(self) -> str:
        return f"spk:{self.kind}:{self.uid}"

    @abc.abstractmethod
    async def load(self, media: Media, position_s: float, play: bool) -> None: ...

    @abc.abstractmethod
    async def play(self) -> None: ...

    @abc.abstractmethod
    async def pause(self) -> None: ...

    @abc.abstractmethod
    async def stop(self) -> None: ...

    @abc.abstractmethod
    async def seek(self, position_s: float) -> None: ...

    @abc.abstractmethod
    async def set_volume(self, volume: float) -> None: ...

    @abc.abstractmethod
    async def status(self) -> Status:
        """Current state. `finished` = the loaded track played to its end (the session advances)."""

    async def close(self) -> None:  # noqa: B027 - optional
        pass

    async def reset(self) -> None:  # noqa: B027 - optional
        """Drop any half-open connection after a failure, so the next attempt starts clean."""

    def info(self) -> dict:
        return {"id": self.device_id, "name": self.name, "castType": self.kind}
