"""Lyrics from the music file itself (embedded tags) or a sidecar .lrc file.

Read on demand: tag parsing is cheap and lyrics are fetched once per track by the app (which caches
them for offline use), so nothing is stored in the database.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import mutagen
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

# [mm:ss], [mm:ss.x], [mm:ss.xx], [mm:ss.xxx], also [mm:ss:xx] seen in the wild.
_STAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
_OFFSET = re.compile(r"^\[offset:\s*([+-]?\d+)\s*\]\s*$", re.IGNORECASE)
_META = re.compile(r"^\[(ar|ti|al|au|by|re|ve|length|tool|#):.*\]\s*$", re.IGNORECASE)  # LRC header tags only
_WORD_STAMP = re.compile(r"<\d{1,3}:\d{1,2}(?:[.:]\d{1,3})?>")  # enhanced-LRC word timings

_VORBIS_SYNCED = ("syncedlyrics", "lyrics", "unsyncedlyrics", "lyric")
_MP4_KEY = "\xa9lyr"


@dataclass
class Lyrics:
    synced: bool
    source: str                      # "embedded" | "lrc_file"
    lines: list[dict] = field(default_factory=list)   # {"timeMs": int|None, "text": str}

    def as_api(self, track_id: str) -> dict:
        text = "\n".join(ln["text"] for ln in self.lines if not self.synced or ln["text"].strip())
        return {"trackId": track_id, "synced": self.synced, "source": self.source,
                "lines": self.lines, "text": text}


def _ms(m: re.Match) -> int:
    mins, secs, frac = int(m.group(1)), int(m.group(2)), m.group(3)
    ms = 0
    if frac:
        ms = int(frac) * (10 if len(frac) == 2 else 100 if len(frac) == 1 else 1)
    return (mins * 60 + secs) * 1000 + ms


def parse(raw: str) -> tuple[bool, list[dict]] | None:
    """Parse LRC or plain text. Returns (synced, lines) or None when there is nothing to show."""
    if not raw or not raw.strip():
        return None
    raw = raw.replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    offset = 0
    timed: list[tuple[int, int, str]] = []
    plain: list[str] = []
    for i, line in enumerate(raw.split("\n")):
        om = _OFFSET.match(line.strip())
        if om:
            offset = int(om.group(1))
            continue
        stamps = list(_STAMP.finditer(line))
        # timestamps must lead the line: "[00:12.00][00:40.00]text"
        lead, pos = [], 0
        for m in stamps:
            if line[pos:m.start()].strip():
                break
            lead.append(m)
            pos = m.end()
        if lead:
            text = _WORD_STAMP.sub("", line[pos:]).strip()
            for m in lead:
                timed.append((_ms(m), i, text))
            continue
        if _META.match(line.strip()):
            continue
        plain.append(line.rstrip())
    if timed:
        # LRC [offset:+n] means the lyrics are shown n ms earlier.
        timed.sort(key=lambda t: (t[0], t[1]))
        lines = [{"timeMs": max(0, t - offset), "text": text} for t, _, text in timed]
        if not any(ln["text"] for ln in lines):
            return None
        return True, lines
    while plain and not plain[0].strip():
        plain.pop(0)
    while plain and not plain[-1].strip():
        plain.pop()
    if not plain:
        return None
    return False, [{"timeMs": None, "text": t} for t in plain]


_LYRIC_TXXX = ("lyrics", "unsyncedlyrics", "syncedlyrics", "unsynced lyrics", "synced lyrics", "lyric",
               "lyrics_synced", "synced_lyrics")


def _texts(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [t for x in v for t in _texts(x)]
    if isinstance(v, bytes):
        return [v.decode("utf-8", "replace")]
    return [str(v)]


def _id3_candidates(tags: ID3) -> list[str]:
    found: list[str] = []
    for fr in tags.getall("SYLT"):
        # SYLT: list of (text, time); time in ms when format == 2
        if getattr(fr, "format", 2) == 2 and fr.text:
            found.append("\n".join(f"[{t // 60000:02d}:{(t // 1000) % 60:02d}.{(t % 1000) // 10:02d}]{s}"
                                   for s, t in fr.text))
    found += [str(fr.text) for fr in tags.getall("USLT")]  # every language / description
    for fr in tags.getall("TXXX"):
        if (fr.desc or "").strip().casefold() in _LYRIC_TXXX:
            found += _texts(fr.text)
    for fr in tags.getall("COMM"):
        text = "\n".join(_texts(fr.text))
        if "lyric" in (fr.desc or "").casefold() or len(_STAMP.findall(text)) >= 3:  # LRC stored as a comment
            found.append(text)
    return found


def _apev2_candidates(path: Path) -> list[str]:
    """APEv2 tags (also found appended to MP3s by some tools)."""
    try:
        from mutagen.apev2 import APEv2

        ape = APEv2(path)
    except Exception:  # no APE tag (APENoHeaderError) or unreadable
        return []
    return [str(v) for k, v in ape.items() if "lyric" in k.casefold()]


def _embedded_candidates(path: Path) -> list[str]:
    """All lyric texts found in the file's tags, synced-looking ones first."""
    try:
        f = mutagen.File(path)
    except (mutagen.MutagenError, OSError, ValueError):
        f = None
    found: list[str] = []
    tags = f.tags if f is not None else None
    if isinstance(f, MP4) and tags is not None:
        for k, v in tags.items():
            if k == _MP4_KEY or (k.startswith("----:") and "lyric" in k.casefold()):
                found += _texts(v)
    elif isinstance(tags, ID3):
        found += _id3_candidates(tags)
    elif tags is not None:  # Vorbis comments (FLAC/Ogg/Opus), APEv2 files (.ape/.wv/.mpc): any key with "lyric"
        try:
            items = list(tags.items())
        except (TypeError, AttributeError):
            items = []
        for k, v in items:
            if "lyric" in str(k).casefold():
                found += _texts(v)
    if path.suffix.lower() == ".mp3" or f is None:
        found += _apev2_candidates(path)
    found = list(dict.fromkeys(s for s in found if s and s.strip()))
    found.sort(key=lambda s: 0 if _STAMP.search(s) else 1)   # stable: synced first
    return found


