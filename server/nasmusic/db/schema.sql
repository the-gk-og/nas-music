-- nas-music shared schema v10 (SQLite, WAL mode). Contract between server core and nasmusic.ai.
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
-- (radio_sessions.station_key/params, radio_stations, radio_feedback). v7 adds voice control (voice_tokens,
-- voice_devices). Older databases are upgraded by nasmusic.db.migrations. Also v7 adds uploads (upload_access,
-- uploads, upload_sessions): files people upload live under config upload_root (a second, writable library
-- root; tracks.path '@uploads/<path>'). Also v7 adds smart playlists (smart_playlists + the
-- smart_playlist_tracks cache) and the artist-page metadata cache (artist_info). Older databases are upgraded
-- by nasmusic.db.migrations. Also v7 adds spoken word and live audio (podcasts, podcast_episodes,
-- podcast_subscriptions, audiobooks, audiobook_files, audiobook_chapters, audiobook_bookmarks, spoken_progress,
-- web_stations, web_station_favourites). These are deliberately apart from tracks/events: the recommender,
-- Wrapped and scrobbling never see them. Older databases are upgraded by nasmusic.db.migrations. Also v7 adds
-- track_dj (DJ automix analysis: beat grid, Camelot key, mix-in/mix-out points, energy curve). Older databases
-- are upgraded by nasmusic.db.migrations.
-- v8 adds toget_admin (the admin's check-off of users' want-list items) and continue_dismissed (Continue
-- listening items the user swiped away; an item comes back once it is played again after that).
-- v9 adds the lyrics search index (lyrics_index + the FTS5 table lyrics_fts), filled by the scan job.
-- v10 adds playlist folders (playlist_folders, playlist_folder_items: each user files the playlists they can
-- see, their own and shared ones, into their own folder tree) and classical-music fields on tracks (work,
-- movement, movement_no: from WORK/MOVEMENTNAME/MOVEMENT tags or a "Work: I. Movement" title).
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
    loudness_lufs   REAL,              -- integrated loudness of the track (measured, or derived from the gain)
    -- v10 classical music (nasmusic.library.classical). `work` is the composition a track belongs to, from the
    -- WORK tag (ID3 TIT1/GRP1, MP4 \xa9wrk) or a "Work: I. Movement" title; NULL = not read since v10
    -- (the scanner backfills it), '' = not part of a work. movement_no is the movement's number in the work.
    work            TEXT,
    movement        TEXT,
    movement_no     INTEGER
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

-- v7 voice control (Home Assistant Assist): per-user long-lived tokens that can ONLY call POST /voice/command
-- (not device tokens: they never authenticate anything else). Only the sha256 is stored.
CREATE TABLE IF NOT EXISTS voice_tokens (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    token_hash  TEXT NOT NULL UNIQUE,
    created_at  TEXT NOT NULL,
    last_used   TEXT,
    revoked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS voice_tokens_user ON voice_tokens(user_id);

-- v7: what a user calls a Connect device / speaker by voice ("bedroom", "Stacey's room"), and the device voice
-- commands play on when no room is named and nothing is playing. device_id = a devices.id or a speaker id
-- ('spk:<castType>:<id>'), so no foreign key.
CREATE TABLE IF NOT EXISTS voice_devices (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    device_id   TEXT NOT NULL,
    aliases     TEXT NOT NULL DEFAULT '[]',   -- JSON list of strings
    is_default  INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, device_id)
);

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
    context     TEXT,                  -- JSON EventContext (docs/v2.md §10), only enabled signals
    -- v7: NULL = recorded by a client; 'listenbrainz' = imported listen history (never scrobbled back; the user
    -- can remove every imported event in one go). Imported ids are 'import:<service>:<user>:<ts>:<track>'.
    imported    TEXT
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

