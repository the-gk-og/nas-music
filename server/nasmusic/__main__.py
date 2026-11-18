"""nasmusic CLI: serve | scan | pair-code | hash-password."""
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
    if not cfg.admin_password_hash:
        log.warning("admin_password_hash is not set; the web UI login is disabled (run `nasmusic hash-password`)")

    jobs = Jobs(cfg)
    ctx = build_context(cfg, jobs)
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
    if not args.no_scheduler:
        jobs.start(initial_scan=not args.no_scan)
    log.info("serving HTTPS on %s:%d%s", cfg.bind_host, cfg.https_port,
             f" and HTTP on {cfg.bind_host}:{cfg.http_port}" if cfg.http_port else "")
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
        code, expires = auth.create_pair_code(conn)
        payload = qr.payload(cfg, server_id(conn), tls.fingerprint(files.cert), code)
    finally:
        conn.close()
    if args.json:
        print(qr.payload_json(payload))
        return
    print(qr.terminal(qr.payload_json(payload)))
    print(f"Pair code: {code}   (single use, expires {expires})")


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
    s.add_argument("--json", action="store_true", help="print the QR payload JSON instead")
    s.set_defaults(fn=cmd_pair_code)
    s = sub.add_parser("hash-password", parents=[common], help="hash an admin password for config.toml")
    s.set_defaults(fn=cmd_hash_password)
    args = p.parse_args(argv)
    if not hasattr(args, "config"):
        args.config = None
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    args.fn(args)


if __name__ == "__main__":
    main()
