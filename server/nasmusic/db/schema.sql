-- nas-music shared schema v3 (SQLite, WAL mode). Contract between server core and nasmusic.ai.
-- v2 (docs/v2.md §2): multi-user. The music library and audio analysis are shared; everything about
-- listening is per user. v3 (docs/v3-ux.md phase 1) adds pins, toget, catalog_items and queue source/radio
-- columns. Existing v1/v2 databases are upgraded by nasmusic.db.migrations.
-- Server core writes: users, invites, artists, albums, tracks, user_tracks, events, devices, pair_codes,
--   playlists, queues, lyrics_overrides, playback_state, user_settings.
-- AI writes: features, analysis_jobs, taste_snapshots, mixes, mix_tracks, suggestions (+ suggestions.status
--   is also written by server core when a user answers).
-- Timestamps are ISO-8601 UTC text. IDs are text (stable hashes or uuid4 hex).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                -- JSON
);

-- ---------- users ----------

CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,       -- scrypt$...; '!' = no password set yet (cannot log in)
    is_admin      INTEGER NOT NULL DEFAULT 0,
    disabled      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    last_login    TEXT
);

CREATE TABLE IF NOT EXISTS invites (
    code        TEXT PRIMARY KEY,
    created_by  TEXT NOT NULL REFERENCES users(id),
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    note        TEXT,
    used_by     TEXT REFERENCES users(id),
    used_at     TEXT
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key      TEXT NOT NULL,
    value    TEXT NOT NULL,            -- JSON
    PRIMARY KEY (user_id, key)
);

-- ---------- library (shared) ----------

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
    id              TEXT PRIMARY KEY,  -- sha1 of path relative to library root
    path            TEXT NOT NULL UNIQUE,  -- relative to library root
    album_id        TEXT NOT NULL REFERENCES albums(id),
    artist_id       TEXT NOT NULL REFERENCES artists(id),  -- track artist
    title           TEXT NOT NULL,
    track_no        INTEGER,
    disc_no         INTEGER,
    year            INTEGER,
    genre           TEXT,
    duration_s      REAL NOT NULL,
    flac_bytes      INTEGER NOT NULL,  -- size of the original file (any format)
    mtime           REAL NOT NULL,     -- file mtime at last scan, to detect changes
    mbid            TEXT,              -- MusicBrainz recording id
    added_at        TEXT NOT NULL,
    missing         INTEGER NOT NULL DEFAULT 0,  -- file vanished on last scan (kept for history)
    -- v2 song-info fields, filled by the scanner
    composer        TEXT,
    lyricist        TEXT,
    comment         TEXT,
    album_artist    TEXT,              -- the tag as written (albums.artist_id is the grouping)
    codec           TEXT,              -- FLAC, MP3, AAC, ALAC, Opus, Vorbis
    bitrate         INTEGER,           -- kbps (average)
    sample_rate     INTEGER,           -- Hz
    bits_per_sample INTEGER,
    channels        INTEGER,
    folder          TEXT NOT NULL DEFAULT ''   -- relative directory ('' = library root)
);
CREATE INDEX IF NOT EXISTS tracks_album ON tracks(album_id);
CREATE INDEX IF NOT EXISTS tracks_artist ON tracks(artist_id);
CREATE INDEX IF NOT EXISTS tracks_folder ON tracks(folder);
CREATE INDEX IF NOT EXISTS tracks_genre ON tracks(genre COLLATE NOCASE);

-- Per-user track state (v1 kept starred_at / rating / blocked on tracks).
CREATE TABLE IF NOT EXISTS user_tracks (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    track_id    TEXT NOT NULL REFERENCES tracks(id),
    starred_at  TEXT,                  -- NULL = not a favourite
    rating      INTEGER,               -- 1..5 or NULL
    blocked     INTEGER NOT NULL DEFAULT 0,  -- thumbs-down: never put in this user's mixes
    play_count  INTEGER NOT NULL DEFAULT 0,
    skip_count  INTEGER NOT NULL DEFAULT 0,
    last_played TEXT,
    PRIMARY KEY (user_id, track_id)
);
CREATE INDEX IF NOT EXISTS user_tracks_starred ON user_tracks(user_id, starred_at);
CREATE INDEX IF NOT EXISTS user_tracks_track ON user_tracks(track_id);

