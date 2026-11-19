"""Database backups in the data directory: manual and pre-prune ones in data_dir/backups/, plus the
automatic pre-migration copies next to the database (nasmusic.db.vN-backup-<time>)."""
from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from nasmusic.config import Config

MIGRATION = re.compile(r"^nasmusic\.db\.v\d+-backup-\d{8}T\d{6}Z$")
OWN = re.compile(r"^nasmusic-(manual|prune)-\d{8}T\d{6}Z(-\d+)?\.db$")


def backup_dir(cfg: Config) -> Path:
    return cfg.data_dir / "backups"


def _info(p: Path, kind: str) -> dict:
    st = p.stat()
    return {"name": p.name, "kind": kind, "bytes": st.st_size,
            "createdAt": datetime.fromtimestamp(st.st_mtime, UTC).replace(microsecond=0).isoformat()
            .replace("+00:00", "Z")}


def list_all(cfg: Config) -> list[dict]:
    out = []
    for p in cfg.data_dir.glob("nasmusic.db.v*-backup-*"):
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
    dest = d / f"nasmusic-{kind}-{stamp}.db"
    n = 1
    while dest.exists():
        n += 1
        dest = d / f"nasmusic-{kind}-{stamp}-{n}.db"
    out = sqlite3.connect(dest)
    try:
        conn.backup(out)  # consistent online copy, even while the server writes
    finally:
        out.close()
    return _info(dest, kind)


def total(cfg: Config) -> dict:
    items = list_all(cfg)
    return {"count": len(items), "bytes": sum(b["bytes"] for b in items)}
