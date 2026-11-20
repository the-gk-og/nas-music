"""DJ automix: the resumable analysis job, Track.mix in API responses, /tracks/{id}/mix and /automix/order."""
import json
import math
import shutil
import subprocess

import pytest

from nasmusic.ai import automix, djmix
from tests.ai.test_djmix import SR, synth

# ---------------------------------------------------------------- job + API

def _write_wav(path, audio, sr=SR):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
                    "-c:a", "flac", str(path)], input=audio.astype("<f4").tobytes(), check=True)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg")
def test_job_resumable_and_track_mix_exposed(cfg, scanned, client, monkeypatch):
    from nasmusic.ai import api as ai
    from nasmusic.core import ai_config
    from tests.conftest import track_ids

    conn = scanned
    ids = track_ids(conn)
    # Replace one library file's audio by a beat track (the library is a tmp copy in tests).
    row = conn.execute("SELECT path FROM tracks WHERE id = ?", (ids["Song 1"],)).fetchone()
    audio, _ = synth(bars=40, intro_bars=8, outro_bars=8)
    target = cfg.library_root / row["path"]
    _write_wav(target, audio)
    monkeypatch.setattr(djmix, "essentia_available", lambda: False)
    # in-process, one track per run: resumable
    r1 = djmix.run(conn, cfg.library_root, limit=1, in_process=True)
    assert r1["analysed"] + r1["failed"] == 1
    r2 = djmix.run(conn, cfg.library_root, in_process=True)
    assert r2["analysed"] + r2["failed"] == len(ids) - 1
    assert djmix.run(conn, cfg.library_root, in_process=True)["left"] == 0   # nothing to redo
    d = conn.execute("SELECT * FROM track_dj WHERE track_id = ?", (ids["Song 1"],)).fetchone()
    assert d["status"] == "done" and abs(d["bpm"] - 124) < 0.5 and d["grid_stable"] == 1
    # the API: compact mix in Track, detail endpoint
    mix = client.get(f"/api/v1/library/tracks/{ids['Song 1']}").json()["mix"]
    assert mix["gridStable"] is True and abs(mix["bpm"] - 124) < 0.5
    assert {"beatS", "downbeatS", "mixInS", "mixOutS", "lastBeatS", "camelot", "energy"} <= set(mix)
    det = client.get(f"/api/v1/tracks/{ids['Song 1']}/mix").json()
    assert det["status"] == "done" and det["mix"]["bpm"] == mix["bpm"] and len(det["energyCurve"]) > 10
    assert client.get("/api/v1/tracks/nope/mix").status_code == 404
    # api.py entry point never raises
    res = ai.automix_analyse_pending(conn, ai_config(cfg), time_budget_s=0)
    assert "analysed" in res


def test_order_endpoint(client, scanned):
    from tests.conftest import track_ids

    conn = scanned
    ids = list(track_ids(conn).values())
    rows = [("8A", 124.0, 0.5), ("3B", 90.0, 0.9), ("9A", 125.0, 0.55), ("8B", 123.0, 0.6), ("2A", 170.0, 0.2)]
    for tid, (cam, bpm, e) in zip(ids, rows):
        djmix.write_row(conn, tid, {"version": "dj-basic-1", "bpm": bpm, "camelot": cam, "energy": e,
                                    "grid_stable": True, "mix_out_s": 1.0, "energy_curve": []})
    r = client.post("/api/v1/automix/order", json={"trackIds": ids, "window": 4, "arc": False})
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["trackIds"]) == sorted(ids) and body["trackIds"][0] == ids[0]
    # the two tempo/key neighbours of the first track follow it
    assert set(body["trackIds"][1:3]) == {ids[2], ids[3]}
    assert len(body["transitions"]) == len(ids) - 1
    assert body["transitions"][0]["beatmatch"] is True
    assert client.post("/api/v1/automix/order", json={"trackIds": ids[:2]}).json()["trackIds"] == ids[:2]
    # compact mix only when done
    djmix.write_failure(conn, ids[4], "dj-basic-1", "boom")
    assert djmix.row_to_mix(conn.execute("SELECT * FROM track_dj WHERE track_id = ?", (ids[4],)).fetchone()) is None
    assert json.loads(conn.execute("SELECT energy_curve FROM track_dj WHERE track_id = ?", (ids[0],)).fetchone()[0]) == []
    assert math.isfinite(automix.pair_cost(automix.Item("a"), automix.Item("b")))
