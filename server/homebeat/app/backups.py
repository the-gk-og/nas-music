"""Database backups.

Local: manual, scheduled, pre-prune and pre-restore copies in data_dir/backups/, plus the automatic pre-migration
copies next to the database (homebeat.db.vN-backup-<time>).

Off-site (config `backup_copy_to`): the nightly `backup` job gzips its fresh copy to a second folder (another
dataset / disk mounted in the container) or an rclone remote ("name:path"), keeps the newest `backup_keep` there,
and (optionally) mirrors config.toml and the TLS certificate into `config/` beside them.

Restore: any listed local or off-site backup is checked (integrity, schema version) and copied over the live
database with SQLite's online backup API, after a "prerestore" safety copy; older schemas are then migrated up.
"""
from __future__ import annotations

import gzip
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path

from homebeat import db
from homebeat.config import Config
from homebeat.core import get_setting, now_iso, set_setting

log = logging.getLogger(__name__)

# "nasmusic" names: backups made before the Homebeat rename stay listed and restorable.
MIGRATION = re.compile(r"^(?:homebeat|nasmusic)\.db\.v\d+-backup-\d{8}T\d{6}Z$")
OWN = re.compile(r"^(?:homebeat|nasmusic)-(manual|scheduled|prune|prerestore)-\d{8}T\d{6}Z(-\d+)?\.db$")
OFFSITE = re.compile(r"^(?:homebeat|nasmusic)-(manual|scheduled|prune|prerestore)-(\d{8}T\d{6}Z)(-\d+)?\.db\.gz$")
# Scheduled copies kept in data_dir/backups (off-site keeps cfg.backup_keep). Manual ones are never auto-deleted.
KEEP_LOCAL_SCHEDULED = 3
STATUS_KEY = "backup.offsite"
RCLONE_TIMEOUT_S = 30 * 60
# One restore / off-site copy at a time.
_lock = threading.Lock()


