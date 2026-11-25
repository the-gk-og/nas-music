"""run_nightly and analyse_pending run at the same time on one DB file (scan-triggered analysis vs
the 02:00 job). Both must finish cleanly, and neither may hold the write lock for long."""
from __future__ import annotations

import logging
import threading
import time

import pytest

from homebeat import db
from homebeat.ai import api
from homebeat.ai.common import ensure_default_settings, read_status
from homebeat.ai.recommender import Recommender
from tests.ai.conftest import build_library, ffmpeg_ok, write_tone
from tests.ai.schema_v2 import add_user, init_v2
from tests.ai.test_analyser import add_track

pytestmark = pytest.mark.skipif(not ffmpeg_ok(), reason="ffmpeg not installed")


def test_nightly_and_analysis_concurrently(tmp_path, cfg, caplog):
    path = tmp_path / "shared.db"
    setup = db.connect(path)
    init_v2(setup)
    add_user(setup, "alice")
    add_user(setup, "bob")
    ensure_default_settings(setup)
    setup.execute("UPDATE settings SET value = '1' WHERE key = 'ai.analysis.workers'")
    # analysed synthetic library, same dims as basic-1 so new tracks join it
    build_library(setup, cfg, n_clusters=4, artists_per_cluster=6, tracks_per_artist=6, dims=107)
    n_new = 10
    for k in range(n_new):
        write_tone(cfg.library_root / f"new{k}.flac", 150 + 70 * k, seconds=30)
        add_track(setup, f"new{k}.flac", duration=30)
    setup.close()

    caplog.set_level(logging.WARNING, logger="homebeat")
    done = threading.Event()
    results: dict[str, list] = {"analysed": [], "nightly": [], "lock_waits": []}
    errors: list[BaseException] = []

    def analyse():
        try:
            conn = db.connect(path)
            results["analysed"].append(api.analyse_pending(conn, cfg))
            conn.close()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
        finally:
            done.set()

    def nightly():
        try:
            conn = db.connect(path)
            while not done.is_set() or len(results["nightly"]) < 2:
                results["nightly"].append(api.run_nightly(conn, cfg))
            conn.close()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    def probe():
        """Another writer (server core recording events) must never wait long for the lock."""
        conn = db.connect(path)
        while not done.is_set():
            t = time.monotonic()
            conn.execute("BEGIN IMMEDIATE")
            results["lock_waits"].append(time.monotonic() - t)
            conn.execute("COMMIT")
            time.sleep(0.01)
        conn.close()

    threads = [threading.Thread(target=f) for f in (analyse, nightly, probe)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)
    assert not any(t.is_alive() for t in threads)
    assert not errors
    # the api swallows exceptions and logs them, so look for errors in the log too
    bad = [r for r in caplog.records if r.levelno >= logging.ERROR or "locked" in r.getMessage()]
    assert not bad, [r.getMessage() for r in bad]

    assert results["analysed"] == [n_new]
    assert len(results["nightly"]) >= 2
    assert all(set(r) == {"alice", "bob"} and all(len(ids) == 4 for ids in r.values()) for r in results["nightly"])
    assert max(results["lock_waits"]) < 2.0, max(results["lock_waits"])

    conn = db.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM analysis_jobs WHERE status != 'done'").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM mixes WHERE active = 1 AND kind = 'daily'").fetchone()[0] == 2
    assert len(Recommender(conn, cfg, "alice").lib) == 4 * 6 * 6 + n_new
    assert read_status(conn)["analysing"] == "basic-1"
