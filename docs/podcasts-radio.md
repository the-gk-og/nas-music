# Podcasts, audiobooks and internet radio

Spoken word and live radio live in **Library → Podcasts & more** (web and Android), with three tabs:
Podcasts · Audiobooks · Radio. They are deliberately kept apart from music:

- Their own tables (schema v7: `podcasts`, `podcast_episodes`, `podcast_subscriptions`, `audiobooks`,
  `audiobook_files`, `audiobook_chapters`, `audiobook_bookmarks`, `spoken_progress`, `web_stations`,
  `web_station_favourites`). Nothing is written to `events` / `user_tracks`, so the recommender, mixes,
  Wrapped, Last.fm/ListenBrainz scrobbling and smart downloads never see them.
- Their own player: the web player's `SpokenBar` and Android's `SpokenService` (a second Media3 session
  with speech audio attributes). Starting one pauses the other (audio focus / the web bar).
- Family mode hides shows, episodes and stations flagged explicit (`<itunes:explicit>`, the directory's
  flag, or Radio Browser tags like `explicit`/`adult`). Locked users get 404 on their streams too.

## Setup (config.toml)

```toml
# Audiobooks: a SECOND read-only mount. Folder = book, files = parts (natural order), embedded chapters.
audiobooks_root = "/audiobooks"

# Podcasts
podcast_poll_minutes = 60        # feed polling (ETag / If-Modified-Since)
podcast_keep_last = 3            # newest N episodes per show downloaded to /data/podcasts (0 = stream only)
podcast_storage_gb = 20          # cap; older downloads are dropped first
podcastindex_api_key = ""        # optional: free key + secret at https://api.podcastindex.org/signup
podcastindex_api_secret = ""
podcast_itunes_search = true     # iTunes Search API, discovery only (finds feed URLs; no key)

radio_browser_url = "https://all.api.radio-browser.info"
```

Docker: add the audiobooks folder read-only next to the music (commented line in `deploy/*/compose.yaml`):

```yaml
volumes:
  - /mnt/NAS/Shared/music:/music:ro
  - /mnt/NAS/Shared/audiobooks:/audiobooks:ro
  - nasmusic-data:/data
```

The `audiobooks` job runs after every library scan (cheap when nothing changed: it compares file names,
sizes and mtimes per folder). Run it now from Admin → Jobs. Podcast episode downloads land in the data
volume (`/data/podcasts/<showId>/`), never in the music or audiobook folders.

## How it works

- **Podcasts.** Subscribe by RSS URL or from search (Podcast Index when configured, plus iTunes).
  The `podcasts` job polls every subscribed feed, stores new episodes, then keeps each show's newest
  `keep_last` episodes downloaded within `podcast_storage_gb` (a show's own "Keep on server" overrides
  the default, shared by all its subscribers). `/stream/episode/{id}` serves the server copy, or proxies
  the feed's file with Range passed through, so seeking works and the phone never talks to podcast hosts.
  Chapters: Podlove `psc:chapters` in the feed, Podcasting 2.0 JSON chapters (fetched once), or the
  downloaded file's embedded chapters (ffprobe). Per-user, per-show speed and skip silence (Android).
  Android can save episodes on the phone, and downloads new episodes of shows marked "Download new
  episodes to this phone" while on the home network.
- **Audiobooks.** Folder = book (an audio file directly in the root is a one-file book). Titles/authors
  from tags (`album`/`albumartist`; narrator from `©nrt`/`TXXX:NARRATOR`/composer), covers from a folder
  image or the embedded picture, chapters from the files (m4b/mp4 chapters, ID3 CHAP) offset by each
  part's start, else one chapter per part. Positions are book-wide seconds. Bookmarks per user.
- **Internet radio.** Add a stream URL (`.pls`/`.m3u` playlists are followed; HLS is refused) or a
  Radio Browser result. Playback goes through the server (`/stream/station/{id}`), which strips the ICY
  metadata and remembers the current `StreamTitle` for `/stations/{id}/now-playing`, so LAN, tunnel and
  HTTPS-only web clients all work. Plays are reported to Radio Browser's click counter, as it asks.
- **Resume positions** sync through `PUT /spoken/progress` (batched; the newest `updatedAt` wins, so an
  offline device can't rewind progress made elsewhere) and `GET /spoken/progress?since=`. Clients keep
  an outbox of unsent positions. Reaching the last 30 s marks an episode played.
- **Casting.** `POST /cast/sign-spoken` returns signed URLs (`/cast/spoken/...` on the cast port) for an
  episode, each audiobook part, or a station (live). Clients that cast natively can load these; the
  server-side speaker queue (DLNA/Snapcast/HA) still only plays music queues.