class BackupError(Exception):
    def __init__(self, detail: str, status: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status = status


def backup_dir(cfg: Config) -> Path:
    return cfg.data_dir / "backups"


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _info(p: Path, kind: str) -> dict:
    st = p.stat()
    return {"name": p.name, "kind": kind, "bytes": st.st_size, "createdAt": _iso(st.st_mtime)}


def list_all(cfg: Config) -> list[dict]:
    out = []
    for p in cfg.data_dir.glob("*.db.v*-backup-*"):
        if MIGRATION.match(p.name) and p.is_file():
            out.append(_info(p, "migration"))
    d = backup_dir(cfg)
    if d.is_dir():
        for p in d.iterdir():
            m = OWN.match(p.name)
            if m and p.is_file():
                out.append(_info(p, m.group(1)))
    return sorted(out, key=lambda b: b["createdAt"], reverse=True)


def path_for(cfg: Config, name: str) -> Path | None:
    """Only names this module creates or lists; never anything else in the data volume."""
    if MIGRATION.match(name):
        p = cfg.data_dir / name
    elif OWN.match(name):
        p = backup_dir(cfg) / name
    else:
        return None
    return p if p.is_file() else None


def create(cfg: Config, conn: sqlite3.Connection, kind: str = "manual") -> dict:
    d = backup_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = d / f"homebeat-{kind}-{stamp}.db"
    n = 1
    while dest.exists():
        n += 1
        dest = d / f"homebeat-{kind}-{stamp}-{n}.db"
    out = sqlite3.connect(dest)
    try:
        conn.backup(out)  # consistent online copy, even while the server writes
    finally:
        out.close()
    return _info(dest, kind)


def total(cfg: Config) -> dict:
    items = list_all(cfg)
    return {"count": len(items), "bytes": sum(b["bytes"] for b in items)}


def prune_local_scheduled(cfg: Config, newest: str, keep: int = KEEP_LOCAL_SCHEDULED) -> int:
    """Delete scheduled copies beyond the newest `keep` (`newest`, just made, always stays)."""
    d = backup_dir(cfg)
    files = [p for p in d.iterdir() if p.is_file() and (m := OWN.match(p.name)) and m.group(1) == "scheduled"
             and p.name != newest]
    files.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
    old = files[max(keep - 1, 0):]
    for p in old:
        p.unlink(missing_ok=True)
    return len(old)


# ---------- off-site destination ----------


def is_rclone(dest: str) -> bool:
    return not dest.startswith("/") and ":" in dest


def _rclone(args: list[str], timeout: float = RCLONE_TIMEOUT_S) -> str:
    exe = shutil.which("rclone")
    if exe is None:
        raise BackupError("rclone is not installed (the Docker image includes it; elsewhere install rclone)", 500)
    try:
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise BackupError(f"rclone {args[0]} timed out", 502) from e
    if r.returncode != 0:
        err = (r.stderr or r.stdout).strip().splitlines()
        raise BackupError(f"rclone {args[0]} failed: {err[-1] if err else f'exit {r.returncode}'}", 502)
    return r.stdout


def _remote(dest: str, *parts: str) -> str:
    base = dest.rstrip("/")
    sep = "" if base.endswith(":") else "/"
    return base + sep + "/".join(parts) if parts else base


def _include_config(cfg: Config) -> bool:
    if cfg.backup_include_config is not None:
        return cfg.backup_include_config
    return not is_rclone(cfg.backup_copy_to or "")  # default: yes for a folder, no for a cloud remote


def offsite_list(cfg: Config) -> list[dict]:
    """Backups in the off-site destination, newest first. Names come from the destination: only ones matching
    our pattern are listed (and so can ever be restored or deleted)."""
    dest = cfg.backup_copy_to
    if not dest:
        return []
    out = []
    if is_rclone(dest):
        import json

        try:
            items = json.loads(_rclone(["lsjson", "--files-only", _remote(dest)], timeout=120) or "[]")
        except BackupError as e:
            if "directory not found" in e.detail:
                return []
            raise
        for it in items:
            m = OFFSITE.match(it.get("Name", ""))
            if m:
                out.append({"name": it["Name"], "kind": m.group(1), "bytes": int(it.get("Size") or 0),
                            "createdAt": _stamp_iso(m.group(2))})
    else:
        d = Path(dest)
        if d.is_dir():
            for p in d.iterdir():
                m = OFFSITE.match(p.name)
                if m and p.is_file():
                    out.append({"name": p.name, "kind": m.group(1), "bytes": p.stat().st_size,
                                "createdAt": _stamp_iso(m.group(2))})
    return sorted(out, key=lambda b: b["name"].split("-", 2)[2], reverse=True)


def _stamp_iso(stamp: str) -> str:
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _gzip(src: Path, dest: Path) -> None:
    with open(src, "rb") as f, gzip.open(dest, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g, 1024 * 1024)


def copy_offsite(cfg: Config, local_name: str) -> dict:
    """gzip one local backup to the destination, mirror config (if enabled) and prune old off-site copies."""
    dest = cfg.backup_copy_to
    if not dest:
        raise BackupError("no off-site destination: set backup_copy_to in config.toml")
    if MIGRATION.match(local_name):
        raise BackupError("pre-upgrade copies stay local; make a backup now and copy that one")
    src = path_for(cfg, local_name)
    if src is None or not OWN.match(local_name):
        raise BackupError("no such backup", 404)
    name = local_name + ".gz"
    with _lock:
        if is_rclone(dest):
            tmp = backup_dir(cfg) / f".upload-{name}"
            try:
                _gzip(src, tmp)
                _rclone(["copyto", str(tmp), _remote(dest, name)])
            finally:
                tmp.unlink(missing_ok=True)
        else:
            d = Path(dest)
            try:
                d.mkdir(parents=True, exist_ok=True)
                part = d / f".{name}.part"
                _gzip(src, part)
                os.replace(part, d / name)  # never leave a half-written file under the real name
            except OSError as e:
                raise BackupError(f"can't write to {dest}: {e.strerror or e}", 502) from e
        config_copied = _copy_config(cfg) if _include_config(cfg) else False
        pruned = 0
        for b in offsite_list(cfg)[max(cfg.backup_keep, 1):]:
            _offsite_delete(cfg, b["name"])
            pruned += 1
    return {"name": name, "destination": dest, "configCopied": config_copied, "pruned": pruned}


def _offsite_delete(cfg: Config, name: str) -> None:
    if not OFFSITE.match(name):
        return
    dest = cfg.backup_copy_to or ""
    if is_rclone(dest):
        _rclone(["deletefile", _remote(dest, name)], timeout=120)
    else:
        (Path(dest) / name).unlink(missing_ok=True)


def _copy_config(cfg: Config) -> bool:
    """config.toml + the TLS certificate the apps are pinned to, as config/ in the destination (latest only)."""
    files: list[tuple[Path, str]] = []
    if cfg.source_path is not None and cfg.source_path.is_file():
        files.append((cfg.source_path, "config.toml"))
    tls = cfg.data_dir / "tls"
    if tls.is_dir():
        files += [(p, f"tls/{p.name}") for p in sorted(tls.iterdir()) if p.is_file()]
    rc = cfg.data_dir / "rclone.conf"
    if rc.is_file():
        files.append((rc, "rclone.conf"))
    if not files:
        return False
    dest = cfg.backup_copy_to or ""
    for src, rel in files:
        if is_rclone(dest):
            _rclone(["copyto", str(src), _remote(dest, "config", rel)], timeout=300)
        else:
            out = Path(dest) / "config" / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            part = out.with_name(f".{out.name}.part")
            shutil.copyfile(src, part)
            os.chmod(part, 0o600)
            os.replace(part, out)
    return True


def scheduled(cfg: Config, conn: sqlite3.Connection) -> dict:
    """The nightly `backup` job: a local copy, then (if configured) the off-site copy. A failed off-site copy is
    recorded (shown in the admin console) and fails the job; the local copy is kept either way."""
    b = create(cfg, conn, "scheduled")
    res: dict = {"name": b["name"], "kind": b["kind"], "bytes": b["bytes"],
                 "localPruned": prune_local_scheduled(cfg, b["name"])}
    if not cfg.backup_copy_to:
        res["offsite"] = None
        return res
    try:
        res["offsite"] = copy_offsite(cfg, b["name"])
    except BackupError as e:
        set_setting(conn, STATUS_KEY, {"at": now_iso(), "ok": False, "error": e.detail, "name": None})
        raise
    set_setting(conn, STATUS_KEY, {"at": now_iso(), "ok": True, "error": None, "name": res["offsite"]["name"]})
    return res


def offsite_status(cfg: Config, conn: sqlite3.Connection, with_items: bool = True) -> dict:
    dest = cfg.backup_copy_to
    out: dict = {"configured": bool(dest), "destination": dest, "kind": None, "keep": cfg.backup_keep,
                 "includeConfig": _include_config(cfg) if dest else False,
                 "last": get_setting(conn, STATUS_KEY, None), "items": [], "error": None}
    if dest:
        out["kind"] = "rclone" if is_rclone(dest) else "folder"
        if with_items:
            try:
                out["items"] = offsite_list(cfg)
            except BackupError as e:
                out["error"] = e.detail
    return out


# ---------- restore ----------


def _fetch_offsite(cfg: Config, name: str, stage: Path) -> None:
    if not OFFSITE.match(name) or not cfg.backup_copy_to:
        raise BackupError("no such off-site backup", 404)
    dest = cfg.backup_copy_to
    gz = stage.with_name(stage.name + ".gz")
    try:
        if is_rclone(dest):
            _rclone(["copyto", _remote(dest, name), str(gz)])
        else:
            src = Path(dest) / name
            if not src.is_file():
                raise BackupError("no such off-site backup", 404)
            shutil.copyfile(src, gz)
        with gzip.open(gz, "rb") as g, open(stage, "wb") as f:
            shutil.copyfileobj(g, f, 1024 * 1024)
    except (OSError, EOFError, gzip.BadGzipFile) as e:
        raise BackupError(f"can't read the off-site backup: {e}", 422) from e
    finally:
        gz.unlink(missing_ok=True)


def check(path: Path) -> int:
    """Validate a backup file; return its schema version."""
    try:
        c = sqlite3.connect(path)
        try:
            ok = c.execute("PRAGMA quick_check").fetchone()[0]
            if ok != "ok":
                raise BackupError(f"the backup is damaged ({ok})", 422)
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if not {"schema_version", "users", "tracks"} <= names:
                raise BackupError("that file isn't a Homebeat database", 422)
            v = c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        finally:
            c.close()
    except sqlite3.DatabaseError as e:
        raise BackupError(f"that file isn't a readable database ({e})", 422) from e
    if not isinstance(v, int) or v < 1:
        raise BackupError("the backup has no schema version", 422)
    if v > db.SCHEMA_VERSION:
        raise BackupError(f"the backup is from a newer server (schema v{v}; this server is v{db.SCHEMA_VERSION}). "
                          "Update the server first.", 409)
    return v


def restore(cfg: Config, source: str, name: str) -> dict:
    """Replace the live database with a backup. `source` is 'local' or 'offsite'. Returns what happened, including
    the name of the safety copy taken first (restoring that undoes this)."""
    if source not in ("local", "offsite"):
        raise BackupError("source must be local or offsite")
    if not _lock.acquire(blocking=False):
        raise BackupError("another backup or restore is running", 409)
    d = backup_dir(cfg)
    d.mkdir(parents=True, exist_ok=True)
    stage = d / f".restore-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.db"
    try:
        if source == "local":
            src = path_for(cfg, name)
            if src is None:
                raise BackupError("no such backup", 404)
            # Copy first: the check and the restore never touch the listed file, and a WAL-mode copy opens cleanly.
            ro = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            out = sqlite3.connect(stage)
            try:
                ro.backup(out)
            except sqlite3.DatabaseError as e:
                raise BackupError(f"that file isn't a readable database ({e})", 422) from e
            finally:
                out.close()
                ro.close()
        else:
            _fetch_offsite(cfg, name, stage)
        version = check(stage)
        live = db.connect(cfg.db_path)
        try:
            safety = create(cfg, live, "prerestore")
            src_conn = sqlite3.connect(stage)
            try:
                src_conn.execute("PRAGMA journal_mode=DELETE")  # page copy below; the live file stays WAL
                src_conn.backup(live)
            finally:
                src_conn.close()
            live.execute("PRAGMA journal_mode=WAL")
            db.init(live, cfg.admin_username, cfg.admin_password_hash, backup=False)
            from homebeat.auth import users

            users.ensure_admin(live, cfg.admin_username, cfg.admin_password_hash)
            now_v = live.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        finally:
            live.close()
    finally:
        stage.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(stage) + suffix).unlink(missing_ok=True)
        _lock.release()
    log.warning("database restored from %s backup %s (schema v%d -> v%d); safety copy %s",
                source, name, version, now_v, safety["name"])
    return {"restored": name, "source": source, "schemaFrom": version, "schemaTo": now_v,
            "safetyBackup": safety["name"]}
