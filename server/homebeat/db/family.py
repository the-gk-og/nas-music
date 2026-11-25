"""Family mode: THE shared content filter (schema v6 tables `family_mode` and `track_explicit`).

Everything that lists, picks or plays library tracks for a user goes through this module, so one rule
decides what a family-mode user can see:

- server core: `homebeat.library.queries` (browse, search, smart lists, single tracks, tracks_by_ids, which
  mixes, Home/For you, blends, queues and playlists use), radio/queue writes, the sync manifest, /stream and
  cast links (locked users);
- the AI engine (allowed to import `homebeat.db`): the recommender's `Signals.hidden` mask, which mixes,
  radio, "more like this" and blends exclude.

Private uploads (schema v7 `uploads.visibility = 'private'`) ride on the same filter: a user never sees
another user's "only me" uploads, whether or not family mode is on.

Statuses: explicit | probable | clean | unknown (no track_explicit row = unknown). Family mode always hides
`explicit`; `hide_probable` and `hide_unknown` are the user's choice. Status values are constants, so the
SQL fragments below inline them (no parameters to thread through callers' queries).
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

STATUSES = ("explicit", "probable", "clean", "unknown")


@dataclass(frozen=True)
class Policy:
    enabled: bool = False
    hide_probable: bool = True
    hide_unknown: bool = False
    locked: bool = False
    has_pin: bool = False
    viewer: str | None = None          # the user this policy is for (private uploads of others are hidden)
    private_others: bool = False       # someone else has private uploads, so the filter must check them

    @property
    def filters(self) -> bool:
        """True when this user must not see some tracks (family mode or other people's private uploads)."""
        return bool(self.hidden) or self.private_others

    @property
    def hidden(self) -> tuple[str, ...]:
        """Statuses this user must not see (empty when family mode is off)."""
        if not self.enabled:
            return ()
        out = ["explicit"]
        if self.hide_probable:
            out.append("probable")
        if self.hide_unknown:
            out.append("unknown")
        return tuple(out)

    @property
    def active(self) -> bool:
        return self.enabled

    def hides(self, status: str | None) -> bool:
        return (status or "unknown") in self.hidden

    def as_api(self) -> dict:
        return {"enabled": self.enabled, "hideProbable": self.hide_probable, "hideUnknown": self.hide_unknown,
                "locked": self.locked, "hasPin": self.has_pin}


OFF = Policy()


def _has_table(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'family_mode'").fetchone() \
        is not None


def _private_others(conn: sqlite3.Connection, user_id: str) -> bool:
    try:
        return conn.execute("SELECT 1 FROM uploads WHERE visibility = 'private' AND (user_id IS NULL OR user_id != ?) "
                            "LIMIT 1", (user_id,)).fetchone() is not None
    except sqlite3.OperationalError:  # pre-v7 database
        return False


def policy(conn: sqlite3.Connection, user_id: str | None) -> Policy:
    """The user's family-mode policy (OFF for unknown users, None, or a pre-v6 database)."""
    if not user_id:
        return OFF
    priv = _private_others(conn, user_id)
    try:
        r = conn.execute("SELECT enabled, hide_probable, hide_unknown, locked, pin_hash FROM family_mode "
                         "WHERE user_id = ?", (user_id,)).fetchone()
    except sqlite3.OperationalError:  # pre-v6 database (the AI's tests build partial schemas)
        r = None
    if r is None:
        return Policy(viewer=user_id, private_others=priv) if priv else OFF
    return Policy(enabled=bool(r[0]), hide_probable=bool(r[1]), hide_unknown=bool(r[2]), locked=bool(r[3]),
                  has_pin=r[4] is not None, viewer=user_id, private_others=priv)


def private_condition(p: Policy, alias: str = "t") -> str:
    """SQL boolean that is false for other users' private uploads ("1" when there are none)."""
    if not p.private_others:
        return "1"
    viewer = (p.viewer or "").replace("'", "''")
    return (f"NOT EXISTS (SELECT 1 FROM uploads fm_u WHERE fm_u.track_id = {alias}.id AND fm_u.visibility = 'private' "
            f"AND (fm_u.user_id IS NULL OR fm_u.user_id != '{viewer}'))")


def _as_policy(conn: sqlite3.Connection, who: Policy | str | None) -> Policy:
    return who if isinstance(who, Policy) else policy(conn, who)


def condition(p: Policy, alias: str = "t") -> str:
    """SQL boolean (no parameters) that is true for tracks `p` allows; `alias` is the tracks alias.
    Returns "1" when nothing is hidden."""
    status = _status_condition(p, alias)
    priv = private_condition(p, alias)
    if priv == "1":
        return status
    return priv if status == "1" else f"({status}) AND {priv}"


def _status_condition(p: Policy, alias: str) -> str:
    hidden = p.hidden
    if not hidden:
        return "1"
    marks = ",".join(f"'{s}'" for s in hidden if s != "unknown")
    if "unknown" in hidden:
        # A track needs a known, allowed verdict.
        allowed = ",".join(f"'{s}'" for s in STATUSES if s not in hidden)
        return (f"EXISTS (SELECT 1 FROM track_explicit fm_x WHERE fm_x.track_id = {alias}.id "
                f"AND fm_x.status IN ({allowed}))")
    return f"NOT EXISTS (SELECT 1 FROM track_explicit fm_x WHERE fm_x.track_id = {alias}.id AND fm_x.status IN ({marks}))"


def where(conn: sqlite3.Connection, who: Policy | str | None, alias: str = "t") -> str:
    """" AND <condition>" for appending to a WHERE clause, or "" when the user sees everything."""
    p = _as_policy(conn, who)
    return f" AND {condition(p, alias)}" if p.filters else ""


def hidden_among(conn: sqlite3.Connection, who: Policy | str | None, track_ids: Iterable[str]) -> set[str]:
    """The ids in `track_ids` that the user must not see."""
    p = _as_policy(conn, who)
    ids = list(dict.fromkeys(track_ids))
    if not p.filters or not ids:
        return set()
    out: set[str] = set()
    cond = condition(p, "t")
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        out.update(r[0] for r in conn.execute(
            f"SELECT t.id FROM tracks t WHERE t.id IN ({','.join('?' * len(chunk))}) AND NOT ({cond})", chunk))
    # Ids that are not tracks at all are left to the caller (they 404 elsewhere).
    return out


def filter_ids(conn: sqlite3.Connection, who: Policy | str | None, track_ids: Iterable[str]) -> list[str]:
    """`track_ids` without the hidden ones (order and duplicates kept). Use it for anything that ADDS tracks
    for a user: radio, queues, Jam guest adds, AI results."""
    ids = list(track_ids)
    hidden = hidden_among(conn, who, ids)
    return [i for i in ids if i not in hidden] if hidden else ids


def allowed(conn: sqlite3.Connection, who: Policy | str | None, track_id: str) -> bool:
    return not hidden_among(conn, who, [track_id])


def all_hidden(conn: sqlite3.Connection, who: Policy | str | None) -> set[str]:
    """Every library track id the user must not see (for the AI's per-library masks)."""
    p = _as_policy(conn, who)
    if not p.filters:
        return set()
    try:
        return {r[0] for r in conn.execute(f"SELECT t.id FROM tracks t WHERE NOT ({condition(p, 't')})")}
    except sqlite3.OperationalError:  # no track_explicit table (pre-v6)
        return set()


def status_of(conn: sqlite3.Connection, track_ids: Iterable[str]) -> dict[str, str]:
    """{track_id: status} for the given ids (unknown ones included)."""
    ids = list(dict.fromkeys(track_ids))
    out = {i: "unknown" for i in ids}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for r in conn.execute(f"SELECT track_id, status FROM track_explicit WHERE track_id IN "
                              f"({','.join('?' * len(chunk))})", chunk):
            out[r[0]] = r[1]
    return out
