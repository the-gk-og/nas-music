"""Synthetic library + events for AI engine tests. No network, no real audio (except analyser tests)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from nasmusic import db
from nasmusic.ai.api import AIConfig
from nasmusic.ai.common import iso, to_blob

NOW = datetime.now(UTC).replace(microsecond=0)
GENRES = ["Rock---Indie Rock", "Jazz---Hard Bop", "Electronic---Techno", "Folk---Folk Rock",
          "Hip Hop---Boom Bap", "Classical---Baroque"]


def _id(s: str) -> str:
    return hashlib.sha1(s.encode()).hexdigest()


@dataclass
class SynthLib:
    conn: object
    cfg: AIConfig
    centres: np.ndarray
    tracks: list[dict] = field(default_factory=list)
    n_events: int = 0

    def by_cluster(self, c: int) -> list[dict]:
        return [t for t in self.tracks if t["cluster"] == c]

    def event(self, track_id: str, etype: str, at: datetime, position_s: float | None = None,
              duration_s: float | None = None, rating: int | None = None) -> None:
        self.n_events += 1
        self.conn.execute(
            "INSERT INTO events (id, device_id, type, track_id, at, position_s, duration_s, source, mix_id, "
            "rating, offline, received_at) VALUES (?, NULL, ?, ?, ?, ?, ?, 'library', NULL, ?, 0, ?)",
            (f"ev{self.n_events}", etype, track_id, iso(at), position_s, duration_s, rating, iso(at)))

    def play(self, t: dict, at: datetime, full: bool = True) -> None:
        d = t["duration"]
        self.event(t["id"], "play", at, d if full else d * 0.3, d)

    def skip(self, t: dict, at: datetime, pos: float = 10.0) -> None:
        self.event(t["id"], "skip", at, pos, t["duration"])


def build_library(conn, cfg: AIConfig, n_clusters: int = 6, artists_per_cluster: int = 8,
                  tracks_per_artist: int = 8, dims: int = 32, seed: int = 1,
                  version: str = "basic-1") -> SynthLib:
    rng = np.random.default_rng(seed)
    centres = rng.normal(size=(n_clusters, dims)) * 3
    lib = SynthLib(conn, cfg, centres)
    added = iso(NOW - timedelta(days=100))
    for c in range(n_clusters):
        for a in range(artists_per_cluster):
            aname = f"Artist {c}-{a}"
            aid = _id(aname)
            conn.execute("INSERT INTO artists (id, name, sort_name) VALUES (?,?,?)", (aid, aname, aname))
            alb = _id(aname + "album")
            conn.execute("INSERT INTO albums (id, artist_id, title, year, added_at) VALUES (?,?,?,?,?)",
                         (alb, aid, f"Album {c}-{a}", 2000 + a, added))
            for k in range(tracks_per_artist):
                path = f"{aname}/{k:02d}.flac"
                tid = _id(path)
                dur = float(rng.uniform(150, 330))
                conn.execute(
                    "INSERT INTO tracks (id, path, album_id, artist_id, title, track_no, duration_s, flac_bytes, "
                    "mtime, added_at, genre) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (tid, path, alb, aid, f"Song {c}-{a}-{k}", k + 1, dur, 1000, 0.0, added, GENRES[c % 6]))
                emb = centres[c] + rng.normal(size=dims)
                energy = float(np.clip(0.15 * c + rng.normal(0.1, 0.1), 0, 1))
                conn.execute(
                    "INSERT INTO features (track_id, model_version, embedding, dims, bpm, key, energy, "
                    "danceability, tags, analysed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (tid, version, to_blob(emb.astype(np.float32)), dims, 90 + 10 * c, "C major", energy,
                     0.5, json.dumps({f"genre:{GENRES[c % 6]}": 0.8, f"mood:m{c}": 0.5}), iso(NOW)))
                lib.tracks.append({"id": tid, "artist_id": aid, "cluster": c, "duration": dur, "artist": aname})
    return lib


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.db")
    db.init(c)
    yield c
    c.close()


@pytest.fixture
def cfg(tmp_path) -> AIConfig:
    lib = tmp_path / "library"
    lib.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    return AIConfig(library_root=lib, data_dir=data, model_version="basic-1", timezone="Europe/London")


@pytest.fixture
def synth(conn, cfg) -> SynthLib:
    return build_library(conn, cfg)


def mix_rows(conn, mix_id: str) -> list[tuple]:
    return conn.execute(
        "SELECT mt.position, mt.track_id, t.artist_id, mt.reason, t.duration_s, t.blocked FROM mix_tracks mt "
        "JOIN tracks t ON t.id = mt.track_id WHERE mt.mix_id = ? ORDER BY mt.position", (mix_id,)).fetchall()


def ffmpeg_ok() -> bool:
    import shutil
    return shutil.which("ffmpeg") is not None


def write_tone(path: Path, freq: float, seconds: float = 12, noise: float = 0.05) -> None:
    import subprocess
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
         "-f", "lavfi", "-i", f"anoisesrc=d={seconds}:a={noise}:seed=3",
         "-filter_complex", "amix=inputs=2:duration=shortest", "-ar", "44100", "-c:a", "flac", str(path)],
        check=True)
