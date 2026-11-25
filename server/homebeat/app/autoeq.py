"""AutoEq headphone profiles, fetched on demand and cached (never bundled).

AutoEq (https://github.com/jaakkopasanen/AutoEq, MIT) publishes parametric EQ results computed from
measurements by several third parties whose own terms vary, so Homebeat does not ship the data: the server
downloads `results/INDEX.md` (refreshed weekly) and individual `ParametricEQ.txt` files when a user picks a
headphone, caches them under <data_dir>/autoeq/, and serves them as JSON. Clients never contact GitHub
(the web player's CSP only allows 'self'), and only index entries can be fetched (no arbitrary paths).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote

import httpx

from homebeat.app import upstream

log = logging.getLogger(__name__)

RAW_BASE = "https://raw.githubusercontent.com/jaakkopasanen/AutoEq/master/results"
INDEX_MAX_AGE_S = 7 * 24 * 3600
MAX_FILTERS = 20
FILTER_TYPES = {"PK": "PK", "PEQ": "PK", "LSC": "LSC", "LS": "LSC", "HSC": "HSC", "HS": "HSC"}

_ENTRY = re.compile(r"^- \[(?P<name>.+?)\]\(\./(?P<path>[^)]*(?:\([^)]*\)[^)]*)*)\) by (?P<by>.+?)\s*$")
_PREAMP = re.compile(r"^Preamp:\s*(-?\d+(?:\.\d+)?)\s*dB", re.IGNORECASE)
_FILTER = re.compile(
    r"^Filter\s*\d*:\s*(ON|OFF)\s+([A-Z]+)\s+Fc\s+(\d+(?:\.\d+)?)\s*Hz\s+Gain\s+(-?\d+(?:\.\d+)?)\s*dB"
    r"(?:\s+Q\s+(\d+(?:\.\d+)?))?", re.IGNORECASE)


class AutoEqError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    name: str
    path: str          # decoded, relative to results/, e.g. "oratory1990/over-ear/Sennheiser HD 650"
    source: str        # measurer, e.g. "oratory1990"
    rig: str | None    # "GRAS 43AG-7" when given
    form: str          # "over-ear" / "in-ear" / "earbud" (from the directory name)

    def as_dict(self) -> dict:
        return {"id": self.path, "name": self.name, "source": self.source, "rig": self.rig, "form": self.form}


def _norm(s: str) -> str:
    return " ".join(unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().casefold().split())


def parse_index(md: str) -> list[Entry]:
    out = []
    for line in md.splitlines():
        m = _ENTRY.match(line.strip())
        if not m:
            continue
        path = unquote(m.group("path")).strip("/")
        if ".." in path.split("/") or not path:
            continue
        by = m.group("by")
        source, _, rig = by.partition(" on ")
        parts = path.split("/")
        form_dir = parts[1] if len(parts) >= 3 else ""
        form = next((f for f in ("over-ear", "in-ear", "earbud") if form_dir.endswith(f)), form_dir or "unknown")
        out.append(Entry(m.group("name"), path, source.strip(), rig.strip() or None, form))
    return out


def parse_parametric(txt: str) -> dict:
    """ParametricEQ.txt -> {preampDb, filters: [{type: PK|LSC|HSC, fc, gainDb, q}]} (OFF filters dropped)."""
    preamp = 0.0
    filters = []
    for line in txt.splitlines():
        line = line.strip()
        if m := _PREAMP.match(line):
            preamp = float(m.group(1))
            continue
        m = _FILTER.match(line)
        if not m or m.group(1).upper() != "ON":
            continue
        typ = FILTER_TYPES.get(m.group(2).upper())
        if typ is None:
            continue
        fc, gain = float(m.group(3)), float(m.group(4))
        q = float(m.group(5)) if m.group(5) else 0.707
        if not (10 <= fc <= 24000) or not (-30 <= gain <= 30) or not (0.05 <= q <= 30):
            continue
        filters.append({"type": typ, "fc": fc, "gainDb": gain, "q": q})
    if not filters:
        raise AutoEqError("no filters in profile")
    return {"preampDb": max(-30.0, min(0.0, preamp)), "filters": filters[:MAX_FILTERS]}


def search(entries: list[Entry], q: str, limit: int = 50) -> list[Entry]:
    """Every query word must appear in the name; names starting with the query rank first, then
    oratory1990 (the most trusted source), then alphabetical."""
    words = _norm(q).split()
    if not words:
        return entries[:limit]
    hits = []
    for e in entries:
        n = _norm(e.name)
        if all(w in n for w in words):
            hits.append((0 if n.startswith(words[0]) else 1, 0 if e.source == "oratory1990" else 1, n, e))
    hits.sort(key=lambda h: h[:3])
    return [h[3] for h in hits[:limit]]


class AutoEq:
    def __init__(self, cache_dir: Path, transport: httpx.BaseTransport | None = None):
        self.dir = cache_dir
        self.transport = transport
        self._lock = threading.Lock()
        self._entries: list[Entry] | None = None
        self._by_path: dict[str, Entry] = {}
        self._loaded_mtime = 0.0

    def _get(self, url: str) -> str:
        try:
            with upstream.client(self.transport) as c:
                r = c.get(url)
                r.raise_for_status()
                return r.text
        except httpx.HTTPError as e:
            raise AutoEqError(f"AutoEq fetch failed: {e}") from e

    def entries(self) -> list[Entry]:
        idx = self.dir / "INDEX.md"
        with self._lock:
            try:
                age = time.time() - idx.stat().st_mtime
            except OSError:
                age = None
            if age is None or age > INDEX_MAX_AGE_S:
                try:
                    text = self._get(f"{RAW_BASE}/INDEX.md")
                    if not parse_index(text):
                        raise AutoEqError("AutoEq index is empty")
                    self.dir.mkdir(parents=True, exist_ok=True)
                    tmp = idx.with_suffix(".tmp")
                    tmp.write_text(text)
                    tmp.replace(idx)
                except AutoEqError:
                    if age is None:
                        raise
                    log.warning("AutoEq index refresh failed; using the cached copy", exc_info=True)
            mtime = idx.stat().st_mtime
            if self._entries is None or mtime != self._loaded_mtime:
                self._entries = parse_index(idx.read_text())
                self._by_path = {e.path: e for e in self._entries}
                self._loaded_mtime = mtime
            return self._entries

    def profile(self, path: str) -> dict:
        self.entries()
        e = self._by_path.get(path)
        if e is None:
            raise KeyError(path)
        cache = self.dir / "profiles" / f"{hashlib.sha1(path.encode()).hexdigest()}.json"
        try:
            return json.loads(cache.read_text())
        except (OSError, ValueError):
            pass
        leaf = path.rsplit("/", 1)[-1]
        url = f"{RAW_BASE}/{quote(path)}/{quote(leaf)}%20ParametricEQ.txt"
        prof = {**e.as_dict(), **parse_parametric(self._get(url))}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(prof))
        return prof
