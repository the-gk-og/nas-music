"""Audio-content hashes, so the expensive analysis (Essentia, beat grids) follows the audio, not the file.

A retag rewrites the file (new mtime, often a new size) and a move or rename gives it a new track id; neither
changes the audio. `audio_hash` hashes only the audio payload, so both keep the same hash, and the analysers
(`analyser`, `djmix`) copy the existing result instead of decoding the file again.

What is hashed, by container (the result is '<scheme>:<hex>'; schemes never compare equal to each other):
* FLAC: the MD5 of the decoded audio that the encoder stores in STREAMINFO ('flac-md5'). Free: only the first
  few bytes are read. Files whose encoder left it zero hash their audio frames instead ('flac').
* MP4/M4A/ALAC: the contents of the `mdat` atoms ('mp4'); tags live in `moov`.
* Ogg (Vorbis, Opus): the audio pages after the header pages ('ogg'), without the page serial, sequence number
  and CRC, which a retag that adds or removes header pages renumbers.
* WAV / AIFF: the `data` / `SSND` chunk ('riff' / 'aiff').
* Anything else (MP3, AAC, WavPack, APE...): the bytes between a leading ID3v2 tag and trailing APEv2,
  Lyrics3v2 and ID3v1 tags ('raw').

Hashes are cached per track in `audio_hashes`, keyed by (tracks.mtime, tracks.flac_bytes), so each file is read
at most once per change. Everything here only reads the library.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import struct
import time
from pathlib import Path

from .common import iso, transaction

log = logging.getLogger("homebeat.ai.audiohash")

CHUNK = 1 << 20


class _Hasher:
    def __init__(self, scheme: str):
        self.scheme = scheme
        self.h = hashlib.blake2b(digest_size=20)

    def update(self, b: bytes) -> None:
        self.h.update(b)

    def copy_range(self, f, start: int, end: int) -> None:
        f.seek(start)
        left = end - start
        while left > 0:
            b = f.read(min(CHUNK, left))
            if not b:
                break
            self.h.update(b)
            left -= len(b)

    def result(self) -> str:
        return f"{self.scheme}:{self.h.hexdigest()}"


def _id3v2_end(f, start: int = 0) -> int:
    """Offset just past an ID3v2 tag at `start` (several may be stacked), or `start` if there is none."""
    pos = start
    while True:
        f.seek(pos)
        h = f.read(10)
        if len(h) < 10 or h[:3] != b"ID3" or any(b & 0x80 for b in h[6:10]):
            return pos
        size = (h[6] << 21) | (h[7] << 14) | (h[8] << 7) | h[9]
        pos += 10 + size + (10 if h[5] & 0x10 else 0)


def _trailer_start(f, start: int, end: int) -> int:
    """Strip trailing ID3v1, Lyrics3v2 and APEv2 tags (in any order they are usually written)."""
    changed = True
    while changed and end - start > 0:
        changed = False
        if end - start >= 128:
            f.seek(end - 128)
            if f.read(3) == b"TAG":
                end -= 128
                changed = True
                continue
        if end - start >= 32:
            f.seek(end - 32)
            foot = f.read(32)
            if foot[:8] == b"APETAGEX":
                size, _items, flags = struct.unpack("<III", foot[12:24])
                end -= size + (32 if flags & 0x80000000 else 0)
                changed = True
                continue
        if end - start >= 15:
            f.seek(end - 15)
            foot = f.read(15)
            if foot[6:] == b"LYRICS200" and foot[:6].isdigit():
                end -= int(foot[:6]) + 15
                changed = True
    return max(start, end)


def _flac(f, start: int, size: int) -> str:
    pos = start + 4
    first = True
    while True:
        f.seek(pos)
        h = f.read(4)
        if len(h) < 4:
            raise ValueError("truncated FLAC metadata")
        last, btype, blen = h[0] & 0x80, h[0] & 0x7F, int.from_bytes(h[1:4], "big")
        if first:
            if btype != 0 or blen < 34:
                raise ValueError("FLAC without STREAMINFO")
            info = f.read(34)
            md5 = info[18:34]
            if any(md5):
                return f"flac-md5:{md5.hex()}"
            first = False
        pos += 4 + blen
        if last:
            break
    hs = _Hasher("flac")
    hs.copy_range(f, pos, _trailer_start(f, pos, size))
    return hs.result()


def _mp4(f, size: int) -> str:
    hs = _Hasher("mp4")
    pos, found = 0, False
    while pos + 8 <= size:
        f.seek(pos)
        h = f.read(8)
        asize, atype = struct.unpack(">I4s", h)
        hdr = 8
        if asize == 1:
            asize, hdr = struct.unpack(">Q", f.read(8))[0], 16
        elif asize == 0:
            asize = size - pos
        if asize < hdr:
            raise ValueError("bad MP4 atom")
        if atype == b"mdat":
            hs.copy_range(f, pos + hdr, min(pos + asize, size))
            found = True
        pos += asize
    if not found:
        raise ValueError("MP4 without mdat")
    return hs.result()


def _ogg(f, size: int) -> str:
    hs = _Hasher("ogg")
    pos, started = 0, False
    while pos + 27 <= size:
        f.seek(pos)
        h = f.read(27)
        if h[:4] != b"OggS":
            raise ValueError("lost Ogg page sync")
        granule = struct.unpack("<q", h[6:14])[0]
        nseg = h[26]
        segs = f.read(nseg)
        body = sum(segs)
        # Header pages carry granule 0 (or -1 while a big comment/cover packet spans pages); the first audio
        # page starts fresh (Vorbis I and RFC 7845 require it) and is the first with a positive granule.
        if not started and granule > 0:
            started = True
        if started:
            hs.update(h[5:14])        # header type + granule position
            hs.update(segs)
            hs.copy_range(f, pos + 27 + nseg, pos + 27 + nseg + body)
        pos += 27 + nseg + body
    if not started:
        raise ValueError("Ogg without audio pages")
    return hs.result()


def _chunks(f, size: int, start: int, big_endian: bool, want: bytes, scheme: str) -> str:
    fmt = ">4sI" if big_endian else "<4sI"
    pos = start
    while pos + 8 <= size:
        f.seek(pos)
        cid, clen = struct.unpack(fmt, f.read(8))
        if cid == want:
            hs = _Hasher(scheme)
            hs.copy_range(f, pos + 8, min(pos + 8 + clen, size))
            return hs.result()
        pos += 8 + clen + (clen & 1)
    raise ValueError(f"no {want.decode()} chunk")


def audio_hash(path: Path) -> str:
    """Hash of the audio payload of `path` (see the module docstring). Raises OSError / ValueError."""
    with open(path, "rb") as f:
        size = f.seek(0, 2)
        start = _id3v2_end(f)
        f.seek(start)
        head = f.read(12)
        try:
            if head[:4] == b"fLaC":
                return _flac(f, start, size)
            if start == 0 and head[4:8] == b"ftyp":
                return _mp4(f, size)
            if start == 0 and head[:4] == b"OggS":
                return _ogg(f, size)
            if start == 0 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
                return _chunks(f, size, 12, False, b"data", "riff")
            if start == 0 and head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
                return _chunks(f, size, 12, True, b"SSND", "aiff")
        except (ValueError, struct.error) as e:
            log.debug("%s: %s; hashing the raw payload", path, e)
        hs = _Hasher("raw")
        hs.copy_range(f, start, _trailer_start(f, start, size))
        return hs.result()


# ---------------------------------------------------------------- the per-track cache

def current(conn: sqlite3.Connection, root: Path, track_id: str, path: str, mtime: float, size: int) -> str | None:
    """The audio hash of a track's file as it is now: from `audio_hashes` when (mtime, size) still match,
    else computed (reading the file once) and stored. None if the file cannot be read."""
    r = conn.execute("SELECT mtime, bytes, audio_hash FROM audio_hashes WHERE track_id = ?", (track_id,)).fetchone()
    if r is not None and r[0] == mtime and r[1] == size:
        return r[2]
    try:
        h: str | None = audio_hash(Path(root) / path)
    except OSError as e:
        log.warning("cannot hash %s: %s", path, e)
        h = None
    with transaction(conn):
        conn.execute("INSERT OR REPLACE INTO audio_hashes (track_id, mtime, bytes, audio_hash, computed_at) "
                     "VALUES (?,?,?,?,?)", (track_id, mtime, size, h, iso()))
    return h


def backfill(conn: sqlite3.Connection, root: Path, table: str, version_col: str | None = None,
             version: str | None = None, time_budget_s: float = 20 * 60) -> int:
    """Fill `<table>.audio_hash` for rows analysed before v13 whose file has not changed since (analysed_at at
    or after tracks.mtime), so a later retag or move can reuse them. Resumable: each row is committed as it
    goes. Returns how many rows got a hash."""
    assert table in ("features", "track_dj")
    where = f" AND x.{version_col} = ?" if version_col else ""
    if table == "track_dj":
        where += " AND x.status = 'done'"
    vargs: tuple = (version,) if version_col else ()
    rows = conn.execute(
        f"SELECT x.track_id, t.path, t.mtime, t.flac_bytes FROM {table} x JOIN tracks t ON t.id = x.track_id "
        f"WHERE x.audio_hash IS NULL AND t.missing = 0{where} "
        "AND x.analysed_at >= strftime('%Y-%m-%dT%H:%M:%SZ', t.mtime, 'unixepoch')", vargs).fetchall()
    t0, n = time.monotonic(), 0
    for r in rows:
        if time.monotonic() - t0 > time_budget_s:
            break
        h = current(conn, root, r[0], r[1], r[2], r[3])
        if h is None:
            continue
        with transaction(conn):
            conn.execute(f"UPDATE {table} SET audio_hash = ? WHERE track_id = ? AND audio_hash IS NULL"
                         + (f" AND {version_col} = ?" if version_col else ""),
                         (h, r[0], *vargs))
        n += 1
    if n:
        log.info("audio hashes backfilled for %d %s rows (%d left)", n, table, len(rows) - n)
    return n
