# nas-music v3 — UI/UX redesign (phase 1)

Owner's answers (2026-11-19) turned into a spec. Builds on `docs/v2.md` (accounts, Connect, lyrics,
EQ, data settings all stay). Phase 1 is the redesign plus the features it needs to have something to
show; the rest of the feature list is in §8 for later phases.

## 1. Principles

- **Keep from Musicolet: the player and the queues** — Now Playing controls/layout, multiple named
  queues with their selector and drag reordering, tap-to-play-list-as-queue behaviour. Everything
  else is redesigned: browsing feels like a **mix of Spotify and YouTube Music**.
- **Colours: true black + one accent** (green). The only exception: **Now Playing uses a dark,
  blurred version of the cover as its background** (heavily blurred, darkened so text stays AA).
- **Motion is customisable**: Settings → Appearance → Animations: `Off` (instant), `Quick`
  (default: cover grows from mini player into Now Playing, short fades, ≤ 200 ms), `Expressive`
  (parallax headers, animated lyrics, longer transitions). Also respect the OS "reduce motion".
- **Every screen type is designed, not stretched**: phone, tablet/foldable, desktop/web. See §6.
- **Global search everywhere** (search bar/shortcut on every screen) that covers your library,
  playlists, queues **and music you don't own yet** (§4).
- **Switchable views**: grid ↔ list toggle per Library section and on album pages (§3), remembered
  per device; a "Default view" global setting.

## 2. Navigation (phone)

Five bottom tabs + a **mini player bar** above them whenever something is playing:

| Tab | What it is |
| --- | --- |
| **Home** | Quick access (§3.1) |
| **For You** | AI picks and discovery (§3.2) — this is the "Discover" tab |
| **Search** | Global search (§4) |
| **Library** | Playlists, Albums, Artists, Genres, Folders, Smart lists — filter chips at the top (§3.3) |
| **Queue** | Musicolet's multi-queue screen (unchanged behaviour from v2) |

Mini player: cover, title/artist, play/pause, next; progress line along its top edge; swipe it
left/right to skip; tap or swipe up → Now Playing (cover animates up per motion setting). When
another device is active it reads "Playing on <device>" and controls that device.

App opens on **Home**. Settings, Equalizer, Devices, Account, Admin live under the profile avatar in
the top-right of Home (and in the desktop sidebar).

## 3. Screens

### 3.1 Home (quick access)
Top: greeting + avatar. Shelves (each hides when empty), user can reorder/hide shelves:
1. **Start radio** — one big button: "Start a random radio" (seeded from your taste; §5).
2. **Your devices** — other online devices with what they're playing; one tap = take over here.
3. **Recently played** — 2-row quick-pick grid (albums, playlists, mixes, radios) like Spotify.
4. **Continue listening** — unfinished albums/queues (and later podcasts/audiobooks).
5. **Pinned** — playlists/albums/artists the user pinned (long-press anything → "Pin to Home").

### 3.2 For You
YouTube-Music-style horizontal shelves:
1. **Your mixes** — Daily Mix, Morning/Evening, Deep Cuts, context mixes (Gym, Drive, Late night).
2. **Radio** — "<Artist> radio", "<Song> radio" seeded from recent favourites.
3. **Because you played <X>** — 1–3 shelves of similar tracks/albums from your library.
4. **Release Radar** *(phase 2 data; shelf hidden until it exists)*.
5. **Suggestions** — weekly album suggestions with 30 s previews, Want / Not interested / Have.
6. **To get** — your wishlist of music you don't own (§4).
7. **Blends** and **Wrapped** *(phase 2; hidden until they exist)*.

### 3.3 Library
Filter chips: Playlists · Albums · Artists · Genres · Folders · Songs. Sort menu. Grid/list toggle
(remembered per chip). Smart lists (Favourites, Recently added, Recently played, Most played, Not
played) appear at the top of Playlists. "+ New playlist".

### 3.4 Album / artist / playlist pages
Switchable between **Big header** (large cover, colour from the art bleeding into the black, Play +
Shuffle + Radio + ⋯ buttons, track list, "More by this artist") and **Simple list** (small header,
straight to the tracks, Musicolet-like). Toggle in the page's ⋯ menu; global default in Settings.
Artist page: top tracks, albums grid, "Artist radio", "Similar artists" (from discovery data).