-- ---------- devices + auth ----------

CREATE TABLE IF NOT EXISTS devices (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'other' CHECK (kind IN ('android','web','desktop','watch','other')),
    app_version TEXT,
    token_hash  TEXT NOT NULL UNIQUE,  -- sha256 of the bearer token
    created_at  TEXT NOT NULL,
    last_seen   TEXT,
    revoked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS devices_user ON devices(user_id);

CREATE TABLE IF NOT EXISTS pair_codes (
    code        TEXT PRIMARY KEY,
    user_id     TEXT REFERENCES users(id) ON DELETE CASCADE,  -- the device logs in as this user
    expires_at  TEXT NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0
);

-- ---------- listening (per user) ----------

CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,      -- client UUID (idempotency)
    user_id     TEXT NOT NULL REFERENCES users(id),
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
    received_at TEXT NOT NULL,
    context     TEXT                   -- JSON EventContext (docs/v2.md §10), only enabled signals
);
CREATE INDEX IF NOT EXISTS events_track ON events(track_id, at);
CREATE INDEX IF NOT EXISTS events_at ON events(at);
CREATE INDEX IF NOT EXISTS events_user ON events(user_id, at);

CREATE TABLE IF NOT EXISTS playlists (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS playlists_user ON playlists(user_id);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    track_id    TEXT NOT NULL REFERENCES tracks(id),
    added_at    TEXT NOT NULL,
    PRIMARY KEY (playlist_id, position)
);

-- Musicolet-style named queues shared by a user's devices. Max 20 per user.
CREATE TABLE IF NOT EXISTS queues (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    slot          INTEGER NOT NULL CHECK (slot BETWEEN 1 AND 20),
    current_index INTEGER NOT NULL DEFAULT 0,
    position_ms   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    source_type   TEXT,                -- v3: what it was started from (album, playlist, mix, radio, ...)
    source_id     TEXT,
    radio_seed    TEXT,                -- v3: JSON RadioSeed for radio queues, else NULL
    radio_id      TEXT,                -- v3: radio_sessions.id (the AI's radio) behind a radio queue
    UNIQUE (user_id, slot)
);

CREATE TABLE IF NOT EXISTS queue_tracks (
    queue_id  TEXT NOT NULL REFERENCES queues(id) ON DELETE CASCADE,
    position  INTEGER NOT NULL,
    track_id  TEXT NOT NULL REFERENCES tracks(id),
    PRIMARY KEY (queue_id, position)
);

-- Per-user lyrics edits; never written to the music file.
CREATE TABLE IF NOT EXISTS lyrics_overrides (
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    track_id   TEXT NOT NULL REFERENCES tracks(id),
    raw        TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, track_id)
);

-- Last known Connect playback state per user (also held in memory by the hub).
CREATE TABLE IF NOT EXISTS playback_state (
    user_id    TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    state      TEXT NOT NULL,          -- JSON PlaybackState
    updated_at TEXT NOT NULL
);

-- ---------- v3: Home pins, To get list, online catalogue cache ----------

CREATE TABLE IF NOT EXISTS pins (
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type       TEXT NOT NULL,          -- album, artist, playlist, mix, queue, smart_list, genre, folder
    item_id    TEXT NOT NULL,
    position   INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, type, item_id)
);

