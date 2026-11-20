-- nas-music shared schema v7 (SQLite, WAL mode). Contract between server core and nasmusic.ai.
-- v2 (docs/v2.md §2): multi-user. The music library and audio analysis are shared; everything about
-- listening is per user. v3 (docs/v3-ux.md phase 1) adds pins, toget, catalog_items and queue source/radio
-- columns. v4 adds lyrics_cache (LRCLIB) and bug_reports. v5 adds admin tag overrides (tracks.file_tags,
-- tag_overrides, tag_edits), collaborative playlists (playlist_members, playlist_activity, playlists.rev;
-- playlist_tracks rebuilt with entry ids and a fractional REAL position), release_radar, blends,
-- blend_members and blend_tracks (Release Radar, Blend; Wrapped needs no table), and ReplayGain/loudness
-- columns on tracks. v6 adds explicit-content flags (track_explicit, tag_overrides field 'explicit'),
-- family mode (family_mode), automatic tag fixing (fingerprints, acoustid_lookups, mb_releases,
-- autotag_suggestions), MusicBrainz-id override fields in tag_overrides, and hidden duplicates
-- (duplicate_hidden; tracks.missing = 2), Jam (jams, jam_guests, jam_requests, jam_votes), and AI radio
-- (radio_sessions.station_key/params, radio_stations, radio_feedback). v7 adds track_dj (DJ automix analysis:
-- beat grid, Camelot key, mix-in/mix-out points, energy curve). Older databases are upgraded by
-- nasmusic.db.migrations.
-- Server core writes: users, invites, artists, albums, tracks, user_tracks, events, devices, pair_codes,
--   playlists, queues, lyrics_overrides, playback_state, user_settings.
-- AI writes: features, analysis_jobs, taste_snapshots, mixes, mix_tracks, suggestions (+ suggestions.status
--   is also written by server core when a user answers), release_radar, blend_tracks (+ blends.match/built_at),
--   radio_sessions, radio_stations, radio_feedback, track_dj.
-- Server core writes blends / blend_members (membership and consent); the AI only reads them.
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
    -- 0 = present; 1 = file vanished on last scan (kept for history); 2 (v6) = hidden by an admin as a
    -- duplicate copy (see duplicate_hidden). Everything that filters `missing = 0` skips both.
    missing         INTEGER NOT NULL DEFAULT 0,
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
    folder          TEXT NOT NULL DEFAULT '',  -- relative directory ('' = library root)
    -- v5: the tags as read from the file (JSON). The columns above hold the EFFECTIVE tags: file tags with
    -- tag_overrides applied, so every reader (search, API, AI) sees admin edits without knowing about them.
    -- NULL = not read since v5; the server re-reads the file (read-only) when it needs them.
    file_tags       TEXT,
    -- v5 volume levelling (ReplayGain 2.0 scale: reference -18 LUFS). Gains in dB, peaks linear (1.0 = full scale).
    -- From REPLAYGAIN_* / R128_* tags when the file has them (rg_source 'tags'), else measured with ffmpeg's
    -- ebur128 filter by the `loudness` job (rg_source 'ebur128'). rg_source NULL = not measured yet.
    rg_track_gain   REAL,
    rg_track_peak   REAL,
    rg_album_gain   REAL,
    rg_album_peak   REAL,
    rg_source       TEXT,
    loudness_lufs   REAL               -- integrated loudness of the track (measured, or derived from the gain)
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
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,   -- the owner
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    rev         INTEGER NOT NULL DEFAULT 0   -- v5: bumped on every change (live sync / stale-client checks)
);
CREATE INDEX IF NOT EXISTS playlists_user ON playlists(user_id);

