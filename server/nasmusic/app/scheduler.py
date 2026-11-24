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

JOB_NAMES = ("scan", "full_scan", "analyse", "loudness", "nightly", "pretranscode", "lyrics", "discovery", "release_radar",
             "blends", "cleanup", "backup", "explicit", "autotag", "upload_scan", "smart", "artist_info",
             "podcasts", "audiobooks", "automix", "lossless")
# The loudness job runs after every scan; a first pass over a big library takes hours, so each run stops
# after this long and the next scan (or the nightly chain) carries on.
LOUDNESS_BUDGET_S = 50 * 60
HISTORY = 20
LYRICS_PER_RUN = 200
# Lyrics search index: each scan indexes new/changed tracks for at most this long (the first pass over a big
# library takes several scans; search works for what is indexed so far).
LYRICS_INDEX_BUDGET_S = 120
# The explicit-content job (file tags, Deezer, lyrics word list) is resumable: each run stops after this.
EXPLICIT_BUDGET_S = 20 * 60
# Fake-lossless checker (spectrum of FLAC/ALAC/WAV files): resumable; each run stops after this.
LOSSLESS_BUDGET_S = 30 * 60
# DJ automix analysis (beat grid, key, mix points): resumable, one low-priority worker; each run stops after this.
AUTOMIX_BUDGET_S = 45 * 60


