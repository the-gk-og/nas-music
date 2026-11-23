import os
import subprocess
import threading
import time

from tests.conftest import track_ids

from nasmusic.stream.transcode import OpusCache


def test_flac_full_and_range(client, conn, cfg):
    tid = track_ids(conn)["Song 1"]
    data = (cfg.library_root / "Artist One/First Album/01 Song 1.flac").read_bytes()

    r = client.get(f"/api/v1/stream/{tid}?format=flac")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/flac"
    assert r.headers["accept-ranges"] == "bytes"
    assert int(r.headers["content-length"]) == len(data)
    assert r.content == data

    r = client.get(f"/api/v1/stream/{tid}?format=flac", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 100-199/{len(data)}"
    assert r.content == data[100:200]

    r = client.get(f"/api/v1/stream/{tid}?format=flac", headers={"Range": "bytes=-50"})
    assert r.status_code == 206 and r.content == data[-50:]

    r = client.get(f"/api/v1/stream/{tid}?format=flac", headers={"Range": f"bytes={len(data) + 10}-"})
    assert r.status_code == 416


def test_opus_transcode_cached_and_ranged(client, conn, app, cfg):
    tid = track_ids(conn)["Song 2"]
    cache: OpusCache = app.state.ctx.opus
    r = client.get(f"/api/v1/stream/{tid}?format=opus&bitrate=96")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/ogg"
    body = r.content
    assert body[:4] == b"OggS" and b"OpusHead" in body[:100]
    assert int(r.headers["content-length"]) == len(body)
    assert cache.transcodes == 1

    r = client.get(f"/api/v1/stream/{tid}?bitrate=96", headers={"Range": "bytes=10-"})
    assert r.status_code == 206 and r.content == body[10:]
    assert cache.transcodes == 1  # served from cache

    files = list((cfg.data_dir / "opus").glob(f"{tid}_96_*.opus"))
    assert len(files) == 1

    # ffprobe agrees it's a ~2 s Opus stream.
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name:format=duration",
                            "-of", "csv=p=0", str(files[0])], capture_output=True, text=True, check=True)
    assert "opus" in probe.stdout


def test_concurrent_requests_transcode_once(client, conn, app):
    tid = track_ids(conn)["Opening"]
    cache: OpusCache = app.state.ctx.opus
    results = []

    def fetch():
        results.append(client.get(f"/api/v1/stream/{tid}?bitrate=64").status_code)

    threads = [threading.Thread(target=fetch) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200] * 5
    assert cache.transcodes == 1


def test_stream_404(client):
    assert client.get("/api/v1/stream/doesnotexist").status_code == 404


def test_source_change_invalidates_cache(tmp_path, base_flac):
    cache = OpusCache(tmp_path / "opus", 10**9)
    a = cache.get(base_flac, "t1", 64, 1000.0)
    b = cache.get(base_flac, "t1", 64, 2000.0)
    assert a != b and not a.exists() and b.exists()
    assert cache.transcodes == 2


def test_lru_eviction(tmp_path):
    d = tmp_path / "opus"
    d.mkdir()
    cache = OpusCache(d, max_bytes=2500)
    now = time.time()
    for i in range(4):
        p = d / f"t{i}_64_1.opus"
        p.write_bytes(b"x" * 1000)
        os.utime(p, (now - 100 + i, now - 100 + i))  # t0 oldest
    removed = cache.evict()
    left = sorted(p.name for p in d.iterdir())
    assert removed == 2 and left == ["t2_64_1.opus", "t3_64_1.opus"]


def test_art_endpoint(client, conn):
    album = conn.execute("SELECT id FROM albums WHERE title = 'First Album'").fetchone()[0]
    r = client.get(f"/api/v1/art/{album}?size=100")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    loose = conn.execute("SELECT id FROM albums WHERE title = 'Loose'").fetchone()[0]
    assert client.get(f"/api/v1/art/{loose}").status_code == 404
    assert client.get("/api/v1/art/nope").status_code == 404



# ---------- quality selection ----------