-- v5: one row per entry (the same track may appear twice). `position` is a fractional index: order by it;
-- inserting between two entries takes the midpoint, so concurrent edits never renumber other entries.
CREATE TABLE IF NOT EXISTS playlist_tracks (
    id          TEXT PRIMARY KEY,          -- entry id (uuid4 hex)
    playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    position    REAL NOT NULL,
    track_id    TEXT NOT NULL REFERENCES tracks(id),
    added_at    TEXT NOT NULL,
    added_by    TEXT REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS playlist_tracks_order ON playlist_tracks(playlist_id, position);
CREATE INDEX IF NOT EXISTS playlist_tracks_track ON playlist_tracks(track_id);

-- v5: collaborative playlists. The owner (playlists.user_id) is not listed here.
CREATE TABLE IF NOT EXISTS playlist_members (
    playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('editor','viewer')),
    added_by    TEXT REFERENCES users(id) ON DELETE SET NULL,
    added_at    TEXT NOT NULL,
    PRIMARY KEY (playlist_id, user_id)
);
CREATE INDEX IF NOT EXISTS playlist_members_user ON playlist_members(user_id);

CREATE TABLE IF NOT EXISTS playlist_activity (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    user_id     TEXT REFERENCES users(id) ON DELETE SET NULL,
    at          TEXT NOT NULL,
    action      TEXT NOT NULL,             -- create, rename, add, remove, move, member_add, member_role, member_remove, leave
    track_id    TEXT,
    detail      TEXT                       -- JSON
);
CREATE INDEX IF NOT EXISTS playlist_activity_pl ON playlist_activity(playlist_id, id);

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

-- ---------- v4: LRCLIB lyrics cache, bug reports ----------

-- Lyrics fetched from lrclib.net for tracks whose files have none (shared; not per user).
CREATE TABLE IF NOT EXISTS lyrics_cache (
    track_id     TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    status       TEXT NOT NULL CHECK (status IN ('found','not_found','instrumental')),
    synced       INTEGER NOT NULL DEFAULT 0,
    raw          TEXT,                 -- LRC (synced) or plain text
    lrclib_id    INTEGER,
    fetched_at   TEXT NOT NULL,
    retry_after  TEXT                  -- not_found/instrumental: ask again after this time
);

CREATE TABLE IF NOT EXISTS bug_reports (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id   TEXT,
    created_at  TEXT NOT NULL,
    app_version TEXT,
    platform    TEXT,
    description TEXT,
    logs        TEXT NOT NULL,
    crash       TEXT,
    extra       TEXT,                  -- JSON
    bytes       INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed'))
);
CREATE INDEX IF NOT EXISTS bug_reports_created ON bug_reports(created_at);

-- ---------- v5: admin tag edits (the library is read-only, so edits live here) ----------

-- One row per overridden field. `value` is JSON (string, integer or null = "clear this tag").
-- Fields: title, artist, album_artist, album, track_no, disc_no, year, genre, lyrics (raw LRC/plain text)
-- and cover (sha256 of an uploaded image stored under data_dir/art/uploads/). v6: explicit ("explicit" or
-- "clean": an admin's verdict, which beats every automatic source in track_explicit); mb_track (recording
-- MBID -> tracks.mbid), mb_releasegroup (-> albums.mbid), mb_artist, mb_albumartist (artist MBIDs; these
-- change artist ids to 'mb:<mbid>'), written by accepted automatic tag suggestions.
CREATE TABLE IF NOT EXISTS tag_overrides (
    track_id   TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    field      TEXT NOT NULL CHECK (field IN ('title','artist','album_artist','album','track_no','disc_no',
                                              'year','genre','lyrics','cover','explicit','mb_track',
                                              'mb_releasegroup','mb_artist','mb_albumartist')),
    value      TEXT NOT NULL,
    updated_by TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (track_id, field)
);
CREATE INDEX IF NOT EXISTS tag_overrides_field ON tag_overrides(field);

