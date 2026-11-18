"""Read the tags we care about from FLAC / Ogg (Vorbis, Opus) / MP3 / MP4 files with mutagen."""
from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path

import mutagen
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus"}


@dataclass
class TrackTags:
    title: str | None = None
    artist: str | None = None
    albumartist: str | None = None
    album: str | None = None
    track_no: int | None = None
    disc_no: int | None = None
    year: int | None = None
    genre: str | None = None
    duration_s: float = 0.0
    mb_track: str | None = None          # recording id
    mb_artist: str | None = None
    mb_albumartist: str | None = None
    mb_releasegroup: str | None = None


_NUM = re.compile(r"\d+")
_YEAR = re.compile(r"(\d{4})")


def _first(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        v = v[0] if v else None
        if v is None:
            return None
    s = str(v).strip().strip("\x00")
    return s or None


def _int(v) -> int | None:
    s = _first(v)
    if not s:
        return None
    m = _NUM.search(s)
    return int(m.group()) if m else None


def _year(v) -> int | None:
    s = _first(v)
    if not s:
        return None
    m = _YEAR.search(s)
    if not m:
        return None
    y = int(m.group(1))
    return y if 1000 <= y <= 2999 else None


# Vorbis-comment keys (FLAC, Ogg Vorbis, Ogg Opus): as written by Picard.
_VORBIS = {
    "title": "title", "artist": "artist", "albumartist": "albumartist", "album": "album",
    "genre": "genre", "mb_track": "musicbrainz_trackid", "mb_artist": "musicbrainz_artistid",
    "mb_albumartist": "musicbrainz_albumartistid", "mb_releasegroup": "musicbrainz_releasegroupid",
}

_ID3 = {
    "title": "TIT2", "artist": "TPE1", "albumartist": "TPE2", "album": "TALB", "genre": "TCON",
    "mb_artist": "TXXX:MusicBrainz Artist Id", "mb_albumartist": "TXXX:MusicBrainz Album Artist Id",
    "mb_releasegroup": "TXXX:MusicBrainz Release Group Id",
}

_MP4 = {
    "title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART", "album": "\xa9alb", "genre": "\xa9gen",
    "mb_track": "----:com.apple.iTunes:MusicBrainz Track Id",
    "mb_artist": "----:com.apple.iTunes:MusicBrainz Artist Id",
    "mb_albumartist": "----:com.apple.iTunes:MusicBrainz Album Artist Id",
    "mb_releasegroup": "----:com.apple.iTunes:MusicBrainz Release Group Id",
}


def _decode(v):
    if isinstance(v, list):
        return [_decode(x) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", "replace")
    return v


def read(path: Path) -> tuple[TrackTags, mutagen.FileType | None]:
    """Parse tags. Returns (tags, mutagen file object or None if unreadable)."""
    t = TrackTags()
    f = mutagen.File(path)
    if f is None:
        return t, None
    if f.info is not None:
        t.duration_s = float(getattr(f.info, "length", 0.0) or 0.0)
    tags = f.tags
    if tags is None:
        return t, f
    if isinstance(f, MP4):
        for attr, key in _MP4.items():
            setattr(t, attr, _first(_decode(tags.get(key))))
        trkn, disk = tags.get("trkn"), tags.get("disk")
        t.track_no = trkn[0][0] if trkn and trkn[0][0] else None
        t.disc_no = disk[0][0] if disk and disk[0][0] else None
        t.year = _year(tags.get("\xa9day"))
    elif isinstance(tags, ID3):
        for attr, key in _ID3.items():
            fr = tags.get(key)
            setattr(t, attr, _first(fr.text if fr is not None else None))
        t.track_no = _int(tags.get("TRCK").text if tags.get("TRCK") else None)
        t.disc_no = _int(tags.get("TPOS").text if tags.get("TPOS") else None)
        day = tags.get("TDRC") or tags.get("TYER") or tags.get("TDOR")
        t.year = _year(str(day.text[0]) if day is not None and day.text else None)
        ufid = tags.get("UFID:http://musicbrainz.org")
        if ufid is not None:
            t.mb_track = _first(ufid.data.decode("ascii", "replace"))
    else:  # Vorbis comments (FLAC, Ogg)
        for attr, key in _VORBIS.items():
            setattr(t, attr, _first(tags.get(key)))
        if t.albumartist is None:
            t.albumartist = _first(tags.get("album artist") or tags.get("album_artist"))
        t.track_no = _int(tags.get("tracknumber"))
        t.disc_no = _int(tags.get("discnumber"))
        t.year = _year(tags.get("date") or tags.get("originaldate") or tags.get("year"))
    return t, f


def embedded_picture(f: mutagen.FileType | None) -> bytes | None:
    """Return the front cover (or first picture) bytes from a parsed file, if any."""
    if f is None:
        return None
    pics: list[tuple[int, bytes]] = []  # (type, data); type 3 = front cover
    try:
        if isinstance(f, FLAC):
            pics = [(p.type, p.data) for p in f.pictures]
        elif isinstance(f, MP4):
            pics = [(3, bytes(c)) for c in (f.tags or {}).get("covr", [])]
        elif isinstance(f.tags, ID3):
            pics = [(fr.type, fr.data) for fr in f.tags.getall("APIC")]
        elif f.tags is not None:
            for b64 in f.tags.get("metadata_block_picture", []):
                p = Picture(base64.b64decode(b64))
                pics.append((p.type, p.data))
    except Exception:
        return None
    if not pics:
        return None
    pics.sort(key=lambda tp: 0 if tp[0] == 3 else 1)
    return pics[0][1]
