"""Per-process state shared by the API and web UI, plus FastAPI dependencies."""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import Depends, HTTPException, Request, status

from nasmusic import auth
from nasmusic.app.scheduler import Jobs
from nasmusic.config import Config
from nasmusic.core import open_db
from nasmusic.stream.transcode import OpusCache


@dataclass
class AppContext:
    cfg: Config
    server_id: str
    cert_sha256: str | None
    opus: OpusCache
    version: str
    web_secret: bytes = field(repr=False, default=b"")
    jobs: Jobs | None = None

    @property
    def data_dir(self) -> Path:
        return self.cfg.data_dir


def ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = open_db(request.app.state.ctx.cfg)
    try:
        yield conn
    finally:
        conn.close()


def device(request: Request, conn: sqlite3.Connection = Depends(db)) -> auth.Device:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    dev = auth.device_for_token(conn, token.strip())
    if dev is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked token",
                            headers={"WWW-Authenticate": "Bearer"})
    return dev