-- Audit log of tag edits and reverts (one row per track and field).
CREATE TABLE IF NOT EXISTS tag_edits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id   TEXT NOT NULL,              -- one admin action (a bulk edit is one batch)
    track_id   TEXT NOT NULL REFERENCES tracks(id) ON DELETE CASCADE,
    user_id    TEXT REFERENCES users(id) ON DELETE SET NULL,
    at         TEXT NOT NULL,
    action     TEXT NOT NULL CHECK (action IN ('set','revert')),
    field      TEXT NOT NULL,
    old_value  TEXT,                       -- JSON, effective value before
    new_value  TEXT                        -- JSON, effective value after
);
CREATE INDEX IF NOT EXISTS tag_edits_track ON tag_edits(track_id, id);
CREATE INDEX IF NOT EXISTS tag_edits_at ON tag_edits(at);

-- ---------- v6: explicit-content flags + family mode ----------

-- One row per track that the `explicit` job (nasmusic.library.explicit) has looked at; no row = unknown.
-- `status` is the effective verdict, in priority order: an admin's tag override (tag_overrides field
-- 'explicit'), the file's tags (iTunes rtng/ITUNESADVISORY, EXPLICIT, "(Explicit)"/"(Clean)" in title or
-- album), the matching Deezer track's explicit_lyrics flag, then a profanity word list run over the
-- track's lyrics (embedded, admin or cached LRCLIB), which only ever gives 'probable'.
-- Everyone filtering by it goes through nasmusic.db.family.
CREATE TABLE IF NOT EXISTS track_explicit (
    track_id     TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    status       TEXT NOT NULL DEFAULT 'unknown' CHECK (status IN ('explicit','probable','clean','unknown')),
    source       TEXT CHECK (source IN ('admin','tags','deezer','lyrics')),   -- what decided `status`
    tags_flag    TEXT CHECK (tags_flag IN ('explicit','clean')),              -- NULL = the file says nothing
    tags_mtime   REAL,                     -- tracks.mtime when the file was read (re-read when it changes)
    deezer_flag  TEXT CHECK (deezer_flag IN ('explicit','clean','not_found')),
    deezer_id    INTEGER,
    deezer_at    TEXT,                     -- not_found is asked again after 90 days
    lyrics_flag  TEXT CHECK (lyrics_flag IN ('probable','clean','none')),    -- none = no lyrics found
    lyrics_hits  INTEGER,
    lyrics_at    TEXT,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS track_explicit_status ON track_explicit(status);

-- Per-user family mode. Not in user_settings because a locked row must not be writable by the user:
-- only an admin (or someone with the PIN) can unlock it. No row = off.
CREATE TABLE IF NOT EXISTS family_mode (
    user_id       TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    enabled       INTEGER NOT NULL DEFAULT 0,
    hide_probable INTEGER NOT NULL DEFAULT 1,  -- also hide "probably explicit" (lyrics word list) tracks
    hide_unknown  INTEGER NOT NULL DEFAULT 0,  -- also hide tracks nobody has classified yet
    locked        INTEGER NOT NULL DEFAULT 0,  -- set by an admin: the user cannot loosen it without the PIN
    pin_hash      TEXT,                        -- scrypt hash of the unlock PIN (NULL = admin-only unlock)
    locked_by     TEXT REFERENCES users(id) ON DELETE SET NULL,
    updated_at    TEXT NOT NULL
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
    updated_at TEXT NOT NULL,
    station_key TEXT,                  -- v6: radio_stations.station_key ('moment:car', 'vibe:…', 'artist:<id>', 'random')
    params     TEXT                    -- v6: JSON {explanation, text, constraints, context} for moment / vibe radios
);
CREATE INDEX IF NOT EXISTS radio_sessions_user ON radio_sessions(user_id, updated_at);

-- v6 AI radio. A station is "the same radio" across sessions: the smart shuffle per device kind
-- ('moment:<device>'), a vibe ('vibe:<sorted chip ids>') or a seed ('<type>:<id>', 'random'). Its steering
-- (JSON {energy, variety, moreLike[], lessLike[], never[]}) applies to every pick and is remembered.
CREATE TABLE IF NOT EXISTS radio_stations (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_key TEXT NOT NULL,
    title       TEXT NOT NULL,
    steering    TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, station_key)
);

