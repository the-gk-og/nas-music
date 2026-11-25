"""Audio-content hashes (schema v13): retags and moves keep the hash, so analysis is reused, not redone."""
from __future__ import annotations

import os
import shutil
import subprocess

import numpy as np
import pytest
from mutagen import File as MutagenFile
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TIT2

from homebeat.ai import analyser, api, audiohash, djmix
from homebeat.ai.common import iso
from tests.ai.conftest import ffmpeg_ok, write_tone
from tests.ai.test_analyser import add_track

pytestmark = pytest.mark.skipif(not ffmpeg_ok(), reason="ffmpeg not installed")

COVER = b"\x89PNG\r\n\x1a\n" + os.urandom(150_000)   # a big tag makes taggers move/pad the audio


def encode(path, freq: float, codec: list[str], seconds: float = 6) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}",
                    "-ar", "48000", *codec, str(path)], check=True)


FORMATS = {
    "flac": ["-c:a", "flac"],
    "mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
    "m4a": ["-c:a", "aac", "-b:a", "96k"],
    "ogg": ["-c:a", "libvorbis"],
    "opus": ["-c:a", "libopus", "-b:a", "64k"],
    "wav": ["-c:a", "pcm_s16le"],
}


def retag(path) -> None:
    """What a tagger does: new title, a big embedded cover. Only metadata changes."""
    if path.suffix == ".mp3":
        tags = ID3(path)
        tags.add(TIT2(encoding=3, text="Retagged"))
        tags.add(APIC(encoding=3, mime="image/png", type=3, desc="cover", data=COVER))
        tags.save(path)
        return
    if path.suffix == ".flac":
        f = FLAC(path)
        f["title"] = "Retagged"
        pic = Picture()
        pic.data, pic.mime, pic.type = COVER, "image/png", 3
        f.add_picture(pic)
        f.save()
        return
    if path.suffix == ".wav":
        f = MutagenFile(path)
        f.add_tags()
        f.tags.add(TIT2(encoding=3, text="Retagged"))
        f.save()
        return
    f = MutagenFile(path)
    if path.suffix == ".m4a":
        f["\xa9nam"] = ["Retagged"]
        f["covr"] = [COVER]
    else:
        f["title"] = ["Retagged"]
        f["comment"] = ["x" * 120_000]   # spans several Ogg pages
    f.save()


@pytest.mark.parametrize("ext", sorted(FORMATS))
def test_hash_survives_retag_and_differs_for_other_audio(tmp_path, ext):
    a, b = tmp_path / f"a.{ext}", tmp_path / f"b.{ext}"
    encode(a, 440, FORMATS[ext])
    encode(b, 660, FORMATS[ext])
    before = audiohash.audio_hash(a)
    size = a.stat().st_size
    retag(a)
    assert a.stat().st_size != size          # the file really changed
    assert audiohash.audio_hash(a) == before
    assert audiohash.audio_hash(b) != before
    scheme = before.split(":")[0]
    assert scheme == {"flac": "flac-md5", "mp3": "raw", "m4a": "mp4", "ogg": "ogg", "opus": "ogg", "wav": "riff"}[ext]


def test_flac_without_md5_hashes_frames(tmp_path):
    a = tmp_path / "a.flac"
    encode(a, 440, FORMATS["flac"])
    raw = bytearray(a.read_bytes())
    assert raw[:4] == b"fLaC"
    raw[8 + 18:8 + 34] = bytes(16)           # STREAMINFO md5 left unset by the encoder
    a.write_bytes(raw)
    h = audiohash.audio_hash(a)
    assert h.startswith("flac:")
    retag(a)
    assert audiohash.audio_hash(a) == h


def test_mp3_trailing_tags_are_ignored(tmp_path):
    a = tmp_path / "a.mp3"
    encode(a, 440, FORMATS["mp3"])
    h = audiohash.audio_hash(a)
    with open(a, "ab") as f:                 # ID3v1 at the very end
        f.write(b"TAG" + b"Title".ljust(125, b"\0"))
    assert audiohash.audio_hash(a) == h


def test_cache_is_per_mtime_and_size(conn, cfg):
    write_tone(cfg.library_root / "x.flac", 300, seconds=3)
    tid = add_track(conn, "x.flac")
    h = audiohash.current(conn, cfg.library_root, tid, "x.flac", 0.0, 1)
    assert h and conn.execute("SELECT audio_hash FROM audio_hashes WHERE track_id = ?", (tid,)).fetchone()[0] == h
    (cfg.library_root / "x.flac").write_bytes(b"gone")     # cached: not reread while (mtime, size) match
    assert audiohash.current(conn, cfg.library_root, tid, "x.flac", 0.0, 1) == h
    assert audiohash.current(conn, cfg.library_root, tid, "x.flac", 5.0, 4) != h
    assert audiohash.current(conn, cfg.library_root, tid, "nope.flac", 6.0, 4) is None


class NoPool:
    def __init__(self, *a, **k):
        raise AssertionError("decoded a track whose audio was already analysed")


