"""Download and verify the Essentia model files for a `model_version`.

The Essentia models are CC BY-NC-SA 4.0 (non-commercial): they are downloaded at install time into
`data_dir/models/<model_version>/` and never committed to the repo. Each file is pinned by SHA-256;
a mismatch deletes the download and raises.

    uv run python -m nasmusic.ai download-models --data-dir /data
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("nasmusic.ai.models")

BASE = "https://essentia.upf.edu/models"
USER_AGENT = "nas-music/0.1 (open-source self-hosted music server)"


@dataclass(frozen=True)
class ModelFile:
    url_path: str           # relative to BASE
    sha256: str

    @property
    def filename(self) -> str:
        return self.url_path.rsplit("/", 1)[-1]

    @property
    def url(self) -> str:
        return f"{BASE}/{self.url_path}"


@dataclass(frozen=True)
class Head:
    name: str
    graph: ModelFile
    meta: ModelFile             # JSON with the class list
    input: str
    output: str
    kind: str                   # 'multilabel' | 'binary'
    tag_prefix: str
    positive: str = ""          # binary heads: class whose probability we keep
    top_n: int = 5
    min_prob: float = 0.1

    @property
    def filename(self) -> str:
        return self.graph.filename


@dataclass(frozen=True)
class ModelSet:
    version: str
    embedding: ModelFile
    heads: tuple[Head, ...]

    def files(self) -> list[ModelFile]:
        out = [self.embedding]
        for h in self.heads:
            out += [h.graph, h.meta]
        return out


def _binary(name: str, positive: str, gsha: str, jsha: str, prefix: str = "mood:") -> Head:
    p = f"classification-heads/{name}/{name}-discogs-effnet-1"
    return Head(name=name, graph=ModelFile(f"{p}.pb", gsha), meta=ModelFile(f"{p}.json", jsha),
                input="model/Placeholder", output="model/Softmax", kind="binary", tag_prefix=prefix,
                positive=positive)


_G = "classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1"
_T = "classification-heads/mtg_jamendo_moodtheme/mtg_jamendo_moodtheme-discogs-effnet-1"

# SHA-256 values were taken from the files served by essentia.upf.edu on 2026-11-18.
MODEL_SETS: dict[str, ModelSet] = {
    "effnet-discogs-1": ModelSet(
        version="effnet-discogs-1",
        embedding=ModelFile("feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb",
                            "3ed9af50d5367c0b9c795b294b00e7599e4943244f4cbd376869f3bfc87721b1"),
        heads=(
            Head(name="genre_discogs400", graph=ModelFile(f"{_G}.pb", "3885ba078a35249af94b8e5e4247689afac40deca4401a4bc888daf5a579c01c"),
                 meta=ModelFile(f"{_G}.json", "2d367319d9b782ffa10f69abf0e805b3ac4e10899025e5bdbaceda3919b243e0"),
                 input="serving_default_model_Placeholder", output="PartitionedCall:0",
                 kind="multilabel", tag_prefix="genre:", top_n=5, min_prob=0.08),
            Head(name="mtg_jamendo_moodtheme", graph=ModelFile(f"{_T}.pb", "03f2b047020aee4ab39f8880da7bdae2a36d06a1508d656c6d424ad4d6de07a9"),
                 meta=ModelFile(f"{_T}.json", "d62cd90263e4d613fa7fcce7a831e339450394794af63685f96e065c1a896ab0"),
                 input="model/Placeholder", output="model/Sigmoid",
                 kind="multilabel", tag_prefix="theme:", top_n=3, min_prob=0.05),
            _binary("danceability", "danceable", "e1251f02cdc846445e2bc1fb3fe9963e32728c5c000e009188ae47c8963cb4c4", "589e61dc05f0d7935be4b5fa7ab4341e5dc7cfecc6898f83503558564790cd75", prefix="dance:"),
            _binary("mood_aggressive", "aggressive", "7705284e3a67f23f04d3f2fd75e18a82c0e70db8875b7b6f7061f2432de80858", "81773e95d78db1b93283d73b2d06344d1ff79685b57d9428a40d47fdfcf537b8"),
            _binary("mood_happy", "happy", "de322aee7f4da29ecbd149c86e7964bd6fabe41ca85698a0be40a39d49e88633", "ed4601bc396b23367d29cf45aa327366cdfdbd3e2baeb7a3f7893237f1ffd9e6"),
            _binary("mood_relaxed", "relaxed", "2c5aa6666b58fe80429a2dc677a135e9995922889b5a399af87d1c15f0ebb71d", "86c0fe1c2c6d49bf08537bc2d3a602204feaede011ed119c1fc6c36270f60e6a"),
            _binary("mood_sad", "sad", "4865cba49968b6ec295db3e8af6b4a7bb506b1a646628b9f07ee9910d52df82c", "7f2c00099ad8255af33e43ab77d1ac2c86a1dd9f46d3e5ea2a9a52246f5033d9"),
            _binary("mood_party", "party", "bbd773a27002978179dd6fc5ffb75b7c41e6a7e412b5c5254d51a025e5b9373e", "a7f15c6e05db7c3e9226646a87162ebb9cc00aea8bdbbc910aedafa7f4a8fdfb"),
            _binary("mood_acoustic", "acoustic", "96a48d403f52fe9047214437fe396dd2f46699da765c911cd6dfbd456a20f733", "45f61055d1d87d5e7f8a413b06b57eca20c3af1a23cf8912e0d916422e5207e1"),
            _binary("voice_instrumental", "instrumental", "c8033548e17c292874265db62e82a051247768d10375a35a769e7bf695f16acf", "43ac2c3b055dfaed20f6232e0f10636c287f1c5a6e5bd02c5585860031964c8f", prefix="voice:"),
        ),
    ),
}


class ModelDownloadError(RuntimeError):
    pass


def model_dir(data_dir: Path, version: str) -> Path:
    return Path(data_dir) / "models" / version


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(data_dir: Path, version: str) -> list[str]:
    """Return the filenames that are missing or fail their checksum."""
    mset = MODEL_SETS[version]
    d = model_dir(data_dir, version)
    bad = []
    for mf in mset.files():
        p = d / mf.filename
        if not p.is_file() or _sha256(p) != mf.sha256:
            bad.append(mf.filename)
    return bad


def download(data_dir: Path, version: str, client: httpx.Client | None = None) -> Path:
    """Fetch any missing/corrupt files for `version`. Atomic per file (download to .part, rename)."""
    mset = MODEL_SETS[version]
    d = model_dir(data_dir, version)
    d.mkdir(parents=True, exist_ok=True)
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=120, follow_redirects=True)
    try:
        for mf in mset.files():
            dest = d / mf.filename
            if dest.is_file() and _sha256(dest) == mf.sha256:
                continue
            part = dest.with_suffix(dest.suffix + ".part")
            log.info("downloading %s", mf.url)
            h = hashlib.sha256()
            try:
                with client.stream("GET", mf.url) as r:
                    r.raise_for_status()
                    with open(part, "wb") as f:
                        for chunk in r.iter_bytes(1 << 16):
                            h.update(chunk)
                            f.write(chunk)
            except httpx.HTTPError as e:
                part.unlink(missing_ok=True)
                raise ModelDownloadError(f"download failed for {mf.url}: {e}") from e
            if h.hexdigest() != mf.sha256:
                part.unlink(missing_ok=True)
                raise ModelDownloadError(f"checksum mismatch for {mf.filename}: got {h.hexdigest()}")
            os.replace(part, dest)
    finally:
        if own:
            client.close()
    return d


def ensure_models(data_dir: Path, version: str) -> Path:
    """Return the model directory, downloading anything missing first."""
    if verify(data_dir, version):
        download(data_dir, version)
    return model_dir(data_dir, version)


def head_classes(data_dir: Path, version: str, head: Head) -> list[str]:
    meta = json.loads((model_dir(data_dir, version) / head.meta.filename).read_text())
    return list(meta["classes"])
