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


def _embedded_candidates(path: Path) -> list[str]:
    """All lyric texts found in the file's tags, synced-looking ones first."""
    try:
        f = mutagen.File(path)
    except (mutagen.MutagenError, OSError, ValueError):
        return []
    if f is None or f.tags is None:
        return []
    tags, found = f.tags, []
    if isinstance(f, MP4):
        found += [str(v) for v in tags.get(_MP4_KEY, [])]
    elif isinstance(tags, ID3):
        for fr in tags.getall("SYLT"):
            # SYLT: list of (text, time) with time in ms when format == 2
            if getattr(fr, "format", 2) == 2 and fr.text:
                found.append("\n".join(f"[{t // 60000:02d}:{(t // 1000) % 60:02d}.{(t % 1000) // 10:02d}]{s}"
                                       for s, t in fr.text))
        found += [str(fr.text) for fr in tags.getall("USLT")]
    else:  # Vorbis comments: keys are case-insensitive in mutagen
        for key in _VORBIS_SYNCED:
            found += [str(v) for v in tags.get(key, []) or []]
    found = [s for s in found if s and s.strip()]
    found.sort(key=lambda s: 0 if _STAMP.search(s) else 1)   # stable: synced first
    return found


def _sidecar(path: Path) -> str | None:
    for cand in (path.with_suffix(".lrc"), path.with_suffix(".LRC")):
        try:
            if cand.is_file():
                data = cand.read_bytes()
                for enc in ("utf-8-sig", "cp1252"):
                    try:
                        return data.decode(enc)
                    except UnicodeDecodeError:
                        continue
        except OSError:
            continue
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
