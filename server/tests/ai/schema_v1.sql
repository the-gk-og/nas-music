-- Frozen copy of the v1 schema.sql, for the AI engine's transitional v1 test (test_transitional_v1_database).
-- nas-music shared schema (SQLite, WAL mode). Contract between server core and nasmusic.ai.
-- Server core writes: artists, albums, tracks, events, devices, pair_codes, favourites (via tracks.starred_at).
-- AI writes: features, taste_snapshots, mixes, mix_tracks, suggestions, analysis_jobs.
-- Timestamps are ISO-8601 UTC text. IDs are text (stable hashes or uuid4 hex).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                -- JSON
);

CREATE TABLE IF NOT EXISTS artists (
    id         TEXT PRIMARY KEY,       -- sha1 of normalised name (or 'mb:' + MBID when tagged)
    name       TEXT NOT NULL,
    sort_name  TEXT NOT NULL,
    mbid       TEXT
);

CREATE TABLE IF NOT EXISTS albums (
    id         TEXT PRIMARY KEY,
    artist_id  TEXT NOT NULL REFERENCES artists(id),
    title      TEXT NOT NULL,
    year       INTEGER,
    mbid       TEXT,                   -- MusicBrainz release-group id when tagged
    art_path   TEXT,                   -- cover.jpg/folder.jpg path, or NULL if embedded/none
    added_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tracks (
    id          TEXT PRIMARY KEY,      -- sha1 of path relative to library root
    path        TEXT NOT NULL UNIQUE,  -- relative to library root
    album_id    TEXT NOT NULL REFERENCES albums(id),
    artist_id   TEXT NOT NULL REFERENCES artists(id),  -- track artist
    title       TEXT NOT NULL,
    track_no    INTEGER,
    disc_no     INTEGER,
    year        INTEGER,
    genre       TEXT,
    duration_s  REAL NOT NULL,
    flac_bytes  INTEGER NOT NULL,
    mtime       REAL NOT NULL,         -- file mtime at last scan, to detect changes
    mbid        TEXT,                  -- MusicBrainz recording id
    starred_at  TEXT,                  -- NULL = not a favourite
    rating      INTEGER,               -- 1..5 or NULL
    blocked     INTEGER NOT NULL DEFAULT 0,  -- thumbs-down: never put in mixes
    added_at    TEXT NOT NULL,
    missing     INTEGER NOT NULL DEFAULT 0   -- file vanished on last scan (kept for history)
);
CREATE INDEX IF NOT EXISTS tracks_album ON tracks(album_id);
CREATE INDEX IF NOT EXISTS tracks_artist ON tracks(artist_id);
CREATE INDEX IF NOT EXISTS tracks_starred ON tracks(starred_at);

CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,      -- client UUID (idempotency)
    device_id   TEXT REFERENCES devices(id),
    type        TEXT NOT NULL CHECK (type IN ('play','skip','star','unstar','rate','thumb_up','thumb_down','thumb_clear')),
    track_id    TEXT NOT NULL REFERENCES tracks(id),
    at          TEXT NOT NULL,
    position_s  REAL,
    duration_s  REAL,
    source      TEXT,
    mix_id      TEXT,
    rating      INTEGER,
    offline     INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_track ON events(track_id, at);
CREATE INDEX IF NOT EXISTS events_at ON events(at);

CREATE TABLE IF NOT EXISTS devices (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,  -- sha256 of the bearer token
    created_at  TEXT NOT NULL,
    last_seen   TEXT,
    revoked     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS pair_codes (
    code        TEXT PRIMARY KEY,
    expires_at  TEXT NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0
);

-- ---------- AI-owned tables ----------

CREATE TABLE IF NOT EXISTS features (
    track_id      TEXT NOT NULL REFERENCES tracks(id),
    model_version TEXT NOT NULL,       -- e.g. 'effnet-discogs-1'
    embedding     BLOB NOT NULL,       -- float32 little-endian array
    dims          INTEGER NOT NULL,
    bpm           REAL,
    key           TEXT,
    energy        REAL,                -- 0..1
    danceability  REAL,                -- 0..1
    tags          TEXT,                -- JSON {"genre:rock": 0.8, "mood:happy": 0.4, ...}
    analysed_at   TEXT NOT NULL,
    PRIMARY KEY (track_id, model_version)
);

CREATE TABLE IF NOT EXISTS analysis_jobs (
    track_id      TEXT NOT NULL REFERENCES tracks(id),
    model_version TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('pending','done','failed')),
    error         TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (track_id, model_version)
);

CREATE TABLE IF NOT EXISTS taste_snapshots (
    id          TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL,
    model_version TEXT NOT NULL,
    payload     BLOB NOT NULL,         -- serialized centroids + learned model
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS mixes (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('daily','morning','evening','deep_cuts','more_like')),
    title       TEXT NOT NULL,
    for_date    TEXT NOT NULL,         -- local date YYYY-MM-DD the mix is for
    created_at  TEXT NOT NULL,
    seed_track  TEXT,                  -- for more_like
    active      INTEGER NOT NULL DEFAULT 1  -- 1 = part of "today"; nightly run deactivates old ones
);
CREATE INDEX IF NOT EXISTS mixes_active ON mixes(active, kind);

CREATE TABLE IF NOT EXISTS mix_tracks (
    mix_id    TEXT NOT NULL REFERENCES mixes(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    track_id  TEXT NOT NULL REFERENCES tracks(id),
    score     REAL,
    reason    TEXT NOT NULL,
    PRIMARY KEY (mix_id, position)
);

CREATE TABLE IF NOT EXISTS suggestions (
    id                  TEXT PRIMARY KEY,
    artist              TEXT NOT NULL,
    album               TEXT NOT NULL,
    year                INTEGER,
    artist_mbid         TEXT,
    release_group_mbid  TEXT UNIQUE,
    cover_url           TEXT,
    preview_url         TEXT,
    buy_links           TEXT,          -- JSON [{name,url}]
    reason              TEXT NOT NULL,
    score               REAL,
    status              TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','want','not_interested','have')),
    created_at          TEXT NOT NULL,
    answered_at         TEXT
);
