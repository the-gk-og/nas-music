"""nasmusic CLI: serve | scan | pair-code | hash-password | user."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import json
import logging
import signal
import sys

import uvicorn

from nasmusic.config import ConfigError, load


def _load(args):
    try:
        return load(args.config)
    except ConfigError as e:
        sys.exit(f"nasmusic: {e}")


class _Server(uvicorn.Server):
    """uvicorn.Server that leaves signal handling to us, so two servers can share one loop."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def _run_servers(servers) -> None:
    loop = asyncio.get_running_loop()

    def stop():
        for s in servers:
            if s.should_exit:
                s.force_exit = True
            s.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop)
    await asyncio.gather(*(s.serve() for s in servers))


def cmd_serve(args) -> None:
    from nasmusic.app import build_context, create_app
    from nasmusic.app.scheduler import Jobs
    from nasmusic.auth import tls

    cfg = _load(args)
    if not cfg.library_root.is_dir():
        logging.getLogger("nasmusic").warning("library_root %s does not exist yet", cfg.library_root)
    files, created = tls.ensure_cert(cfg.data_dir, cfg.lan_host)
    fp = tls.fingerprint(files.cert)
    log = logging.getLogger("nasmusic")
    log.info("%s TLS certificate for %s, SHA-256 fingerprint:\n    %s",
             "Generated new" if created else "Using", cfg.lan_host, fp)

    jobs = Jobs(cfg)
    ctx = build_context(cfg, jobs)
    from nasmusic.core import open_db as _open

    _c = _open(cfg)
    try:
        if not _c.execute("SELECT 1 FROM users WHERE password_hash != '!' AND is_admin = 1").fetchone():
            log.warning("no admin has a password yet: set admin_password_hash in the config, or run "
                        "`nasmusic user passwd %s`", cfg.admin_username)
    finally:
        _c.close()
    app = create_app(cfg, context=ctx)
    common = {"host": cfg.bind_host, "lifespan": "off", "log_config": None, "timeout_graceful_shutdown": 5,
              "server_header": False,
              # Protocol-level WebSocket pings keep Connect sockets alive through the Cloudflare tunnel.
              "ws_ping_interval": 20.0, "ws_ping_timeout": 20.0}
    servers = [
        _Server(uvicorn.Config(app, port=cfg.https_port, ssl_certfile=str(files.cert),
                               ssl_keyfile=str(files.key), **common)),
    ]
    if cfg.http_port:
        servers.append(_Server(uvicorn.Config(app, port=cfg.http_port, **common)))
    if cfg.cast_enabled and cfg.cast_port:
        from nasmusic.app.cast_api import cast_app

        # Speakers fetch signed media URLs over plain HTTP here; nothing else is served on this port.
        servers.append(_Server(uvicorn.Config(cast_app(ctx), port=cfg.cast_port, **common)))
    if not args.no_scheduler:
        jobs.start(initial_scan=not args.no_scan)
    log.info("serving HTTPS on %s:%d%s", cfg.bind_host, cfg.https_port,
             f" and HTTP on {cfg.bind_host}:{cfg.http_port}" if cfg.http_port else "")
    if cfg.cast_enabled and cfg.cast_port:
        log.info("casting: speakers fetch media from %s (cast port %d)", cfg.cast_base_url, cfg.cast_port)
    try:
        asyncio.run(_run_servers(servers))
    finally:
        jobs.shutdown()


def cmd_scan(args) -> None:
    from nasmusic.core import init_db, open_db
    from nasmusic.library import scanner

    cfg = _load(args)
    init_db(cfg)
    conn = open_db(cfg)
    try:
        res = scanner.scan(cfg, conn, full=args.full)
        from nasmusic.app import toget

        toget.match_owned(conn)
    finally:
        conn.close()
    print(json.dumps(res.as_dict(), indent=2))
    if args.analyse:
        from nasmusic.app.scheduler import Jobs

        print(json.dumps(Jobs(cfg).run("analyse"), indent=2))


