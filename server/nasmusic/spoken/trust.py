"""Which feeds and stations may reach home-network addresses (nasmusic.spoken.net.local_allowed).

An admin vouches for a podcast by subscribing to it and for a station by adding it; everyone else's feeds and
stations can only reach the public internet, so a feed can't be used to read the NAS's or router's web pages."""
from __future__ import annotations

import sqlite3

from nasmusic.spoken import net


def is_admin(conn: sqlite3.Connection, uid: str | None) -> bool:
    if not uid:
        return False
    r = conn.execute("SELECT is_admin FROM users WHERE id = ? AND disabled = 0", (uid,)).fetchone()
    return bool(r and r[0])


def podcast_ok(conn: sqlite3.Connection, pid: str) -> bool:
    return conn.execute("SELECT 1 FROM podcast_subscriptions s JOIN users u ON u.id = s.user_id "
                        "WHERE s.podcast_id = ? AND u.is_admin = 1 AND u.disabled = 0 LIMIT 1",
                        (pid,)).fetchone() is not None


def station_ok(conn: sqlite3.Connection, sid: str) -> bool:
    return conn.execute("SELECT 1 FROM web_stations w JOIN users u ON u.id = w.created_by "
                        "WHERE w.id = ? AND u.is_admin = 1 AND u.disabled = 0", (sid,)).fetchone() is not None


def for_user(conn: sqlite3.Connection, uid: str | None):
    return net.local_allowed(is_admin(conn, uid))


def for_podcast(conn: sqlite3.Connection, pid: str):
    return net.local_allowed(podcast_ok(conn, pid))


def for_station(conn: sqlite3.Connection, sid: str):
    return net.local_allowed(station_ok(conn, sid))
