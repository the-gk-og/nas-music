"""Cover images for podcasts, stations and audiobooks, cached under data_dir/spoken/art and resized with the
same bucketed JPEG cache as album art (keys are prefixed so they never collide with album ids)."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from nasmusic.library import art
from nasmusic.spoken import net

log = logging.getLogger(__name__)
REFRESH_S = 7 * 86400
FAIL_RETRY_S = 6 * 3600


def src_dir(data_dir: Path) -> Path:
    return data_dir / "spoken" / "art"


def source(data_dir: Path, key: str) -> Path:
    return src_dir(data_dir) / f"{key}.img"


def store(data_dir: Path, key: str, data: bytes) -> None:
    p = source(data_dir, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)
    art.forget_resized(data_dir, key)


def fetch_remote(data_dir: Path, key: str, url: str | None, transport=None) -> Path | None:
    """The cached source image for `key`, downloading `url` when missing or a week old."""
    p = source(data_dir, key)
    fail = p.with_suffix(".fail")
    now = time.time()
    try:
        if p.stat().st_mtime > now - REFRESH_S:
            return p
    except OSError:
        pass
    if not url:
        return p if p.exists() else None
    try:
        if fail.stat().st_mtime > now - FAIL_RETRY_S:
            return p if p.exists() else None
    except OSError:
        pass
    try:
        with net.client(transport, timeout=net.httpx.Timeout(10.0, connect=5.0)) as c:
            r = net.get_limited(c, url, net.MAX_IMAGE_BYTES)
        if r.status_code != 200 or not r.content:
            raise net.BadUrl(f"HTTP {r.status_code}")
        store(data_dir, key, r.content)
        fail.unlink(missing_ok=True)
    except Exception as e:  # network, bad URL: keep whatever we had
        log.info("cover %s from %s failed: %s", key, url, e)
        fail.parent.mkdir(parents=True, exist_ok=True)
        fail.touch()
    return p if p.exists() else None


def resized(data_dir: Path, key: str, src: Path | None, size: int) -> Path | None:
    if src is None:
        return None
    # art.resized joins library_root / art_path; an absolute art_path wins over the root.
    return art.resized(data_dir, Path("/"), key, str(src.resolve()), size)