def _decode(data: bytes) -> str | None:
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = data.decode(enc)
        except UnicodeDecodeError:
            continue
        if enc == "utf-16" and not data.startswith((b"\xff\xfe", b"\xfe\xff")):
            continue  # only trust UTF-16 with a BOM
        return text
    return None


def _sidecar(path: Path) -> str | None:
    """A same-named .lrc (any case), else .txt, next to the file. Case-insensitive on case-sensitive filesystems."""
    stem = path.stem.casefold()
    try:
        entries = [e for e in path.parent.iterdir() if e.is_file()]
    except OSError:
        return None
    for ext in (".lrc", ".txt"):
        for e in sorted(entries):
            if e.suffix.casefold() == ext and e.stem.casefold() == stem:
                try:
                    data = e.read_bytes()
                except OSError:
                    continue
                if len(data) > 512 * 1024:
                    continue
                text = _decode(data)
                if text and text.strip():
                    return text
    return None


def read(path: Path) -> Lyrics | None:
    """Best lyrics for a file: synced beats unsynced; embedded beats sidecar when equally synced."""
    options: list[tuple[str, tuple[bool, list[dict]]]] = []
    for raw in _embedded_candidates(path):
        p = parse(raw)
        if p:
            options.append(("embedded", p))
    side = _sidecar(path)
    if side:
        p = parse(side)
        if p:
            options.append(("lrc_file", p))
    if not options:
        return None
    synced = [o for o in options if o[1][0]]
    source, (is_synced, lines) = (synced or options)[0]
    return Lyrics(synced=is_synced, source=source, lines=lines)


def read_all(path: Path) -> dict[str, Lyrics]:
    """Best lyrics per file source: {"embedded": ..., "lrc_file": ...} (only sources that have lyrics)."""
    out: dict[str, Lyrics] = {}
    emb = [p for p in (parse(raw) for raw in _embedded_candidates(path)) if p]
    if emb:
        synced = [p for p in emb if p[0]]
        is_synced, lines = (synced or emb)[0]
        out["embedded"] = Lyrics(synced=is_synced, source="embedded", lines=lines)
    side = _sidecar(path)
    p = parse(side) if side else None
    if p:
        out["lrc_file"] = Lyrics(synced=p[0], source="lrc_file", lines=p[1])
    return out


def from_raw(raw: str, source: str = "user") -> Lyrics | None:
    p = parse(raw)
    return Lyrics(synced=p[0], source=source, lines=p[1]) if p else None