-- v10: playlist folders. Each user has their own tree (parent_id NULL = top level); `position` orders siblings.
-- playlist_folder_items files a playlist the user can see (own or shared) into one of their folders; a
-- playlist with no row is at the top level. Deleting a folder moves its contents up (nasmusic.app.playlist_folders).
CREATE TABLE IF NOT EXISTS playlist_folders (
    id          TEXT PRIMARY KEY,          -- uuid4 hex
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    parent_id   TEXT REFERENCES playlist_folders(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    position    REAL NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS playlist_folders_user ON playlist_folders(user_id, parent_id);

CREATE TABLE IF NOT EXISTS playlist_folder_items (
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    playlist_id TEXT NOT NULL REFERENCES playlists(id) ON DELETE CASCADE,
    folder_id   TEXT NOT NULL REFERENCES playlist_folders(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, playlist_id)
);
CREATE INDEX IF NOT EXISTS playlist_folder_items_folder ON playlist_folder_items(folder_id);

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

-- v8: the admin's check-off of a want-list item ("added to the library"). A row = checked; un-check deletes it.
-- Kept apart from toget.status, which is the user's own want/got/dismissed.
CREATE TABLE IF NOT EXISTS toget_admin (
    toget_id   TEXT PRIMARY KEY REFERENCES toget(id) ON DELETE CASCADE,
    note       TEXT,
    done_by    TEXT REFERENCES users(id) ON DELETE SET NULL,
    done_at    TEXT NOT NULL,
    auto       INTEGER NOT NULL DEFAULT 0   -- 1 = checked from the "found in library" suggestion
);

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

-- ---------- v7: uploads (server core; nasmusic/library/uploads.py) ----------
-- Who may upload and how much. No row = defaults: admins may upload, others may not; quota from config.
CREATE TABLE IF NOT EXISTS upload_access (
    user_id     TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    allowed     INTEGER,                -- NULL = default (admins only)
    quota_bytes INTEGER,                -- NULL = config upload_quota_gb; 0 = unlimited
    updated_at  TEXT NOT NULL
);

-- One row per uploaded file under upload_root. The track row (tracks.path = '@uploads/' || path,
-- tracks.id = sha1 of that) appears after the next scan; deleting an upload removes the file.
-- visibility 'private' = only the uploader sees the track (nasmusic.db.family applies it everywhere).
CREATE TABLE IF NOT EXISTS uploads (
    id            TEXT PRIMARY KEY,
    user_id       TEXT REFERENCES users(id) ON DELETE SET NULL,
    path          TEXT NOT NULL UNIQUE,  -- relative to upload_root: <username>/<Artist>/<Album>/<NN Title>.<ext>
    track_id      TEXT NOT NULL,
    bytes         INTEGER NOT NULL,
    original_name TEXT NOT NULL,
    format        TEXT NOT NULL,         -- flac | mp3 | m4a | ogg | opus | wav (sniffed, not the file name)
    visibility    TEXT NOT NULL DEFAULT 'everyone' CHECK (visibility IN ('everyone', 'private')),
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS uploads_user ON uploads(user_id);
CREATE INDEX IF NOT EXISTS uploads_track ON uploads(track_id);
CREATE INDEX IF NOT EXISTS uploads_visibility ON uploads(visibility, user_id);

-- Resumable (chunk + offset) uploads in progress; bytes go to upload_root/.incoming/<id>.part.
CREATE TABLE IF NOT EXISTS upload_sessions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename    TEXT NOT NULL,
    bytes       INTEGER NOT NULL,
    received    INTEGER NOT NULL DEFAULT 0,
    visibility  TEXT NOT NULL DEFAULT 'everyone' CHECK (visibility IN ('everyone', 'private')),
    status      TEXT NOT NULL DEFAULT 'uploading' CHECK (status IN ('uploading', 'done', 'failed')),
    error       TEXT,
    upload_id   TEXT,                    -- uploads.id once done
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS upload_sessions_user ON upload_sessions(user_id, status);

-- ---------- v7: smart playlists (rule-based, per user) ----------
-- `definition` is JSON {match, rules, sort, limit} (api/openapi.yaml SmartPlaylistDefinition), evaluated by
-- nasmusic.app.smart in SQL. The result is cached in smart_playlist_tracks; `stamp` records what it was
-- computed from (rules, user's listening, library, family mode, the date) so a stale cache is recomputed on read
-- and by the nightly `smart` job. The AI reads smart_playlist_tracks for smart-playlist radio seeds.
CREATE TABLE IF NOT EXISTS smart_playlists (
    id            TEXT PRIMARY KEY,           -- 'sp' + uuid4 hex
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    definition    TEXT NOT NULL,              -- JSON
    preset        TEXT,                       -- preset id it was created from, if any
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    refreshed_at  TEXT,                       -- when the cache was last computed
    stamp         TEXT,                       -- cache key of the last computation
    track_count   INTEGER NOT NULL DEFAULT 0,
    duration_s    REAL NOT NULL DEFAULT 0,
    rev           INTEGER NOT NULL DEFAULT 0  -- bumped whenever the cached list changes
);
CREATE INDEX IF NOT EXISTS smart_playlists_user ON smart_playlists(user_id);

CREATE TABLE IF NOT EXISTS smart_playlist_tracks (
    smart_playlist_id TEXT NOT NULL REFERENCES smart_playlists(id) ON DELETE CASCADE,
    position          INTEGER NOT NULL,
    track_id          TEXT NOT NULL REFERENCES tracks(id),
    PRIMARY KEY (smart_playlist_id, position)
);
CREATE INDEX IF NOT EXISTS smart_playlist_tracks_track ON smart_playlist_tracks(track_id);

-- ---------- v7: artist pages (shared metadata cache) ----------
-- From MusicBrainz (+ Wikidata, Wikipedia, Wikimedia Commons). Refreshed rarely (nasmusic.library.artistinfo).
-- The photo (only ever a Wikimedia Commons file, with its licence and attribution) is cached under
-- <data_dir>/artist-art/<artist_id>.jpg.
CREATE TABLE IF NOT EXISTS artist_info (
    artist_id     TEXT PRIMARY KEY REFERENCES artists(id) ON DELETE CASCADE,
    status        TEXT NOT NULL CHECK (status IN ('ok','not_found','error')),
    mbid          TEXT,                       -- the MusicBrainz artist it resolved to
    data          TEXT,                       -- JSON ArtistInfo (bio, genres, lifeSpan, links, image credits)
    error         TEXT,
    fetched_at    TEXT NOT NULL,
    refresh_after TEXT NOT NULL               -- next lookup no earlier than this
);

-- ---------- v7: scrobbling (ListenBrainz / Last.fm) and "new music added" notifications ----------

-- One row per user and service. `token` is the ListenBrainz user token or the Last.fm session key (secret:
-- never returned by the API). Last.fm's web-auth flow keeps the unapproved request token in `pending_token`.
CREATE TABLE IF NOT EXISTS scrobble_accounts (
    user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    service        TEXT NOT NULL CHECK (service IN ('listenbrainz','lastfm')),
    enabled        INTEGER NOT NULL DEFAULT 0,
    token          TEXT,
    username       TEXT,                    -- the account name on that service
    now_playing    INTEGER NOT NULL DEFAULT 1,
    pending_token  TEXT,
    pending_at     TEXT,
    connected_at   TEXT,
    last_scrobble_at TEXT,                  -- when the last scrobble was accepted
    last_error     TEXT,
    last_error_at  TEXT,
    needs_reauth   INTEGER NOT NULL DEFAULT 0,  -- the service rejected the token: paused until reconnected
    PRIMARY KEY (user_id, service)
);

-- Outbox: one row per (user, service, play). Filled from qualifying listening events (scrobble rules: track
-- >= 30 s, played >= half or >= 4 min); sent in batches with exponential backoff. Dedupe: the event id is the
-- key, and a second event for the same track within the same playthrough (listened_at closer than half the
-- track) is skipped.
CREATE TABLE IF NOT EXISTS scrobble_queue (
    user_id        TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    service        TEXT NOT NULL CHECK (service IN ('listenbrainz','lastfm')),
    event_id       TEXT NOT NULL,
    track_id       TEXT NOT NULL REFERENCES tracks(id),
    listened_at    INTEGER NOT NULL,        -- unix seconds the playthrough started
    status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','sent','failed','ignored')),
    attempts       INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error     TEXT,
    created_at     TEXT NOT NULL,
    sent_at        TEXT,
    PRIMARY KEY (user_id, service, event_id)
);
CREATE INDEX IF NOT EXISTS scrobble_queue_due ON scrobble_queue(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS scrobble_queue_track ON scrobble_queue(user_id, service, track_id, listened_at);

-- In-app notifications (bell list). kind 'new_music': one per user per library scan that added tracks;
-- data = {"trackIds": [...], "albumIds": [...], "tracks": n, "albums": n, "artists": [names]}.
CREATE TABLE IF NOT EXISTS notifications (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL CHECK (kind IN ('new_music')),
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    data        TEXT NOT NULL DEFAULT '{}',   -- JSON
    created_at  TEXT NOT NULL,
    read_at     TEXT
);
CREATE INDEX IF NOT EXISTS notifications_user ON notifications(user_id, created_at);

-- ---------- v7 spoken word + internet radio (docs/podcasts-radio.md) ----------
-- Server core only. NOT music: the AI, Wrapped and scrobbling never read these tables.

-- One row per feed (shared by every subscriber). id = sha1(feed_url)[:16].
CREATE TABLE IF NOT EXISTS podcasts (
    id               TEXT PRIMARY KEY,
    feed_url         TEXT NOT NULL UNIQUE,
    title            TEXT NOT NULL,
    author           TEXT,
    description      TEXT,
    link             TEXT,
    language         TEXT,
    image_url        TEXT,
    explicit         INTEGER NOT NULL DEFAULT 0,   -- <itunes:explicit> / directory flag (family mode hides)
    keep_last        INTEGER,                      -- server downloads: newest N episodes; NULL = config default, 0 = stream only
    etag             TEXT,
    last_modified    TEXT,
    last_polled_at   TEXT,
    last_error       TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS podcast_episodes (
    id              TEXT PRIMARY KEY,              -- sha1(podcast_id + guid)[:16]
    podcast_id      TEXT NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    guid            TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT,
    link            TEXT,
    published_at    TEXT,
    duration_s      REAL,
    enclosure_url   TEXT NOT NULL,
    enclosure_type  TEXT,
    enclosure_bytes INTEGER,
    image_url       TEXT,
    explicit        INTEGER NOT NULL DEFAULT 0,
    season          INTEGER,
    episode         INTEGER,
    chapters_url    TEXT,                          -- Podcasting 2.0 JSON chapters (fetched on demand)
    chapters        TEXT,                          -- JSON [{start, title, url?, img?}] (feed psc:chapters or fetched)
    local_path      TEXT,                          -- relative to data_dir/podcasts when downloaded on the server
    local_bytes     INTEGER,
    downloaded_at   TEXT,
    download_error  TEXT,
    UNIQUE (podcast_id, guid)
);
CREATE INDEX IF NOT EXISTS podcast_episodes_pub ON podcast_episodes(podcast_id, published_at);

CREATE TABLE IF NOT EXISTS podcast_subscriptions (
    user_id       TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    podcast_id    TEXT NOT NULL REFERENCES podcasts(id) ON DELETE CASCADE,
    subscribed_at TEXT NOT NULL,
    speed         REAL NOT NULL DEFAULT 1.0,       -- playback speed for this show
    skip_silence  INTEGER NOT NULL DEFAULT 0,
    auto_download INTEGER NOT NULL DEFAULT 0,      -- phone: download new episodes on sync
    PRIMARY KEY (user_id, podcast_id)
);

-- Audiobooks: a read-only folder (config audiobooks_root). Folder = book, files = parts. id = sha1(folder)[:16].
CREATE TABLE IF NOT EXISTS audiobooks (
    id          TEXT PRIMARY KEY,
    folder      TEXT NOT NULL UNIQUE,              -- relative to audiobooks_root ('' parts = a loose file's path)
    title       TEXT NOT NULL,
    author      TEXT,
    narrator    TEXT,
    description TEXT,
    year        INTEGER,
    duration_s  REAL NOT NULL DEFAULT 0,
    cover_path  TEXT,                              -- relative to audiobooks_root; NULL = embedded or none
    signature   TEXT NOT NULL,                     -- file names + sizes + mtimes: rescan when it changes
    missing     INTEGER NOT NULL DEFAULT 0,
    added_at    TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audiobook_files (
    book_id    TEXT NOT NULL REFERENCES audiobooks(id) ON DELETE CASCADE,
    idx        INTEGER NOT NULL,
    path       TEXT NOT NULL,                      -- relative to audiobooks_root
    duration_s REAL NOT NULL DEFAULT 0,
    start_s    REAL NOT NULL DEFAULT 0,            -- offset of this part in the whole book
    bytes      INTEGER,
    PRIMARY KEY (book_id, idx)
);

CREATE TABLE IF NOT EXISTS audiobook_chapters (
    book_id TEXT NOT NULL REFERENCES audiobooks(id) ON DELETE CASCADE,
    idx     INTEGER NOT NULL,
    title   TEXT NOT NULL,
    start_s REAL NOT NULL,                         -- book-wide seconds
    PRIMARY KEY (book_id, idx)
);

CREATE TABLE IF NOT EXISTS audiobook_bookmarks (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    book_id    TEXT NOT NULL REFERENCES audiobooks(id) ON DELETE CASCADE,
    position_s REAL NOT NULL,
    note       TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audiobook_bookmarks_user ON audiobook_bookmarks(user_id, book_id);

-- Per-user resume position for episodes and books (book position is book-wide seconds). Newest write wins.
CREATE TABLE IF NOT EXISTS spoken_progress (
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL CHECK (kind IN ('episode','book')),
    item_id    TEXT NOT NULL,
    position_s REAL NOT NULL DEFAULT 0,
    duration_s REAL,
    played     INTEGER NOT NULL DEFAULT 0,
    speed      REAL,                               -- books: per-book speed (episodes use the show's)
    device_id  TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, kind, item_id)
);
CREATE INDEX IF NOT EXISTS spoken_progress_recent ON spoken_progress(user_id, updated_at);

-- Internet radio stations (shared catalogue of what anyone added) + per-user favourites.
CREATE TABLE IF NOT EXISTS web_stations (
    id           TEXT PRIMARY KEY,                 -- sha1(stream_url)[:16]
    stream_url   TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,
    homepage     TEXT,
    favicon      TEXT,
    country      TEXT,
    tags         TEXT,                             -- comma-separated
    codec        TEXT,
    bitrate      INTEGER,
    explicit     INTEGER NOT NULL DEFAULT 0,
    radiobrowser_uuid TEXT,
    created_by   TEXT REFERENCES users(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_station_favourites (
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    station_id TEXT NOT NULL REFERENCES web_stations(id) ON DELETE CASCADE,
    added_at   TEXT NOT NULL,
    last_played_at TEXT,
    PRIMARY KEY (user_id, station_id)
);

-- ---------- v8: Continue listening ----------

-- Items the user dismissed from the Continue listening shelf. kind: queue | episode | book. An item shows again
-- when its own updated_at (queue / spoken_progress) is newer than dismissed_at.
CREATE TABLE IF NOT EXISTS continue_dismissed (
    user_id      TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind         TEXT NOT NULL CHECK (kind IN ('queue','episode','book')),
    item_id      TEXT NOT NULL,
    dismissed_at TEXT NOT NULL,
    PRIMARY KEY (user_id, kind, item_id)
);

-- ---------- v9: lyrics search ----------

-- One row per present track once the scan job has looked for its lyrics. The indexed text is what everyone
-- sees by default: an admin's edit (tag_overrides field 'lyrics'), else the file's own lyrics (embedded tags or
-- a sidecar .lrc), else cached LRCLIB lyrics; timestamps stripped. Per-user lyrics edits are private and never
-- indexed. source 'none' = looked, found nothing. Re-read when tracks.mtime changes, the admin edit or the
-- LRCLIB cache is newer than indexed_at, or a full scan runs.
CREATE TABLE IF NOT EXISTS lyrics_index (
    track_id   TEXT PRIMARY KEY REFERENCES tracks(id) ON DELETE CASCADE,
    source     TEXT NOT NULL CHECK (source IN ('none','library','embedded','lrc_file','lrclib')),
    mtime      REAL,                   -- tracks.mtime when the file was read
    indexed_at TEXT NOT NULL
);

-- Full-text index over the same lyrics (one row per track with source != 'none'; rowid is not meaningful).
CREATE VIRTUAL TABLE IF NOT EXISTS lyrics_fts USING fts5(
    track_id UNINDEXED,
    body,
    tokenize = 'unicode61 remove_diacritics 2'
);
