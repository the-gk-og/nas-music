import os
import subprocess
import threading
import time

from conftest import track_ids

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

