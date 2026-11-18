from __future__ import annotations

import hashlib
import json

import httpx
import numpy as np
import pytest
from tests.ai.conftest import ffmpeg_ok, write_tone

from nasmusic.ai import analyser, api, models
from nasmusic.ai.common import from_blob, iso
from nasmusic.ai.extractors import BasicExtractor, effective_version, key_from_chroma

pytestmark = pytest.mark.skipif(not ffmpeg_ok(), reason="ffmpeg not installed")


def add_track(conn, path: str, duration: float = 12.0, mtime: float = 0.0) -> str:
    tid = hashlib.sha1(path.encode()).hexdigest()
    conn.execute("INSERT OR IGNORE INTO artists (id, name, sort_name) VALUES ('a', 'A', 'A')")
    conn.execute("INSERT OR IGNORE INTO albums (id, artist_id, title, added_at) VALUES ('al', 'a', 'X', ?)", (iso(),))
    conn.execute("INSERT INTO tracks (id, path, album_id, artist_id, title, duration_s, flac_bytes, mtime, added_at) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", (tid, path, "al", "a", path, duration, 1, mtime, iso()))
    return tid


def test_basic_extractor_on_flac(tmp_path):
    low, high = tmp_path / "low.flac", tmp_path / "high.flac"
    write_tone(low, 110)
    write_tone(high, 3000)
    ex = BasicExtractor()
    a, b = ex.extract_file(low, 12), ex.extract_file(high, 12)
    assert a.embedding.shape == b.embedding.shape and a.embedding.dtype == np.float32
    assert np.all(np.isfinite(a.embedding))
    assert not np.allclose(a.embedding, b.embedding)
    assert 0 <= a.energy <= 1 and 0 <= b.energy <= 1
    assert a.key and a.key.split()[1] in ("major", "minor")


def test_key_from_chroma():
    c_major = np.array([1, 0, 1, 0, 1, 1, 0, 1, 0, 1, 0, 1], float)
    assert key_from_chroma(c_major) in ("C major", "A minor")


def test_analyse_pending_resumable_and_failures(conn, cfg):
    ids = []
    for k, f in enumerate([220, 440, 880]):
        write_tone(cfg.library_root / f"t{k}.flac", f)
        ids.append(add_track(conn, f"t{k}.flac"))

    assert effective_version("basic-1") == "basic-1"
    # interrupted run: only 2 done
    assert api.analyse_pending(conn, cfg, limit=2) == 2
    done = {r[0] for r in conn.execute("SELECT track_id FROM features WHERE model_version='basic-1'")}
    assert len(done) == 2
    (cfg.library_root / "broken.flac").write_bytes(b"not audio")
    broken = add_track(conn, "broken.flac")
    # resume picks up the rest, the broken file is marked failed (not retried immediately)
    assert api.analyse_pending(conn, cfg) == 1
    jobs = dict(conn.execute("SELECT track_id, status FROM analysis_jobs").fetchall())
    assert jobs[broken] == "failed" and all(jobs[i] == "done" for i in ids)
    err = conn.execute("SELECT error FROM analysis_jobs WHERE track_id = ?", (broken,)).fetchone()[0]
    assert "ffmpeg" in err or "audio" in err
    assert api.analyse_pending(conn, cfg) == 0
    # rows are well formed
    for blob, dims, energy, tags in conn.execute("SELECT embedding, dims, energy, tags FROM features"):
        assert from_blob(blob, dims).size == dims and 0 <= energy <= 1 and json.loads(tags) == {}
    # a changed file (newer mtime) is re-analysed; so is a failed file that was replaced
    conn.execute("UPDATE tracks SET mtime = ? WHERE id = ?", (4102444800.0, ids[0]))  # year 2100
    assert [r["id"] for r in analyser.pending_tracks(conn, "basic-1", 7)] == [ids[0]]
    conn.execute("UPDATE tracks SET mtime = ? WHERE id = ?", (4102444800.0, broken))
    assert len(analyser.pending_tracks(conn, "basic-1", 7)) == 2
    # failed tracks are retried after `retry_failed_days`
    conn.execute("UPDATE tracks SET mtime = 0 WHERE id = ?", (broken,))
    assert len(analyser.pending_tracks(conn, "basic-1", 7)) == 1
    conn.execute("UPDATE analysis_jobs SET updated_at = '2000-01-01T00:00:00Z' WHERE track_id = ?", (broken,))
    assert len(analyser.pending_tracks(conn, "basic-1", 7)) == 2


def test_crash_leftover_pending_is_resumed(conn, cfg):
    write_tone(cfg.library_root / "x.flac", 330)
    tid = add_track(conn, "x.flac")
    conn.execute("INSERT INTO analysis_jobs VALUES (?, 'basic-1', 'pending', NULL, ?)", (tid, iso()))
    assert api.analyse_pending(conn, cfg) == 1


def test_essentia_fallback(conn, cfg, monkeypatch):
    """Requesting the Essentia model without Essentia installed falls back to basic-1."""
    from dataclasses import replace

    from nasmusic.ai import extractors
    monkeypatch.setattr(extractors, "essentia_available", lambda: False)
    write_tone(cfg.library_root / "y.flac", 500)
    add_track(conn, "y.flac")
    assert api.analyse_pending(conn, replace(cfg, model_version="effnet-discogs-1")) == 1
    assert conn.execute("SELECT model_version FROM features").fetchone()[0] == "basic-1"


def test_benchmark(conn, cfg):
    for k in range(3):
        write_tone(cfg.library_root / f"b{k}.flac", 200 + 100 * k)
        add_track(conn, f"b{k}.flac")
    r = analyser.benchmark(conn, cfg, n=2, workers=1)
    assert r["ok"] == 2 and r["library_tracks"] == 3 and r["pending_tracks"] == 3
    assert r["est_full_library_s"] > 0 and r["seconds_per_track"] > 0
    assert conn.execute("SELECT COUNT(*) FROM features").fetchone()[0] == 0  # benchmark writes nothing


def test_model_download_verifies_checksums(tmp_path, monkeypatch):
    good, bad = b"model-bytes", b"tampered"
    mset = models.ModelSet("test-1", models.ModelFile("x/emb.pb", hashlib.sha256(good).hexdigest()), ())
    monkeypatch.setitem(models.MODEL_SETS, "test-1", mset)
    payload = {"body": bad}
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, content=payload["body"])))
    with pytest.raises(models.ModelDownloadError, match="checksum"):
        models.download(tmp_path, "test-1", client=client)
    assert not any(models.model_dir(tmp_path, "test-1").iterdir())  # nothing half-written left
    payload["body"] = good
    models.download(tmp_path, "test-1", client=client)
    assert models.verify(tmp_path, "test-1") == []


def test_effnet_model_set_is_pinned():
    files = models.MODEL_SETS["effnet-discogs-1"].files()
    assert len(files) == 21
    assert all(len(f.sha256) == 64 and f.url.startswith("https://essentia.upf.edu/models/") for f in files)


def test_segments():
    from nasmusic.ai.extractors import segments
    # long track, 3 excerpts at 20/50/80 %
    assert segments(300, 480, 3, 40) == [(40.0, 40), (130.0, 40), (220.0, 40)]
    # short track: whole thing
    assert segments(100, 480, 3, 40) == [(0.0, 480)]
    # no excerpts: centre crop
    assert segments(600, 480, 0) == [(60.0, 480)]