-- v6: every more/less-like-this and never-here steer, for the recommender (explicit feedback on track_id).
CREATE TABLE IF NOT EXISTS radio_feedback (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_key TEXT NOT NULL,
    track_id    TEXT NOT NULL,
    action      TEXT NOT NULL,           -- more_like | less_like | never_here
    scope       TEXT NOT NULL DEFAULT 'track',  -- track | artist
    at          TEXT NOT NULL,
    context     TEXT                     -- JSON EventContext of the steer, if the client sent one
);
CREATE INDEX IF NOT EXISTS radio_feedback_user ON radio_feedback(user_id, at);

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

-- v7 DJ automix analysis (global, AI writes; nasmusic.ai.djmix). One row per track, rewritten when the file
-- changes. Times are seconds into the file. The beat grid is constant-tempo: beat k is at beat_s + k*60/bpm;
-- grid_stable = 0 means the tempo drifts (live drummer, rubato) and clients must not beat-match the track.
CREATE TABLE IF NOT EXISTS track_dj (
    track_id       TEXT PRIMARY KEY REFERENCES tracks(id),
    version        TEXT NOT NULL,          -- 'dj-essentia-1' | 'dj-basic-1' (numpy fallback)
    status         TEXT NOT NULL CHECK (status IN ('done','failed')),
    error          TEXT,
    bpm            REAL,
    bpm_confidence REAL,                   -- 0..1, how well the beats fit the constant grid
    beat_s         REAL,                   -- first grid beat at or after 0
    downbeat_s     REAL,                   -- first bar start (beat 1) at or after 0
    beats_per_bar  INTEGER,
    grid_stable    INTEGER,                -- 1 = safe to beat-match
    key            TEXT,                   -- e.g. 'A minor'
    camelot        TEXT,                   -- e.g. '8A'
    key_strength   REAL,
    mix_in_s       REAL,                   -- where an incoming transition should start this track (a downbeat)
    mix_out_s      REAL,                   -- where an outgoing transition should start (a downbeat in the outro)
    intro_end_s    REAL,
    outro_start_s  REAL,
    last_beat_s    REAL,                   -- end of the music (before trailing silence)
    vocal_in_s     REAL,                   -- reserved: not detected yet (NULL)
    vocal_out_s    REAL,
    energy         REAL,                   -- 0..1 whole-track energy
    energy_curve   TEXT,                   -- JSON [0..1, ...], one value per curve_step_s
    curve_step_s   REAL,
    duration_s     REAL,
    analysed_at    TEXT NOT NULL
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

-- ---------- v5: Release Radar (AI-owned cache) ----------

-- New releases (MusicBrainz) by artists the user listens to. Rebuilt per user by the weekly job; whether
-- the library owns a release is decided when it is read, so a scan shows it as owned straight away.
CREATE TABLE IF NOT EXISTS release_radar (
    user_id            TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    release_group_mbid TEXT NOT NULL,
    title              TEXT NOT NULL,
    artist             TEXT NOT NULL,      -- MusicBrainz artist credit
    artist_mbid        TEXT,
    artist_id          TEXT,               -- the library artist that put it on the radar
    primary_type       TEXT,               -- Album, EP, Single
    release_date       TEXT,               -- YYYY[-MM[-DD]] first release date
    cover_url          TEXT,               -- Cover Art Archive release-group front
    score              REAL NOT NULL DEFAULT 0,   -- how much the user plays the artist (0..1)
    created_at         TEXT NOT NULL,
    PRIMARY KEY (user_id, release_group_mbid)
);

-- ---------- v5: Blend (shared playlist of two or more users) ----------

-- Membership and consent are written by server core; nobody is in a blend without accepting.
CREATE TABLE IF NOT EXISTS blends (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_by  TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    match       TEXT,                      -- AI: JSON {"pct": 0-100, "pairs": [{"a","b","pct"}]}
    built_at    TEXT                       -- AI: last time blend_tracks were rebuilt
);

CREATE TABLE IF NOT EXISTS blend_members (
    blend_id    TEXT NOT NULL REFERENCES blends(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    status      TEXT NOT NULL CHECK (status IN ('invited','joined','declined','left')),
    invited_by  TEXT REFERENCES users(id) ON DELETE SET NULL,
    invited_at  TEXT NOT NULL,
    joined_at   TEXT,
    left_at     TEXT,
    PRIMARY KEY (blend_id, user_id)
);
CREATE INDEX IF NOT EXISTS blend_members_user ON blend_members(user_id, status);

-- AI-owned: the blend's current tracks. Only members with status 'joined' contribute.
CREATE TABLE IF NOT EXISTS blend_tracks (
    blend_id    TEXT NOT NULL REFERENCES blends(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    track_id    TEXT NOT NULL REFERENCES tracks(id),
    for_users   TEXT NOT NULL,             -- JSON [user_id, ...] whose taste put it here (first = main)
    reason      TEXT NOT NULL,             -- e.g. "top" | "shared" | "match" (clients word it)
    score       REAL,
    PRIMARY KEY (blend_id, position)
);

-- ---------- v6: automatic tag fixing (AcoustID + MusicBrainz) and duplicates (server core writes all) ----------

-- Chromaprint fingerprint of the first 2 minutes of each file. Recomputed when tracks.mtime changes.
CREATE TABLE IF NOT EXISTS fingerprints (
    track_id     TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    mtime        REAL NOT NULL,            -- tracks.mtime the fingerprint was computed from
    duration_s   INTEGER,                  -- as reported by fpcalc
    fingerprint  TEXT,                     -- compressed (AcoustID format); NULL when fpcalc failed
    raw          BLOB,                     -- little-endian uint32 sub-fingerprints (duplicate finder)
    error        TEXT,
    computed_at  TEXT NOT NULL
);

-- AcoustID lookup per fingerprint (cached; redone only when the fingerprint changes).
CREATE TABLE IF NOT EXISTS acoustid_lookups (
    track_id     TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    mtime        REAL NOT NULL,            -- fingerprints.mtime it was looked up for
    status       TEXT NOT NULL CHECK (status IN ('ok','no_match','error')),
    acoustid     TEXT,                     -- best AcoustID track id
    score        REAL,                     -- best AcoustID score 0..1
    results      TEXT,                     -- JSON: trimmed candidates [{recording, score, releases:[...]}]
    error        TEXT,
    looked_up_at TEXT NOT NULL,
    suggested_at TEXT                      -- when suggestions were last built from it (NULL = to do)
);

-- Cached MusicBrainz release lookups (JSON as returned by /ws/2/release/<mbid>).
CREATE TABLE IF NOT EXISTS mb_releases (
    mbid        TEXT PRIMARY KEY,
    data        TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);

-- One suggestion per track. `suggested` holds DB field names -> values (title, artist, album_artist, album,
-- track_no, disc_no, year, genre, mb_track, mb_releasegroup, mb_artist, mb_albumartist); the current values are
-- read live from tracks, so the diff stays right after manual edits. Accepting writes tag_overrides
-- (audited in tag_edits, batch_id here); nothing is ever written to the files.
CREATE TABLE IF NOT EXISTS autotag_suggestions (
    track_id        TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    group_id        TEXT NOT NULL,         -- album group (the track's album id when matched)
    release_mbid    TEXT,
    recording_mbid  TEXT,
    confidence      REAL NOT NULL,         -- 0..1
    group_confidence REAL NOT NULL,        -- 0..1, the album group's
    suggested       TEXT NOT NULL,         -- JSON {db_field: value}
    cover_url       TEXT,                  -- Cover Art Archive front image of the release, if any
    detail          TEXT,                  -- JSON: acoustidScore, durationDiffS, releaseTitle, ...
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
    created_at      TEXT NOT NULL,
    decided_at      TEXT,
    decided_by      TEXT REFERENCES users(id) ON DELETE SET NULL,
    batch_id        TEXT                   -- tag_edits.batch_id of the accept
);
CREATE INDEX IF NOT EXISTS autotag_suggestions_group ON autotag_suggestions(status, group_id);

-- Duplicate copies an admin chose to hide (tracks.missing = 2). Files are never deleted; unhide = delete row.
CREATE TABLE IF NOT EXISTS duplicate_hidden (
    track_id    TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    kept_id     TEXT REFERENCES tracks(id) ON DELETE SET NULL,
    hidden_by   TEXT REFERENCES users(id) ON DELETE SET NULL,
    hidden_at   TEXT NOT NULL
);

-- v6 Jam (listen together, docs/jam.md): a host shares a join link; guests (no account, or signed-in users of
-- this server) add songs to the host's queue. Tokens are stored as SHA-256 hashes; join codes in clear (the
-- host can show the link again). All rows die with the host's account.
CREATE TABLE IF NOT EXISTS jams (
    id              TEXT PRIMARY KEY,
    host_user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    queue_id        TEXT,                      -- host queue guests add to (follows what the host plays)
    join_code       TEXT UNIQUE,               -- NULL = link closed (no new joins)
    join_expires_at TEXT,
    settings        TEXT NOT NULL,             -- JSON {approval, allowPlayNext, maxPerGuest}
    created_at      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,             -- hard end; every guest token dies with it
    ended_at        TEXT
);
CREATE INDEX IF NOT EXISTS jams_host ON jams(host_user_id, ended_at);

CREATE TABLE IF NOT EXISTS jam_guests (
    id           TEXT PRIMARY KEY,
    jam_id       TEXT NOT NULL REFERENCES jams(id) ON DELETE CASCADE,
    user_id      TEXT REFERENCES users(id) ON DELETE CASCADE,  -- signed-in member joining as themselves
    name         TEXT NOT NULL,
    token_hash   TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    last_seen_at TEXT,
    left_at      TEXT,                         -- left or kicked: the token is dead
    kicked       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jam_guests_jam ON jam_guests(jam_id);

-- Songs guests asked for. 'queued' rows are in the host's queue (matched to entries by track id, in order).
CREATE TABLE IF NOT EXISTS jam_requests (
    id          TEXT PRIMARY KEY,
    jam_id      TEXT NOT NULL REFERENCES jams(id) ON DELETE CASCADE,
    guest_id    TEXT REFERENCES jam_guests(id) ON DELETE CASCADE,   -- NULL = the host
    track_id    TEXT NOT NULL REFERENCES tracks(id),
    mode        TEXT NOT NULL CHECK (mode IN ('end','next')),
    status      TEXT NOT NULL CHECK (status IN ('pending','queued','rejected','withdrawn','removed')),
    created_at  TEXT NOT NULL,
    decided_at  TEXT                       -- approved/rejected/queued time (attribution order)
);
CREATE INDEX IF NOT EXISTS jam_requests_jam ON jam_requests(jam_id, status);

CREATE TABLE IF NOT EXISTS jam_votes (
    jam_id    TEXT NOT NULL REFERENCES jams(id) ON DELETE CASCADE,
    guest_id  TEXT NOT NULL REFERENCES jam_guests(id) ON DELETE CASCADE,
    track_id  TEXT NOT NULL,
    value     INTEGER NOT NULL CHECK (value IN (-1, 1)),
    PRIMARY KEY (jam_id, guest_id, track_id)
);