def cmd_pair_code(args) -> None:
    from nasmusic import auth
    from nasmusic.auth import qr, tls
    from nasmusic.core import init_db, open_db, server_id

    cfg = _load(args)
    init_db(cfg)
    files, _ = tls.ensure_cert(cfg.data_dir, cfg.lan_host)
    conn = open_db(cfg)
    try:
        from nasmusic.auth import users

        u = users.by_username(conn, args.user or cfg.admin_username)
        if u is None:
            sys.exit(f"no such user: {args.user or cfg.admin_username}")
        code, expires = auth.create_pair_code(conn, u.id)
        payload = qr.payload(cfg, server_id(conn), tls.fingerprint(files.cert), code)
    finally:
        conn.close()
    if args.json:
        print(qr.payload_json(payload))
        return
    print(qr.terminal(qr.payload_json(payload)))
    print(f"Pair code: {code}   (single use, expires {expires}, signs in as {u.username})")


def _read_password(prompt: str) -> str:
    if sys.stdin.isatty():
        pw = getpass.getpass(prompt)
        if pw != getpass.getpass("Repeat: "):
            sys.exit("Passwords don't match.")
        return pw
    return sys.stdin.readline().rstrip("\n")


def cmd_user(args) -> None:
    from nasmusic import auth
    from nasmusic.auth import users
    from nasmusic.core import init_db, open_db

    cfg = _load(args)
    init_db(cfg)
    conn = open_db(cfg)
    try:
        if args.action == "list":
            for u in users.list_all(conn):
                flags = " ".join(f for f, on in (("admin", u["isAdmin"]), ("disabled", u["disabled"])) if on)
                print(f"{u['username']:<24} {u['deviceCount']:>3} devices  {flags}")
            return
        if not args.name:
            sys.exit("give a username")
        pw = _read_password(f"Password for {args.name}: ")
        try:
            if args.action == "add":
                users.create(conn, args.name, pw, is_admin=args.admin)
                print(f"created {args.name}{' (admin)' if args.admin else ''}")
            else:
                u = users.by_username(conn, args.name)
                if u is None:
                    sys.exit(f"no such user: {args.name}")
                users.set_password(conn, u.id, pw)
                n = len(auth.revoke_user_devices(conn, u.id))
                print(f"password changed for {u.username}; signed out {n} device(s)")
        except users.UserError as e:
            sys.exit(e.detail)
    finally:
        conn.close()


def cmd_hash_password(args) -> None:
    from nasmusic.auth import hash_password

    if sys.stdin.isatty():
        pw = getpass.getpass("New admin password: ")
        if pw != getpass.getpass("Repeat: "):
            sys.exit("Passwords don't match.")
    else:
        pw = sys.stdin.readline().rstrip("\n")
    if len(pw) < 8:
        sys.exit("Use at least 8 characters.")
    h = hash_password(pw)
    print(f'admin_password_hash = "{h}"' if sys.stdout.isatty() else h)


def main(argv: list[str] | None = None) -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", "-c", default=argparse.SUPPRESS,
                        help="path to config.toml (default: $NASMUSIC_CONFIG or /data/config.toml)")
    p = argparse.ArgumentParser(prog="nasmusic", parents=[common])
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", parents=[common], help="run the HTTPS + HTTP server and scheduler")
    s.add_argument("--no-scan", action="store_true", help="don't scan the library at startup")
    s.add_argument("--no-scheduler", action="store_true", help="no background jobs at all")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("scan", parents=[common], help="scan the library once and exit")
    s.add_argument("--full", action="store_true", help="re-read tags of every file")
    s.add_argument("--analyse", action="store_true", help="also run audio analysis afterwards")
    s.set_defaults(fn=cmd_scan)
    s = sub.add_parser("pair-code", parents=[common], help="print a one-time pairing QR + code")
    s.add_argument("--user", help="the user the device signs in as (default: admin_username)")
    s.add_argument("--json", action="store_true", help="print the QR payload JSON instead")
    s.set_defaults(fn=cmd_pair_code)
    s = sub.add_parser("hash-password", parents=[common], help="hash an admin password for config.toml")
    s.set_defaults(fn=cmd_hash_password)
    s = sub.add_parser("user", parents=[common], help="manage users: list | add NAME [--admin] | passwd NAME")
    s.add_argument("action", choices=["list", "add", "passwd"])
    s.add_argument("name", nargs="?")
    s.add_argument("--admin", action="store_true", help="with add: make the user an admin")
    s.set_defaults(fn=cmd_user)
    args = p.parse_args(argv)
    if not hasattr(args, "config"):
        args.config = None
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    args.fn(args)


if __name__ == "__main__":
    main()
