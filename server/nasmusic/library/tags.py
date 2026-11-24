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

AUDIO_EXTS = {".flac", ".mp3", ".m4a", ".mp4", ".aac", ".ogg", ".oga", ".opus", ".wav"}


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
    composer: str | None = None
    lyricist: str | None = None
    comment: str | None = None
    codec: str | None = None
    bitrate: int | None = None           # kbps
    sample_rate: int | None = None
    bits_per_sample: int | None = None
    channels: int | None = None
    # ReplayGain 2.0 scale (reference -18 LUFS); None when the file has no such tag.
    rg_track_gain: float | None = None   # dB
    rg_track_peak: float | None = None   # linear
    rg_album_gain: float | None = None
    rg_album_peak: float | None = None
    # v10 classical music (see nasmusic.library.classical)
    work: str | None = None
    movement: str | None = None
    movement_no: int | None = None


_NUM = re.compile(r"\d+")
_FLOAT = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
# Opus R128_*_GAIN tags are Q7.8 fixed point relative to -23 LUFS; ReplayGain 2.0 uses -18 LUFS.
R128_TO_RG_DB = 5.0
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
    "composer": "composer", "lyricist": "lyricist",
}

_ID3 = {
    "title": "TIT2", "artist": "TPE1", "albumartist": "TPE2", "album": "TALB", "genre": "TCON",
    "mb_artist": "TXXX:MusicBrainz Artist Id", "mb_albumartist": "TXXX:MusicBrainz Album Artist Id",
    "mb_releasegroup": "TXXX:MusicBrainz Release Group Id",
    "composer": "TCOM", "lyricist": "TEXT",
}

_MP4 = {
    "title": "\xa9nam", "artist": "\xa9ART", "albumartist": "aART", "album": "\xa9alb", "genre": "\xa9gen",
    "mb_track": "----:com.apple.iTunes:MusicBrainz Track Id",
    "mb_artist": "----:com.apple.iTunes:MusicBrainz Artist Id",
    "mb_albumartist": "----:com.apple.iTunes:MusicBrainz Album Artist Id",
    "mb_releasegroup": "----:com.apple.iTunes:MusicBrainz Release Group Id",
    "composer": "\xa9wrt", "lyricist": "----:com.apple.iTunes:LYRICIST", "comment": "\xa9cmt",
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
        _audio_info(t, f)
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
        t.work = _first(_decode(tags.get("\xa9wrk")))
        t.movement = _first(_decode(tags.get("\xa9mvn")))
        mvi = tags.get("\xa9mvi")
        t.movement_no = int(mvi[0]) if mvi and isinstance(mvi[0], int) and mvi[0] > 0 else None
    elif isinstance(tags, ID3):
        for attr, key in _ID3.items():
            fr = tags.get(key)
            setattr(t, attr, _first(fr.text if fr is not None else None))
        t.track_no = _int(tags.get("TRCK").text if tags.get("TRCK") else None)
        t.disc_no = _int(tags.get("TPOS").text if tags.get("TPOS") else None)
        day = tags.get("TDRC") or tags.get("TYER") or tags.get("TDOR")
        t.year = _year(str(day.text[0]) if day is not None and day.text else None)
        comms = [fr for fr in tags.getall("COMM") if fr.text and not fr.desc.startswith("iTun")]
        t.comment = _first(comms[0].text) if comms else None
        _id3_classical(t, tags)
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
        t.comment = _first(tags.get("comment") or tags.get("description"))
        t.work = _first(tags.get("work"))
        t.movement = _first(tags.get("movementname"))
        t.movement_no = _int(tags.get("movement") or tags.get("movementnumber"))
    _replaygain(t, f, tags)
    return t, f


def _id3_classical(t: TrackTags, tags) -> None:
    txxx = {fr.desc.casefold(): fr.text for fr in tags.getall("TXXX")}
    mvnm, mvin = tags.get("MVNM"), tags.get("MVIN")
    t.movement = _first(mvnm.text if mvnm is not None else txxx.get("movementname"))
    t.movement_no = _int(mvin.text if mvin is not None else txxx.get("movement"))
    t.work = _first(txxx.get("work"))
    if t.work is None and (t.movement or t.movement_no):
        # iTunes and Picard (iTunes-compatible grouping) write the work to TIT1 next to MVNM/MVIN.
        tit1 = tags.get("TIT1")
        t.work = _first(tit1.text if tit1 is not None else None)


def _float(v) -> float | None:
    s = _first(v)
    if not s:
        return None
    m = _FLOAT.search(s)
    if not m:
        return None
    try:
        x = float(m.group().replace(",", "."))
    except ValueError:
        return None
    return x if -60.0 <= x <= 60.0 else None


def _rg_values(get) -> dict[str, float | None]:
    """`get(name)` returns the raw tag value for a lower-case ReplayGain tag name."""
    out = {k: _float(get(f"replaygain_{k}")) for k in ("track_gain", "track_peak", "album_gain", "album_peak")}
    for k in ("track_peak", "album_peak"):
        if out[k] is not None and not 0.0 < out[k] <= 20.0:
            out[k] = None
    return out


def _replaygain(t: TrackTags, f, tags) -> None:
    try:
        if isinstance(f, MP4):
            low = {k.lower(): v for k, v in tags.items()}
            vals = _rg_values(lambda n: _decode(low.get(f"----:com.apple.itunes:{n}")))
        elif isinstance(tags, ID3):
            txxx = {fr.desc.lower(): fr.text for fr in tags.getall("TXXX")}
            vals = _rg_values(lambda n: txxx.get(n))
        else:
            vals = _rg_values(lambda n: tags.get(n))
            # Ogg Opus: R128 gains (Q7.8 integers) when no REPLAYGAIN_* tags.
            for kind in ("track", "album"):
                if vals[f"{kind}_gain"] is None:
                    raw = _first(tags.get(f"r128_{kind}_gain"))
                    if raw and re.fullmatch(r"[-+]?\d+", raw):
                        vals[f"{kind}_gain"] = int(raw) / 256.0 + R128_TO_RG_DB
    except Exception:
        return
    t.rg_track_gain = vals["track_gain"]
    t.rg_track_peak = vals["track_peak"]
    t.rg_album_gain = vals["album_gain"]
    t.rg_album_peak = vals["album_peak"]


_CODECS = {"FLAC": "FLAC", "MP3": "MP3", "OggVorbis": "Vorbis", "OggOpus": "Opus", "OggFLAC": "FLAC",
           "AAC": "AAC", "WavPack": "WavPack", "MonkeysAudio": "APE", "WAVE": "WAV"}


def _audio_info(t: TrackTags, f) -> None:
    info = f.info
    name = type(f).__name__
    if isinstance(f, MP4):
        c = (getattr(info, "codec", "") or "").lower()
        t.codec = "ALAC" if c == "alac" else "AAC" if c.startswith("mp4a") else (c.upper() or "MP4")
    else:
        t.codec = _CODECS.get(name, name.upper())
    br = getattr(info, "bitrate", None)
    if br:
        t.bitrate = round(br / 1000)
    t.sample_rate = getattr(info, "sample_rate", None) or None
    t.bits_per_sample = getattr(info, "bits_per_sample", None) or None
    t.channels = getattr(info, "channels", None) or None


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