### 3.5 Now Playing (Musicolet player, kept)
Layout from v2/Musicolet (cover, title/artist/album, action row ♡ ⓘ add-to-playlist ⋯, repeat/
shuffle, seek bar, volume / prev / play / next / EQ) on the **blurred-cover background**. Gestures:
- tap cover → lyrics (v2), **swipe cover left/right → next/previous**, **swipe down → minimise to the
  mini player**, **long-press cover → song menu** (radio, add to playlist/queue, info, go to album/artist).
- Devices button top-right. "Up next" peek at the bottom (swipe up → Queue tab).

### 3.6 Queue
Unchanged Musicolet multi-queue screen from v2.

## 4. Global search, including music you don't own ("To get")

One search box, results grouped: **Top result · Songs · Albums · Artists · Playlists · Not in your
library**. The last group searches an online catalogue so you can find music you don't have:
- Server endpoint proxies **MusicBrainz** (metadata) and **Deezer's public search API** (no key;
  gives 30-second preview clips and cover art). Results marked owned/not-owned by matching
  (MBID, else normalised artist+title/album).
- Not-owned items: ▶ play the 30 s **sample** (proxied by the server, never stored), **Add to To get**,
  "Find similar in my library".
- **To get list** (per user): albums and songs to acquire; status `want` / `got` (auto-marked `got`
  when the scanner finds it in the library) / `dismissed`; optional note; buy/search links
  (Bandcamp, Qobuz). Suggestions marked "Want" land here too.

## 5. Radio (needed by Home/For You in phase 1)

`POST /radio {seed: {type: "random"|"track"|"artist"|"album"|"playlist", id?}}` → creates an endless
queue named "<seed> radio": the AI picks ~25 tracks, and the client asks for more
(`POST /radio/{queueId}/more`) when 5 remain. Uses the per-user recommender (similar sound + taste,
skips respected, no repeats within the session).

## 6. Screen types

| Form | Layout |
| --- | --- |
| **Phone** (< 600 dp) | §2: 5 bottom tabs + mini player; Now Playing full screen. |
| **Tablet / foldable** (600–1200 dp) | Navigation rail on the left (same 5 destinations), content in the middle; landscape shows Now Playing (or Queue) as a side pane. |
| **Desktop / web** (≥ 1200 px) | **Spotify-desktop structure + YouTube-Music shelves**: left sidebar (Home, For You, Search, Library + your playlists list), main content area with YT-Music-style shelves and big headers, **right panel** toggling between Queue / Lyrics / Devices, **player bar along the bottom** (cover, title, controls, seek, volume, devices, EQ, lyrics, queue). Full Now Playing view available (blurred cover, big lyrics) from the player bar. Keyboard shortcuts + global search on `/` or Ctrl+K. |
| **Android Auto / Wear OS** | later phases (§8) |

## 7. Settings additions
Appearance: animations (Off/Quick/Expressive), default view (grid/list), album page style
(Big header/Simple list), accent colour (green default + a few presets), Home shelf order/visibility.
These sync per user via `/me/settings` (device-local overrides allowed).

## 8. Later phases (not in phase 1)
- **Listening**: gapless + crossfade, ReplayGain/R128 loudness levelling (measured during analysis),
  sleep timer, LRCLIB lyrics fallback, AutoEQ headphone profiles, Android Auto.
- **Smarter**: Release Radar, Wrapped (monthly/yearly), Blend between two users, plain-language mix
  requests (after the hardware upgrade).
- **Devices/home**: Wear OS app, Snapcast multi-room (owner has spare PCs/Pis), DLNA/UPnP renderers
  (owner has smart TV/DLNA speakers), Home Assistant integration (owner runs HA), Android widget +
  quick-settings tile.
- **Library**: health page, **tag editing (admins only; music folder becomes read-write; every edit
  logged with the old values so it can be undone)**, podcasts/audiobooks (later). Admins can already
  promote other users to admin (v2 `PATCH /admin/users/{id}`) — make that visible in the Admin UI.
- **Sharing**: collaborative playlists.

## 9. Ownership for phase 1

| Area | Session |
| --- | --- |
| Endpoints: `/home`, `/foryou` (shelf feeds), `/search` (global incl. catalogue), `/catalog/{id}/preview`, `/toget` CRUD + auto-`got` on scan, pins, `/radio` (calls AI), settings keys; `api/openapi.yaml` first | server core |
| Radio generation (`ai.api.radio_start/radio_more`), "because you played" shelves, random-radio seed | AI engine |
| Android redesign (§2–§7 phone + tablet layouts) | Android |
| Web + desktop redesign (§6 desktop layout, phone/tablet breakpoints for the web) | web + desktop |
