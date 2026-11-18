"""Background jobs: library scan (+ analysis), nightly mixes, weekly discovery.

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

from nasmusic.ai import api as ai
from nasmusic.config import Config
from nasmusic.core import ai_config, get_setting, now_iso, open_db, set_setting

log = logging.getLogger(__name__)

JOB_NAMES = ("scan", "full_scan", "analyse", "nightly", "discovery")


class Jobs:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._locks = {n: threading.Lock() for n in JOB_NAMES}
        self._locks["full_scan"] = self._locks["scan"]  # never scan twice at once
        self.scheduler = None

    # ----- job bodies -----

    def _scan(self, conn, full: bool = False):
        from nasmusic.library import scanner

        res = scanner.scan(self.cfg, conn, full=full)
        return res.as_dict()

    def _analyse(self, conn):
        return {"analysed": ai.analyse_pending(conn, ai_config(self.cfg))}

    def _nightly(self, conn):
        return {"mixIds": ai.run_nightly(conn, ai_config(self.cfg))}

    def _discovery(self, conn):
        return {"newSuggestions": ai.run_weekly_discovery(conn, ai_config(self.cfg))}

    def _body(self, name: str) -> Callable:
        return {
            "scan": self._scan,
            "full_scan": lambda conn: self._scan(conn, full=True),
            "analyse": self._analyse,
            "nightly": self._nightly,
            "discovery": self._discovery,
        }[name]

    # ----- running -----

    def is_running(self, name: str) -> bool:
        return self._locks[name].locked()

    def run(self, name: str) -> dict | None:
        """Run a job now in this thread. Returns its record, or None if it was already running."""
        lock = self._locks[name]
        if not lock.acquire(blocking=False):
            log.info("job %s already running; skipped", name)
            return None
        conn = open_db(self.cfg)
        rec: dict = {"startedAt": now_iso()}
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
            finally:
                conn.close()
                lock.release()
        if name in ("scan", "full_scan") and rec["ok"]:
            self.trigger("analyse")
        return rec

    def trigger(self, name: str) -> bool:
        """Start a job in a background thread unless it is already running."""
        if self.is_running(name):
            return False
        threading.Thread(target=self.run, args=(name,), name=f"job-{name}", daemon=True).start()
        return True

    def last(self, conn, name: str) -> dict | None:
        return get_setting(conn, f"job.{name}")

    # ----- schedule -----

    def start(self, initial_scan: bool = True):
        from apscheduler.schedulers.background import BackgroundScheduler

        tz = ZoneInfo(self.cfg.timezone)
        sched = BackgroundScheduler(timezone=tz, job_defaults={"coalesce": True, "max_instances": 1,
                                                               "misfire_grace_time": 3600})
        sched.add_job(self.run, "interval", args=["scan"], id="scan",
                      minutes=self.cfg.scan_interval_minutes,
                      next_run_time=datetime.now(tz) if initial_scan else None)
        sched.add_job(self.run, "cron", args=["nightly"], id="nightly", hour=2, minute=0)
        sched.add_job(self.run, "cron", args=["discovery"], id="discovery", day_of_week="sun", hour=3, minute=0)
        sched.start()
        self.scheduler = sched
        return sched

    def next_runs(self) -> dict[str, str | None]:
        if self.scheduler is None:
            return {}
        out = {}
        for job in self.scheduler.get_jobs():
            out[job.id] = job.next_run_time.isoformat(timespec="minutes") if job.next_run_time else None
        return out

    def shutdown(self):
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)
