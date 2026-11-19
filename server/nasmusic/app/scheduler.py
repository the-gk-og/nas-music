"""Background jobs: library scan (+ analysis), nightly mixes (+ blends), weekly discovery and Release Radar.

Each job runs at most once at a time; its last outcome is stored in `settings` under `job.<name>` as
{"startedAt", "finishedAt", "ok", "durationS", "result", "error"}.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from nasmusic.app import ai_adapter
from nasmusic.config import Config
from nasmusic.core import ai_config, get_setting, now_iso, open_db, set_setting
from nasmusic.stream.transcode import OpusCache

log = logging.getLogger(__name__)

JOB_NAMES = ("scan", "full_scan", "analyse", "nightly", "pretranscode", "lyrics", "discovery", "release_radar", "blends",
             "cleanup", "backup")
HISTORY = 20
LYRICS_PER_RUN = 200


class Jobs:
    def __init__(self, cfg: Config, opus: OpusCache | None = None):
        self.cfg = cfg
        # Shared with the API so on-demand and background transcodes use the same per-track locks.
        self.opus = opus or OpusCache(cfg.data_dir / "opus", int(cfg.opus_cache_gb * 1024**3))
        self.lrclib = None   # nasmusic.library.lrclib.Lrclib for the prefetch job (set by build_context)
        self._locks = {n: threading.Lock() for n in JOB_NAMES}
        self._locks["full_scan"] = self._locks["scan"]  # never scan twice at once
        self.scheduler = None

    # ----- job bodies -----

    def _scan(self, conn, full: bool = False):
        from nasmusic.library import scanner

        from nasmusic.app import toget

        res = scanner.scan(self.cfg, conn, full=full).as_dict()
        res["togetGot"] = toget.match_owned(conn)  # wanted albums/songs that the library now has
        return res

    def _analyse(self, conn):
        return {"analysed": ai_adapter.analyse_pending(conn, ai_config(self.cfg))}

    def _nightly(self, conn):
        return {"mixIds": ai_adapter.run_nightly(conn, ai_config(self.cfg))}

    def _pretranscode(self, conn):
        """Transcode every manifest track to Opus at the sync bitrate, one ffmpeg at a time at nice 19,
        so the manifest reports exact sizes by the time the phone syncs."""
        from nasmusic.stream.transcode import TranscodeError
        from nasmusic.sync import SYNC_KBPS, sync_tracks

        done = cached = failed = 0
        for r in sync_tracks(conn):
            if self.opus.path_for(r["id"], SYNC_KBPS, r["mtime"]).exists():
                cached += 1
                continue
            try:
                self.opus.get(self.cfg.library_root / r["path"], r["id"], SYNC_KBPS, r["mtime"], low_priority=True)
                done += 1
            except TranscodeError as e:
                failed += 1
                log.warning("pre-transcode failed for %s: %s", r["id"], e)
        return {"transcoded": done, "alreadyCached": cached, "failed": failed}

    def _lyrics(self, conn):
        """Pre-fetch LRCLIB lyrics for favourites and active mixes whose files have none (rate-limited)."""
        from nasmusic.library import lrclib, lyrics

        if not self.cfg.lrclib_enabled or self.lrclib is None:
            return {"skipped": "LRCLIB is turned off"}
        rows = conn.execute(
            "SELECT t.id, t.path FROM tracks t WHERE t.missing = 0 AND (t.id IN ("
            "  SELECT ut.track_id FROM user_tracks ut JOIN users u ON u.id = ut.user_id "
            "  WHERE ut.starred_at IS NOT NULL AND u.disabled = 0) OR t.id IN ("
            "  SELECT mt.track_id FROM mix_tracks mt JOIN mixes m ON m.id = mt.mix_id WHERE m.active = 1)) "
            "AND t.id NOT IN (SELECT track_id FROM lyrics_cache WHERE status = 'found' OR retry_after > ?) "
            "ORDER BY t.id", (now_iso(),)).fetchall()
        fetched = in_file = found = unreachable = 0
        for r in rows:
            if fetched >= LYRICS_PER_RUN:
                break
            if lyrics.read_all(self.cfg.library_root / r["path"]):
                in_file += 1
                continue
            row = lrclib.fetch(conn, self.lrclib, r["id"])
            if row is None:
                unreachable += 1
                if unreachable >= 3:  # LRCLIB is down: try again next time
                    break
                continue
            fetched += 1
            found += row["status"] == "found"
        return {"candidates": len(rows), "fetched": fetched, "found": found, "inFile": in_file,
                "unreachable": unreachable}

    def _cleanup(self, conn):
        """Daily housekeeping: expire bug reports (30 days)."""
        from datetime import UTC, datetime, timedelta

        from nasmusic.core import iso

        cutoff = iso(datetime.now(UTC) - timedelta(days=30))
        return {"bugReportsDeleted": conn.execute("DELETE FROM bug_reports WHERE created_at < ?",
                                                  (cutoff,)).rowcount}

    def _backup(self, conn):
        from nasmusic.app import backups

        return backups.create(self.cfg, conn, "manual")

    def _discovery(self, conn):
        return {"newSuggestions": ai_adapter.run_weekly_discovery(conn, ai_config(self.cfg))}

    def _release_radar(self, conn):
        return {"releases": ai_adapter.run_weekly_release_radar(conn, ai_config(self.cfg))}

    def _blends(self, conn):
        return {"blends": ai_adapter.run_blends(conn, ai_config(self.cfg))}

    def _body(self, name: str) -> Callable:
        return {
            "scan": self._scan,
            "full_scan": lambda conn: self._scan(conn, full=True),
            "analyse": self._analyse,
            "nightly": self._nightly,
            "pretranscode": self._pretranscode,
            "discovery": self._discovery,
            "release_radar": self._release_radar,
            "blends": self._blends,
            "lyrics": self._lyrics,
            "cleanup": self._cleanup,
            "backup": self._backup,
        }[name]

    # ----- running -----

    def is_running(self, name: str) -> bool:
        return self._locks[name].locked()

    def run(self, name: str, trigger: str = "manual") -> dict | None:
        """Run a job now in this thread. Returns its record, or None if it was already running.
        `trigger` (schedule / manual / chained) is kept in the job history."""
        lock = self._locks[name]
        if not lock.acquire(blocking=False):
            log.info("job %s already running; skipped", name)
            return None
        conn = open_db(self.cfg)
        rec: dict = {"startedAt": now_iso(), "trigger": trigger}
        t0 = time.monotonic()
        try:
            rec["result"] = self._body(name)(conn)
            rec["ok"] = True
            rec["error"] = None
        except NotImplementedError as e:
            rec.update(ok=False, error=f"not implemented: {e}", result=None)
        except Exception as e:
            log.error("job %s failed:\n%s", name, traceback.format_exc())
            rec.update(ok=False, error=f"{type(e).__name__}: {e}", result=None)
        finally:
            rec["finishedAt"] = now_iso()
            rec["durationS"] = round(time.monotonic() - t0, 2)
            try:
                set_setting(conn, f"job.{name}", rec)
                hist = get_setting(conn, f"jobhist.{name}", []) or []
                set_setting(conn, f"jobhist.{name}", [rec, *hist][:HISTORY])
            finally:
                conn.close()
                lock.release()
        if name in ("scan", "full_scan") and rec["ok"]:
            self.trigger("analyse", "chained")
        if name == "nightly" and rec["ok"]:
            self.trigger("pretranscode", "chained")
            self.trigger("lyrics", "chained")
            self.trigger("blends", "chained")
        return rec

    def trigger(self, name: str, how: str = "manual") -> bool:
        """Start a job in a background thread unless it is already running."""
        if self.is_running(name):
            return False
        threading.Thread(target=self.run, args=(name, how), name=f"job-{name}", daemon=True).start()
        return True

    def last(self, conn, name: str) -> dict | None:
        return get_setting(conn, f"job.{name}")

    # ----- schedule -----

    def start(self, initial_scan: bool = True):
        from apscheduler.schedulers.background import BackgroundScheduler

        tz = ZoneInfo(self.cfg.timezone)
        sched = BackgroundScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1,
                                                               "misfire_grace_time": 3600})
        sched.add_job(self.run, "interval", args=["scan", "schedule"], id="scan",
                      minutes=self.cfg.scan_interval_minutes,
                      next_run_time=datetime.now(tz) if initial_scan else None)
        sched.add_job(self.run, "cron", args=["nightly", "schedule"], id="nightly", hour=2, minute=0)
        sched.add_job(self.run, "cron", args=["discovery", "schedule"], id="discovery", day_of_week="sun", hour=3, minute=0)
        # New music comes out on Fridays: refresh Release Radar early Friday morning (after the 02:00 nightly).
        sched.add_job(self.run, "cron", args=["release_radar", "schedule"], id="release_radar", day_of_week="fri",
                      hour=4, minute=0)
        sched.add_job(self.run, "cron", args=["cleanup", "schedule"], id="cleanup", hour=4, minute=30)
        sched.start()
        self.scheduler = sched
        return sched

    def reschedule(self) -> None:
        """Apply a changed timezone (admin settings) to the cron jobs."""
        if self.scheduler is None:
            return
        tz = ZoneInfo(self.cfg.timezone)
        for job_id, kw in (("nightly", {"hour": 2, "minute": 0}),
                           ("discovery", {"day_of_week": "sun", "hour": 3, "minute": 0}),
                           ("release_radar", {"day_of_week": "fri", "hour": 4, "minute": 0}),
                           ("cleanup", {"hour": 4, "minute": 30})):
            self.scheduler.reschedule_job(job_id, trigger="cron", timezone=tz, **kw)

    def next_runs(self, iso_format: bool = False) -> dict[str, str | None]:
        """Next scheduled run per job id: "YYYY-MM-DD HH:MM" in the server timezone, or ISO-8601 UTC."""
        if self.scheduler is None:
            return {}
        out = {}
        for job in self.scheduler.get_jobs():
            t = job.next_run_time
            if t is None:
                out[job.id] = None
            elif iso_format:
                from nasmusic.core import iso

                out[job.id] = iso(t)
            else:
                out[job.id] = t.strftime("%Y-%m-%d %H:%M")
        return out

    def shutdown(self):
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