class Jobs:
    def __init__(self, cfg: Config, opus: OpusCache | None = None):
        self.cfg = cfg
        # Shared with the API so on-demand and background transcodes use the same per-track locks.
        self.opus = opus or OpusCache(cfg.data_dir / "opus", int(cfg.opus_cache_gb * 1024**3))
        self.lrclib = None   # nasmusic.library.lrclib.Lrclib for the prefetch job (set by build_context)
        self.hub = None      # nasmusic.connect.Hub, for "new music" pushes (set by build_context)
        self.scrobbler = None  # nasmusic.app.scrobble.Scrobbler (set by build_context): retried every few minutes
        self._locks = {n: threading.Lock() for n in JOB_NAMES}
        self._locks["full_scan"] = self._locks["scan"]  # never scan twice at once
        # upload_scan has its own lock (the scanner serialises the actual walk); a request that arrives while
        # one is running makes it go round once more, so no finished upload waits for the 5-minute scan.
        self._upload_scan_again = threading.Event()
        self.scheduler = None
        # Follow-up jobs (scan -> analyse/loudness/explicit, nightly -> pretranscode, ...). Tests that check
        # a job's own result turn this off so a background analysis can't race their assertions.
        self.chain = True

    # ----- job bodies -----

    def _scan(self, conn, full: bool = False):
        from nasmusic.library import scanner

        from nasmusic.app import toget

        res = scanner.scan(self.cfg, conn, full=full).as_dict()
        res["togetGot"] = toget.match_owned(conn)  # wanted albums/songs that the library now has
        res["lyricsIndex"] = self._lyrics_index(conn, full=full)
        try:
            from nasmusic.app import notify

            res["newMusic"] = notify.new_music(conn, getattr(self, "hub", None))
        except Exception:
            log.exception("new-music notifications failed")
        return res

    def _lyrics_index(self, conn, full: bool = False) -> dict | None:
        from nasmusic.library import lyrics_index

        try:
            return lyrics_index.refresh(conn, self.cfg.library_root, full=full, time_budget_s=LYRICS_INDEX_BUDGET_S)
        except Exception:
            log.exception("lyrics index failed")
            return None

    def _upload_scan(self, conn):
        """Index just upload_root (seconds), right after an upload finishes or is deleted."""
        from nasmusic.library import scanner

        self._upload_scan_again.clear()
        if self.cfg.upload_root is None:
            return {"skipped": "upload_root is not configured"}
        res = scanner.scan(self.cfg, conn, uploads_only=True).as_dict()
        res["lyricsIndex"] = self._lyrics_index(conn)
        return res

    def request_upload_scan(self) -> None:
        self._upload_scan_again.set()
        self.trigger("upload_scan", "upload")

    def _analyse(self, conn):
        return {"analysed": ai_adapter.analyse_pending(conn, ai_config(self.cfg))}

    def _loudness(self, conn):
        from nasmusic.library import loudness

        return loudness.run(conn, self.cfg.library_root, time_budget_s=LOUDNESS_BUDGET_S)

    def _automix(self, conn):
        return ai_adapter.automix_analyse_pending(conn, ai_config(self.cfg), AUTOMIX_BUDGET_S)

    def _explicit(self, conn):
        from nasmusic.library import explicit

        deezer = None
        if self.cfg.explicit_deezer:
            deezer = explicit.Deezer(transport=getattr(self, "http_transport", None))
        return explicit.run(conn, self.cfg.library_root, deezer, time_budget_s=EXPLICIT_BUDGET_S)

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
                "unreachable": unreachable, "lyricsIndex": self._lyrics_index(conn) if found else None}

    def _cleanup(self, conn):
        """Daily housekeeping: expire bug reports (30 days), purge the library trash, tidy uploads."""
        from datetime import UTC, datetime, timedelta

        from nasmusic.core import iso

        from nasmusic.library import uploads

        cutoff = iso(datetime.now(UTC) - timedelta(days=30))
        out = {"bugReportsDeleted": conn.execute("DELETE FROM bug_reports WHERE created_at < ?", (cutoff,)).rowcount}
        from nasmusic.library import cleanup

        out["trash"] = cleanup.purge(conn, self.cfg)  # removals older than trash_days (admin console Cleanup)
        if self.cfg.upload_root is not None:
            out.update(uploads.cleanup(conn, self.cfg))
        return out

    def _lossless(self, conn):
        """Fake-lossless checker: flags lossless files made from MP3/AAC or upsampled (time-boxed, resumable)."""
        from nasmusic.library import lossless

        return lossless.run(conn, self.cfg.library_root, time_budget_s=LOSSLESS_BUDGET_S)

    def _backup(self, conn):
        from nasmusic.app import backups

        return backups.create(self.cfg, conn, "manual")

    def _autotag(self, conn):
        """Automatic tag fixing: fingerprints -> AcoustID -> MusicBrainz suggestions (time-boxed, resumable)."""
        from nasmusic.library import autotag

        res = autotag.run(conn, self.cfg)
        res.pop("status", None)
        return res

    def _smart(self, conn):
        """Bring every smart playlist's cached list up to date (reads also refresh stale ones)."""
        from nasmusic.app import smart

        return smart.refresh_all(conn)

    def _artist_info(self, conn):
        """Artist pages: refresh a few due artists' metadata (MusicBrainz 1 req/s; most-played first)."""
        from nasmusic.library import artistinfo

        if not self.cfg.artist_info_enabled:
            return {"skipped": "artist info is turned off"}
        http = artistinfo.Http(getattr(self, "http_transport", None))
        return artistinfo.refresh_due(conn, self.cfg.data_dir, http)
    def _podcasts(self, conn):
        """Poll subscribed podcast feeds and keep each show's newest episodes downloaded (nasmusic.spoken)."""
        from nasmusic.spoken import podcasts

        return podcasts.run(conn, self.cfg, getattr(self, "http_transport", None))

    def _audiobooks(self, conn):
        from nasmusic.spoken import audiobooks

        return audiobooks.scan(conn, self.cfg.audiobooks_root, self.cfg.data_dir)

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
            "loudness": self._loudness,
            "nightly": self._nightly,
            "pretranscode": self._pretranscode,
            "discovery": self._discovery,
            "release_radar": self._release_radar,
            "blends": self._blends,
            "lyrics": self._lyrics,
            "cleanup": self._cleanup,
            "backup": self._backup,
            "explicit": self._explicit,
            "autotag": self._autotag,
            "upload_scan": self._upload_scan,
            "smart": self._smart,
            "artist_info": self._artist_info,
            "podcasts": self._podcasts,
            "audiobooks": self._audiobooks,
            "automix": self._automix,
            "lossless": self._lossless,
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
        if not self.chain:
            return rec
        if name in ("scan", "full_scan") and rec["ok"]:
            if self.cfg.audiobooks_root is not None:
                self.trigger("audiobooks", "chained")
            self.trigger("analyse", "chained")
            self.trigger("loudness", "chained")
            self.trigger("explicit", "chained")
        if name == "upload_scan":
            if self._upload_scan_again.is_set():
                self.trigger("upload_scan", "chained")
            elif rec["ok"] and (rec.get("result") or {}).get("added"):
                # New uploads go through the same jobs as any other track (AcoustID only does new ones).
                for job in ("analyse", "loudness", "explicit", "autotag"):
                    self.trigger(job, "chained")
        if name == "analyse" and rec["ok"]:
            self.trigger("automix", "chained")  # after the embeddings, so the two analysers don't compete
        if name == "nightly" and rec["ok"]:
            self.trigger("pretranscode", "chained")
            self.trigger("lyrics", "chained")
            self.trigger("blends", "chained")
            self.trigger("loudness", "chained")
            self.trigger("explicit", "chained")
            self.trigger("automix", "chained")
            self.trigger("lossless", "chained")
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
        sched.add_job(self.run, "interval", args=["podcasts", "schedule"], id="podcasts",
                      minutes=self.cfg.podcast_poll_minutes)
        sched.add_job(self.run, "cron", args=["discovery", "schedule"], id="discovery", day_of_week="sun", hour=3, minute=0)
        # New music comes out on Fridays: refresh Release Radar early Friday morning (after the 02:00 nightly).
        sched.add_job(self.run, "cron", args=["release_radar", "schedule"], id="release_radar", day_of_week="fri",
                      hour=4, minute=0)
        sched.add_job(self.run, "cron", args=["cleanup", "schedule"], id="cleanup", hour=4, minute=30)
        # Tag-fixing suggestions: fingerprint/look up what is new, after the night's heavier jobs.
        sched.add_job(self.run, "cron", args=["autotag", "schedule"], id="autotag", hour=5, minute=0)
        # Smart playlists move with the date ("not played in a month"): refresh after the nightly jobs.
        sched.add_job(self.run, "cron", args=["smart", "schedule"], id="smart", hour=5, minute=30)
        sched.add_job(self.run, "cron", args=["artist_info", "schedule"], id="artist_info", hour=3, minute=30)
        if self.scrobbler is not None:   # scrobble outbox retries (not a recorded job: it runs every 3 minutes)
            sched.add_job(self.scrobbler.flush_now, "interval", id="scrobble", minutes=3)
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
                           ("cleanup", {"hour": 4, "minute": 30}),
                           ("autotag", {"hour": 5, "minute": 0})):
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