def test_snap_bitrate():
    from nasmusic.stream import OPUS_BITRATES, snap_bitrate
    assert OPUS_BITRATES == (64, 96, 128, 160, 192, 256)
    for b in OPUS_BITRATES:
        assert snap_bitrate(b) == b
    assert snap_bitrate(1) == 64
    assert snap_bitrate(24) == 64
    assert snap_bitrate(100) == 96
    assert snap_bitrate(112) == 128  # tie goes up
    assert snap_bitrate(320) == 256
    assert snap_bitrate(1000) == 256


def test_labels():
    from nasmusic.stream import opus_label, original_label
    assert original_label("FLAC", 16, 44100, 900) == "FLAC 16/44.1"
    assert original_label("FLAC", 24, 96000, None) == "FLAC 24/96"
    assert original_label("FLAC", None, 48000, None) == "FLAC 48 kHz"
    assert original_label("MP3", None, 44100, 320) == "MP3 320"
    assert original_label("OPUS", None, 48000, 128) == "Opus 128"
    assert original_label(None, None, None, None) == "Original"
    assert opus_label(96) == "Opus 96"


def test_stream_format_header_and_original_alias(client, conn):
    tid = track_ids(conn)["Song 1"]
    a = client.get(f"/api/v1/stream/{tid}?format=flac")
    b = client.get(f"/api/v1/stream/{tid}?format=original")
    assert a.status_code == b.status_code == 200
    assert a.content == b.content
    assert a.headers["x-stream-format"].startswith("FLAC")
    assert b.headers["x-stream-format"] == a.headers["x-stream-format"]


def test_stream_rejects_bad_params(client, conn):
    tid = track_ids(conn)["Song 1"]
    assert client.get(f"/api/v1/stream/{tid}?format=mp3").status_code == 422
    assert client.get(f"/api/v1/stream/{tid}?format=opus&bitrate=0").status_code == 422
    assert client.get(f"/api/v1/stream/{tid}?format=opus&bitrate=5000").status_code == 422
    assert client.get(f"/api/v1/stream/{tid}?format=opus&bitrate=abc").status_code == 422


def test_cache_keyed_by_snapped_bitrate(client, conn, app, cfg):
    tid = track_ids(conn)["Song 2"]
    cache: OpusCache = app.state.ctx.opus
    r = client.get(f"/api/v1/stream/{tid}?format=opus&bitrate=128")
    assert r.status_code == 200 and r.headers["x-stream-format"] == "Opus 128"
    assert cache.transcodes == 1
    # 130 and 120 snap to 128: same cache entry, no new transcode.
    for odd in (130, 120):
        r = client.get(f"/api/v1/stream/{tid}?format=opus&bitrate={odd}")
        assert r.status_code == 200 and r.headers["x-stream-format"] == "Opus 128"
    assert cache.transcodes == 1
    # A different tier is its own entry; the first one stays.
    r = client.get(f"/api/v1/stream/{tid}?format=opus&bitrate=256")
    assert r.headers["x-stream-format"] == "Opus 256"
    assert cache.transcodes == 2
    names = sorted(p.name.split("_")[1] for p in (cfg.data_dir / "opus").glob(f"{tid}_*.opus"))
    assert names == ["128", "256"]


def test_default_stream_is_opus_160(client, conn, cfg):
    tid = track_ids(conn)["Opening"]
    r = client.get(f"/api/v1/stream/{tid}")
    assert r.status_code == 200 and r.headers["x-stream-format"] == "Opus 160"
    assert list((cfg.data_dir / "opus").glob(f"{tid}_160_*.opus"))


def test_transcode_keeps_replaygain_tags(tmp_path, base_flac):
    """ReplayGain/loudness come from the API's replayGain fields, but the transcode keeps the source tags too."""
    src = tmp_path / "rg.flac"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(base_flac), "-c", "copy",
                    "-metadata", "REPLAYGAIN_TRACK_GAIN=-6.50 dB", str(src)], check=True)
    out = OpusCache(tmp_path / "opus", 10**9).get(src, "rg", 96, src.stat().st_mtime)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream_tags:format_tags", "-of", "json",
                            str(out)], capture_output=True, text=True, check=True)
    assert "-6.50 dB" in probe.stdout
