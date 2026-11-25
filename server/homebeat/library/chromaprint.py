"""Chromaprint audio fingerprints via the `fpcalc` tool (Chromaprint, LGPL-2.1+; Debian package
`libchromaprint-tools`). We run it as a separate process (never linked), at nice 19, on the read-only library.

`fpcalc -json` prints the compressed fingerprint that the AcoustID web service wants. For duplicate detection
we also need the raw 32-bit sub-fingerprints; instead of running fpcalc twice we decode the compressed form
(the inverse of Chromaprint's FingerprintCompressor, see chromaprint/src/fingerprint_decompressor.cpp)."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from homebeat.config import env

FPCALC_LENGTH_S = 120     # fpcalc's default: fingerprint the first 2 minutes (what AcoustID indexes)
TIMEOUT_S = 120


class FingerprintError(Exception):
    pass


@dataclass
class Fingerprint:
    duration_s: int
    fingerprint: str          # compressed, base64url (AcoustID format)
    raw: np.ndarray           # uint32 sub-fingerprints (~8 per second)


def fpcalc_path() -> str | None:
    return env("HOMEBEAT_FPCALC") or shutil.which("fpcalc")


def compute(path: Path, fpcalc: str | None = None, length_s: int = FPCALC_LENGTH_S) -> Fingerprint:
    exe = fpcalc or fpcalc_path()
    if not exe:
        raise FingerprintError("fpcalc not found (install Chromaprint: apt install libchromaprint-tools)")
    cmd = [exe, "-json", "-length", str(length_s), str(path)]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_S, check=False,
                           preexec_fn=(lambda: os.nice(19)) if hasattr(os, "nice") else None)
    except subprocess.TimeoutExpired as e:
        raise FingerprintError("fpcalc timed out") from e
    except OSError as e:
        raise FingerprintError(f"cannot run fpcalc: {e}") from e
    if p.returncode != 0:
        msg = (p.stderr or p.stdout).decode("utf-8", "replace").strip().splitlines()
        raise FingerprintError(f"fpcalc failed: {msg[-1] if msg else p.returncode}")
    try:
        d = json.loads(p.stdout)
        fp = d["fingerprint"]
        dur = int(round(float(d["duration"])))
    except (ValueError, KeyError, TypeError) as e:
        raise FingerprintError("unexpected fpcalc output") from e
    return Fingerprint(duration_s=dur, fingerprint=fp, raw=decode(fp))


# ---------- compressed fingerprint -> raw ----------

def _unpack(data: bytes, bits: int, count: int) -> list[int]:
    """Little-endian bit-packed unsigned ints (Chromaprint's UnpackInt3Array/UnpackInt5Array)."""
    out: list[int] = []
    acc = nacc = 0
    mask = (1 << bits) - 1
    for b in data:
        acc |= b << nacc
        nacc += 8
        while nacc >= bits and len(out) < count:
            out.append(acc & mask)
            acc >>= bits
            nacc -= bits
        if len(out) >= count:
            break
    return out


def decode(fp: str) -> np.ndarray:
    """Compressed (base64url, unpadded) fingerprint -> uint32 array. Raises FingerprintError if malformed."""
    try:
        data = base64.urlsafe_b64decode(fp + "=" * (-len(fp) % 4))
    except (ValueError, TypeError) as e:
        raise FingerprintError("bad fingerprint encoding") from e
    if len(data) < 4:
        raise FingerprintError("fingerprint too short")
    n = (data[1] << 16) | (data[2] << 8) | data[3]
    body = data[4:]
    if n == 0:
        return np.zeros(0, dtype=np.uint32)
    normal = _unpack(body, 3, len(body) * 8 // 3)
    found = exceptional = 0
    end = None
    for i, v in enumerate(normal):
        if v == 0:
            found += 1
            if found == n:
                end = i + 1
                break
        elif v == 7:
            exceptional += 1
    if end is None:
        raise FingerprintError("truncated fingerprint")
    normal = normal[:end]
    off = (end * 3 + 7) // 8
    exc = _unpack(body[off:], 5, exceptional)
    if len(exc) < exceptional:
        raise FingerprintError("truncated fingerprint")
    out = np.zeros(n, dtype=np.uint32)
    i = last_bit = value = j = 0
    prev = 0
    for v in normal:
        if v == 7:
            v += exc[j]
            j += 1
        if v == 0:
            cur = value ^ prev if i > 0 else value
            out[i] = cur
            prev = cur
            i += 1
            value = last_bit = 0
            continue
        last_bit += v
        value |= 1 << (last_bit - 1)
    return out


def to_blob(raw: np.ndarray) -> bytes:
    return raw.astype("<u4").tobytes()


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype="<u4").astype(np.uint32)


# ---------- similarity ----------

_POP = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def _bit_errors(a: np.ndarray, b: np.ndarray) -> int:
    x = np.bitwise_xor(a, b).view(np.uint8)
    return int(_POP[x].sum())


def similarity(a: np.ndarray, b: np.ndarray, max_offset: int = 12, min_overlap: int = 40) -> float:
    """1 - bit error rate of the best alignment (offset within ±max_offset sub-fingerprints, ~1.5 s).
    ~1.0 = same recording (re-encodes typically > 0.9), ~0.5 = unrelated audio."""
    best = 0.0
    for off in range(-max_offset, max_offset + 1):
        if off >= 0:
            x, y = a[off:], b
        else:
            x, y = a, b[-off:]
        n = min(len(x), len(y))
        if n < min_overlap:
            continue
        s = 1.0 - _bit_errors(x[:n], y[:n]) / (32.0 * n)
        best = max(best, s)
    return best
