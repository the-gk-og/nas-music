"""Offline sync manifest: what the phone should hold right now (active mixes + favourites)."""
from __future__ import annotations

import sqlite3

from nasmusic.core import now_iso


def manifest(conn: sqlite3.Connection, favourites_format: str = "opus") -> dict:
    """Mix tracks are always Opus; favourites use `favourites_format`. A track that is both a favourite
    and in a mix appears once, as a favourite (so a FLAC favourite isn't downgraded), with its mixIds."""
    items: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT mt.track_id, mt.mix_id FROM mix_tracks mt JOIN mixes m ON m.id = mt.mix_id "
        "JOIN tracks t ON t.id = mt.track_id "
        "WHERE m.active = 1 AND t.missing = 0 ORDER BY m.created_at DESC, mt.mix_id, mt.position"
    ):
        it = items.setdefault(r["track_id"], {"trackId": r["track_id"], "format": "opus", "why": "mix",
                                             "mixIds": []})
        if r["mix_id"] not in it["mixIds"]:
            it["mixIds"].append(r["mix_id"])
    for r in conn.execute(
        "SELECT id FROM tracks WHERE starred_at IS NOT NULL AND missing = 0 ORDER BY starred_at DESC, id"
    ):
        it = items.get(r["id"])
        if it is None:
            items[r["id"]] = {"trackId": r["id"], "format": favourites_format, "why": "favourite", "mixIds": []}
        else:
            it["format"], it["why"] = favourites_format, "favourite"
    return {"generatedAt": now_iso(), "items": list(items.values())}
