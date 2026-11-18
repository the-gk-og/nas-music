"""Library analyser: turns tracks into `features` rows, tracked in `analysis_jobs`.

Resumable and interrupt-safe:
* Work is chosen from the DB each run (no `features` row for the version, or the file changed since).
* A job row goes to 'pending' when queued; the `features` row and 'done' are written together in one
  transaction by the parent process (the single DB writer). A crash or shutdown just leaves
  'pending' rows, which the next run picks up again.
* Failures are recorded as 'failed' with the error and retried after `ai.analysis.retry_failed_days`
  (or immediately if the file's mtime changes).

Extraction runs in a small spawn-based process pool (default 2 workers): spawn, not fork, because the
server process has threads (uvicorn, APScheduler) and TensorFlow does not survive fork.
"""
from __future__ import annotations

import json
import logging
import multiprocessing as mp
import random
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .common import BASIC_VERSION, iso, load_settings, now_utc, to_blob, transaction
from .extractors import Features, effective_version, make_extractor

log = logging.getLogger("nasmusic.ai.analyser")

# ---------------------------------------------------------------- worker side

_EXTRACTOR = None


def _init_worker(version: str, data_dir: str, max_seconds: float, excerpts: int = 3,
                 excerpt_seconds: float = 40.0) -> None:
    global _EXTRACTOR
    import os
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    try:
        os.nice(10)  # analysis is background work; keep streaming responsive
    except OSError:
        pass
    _EXTRACTOR = make_extractor(version, Path(data_dir), max_seconds=max_seconds, excerpts=excerpts,
                                excerpt_seconds=excerpt_seconds)


def _extract(path: str, duration_s: float | None) -> dict[str, Any]:
    assert _EXTRACTOR is not None
    f = _EXTRACTOR.extract_file(Path(path), duration_s)
    d = asdict(f)
    d["embedding"] = f.embedding.astype("<f4").tobytes()
    return d


# ---------------------------------------------------------------- parent side

def _excerpt_args(settings) -> tuple[int, float]:
    return settings.int("ai.analysis.excerpts"), settings.num("ai.analysis.excerpt_seconds")


def pending_tracks(conn: sqlite3.Connection, version: str, retry_failed_days: float,
                   limit: int | None = None) -> list[sqlite3.Row]:
    sql = """
        SELECT t.id, t.path, t.duration_s
        FROM tracks t
        LEFT JOIN features f ON f.track_id = t.id AND f.model_version = :v
        LEFT JOIN analysis_jobs j ON j.track_id = t.id AND j.model_version = :v
        WHERE t.missing = 0
          AND (f.track_id IS NULL
               OR f.analysed_at < strftime('%Y-%m-%dT%H:%M:%SZ', t.mtime, 'unixepoch'))
          AND (j.status IS NULL OR j.status != 'failed'
               OR j.updated_at < strftime('%Y-%m-%dT%H:%M:%SZ', 'now', :retry)
               OR j.updated_at < strftime('%Y-%m-%dT%H:%M:%SZ', t.mtime, 'unixepoch'))
        ORDER BY t.added_at DESC, t.id
    """
    params: dict[str, Any] = {"v": version, "retry": f"-{float(retry_failed_days)} days"}
    if limit is not None:
        sql += " LIMIT :lim"
        params["lim"] = int(limit)
    return conn.execute(sql, params).fetchall()


def _set_job(conn: sqlite3.Connection, track_id: str, version: str, status: str, error: str | None = None):
    conn.execute(
        "INSERT INTO analysis_jobs (track_id, model_version, status, error, updated_at) VALUES (?,?,?,?,?) "
        "ON CONFLICT(track_id, model_version) DO UPDATE SET status=excluded.status, error=excluded.error, "
        "updated_at=excluded.updated_at",
        (track_id, version, status, error, iso()),
    )


def write_features(conn: sqlite3.Connection, track_id: str, version: str, f: Features | dict) -> None:
    d = f if isinstance(f, dict) else {**asdict(f), "embedding": f.embedding.astype("<f4").tobytes()}
    emb = d["embedding"] if isinstance(d["embedding"], bytes) else to_blob(d["embedding"])
    with transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO features (track_id, model_version, embedding, dims, bpm, key, energy, "
            "danceability, tags, analysed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (track_id, version, emb, len(emb) // 4, d.get("bpm"), d.get("key"), d.get("energy"),
             d.get("danceability"), json.dumps(d.get("tags") or {}), iso()),
        )
        _set_job(conn, track_id, version, "done")


