from __future__ import annotations

import io
import shutil
import subprocess
from pathlib import Path

import pytest
from mutagen.flac import FLAC, Picture
from PIL import Image

from nasmusic import config as config_mod
from nasmusic.core import init_db, open_db

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="session")
def base_flac(tmp_path_factory) -> Path:
    """A 2-second stereo sine FLAC generated once per test session with ffmpeg."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("base") / "base.flac"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-ac", "2", "-ar", "44100", "-c:a", "flac", str(out)],
        check=True,
    )
    return out


def png_bytes(color=(200, 30, 30), size=(600, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def make_flac(base: Path, dest: Path, *, picture: bytes | None = None, **tags) -> Path:
    """Copy the base FLAC to dest and write vorbis comments (keyword names = tag names)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(base, dest)
    f = FLAC(dest)
    for k, v in tags.items():
        f[k] = str(v)
    if picture:
        p = Picture()
        p.type = 3
        p.mime = "image/png"
        p.data = picture
        f.add_picture(p)
    f.save()
    return dest


@pytest.fixture
def library(tmp_path, base_flac) -> Path:
    """Two albums: one with cover.jpg, one with embedded art, plus an untagged file."""
    root = tmp_path / "music"
    a1 = root / "Artist One" / "First Album"
    for i in (1, 2):
        make_flac(base_flac, a1 / f"0{i} Song {i}.flac", title=f"Song {i}", artist="Artist One",
                  albumartist="Artist One", album="First Album", tracknumber=i, date="2001-05-01",
                  genre="Rock")
    Image.new("RGB", (800, 800), (10, 120, 200)).save(a1 / "cover.jpg", "JPEG")
    a2 = root / "The Band" / "Second"
    make_flac(base_flac, a2 / "01 Opening.flac", picture=png_bytes(), title="Opening", artist="The Band feat. X",
              albumartist="The Band", album="Second", tracknumber="1/2", discnumber=1, date=1999,
              musicbrainz_trackid="11111111-1111-1111-1111-111111111111",
              musicbrainz_albumartistid="22222222-2222-2222-2222-222222222222",
              musicbrainz_releasegroupid="33333333-3333-3333-3333-333333333333")
    make_flac(base_flac, a2 / "02 Closing.flac", title="Closing", artist="The Band", albumartist="The Band",
              album="Second", tracknumber=2, discnumber=1, date=1999,
              musicbrainz_albumartistid="22222222-2222-2222-2222-222222222222")
    make_flac(base_flac, root / "Loose" / "untagged.flac")
    return root


@pytest.fixture
def cfg(tmp_path, library):
    c = config_mod.from_dict({
        "library_root": str(library),
        "data_dir": str(tmp_path / "data"),
        "lan_host": "192.168.1.50",
        "tunnel_url": "https://music.example.com",
        "cf_access_client_id": "cid",
        "cf_access_client_secret": "csecret",
        "opus_cache_gb": 1,
    })
    init_db(c)
    return c


@pytest.fixture
def conn(cfg):
    c = open_db(cfg)
    yield c
    c.close()


@pytest.fixture
def scanned(cfg, conn):
    from nasmusic.library import scanner

    scanner.scan(cfg, conn)
    return conn


@pytest.fixture
def app(cfg, scanned):
    from nasmusic.app import create_app
    from nasmusic.app.scheduler import Jobs

    app = create_app(cfg, jobs=Jobs(cfg))
    offline(app)
    return app


def offline(app):
    """No test may reach a real third-party service: catalogue + weather fail fast unless a test mocks them."""
    import httpx

    from nasmusic.app.weather import Weather
    from nasmusic.catalog import Catalog

    def refuse(request):
        raise httpx.ConnectError(f"tests are offline: {request.url.host}")

    app.state.ctx.catalog = Catalog(httpx.MockTransport(refuse))
    app.state.ctx.weather = Weather(httpx.MockTransport(refuse))
    app.state.ctx.http_transport = httpx.MockTransport(refuse)
    from nasmusic.library.lrclib import Lrclib

    app.state.ctx.lrclib = Lrclib("test", httpx.MockTransport(refuse), min_interval_s=0)
    if app.state.ctx.jobs is not None:
        app.state.ctx.jobs.lrclib = app.state.ctx.lrclib
    return app


@pytest.fixture
def anon(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


def make_user(conn, username: str, admin: bool = False, password: str = "password123"):
    from nasmusic.auth import hash_password, users

    u = users.by_username(conn, username)
    if u is None:
        u = users.create(conn, username, None, is_admin=admin, password_hash=hash_password(password))
    return u


def user_client(app, conn, username: str = "alice", admin: bool = True, kind: str = "android"):
    """A TestClient (not yet entered) with a device token for `username`; attributes user_id/device_id/token."""
    from fastapi.testclient import TestClient

    from nasmusic import auth

    u = make_user(conn, username, admin)
    device_id, token = auth.create_device(conn, u.id, f"{username}'s {kind}", kind)
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    c.user_id, c.device_id, c.token, c.username = u.id, device_id, token, username
    return c


@pytest.fixture
def client(app, conn):
    """A TestClient authenticated as admin user "alice"."""
    with user_client(app, conn, "alice", admin=True) as c:
        yield c


@pytest.fixture
def bob(app, conn, client):
    """A second, non-admin user (shares the app with `client`)."""
    c = user_client(app, conn, "bob", admin=False)
    c.__enter__()
    yield c
    c.__exit__(None, None, None)


def track_ids(conn) -> dict[str, str]:
    """title -> track id"""
    return {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM tracks")}