def _later(conn, tid):
    """The scanner saw a newer file (a retag): its mtime is now after the analysis. Rows are dated 2000 and
    the file 2001 so a re-stamp (now) is after the file again."""
    for table in ("features", "track_dj"):
        conn.execute(f"UPDATE {table} SET analysed_at = '2000-01-01T00:00:00Z' WHERE track_id = ?", (tid,))
    conn.execute("UPDATE tracks SET mtime = mtime + 1000000000.5, flac_bytes = flac_bytes + 1 WHERE id = ?", (tid,))


def test_retag_and_move_reuse_features(conn, cfg, monkeypatch):
    lib = cfg.library_root
    write_tone(lib / "a.flac", 220)
    write_tone(lib / "b.flac", 880)
    a, b = add_track(conn, "a.flac"), add_track(conn, "b.flac")
    assert api.analyse_pending(conn, cfg) == 2
    emb = {r[0]: r[1] for r in conn.execute("SELECT track_id, embedding FROM features")}
    hashes = dict(conn.execute("SELECT track_id, audio_hash FROM features").fetchall())
    assert all(hashes.values()) and hashes[a] != hashes[b]

    monkeypatch.setattr(analyser, "ProcessPoolExecutor", NoPool)
    # retag: same audio, new mtime/size -> the row is re-stamped, not recomputed
    retag(lib / "a.flac")
    _later(conn, a)
    assert [r["id"] for r in analyser.pending_tracks(conn, "basic-1", 7)] == [a]
    assert api.analyse_pending(conn, cfg) == 1
    assert analyser.pending_tracks(conn, "basic-1", 7) == []
    assert conn.execute("SELECT embedding FROM features WHERE track_id = ?", (a,)).fetchone()[0] == emb[a]

    # move/rename: new path = new track id; the old one goes missing -> features copied by audio hash
    (lib / "moved").mkdir()
    shutil.move(lib / "b.flac", lib / "moved" / "B renamed.flac")
    conn.execute("UPDATE tracks SET missing = 1 WHERE id = ?", (b,))
    b2 = add_track(conn, "moved/B renamed.flac")
    assert api.analyse_pending(conn, cfg) == 1
    row = conn.execute("SELECT embedding, audio_hash FROM features WHERE track_id = ?", (b2,)).fetchone()
    assert row[0] == emb[b] and row[1] == hashes[b]
    assert conn.execute("SELECT status FROM analysis_jobs WHERE track_id = ?", (b2,)).fetchone()[0] == "done"

    # really new audio is still decoded
    monkeypatch.undo()
    write_tone(lib / "a.flac", 1500)
    _later(conn, a)
    assert api.analyse_pending(conn, cfg) == 1
    new = conn.execute("SELECT embedding, audio_hash FROM features WHERE track_id = ?", (a,)).fetchone()
    assert new[1] != hashes[a] and not np.array_equal(np.frombuffer(new[0], "<f4"), np.frombuffer(emb[a], "<f4"))


def test_backfill_hashes_pre_v13_rows(conn, cfg, monkeypatch):
    lib = cfg.library_root
    write_tone(lib / "a.flac", 220, seconds=4)
    a = add_track(conn, "a.flac")
    assert api.analyse_pending(conn, cfg) == 1
    conn.execute("UPDATE features SET audio_hash = NULL")      # as migrated from v12
    conn.execute("DELETE FROM audio_hashes")
    assert analyser.analyse_pending(conn, cfg) == 0            # nothing to analyse; backfill runs
    h = conn.execute("SELECT audio_hash FROM features WHERE track_id = ?", (a,)).fetchone()[0]
    assert h == audiohash.audio_hash(lib / "a.flac")
    # a stale row (file changed since it was analysed) is not given the new file's hash
    conn.execute("UPDATE features SET audio_hash = NULL")
    _later(conn, a)
    assert audiohash.backfill(conn, lib, "features", "model_version", "basic-1") == 0


def test_djmix_reuses_by_audio_hash(conn, cfg, monkeypatch):
    lib = cfg.library_root
    write_tone(lib / "a.flac", 220, seconds=8)
    a = add_track(conn, "a.flac")
    monkeypatch.setattr(djmix, "essentia_available", lambda: False)
    r = djmix.run(conn, lib, in_process=True)
    assert r["analysed"] == 1 and r["reused"] == 0
    before = dict(conn.execute("SELECT * FROM track_dj WHERE track_id = ?", (a,)).fetchone())
    assert before["audio_hash"]

    def boom(*a, **k):
        raise AssertionError("decoded again")

    monkeypatch.setattr(djmix, "_work", boom)
    retag(lib / "a.flac")
    _later(conn, a)
    r = djmix.run(conn, lib, in_process=True)
    assert r["analysed"] == r["reused"] == 1 and r["failed"] == 0
    shutil.copy(lib / "a.flac", lib / "copy.flac")
    c = add_track(conn, "copy.flac")
    assert djmix.run(conn, lib, in_process=True)["reused"] == 1
    after = dict(conn.execute("SELECT * FROM track_dj WHERE track_id = ?", (c,)).fetchone())
    skip = {"track_id", "analysed_at"}
    assert {k: v for k, v in after.items() if k not in skip} == {k: v for k, v in before.items() if k not in skip}
    assert after["analysed_at"] >= iso()[:10]
