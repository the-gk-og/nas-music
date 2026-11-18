"""Album cover art: folder images or embedded pictures, resized and cached under data_dir/art."""
from __future__ import annotations

import io
import logging
import os
import threading
from pathlib import Path

from PIL import Image, UnidentifiedImageError

log = logging.getLogger(__name__)

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
PREFERRED_STEMS = ("cover", "folder", "front", "album", "albumart", "albumartlarge")
SIZE_BUCKETS = (64, 128, 256, 384, 512, 768, 1024)
SRC_MAX = 1200

_lock = threading.Lock()


def pick_folder_image(names: list[str]) -> str | None:
    """Choose the best cover image among file names in one directory."""
    imgs = [n for n in names if os.path.splitext(n)[1].lower() in IMAGE_EXTS and not n.startswith(".")]
    if not imgs:
        return None
    by_stem = {os.path.splitext(n)[0].lower(): n for n in imgs}
    for stem in PREFERRED_STEMS:
        if stem in by_stem:
            return by_stem[stem]
    for n in sorted(imgs, key=str.lower):
        low = n.lower()
        if "cover" in low or "front" in low:
            return n
    # Don't guess between "back.jpg", "cd.jpg", "booklet-03.jpg"... unless it's the only image.
    return imgs[0] if len(imgs) == 1 else None


def art_dir(data_dir: Path) -> Path:
    return data_dir / "art"


def src_path(data_dir: Path, album_id: str) -> Path:
    """Where the embedded-art source for an album is stored (already downscaled to SRC_MAX)."""
    return art_dir(data_dir) / "src" / f"{album_id}.jpg"


def none_marker(data_dir: Path, album_id: str) -> Path:
    """Marks 'we looked for embedded art and there is none' so rescans don't re-read the file."""
    return art_dir(data_dir) / "src" / f"{album_id}.none"


def _to_jpeg(img: Image.Image, max_side: int) -> bytes:
    img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
    img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=86, optimize=True, progressive=True)
    return out.getvalue()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{threading.get_ident()}.tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def store_embedded(data_dir: Path, album_id: str, data: bytes | None) -> bool:
    """Save embedded art bytes as the album's source image. Returns True if art was stored."""
    if data:
        try:
            with Image.open(io.BytesIO(data)) as img:
                _atomic_write(src_path(data_dir, album_id), _to_jpeg(img, SRC_MAX))
            none_marker(data_dir, album_id).unlink(missing_ok=True)
            return True
        except (UnidentifiedImageError, OSError, ValueError) as e:
            log.warning("bad embedded art for album %s: %s", album_id, e)
    _atomic_write(none_marker(data_dir, album_id), b"")
    src_path(data_dir, album_id).unlink(missing_ok=True)
    return False


def forget_embedded(data_dir: Path, album_id: str) -> None:
    src_path(data_dir, album_id).unlink(missing_ok=True)
    none_marker(data_dir, album_id).unlink(missing_ok=True)


def embedded_state_known(data_dir: Path, album_id: str) -> bool:
    return src_path(data_dir, album_id).exists() or none_marker(data_dir, album_id).exists()


def has_art(data_dir: Path, art_path: str | None, album_id: str) -> bool:
    return bool(art_path) or src_path(data_dir, album_id).exists()


def bucket(size: int) -> int:
    for b in SIZE_BUCKETS:
        if size <= b:
            return b
    return SIZE_BUCKETS[-1]


def resized(data_dir: Path, library_root: Path, album_id: str, art_path: str | None, size: int) -> Path | None:
    """Return a cached JPEG of the album art at (bucketed) `size`, creating it if needed."""
    source = (library_root / art_path) if art_path else src_path(data_dir, album_id)
    try:
        src_mtime = source.stat().st_mtime
    except OSError:
        return None
    b = bucket(size)
    out = art_dir(data_dir) / str(b) / f"{album_id}.jpg"
    try:
        if out.stat().st_mtime >= src_mtime:
            return out
    except OSError:
        pass
    with _lock:
        try:
            with Image.open(source) as img:
                img.draft("RGB", (b, b))  # fast JPEG downscale on decode
                _atomic_write(out, _to_jpeg(img, b))
        except (UnidentifiedImageError, OSError, ValueError) as e:
            log.warning("cannot read art %s: %s", source, e)
            return None
    return out