CREATE TABLE IF NOT EXISTS toget (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type          TEXT NOT NULL CHECK (type IN ('album','track')),
    title         TEXT NOT NULL,
    artist        TEXT NOT NULL,
    year          INTEGER,
    mbid          TEXT,                -- release group (album) or recording (track)
    catalog_id    TEXT,                -- catalog_items.id when added from search
    note          TEXT,
    status        TEXT NOT NULL DEFAULT 'want' CHECK (status IN ('want','got','dismissed')),
    source        TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('search','suggestion','manual')),
    suggestion_id TEXT,
    match_key     TEXT NOT NULL,       -- normalised "artist|title", for de-duplication and library matching
    owned_id      TEXT,                -- library album/track id once found by a scan
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    got_at        TEXT,
    UNIQUE (user_id, type, match_key)
);
CREATE INDEX IF NOT EXISTS toget_user ON toget(user_id, status);

-- Cache of online-catalogue items seen in search results (shared; no user data).
CREATE TABLE IF NOT EXISTS catalog_items (
    id          TEXT PRIMARY KEY,      -- dz:album:N, dz:track:N, dz:artist:N, mb:rg:<mbid>
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    artist      TEXT,
    album       TEXT,
    year        INTEGER,
    duration_s  REAL,
    mbid        TEXT,
    cover_url   TEXT,
    preview_ref TEXT,                  -- Deezer API URL resolved to a fresh preview link on demand
    fetched_at  TEXT NOT NULL
);

-- ---------- AI-owned tables ----------

-- v3 radio sessions (written by the AI's radio_start/radio_more; server core maps queues.radio_id to them).
CREATE TABLE IF NOT EXISTS radio_sessions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    seed_type  TEXT NOT NULL,
    seed_id    TEXT,
    title      TEXT NOT NULL,          -- display name, e.g. "Song X radio"; used as the queue name
    anchors    TEXT NOT NULL,
    served     TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS radio_sessions_user ON radio_sessions(user_id, updated_at);

CREATE TABLE IF NOT EXISTS features (          -- global: audio analysis is shared
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

CREATE TABLE IF NOT EXISTS analysis_jobs (     -- global
    track_id      TEXT NOT NULL REFERENCES tracks(id),
    model_version TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN ('pending','done','failed')),
    error         TEXT,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (track_id, model_version)
);

CREATE TABLE IF NOT EXISTS taste_snapshots (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL REFERENCES users(id),
    created_at    TEXT NOT NULL,
    model_version TEXT NOT NULL,
    payload       BLOB NOT NULL,       -- serialized centroids + learned model
    notes         TEXT
);
CREATE INDEX IF NOT EXISTS taste_snapshots_user ON taste_snapshots(user_id, created_at);

CREATE TABLE IF NOT EXISTS mixes (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    kind        TEXT NOT NULL CHECK (kind IN ('daily','morning','evening','deep_cuts','more_like','context')),
    title       TEXT NOT NULL,
    for_date    TEXT NOT NULL,         -- local date YYYY-MM-DD the mix is for
    created_at  TEXT NOT NULL,
    seed_track  TEXT,                  -- for more_like
    active      INTEGER NOT NULL DEFAULT 1  -- 1 = part of "today"; nightly run deactivates old ones
);
CREATE INDEX IF NOT EXISTS mixes_active ON mixes(active, kind);
CREATE INDEX IF NOT EXISTS mixes_user ON mixes(user_id, active);

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
    user_id             TEXT NOT NULL REFERENCES users(id),
    artist              TEXT NOT NULL,
    album               TEXT NOT NULL,
    year                INTEGER,
    artist_mbid         TEXT,
    release_group_mbid  TEXT,
    cover_url           TEXT,
    preview_url         TEXT,
    buy_links           TEXT,          -- JSON [{name,url}]
    reason              TEXT NOT NULL,
    score               REAL,
    status              TEXT NOT NULL DEFAULT 'new' CHECK (status IN ('new','want','not_interested','have')),
    created_at          TEXT NOT NULL,
    answered_at         TEXT,
    UNIQUE (user_id, release_group_mbid)
);
CREATE INDEX IF NOT EXISTS suggestions_user ON suggestions(user_id, status);