def analyse_pending(conn: sqlite3.Connection, cfg, limit: int | None = None, workers: int | None = None) -> int:
    settings = load_settings(conn)
    version = effective_version(cfg.model_version)
    todo = pending_tracks(conn, version, settings.num("ai.analysis.retry_failed_days"), limit)
    if not todo:
        return 0
    workers = max(1, int(workers or settings.int("ai.analysis.workers")))
    max_seconds = settings.num("ai.analysis.max_seconds")
    log.info("analysing %d tracks with %s (%d workers)", len(todo), version, workers)

    with transaction(conn):
        for row in todo:
            _set_job(conn, row["id"], version, "pending")

    done = 0
    root = Path(cfg.library_root)
    ctx = mp.get_context("spawn")
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_init_worker,
                               initargs=(version, str(cfg.data_dir), max_seconds, *_excerpt_args(settings)))
    in_flight: dict[Future, sqlite3.Row] = {}
    queue = list(todo)
    started = time.monotonic()
    try:
        while queue or in_flight:
            while queue and len(in_flight) < workers * 2:
                row = queue.pop(0)
                in_flight[pool.submit(_extract, str(root / row["path"]), row["duration_s"])] = row
            finished, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for fut in finished:
                row = in_flight.pop(fut)
                try:
                    write_features(conn, row["id"], version, fut.result())
                    done += 1
                except Exception as e:
                    msg = f"{type(e).__name__}: {e}"[:500]
                    log.warning("analysis failed for %s: %s", row["path"], msg)
                    with transaction(conn):
                        _set_job(conn, row["id"], version, "failed", msg)
                    if "BrokenProcessPool" in msg:
                        raise
            if done and done % 100 == 0:
                rate = done / (time.monotonic() - started)
                log.info("analysed %d/%d (%.2f tracks/s)", done, len(todo), rate)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    log.info("analysis finished: %d analysed, %d failed/left", done, len(todo) - done)
    return done


def benchmark(conn: sqlite3.Connection, cfg, n: int = 20, workers: int | None = None) -> dict[str, Any]:
    """Time `n` random tracks with the real pool settings (nothing is written) and extrapolate.

    Returns a dict with per-track seconds, throughput and the estimated time for the whole library
    and for what is still pending.
    """
    settings = load_settings(conn)
    version = effective_version(cfg.model_version)
    workers = max(1, int(workers or settings.int("ai.analysis.workers")))
    max_seconds = settings.num("ai.analysis.max_seconds")
    if version != BASIC_VERSION:
        from . import models
        models.ensure_models(Path(cfg.data_dir), version)  # once here, not in every worker
    rows = conn.execute("SELECT id, path, duration_s FROM tracks WHERE missing = 0").fetchall()
    total = len(rows)
    sample = random.sample(rows, min(n, total))
    pending = len(pending_tracks(conn, version, settings.num("ai.analysis.retry_failed_days")))
    result: dict[str, Any] = {"model_version": version, "workers": workers, "sampled": len(sample),
                              "library_tracks": total, "pending_tracks": pending, "max_seconds": max_seconds}
    if not sample:
        return result | {"error": "no tracks in library"}
    root = Path(cfg.library_root)
    ctx = mp.get_context("spawn")
    t0 = time.monotonic()
    ok = failed = 0
    audio_s = 0.0
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_init_worker,
                             initargs=(version, str(cfg.data_dir), max_seconds, *_excerpt_args(settings))) as pool:
        # warm-up: model load happens in the initializer; time it separately
        list(pool.map(_noop, range(workers)))
        t1 = time.monotonic()
        futs = {pool.submit(_extract, str(root / r["path"]), r["duration_s"]): r for r in sample}
        for fut, r in futs.items():
            try:
                fut.result()
                ok += 1
                audio_s += min(r["duration_s"] or 0, max_seconds)
            except Exception as e:  # noqa: BLE001
                failed += 1
                log.warning("benchmark: %s failed: %s", r["path"], e)
        t2 = time.monotonic()
    wall = t2 - t1
    per_track_wall = wall / max(ok + failed, 1)
    result |= {
        "startup_s": round(t1 - t0, 2),
        "wall_s": round(wall, 2),
        "ok": ok, "failed": failed,
        "seconds_per_track": round(per_track_wall, 3),          # effective, with `workers` in parallel
        "realtime_factor": round(audio_s / wall, 1) if wall else None,
        "est_full_library_s": round(per_track_wall * total, 2),
        "est_full_library_h": round(per_track_wall * total / 3600, 2),
        "est_pending_h": round(per_track_wall * pending / 3600, 2),
        "measured_at": iso(now_utc()),
    }
    return result


def _noop(_: int) -> None:
    return None
