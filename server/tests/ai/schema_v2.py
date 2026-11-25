"""Schema v2 for AI tests, built from docs/v2.md §2 and §10 until server core's migration lands.

Starts from the v1 schema.sql and applies the spec's deltas for the tables the AI engine touches.
Additions the spec does not define (proposed to server core, see homebeat/ai/README.md "Spec gaps"):
* `user_settings` primary key (user_id, key) and JSON `value`;
* `mixes.kind` also allows 'context' (context mixes: Workout, Drive, Late night, At <place>).
"""
from __future__ import annotations

from homebeat import db

V2_DDL = """
CREATE TABLE users (
  id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, password_hash TEXT NOT NULL,
  is_admin INTEGER NOT NULL DEFAULT 0, disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, last_login TEXT);

CREATE TABLE user_tracks (
  user_id TEXT NOT NULL REFERENCES users(id), track_id TEXT NOT NULL REFERENCES tracks(id),
  starred_at TEXT, rating INTEGER, blocked INTEGER NOT NULL DEFAULT 0,
  play_count INTEGER NOT NULL DEFAULT 0, skip_count INTEGER NOT NULL DEFAULT 0, last_played TEXT,
  PRIMARY KEY (user_id, track_id));

CREATE TABLE user_settings (
  user_id TEXT NOT NULL REFERENCES users(id), key TEXT NOT NULL, value TEXT NOT NULL,
  PRIMARY KEY (user_id, key));

DROP TABLE events;
CREATE TABLE events (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    device_id   TEXT REFERENCES devices(id),
    type        TEXT NOT NULL CHECK (type IN ('play','skip','star','unstar','rate','thumb_up','thumb_down',
                                              'thumb_clear')),
    track_id    TEXT NOT NULL REFERENCES tracks(id),
    at          TEXT NOT NULL,
    position_s  REAL,
    duration_s  REAL,
    source      TEXT,
    mix_id      TEXT,
    rating      INTEGER,
    offline     INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL,
    context     TEXT);
CREATE INDEX events_user ON events(user_id, at);
CREATE INDEX events_track ON events(track_id, at);

DROP TABLE mix_tracks;
DROP TABLE mixes;
CREATE TABLE mixes (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    kind        TEXT NOT NULL CHECK (kind IN ('daily','morning','evening','deep_cuts','more_like','context')),
    title       TEXT NOT NULL,
    for_date    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    seed_track  TEXT,
    active      INTEGER NOT NULL DEFAULT 1);
CREATE INDEX mixes_user ON mixes(user_id, active, kind);
CREATE TABLE mix_tracks (
    mix_id    TEXT NOT NULL REFERENCES mixes(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    track_id  TEXT NOT NULL REFERENCES tracks(id),
    score     REAL,
    reason    TEXT NOT NULL,
    PRIMARY KEY (mix_id, position));

DROP TABLE taste_snapshots;
CREATE TABLE taste_snapshots (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL,
    model_version TEXT NOT NULL,
    payload     BLOB NOT NULL,
    notes       TEXT);

DROP TABLE suggestions;
CREATE TABLE suggestions (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(id),
    artist              TEXT NOT NULL,
    album               TEXT NOT NULL,
    year                INTEGER,
    artist_mbid         TEXT,
    release_group_mbid  TEXT,
    cover_url           TEXT,
    preview_url         TEXT,
    buy_links           TEXT,
    reason              TEXT NOT NULL,
    score               REAL,
    status              TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','want','not_interested','have')),
    created_at          TEXT NOT NULL,
    answered_at         TEXT,
    UNIQUE (user_id, release_group_mbid));

-- per-user track state replaces these v1 columns
DROP INDEX IF EXISTS tracks_starred;
ALTER TABLE tracks DROP COLUMN starred_at;
ALTER TABLE tracks DROP COLUMN rating;
ALTER TABLE tracks DROP COLUMN blocked;
"""


def init_v2(conn) -> None:
    db.init(conn)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'user_tracks'").fetchone():
        return  # server core's schema.sql is already v2: use it as is
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(V2_DDL)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("UPDATE schema_version SET version = 2")


def add_user(conn, user_id: str, disabled: bool = False) -> str:
    conn.execute("INSERT INTO users (id, username, password_hash, disabled, created_at) VALUES (?,?,?,?,?)",
                 (user_id, user_id, "x", int(disabled), f"2026-01-01T00:00:0{len(user_id) % 10}Z"))
    return user_id
