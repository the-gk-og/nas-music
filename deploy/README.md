# Deploying on TrueNAS SCALE

> **New install on any NAS?** Follow [`docker/README.md`](docker/README.md): one Compose file and a `.env`,
> works on Synology, Unraid, QNAP, OpenMediaVault, TrueNAS or plain Linux. This page is the TrueNAS walkthrough
> and the reference for the optional features (uploads, casting, scrobbling, tag fixing).

Phase 1 gets music streaming to your phone with Navidrome while the custom server and app are built.
From Phase 2, `full/compose.yaml` replaces it.

## Before you start: snapshot the music

Phase 1 includes re-tagging your files with MusicBrainz Picard, which rewrites them. Take a snapshot first:
**Datasets → your music dataset → Data Protection → Create Snapshot** (name it `before-picard`).
If anything goes wrong you can roll back.

## 1. Datasets

Under **Datasets**, create (adjust `POOL` to your pool name):

| Dataset | Purpose | Permissions |
| --- | --- | --- |
| `POOL/media/music` | Your FLAC library (probably exists already) | `apps` (568) needs read |
| `POOL/apps/navidrome` | Navidrome database (Phase 1) | owner `apps` (568) |
| `POOL/apps/nasmusic` | Custom server data (Phase 2+) | owner `apps` (568) |

To let the `apps` user read the music, edit the music dataset's ACL and add **read + execute** for user `apps`.

## 2. Cloudflare Tunnel (your domain)

1. Add your domain to a free Cloudflare account, and switch its nameservers to Cloudflare's at your registrar.
2. **Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared**. Name it `nas`.
3. On the install page choose **Docker**, then copy only the long token after `--token` from the command shown.
   That is `PASTE_TUNNEL_TOKEN_HERE`. Treat it like a password.
4. **Public hostname**: subdomain `music`, your domain, service type **HTTP**, URL:
   - Phase 1: `navidrome:4533`
   - Phase 2+: `nasmusic:8080`
5. **Cloudflare Access**: leave it off for Phase 1. Navidrome has its own login, and third-party
   test apps can't send Access tokens. In Phase 2 we add an Access application for `music.<domain>`
   with two policies: **Service Auth** (a service token the app sends) and **email one-time PIN** for you
   in a browser.

## 3. Install Phase 1

**Apps → Discover → ⋮ (top right) → Install via YAML**. Name it `music`, paste `phase1/compose.yaml`
with the three placeholders replaced, and save.

Then open `http://<NAS static IP>:4533`, create the admin user, and wait for the first scan
(5,000 FLACs takes a few minutes).

## 4. Test from the phone

Until our app exists, use any free, open-source Subsonic app (for example **Tempo**, from F-Droid or GitHub):
- At home: server `http://<NAS static IP>:4533`
- Away: `https://music.<your domain>`

Phase 1 is done when music plays on mobile data, away from home Wi-Fi.

## 5. Tag the library with MusicBrainz Picard

Picard is free and open source. Run it on your PC against the music share:
1. Mount the music dataset's SMB share on your PC. The share must be **writable** for this step. The
   containers still mount it read-only.
2. In Picard **Options**: tick *Save tags*. Under *File Naming*, untick *Rename files* and *Move files*,
   so Picard only adds tags and doesn't reorganise the folders.
3. Add one artist folder first. Use **Cluster**, then **Lookup**, check the matches (green = good),
   then **Save**. Once you trust it, do the rest in batches of 20–50 albums.
4. Albums Picard can't match: leave them. The server falls back to matching by name.

### Or: let the server suggest tags (no file writes)

The server can identify songs itself and suggest tags that you review in the web admin console
(**Admin → Tag fixing**). Accepted suggestions are stored on the server as tag overrides; the music share
stays read-only.
1. Get a free AcoustID application key: sign in at https://acoustid.org/new-application, register an
   application (any name, e.g. "nas-music"), copy its **API key**.
2. Add it to `config.toml` in the data volume: `acoustid_api_key = "<key>"`, then restart the container.
3. Admin → Tag fixing → **Run now** (it also runs every night at 05:00). Each run works for up to 50
   minutes and carries on next time: ~1,850 songs take one to two runs. Review albums, accept/reject, or
   "accept all ≥ 90%". **Admin → Duplicates** uses the same fingerprints to hide extra copies
   ("Song (2).flac"); nothing is deleted.

## Scrobbling and "new music" notifications (schema v7)

Scrobbling is per user and off until they connect an account in **Settings → Scrobbling** (web, desktop or
Android). The server scrobbles from the listening events every client already sends, so a play counts once
whichever device (or speaker) played it; unsent scrobbles are queued and retried with backoff.
- **ListenBrainz** (open source, no server setup): each user pastes their user token from
  https://listenbrainz.org/settings/. They can also import their ListenBrainz history so mixes learn from it
  (imported listens are marked and can be removed again).
- **Last.fm** needs one API account for the server: sign in at https://www.last.fm/api/account/create,
  create an application (name e.g. "nas-music"; callback URL can stay empty), then add to `config.toml`:
  `lastfm_api_key = "<API key>"` and `lastfm_api_secret = "<Shared secret>"`, and restart the container.
  Users then press **Connect Last.fm**, approve it on last.fm, and the app finishes the connection.
- **New music added**: users switch on **Settings → Notifications → New music added**. After each scan that
  adds songs, they get one notification ("12 new songs: 2 new albums from AJR"). Web/desktop show it live
  (plus an optional desktop notification); Android shows a system notification (checked about hourly with
  WorkManager — no Google push). **Library → New this week** lists what was added.

## Getting the server image (Phase 2)

GitHub Actions builds `ghcr.io/the-gk-og/nasmusic:latest` on every push to `main` that changes `server/`.
The repository is private, so the NAS needs a token to pull it:

1. On GitHub: **Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token**.
   Tick only **read:packages**, set an expiry, and copy the token.
2. On TrueNAS: **System → Shell** (or SSH in), then log in and pull. Paste the token when it asks for a password:
   ```bash
   sudo docker login ghcr.io -u the-gk-og
   sudo docker pull ghcr.io/the-gk-og/nasmusic:latest
   ```
3. Install `full/compose.yaml` via **Install via YAML**. Because it uses `pull_policy: missing`, the app uses
   the image you just pulled.

To update later: run the `docker pull` line again, then restart the app in **Apps**.

If you make the repository public, the package can be made public too, and the login step goes away.

## Getting the APK

Every push to `main` that changes `android/` publishes a GitHub Release (`v0.1.<build number>`) with a
signed APK attached: **github.com/the-gk-og/nas-music → Releases → latest → `nas-music-v0.1.N.apk`**.
Open that page on the phone (signed in to GitHub, since the repo is private) and tap the APK.

All releases are signed with the same permanent key, so a new release installs as an update over the
old one: pairing, settings and offline music are kept. The first release replaces the earlier
debug builds, which were signed differently; uninstall the debug build once before installing it.

The signing key lives outside the repo (`~/Android/nas-music-release.jks` + `.password` on the dev
machine, and as GitHub Actions secrets). Back it up: without it, future builds can't update the
installed app.

To install, allow "Install unknown apps" for your browser or file manager on the phone.

## Uploads (songs from the web player, desktop app and phone)

People an admin allows can upload songs; they appear in the library within seconds and then go through
analysis, loudness, explicit and AcoustID jobs like every other track. The music library stays **read-only**:
uploads live in a separate, writable folder that the server indexes as a second library root.

1. **Create the folder** on the NAS (once), owned by the app user and the music group, group-writable:
   `/mnt/NAS/Shared/music-uploads`, owner `568` (apps), group `3000`, mode `2775` (setgid so new folders keep
   group 3000). In TrueNAS: Datasets → add a dataset (or a folder in the Shared dataset) and give user `apps`
   (568) **modify** in its ACL.
2. **Mount it read-write** into the container next to the read-only library (`deploy/dockhand/compose.yaml`
   already has it): `- /mnt/NAS/Shared/music-uploads:/uploads` (no `:ro`). The container runs as `568:568`
   with `group_add: ["3000"]`, so the group gets write access too.
3. **Config** (`/data/config.toml`), then restart the container:
   ```toml
   upload_root = "/uploads"         # writable; must not be inside library_root
   # upload_max_file_mb = 2048      # largest single file
   # upload_quota_gb = 50           # default per-user quota (0 = unlimited); admins change it per user
   ```
4. **Who may upload**: Admin → Uploads. By default only admins can; tick "May upload" per user and set a
   quota if you like. Each upload can be "Everyone" (normal library track) or "Only me".

Files are stored as `<username>/<Artist>/<Album>/<NN Title>.<ext>` (from their tags, else
`<username>/Unsorted/<file name>`), kept in their original format (FLAC, MP3, M4A/AAC, Ogg/Opus, WAV); the
content is checked, not just the name. Unfinished uploads sit in `/uploads/.incoming` and are dropped after a
week without progress. The uploader or an admin can delete an upload; that only ever touches files under
`/uploads`. Snapshot/back up the uploads dataset like the music: it is the only copy of those files.

**Body size through the relay.** Clients send uploads in 4 MiB chunks (the server accepts at most 16 MiB per
request) and resume after a dropped connection, so slow relays and big FLACs are fine. Caddy has no request
body limit by default; `deploy/relay/Caddyfile` sets an explicit 32 MB cap on `/api/v1/uploads/*` (copy the
updated Caddyfile to `/data/relay/Caddyfile` and restart the caddy container). The Cloudflare tunnel's 100 MB
limit is also well above a chunk.

## Deleting songs (admin console "Cleanup & trash")

Admin → **Cleanup & trash** lists songs worth pruning (never played, unplayed albums, disliked/skipped, low
bitrate, fake lossless, very short) and lets you find and remove any song, album or artist. Duplicates →
**Keep selected, delete others** does the same for worse copies (press play on each copy to compare them).
A removal leaves the library at once and waits in the **trash** for `trash_days` (30) days, restorable.

When the trash is emptied:
- **Default (`library_delete_files = false`)**: the music folder stays read-only and is never touched. The songs
  stay hidden for good and go on the **delete list**; download its script from the Trash tab and run it on the
  NAS (`sh delete-list.sh /mnt/NAS/Shared/music`). The server forgets the songs once the files are gone.
- **`library_delete_files = true`** in `config.toml`: the server deletes the files itself (plus a same-name
  `.lrc`, and folders left empty). Mount the music folder **read-write** for this: drop `:ro` from the music
  line in your compose file. If the mount is still read-only, nothing breaks: the songs go on the delete list
  and the Trash tab shows why. Take a ZFS snapshot of the music dataset first (see the top of this file).

Uploaded songs are always deleted for real (their folder is writable anyway).

## Casting to speakers

The server can play on Chromecast / Google Cast speakers, DLNA (UPnP AV) renderers, Home Assistant
`media_player` entities and Snapcast groups. They show up in every user's Devices list (phone, web, desktop)
as speakers; picking one hands the queue to the server, which plays it there. Picking a phone or browser
again brings it back at the same position.

**How speakers get the audio.** Speakers fetch tracks from short-lived signed URLs
(`/api/v1/cast/media/<track>?f=…&exp=…&sig=…`). Each URL is an HMAC over that one track, format and expiry
(track length + 1 h), so no login token is ever in a URL. They're served over **plain HTTP on `cast_port`
(8090)**, because DLNA renderers and Snapcast can't do a self-signed HTTPS certificate. That port serves
nothing but those signed URLs. Publish it on the LAN only (never forward it from the internet):

```yaml
    ports:
      - "8443:8443"
      - "127.0.0.1:8080:8080"
      - "8090:8090"            # casting: speakers fetch signed media URLs here (LAN only)
```

If speakers should use another address (a reverse proxy, another port), set `cast_media_url`.

**Finding speakers (Docker networking).** Chromecast discovery uses mDNS and DLNA discovery uses SSDP. Both
are multicast, so neither gets through Docker's default bridge network. Choose one of these:
- `network_mode: host` on the nasmusic service. Discovery then just works. Remove the `ports:` block, because
  the ports are bound on the host directly. Check that 8443/8090 (and 8080 if you use it) are free on the NAS.
- Keep bridge networking and list the speakers by hand in `config.toml`: `chromecast_hosts = ["<ip>", …]`
  (Cast port 8009 is polled directly) and `dlna_renderers = ["http://<ip>:<port>/<description>.xml"]`.
  Your renderer's app or a UPnP browser shows its description URL. Give these speakers DHCP reservations.
  Set `cast_discovery = false` to stop the (useless) multicast attempts.

Home Assistant and Snapcast need no multicast; they work on any networking.

**When a speaker doesn't answer.** Picking a speaker shows "Connecting…" (with Cancel) until it has loaded
the track. If it can't be reached within about 10 s, refuses the track, or drops off later, the app shows
"Couldn't connect to <speaker>" / "Lost connection to <speaker>". Playback then goes back, paused at the
same spot, to the device that picked it. "Stop casting" does the same on purpose. Being discovered is not the
same as being reachable: the container must reach each Chromecast's TCP port 8009 (a Cast group uses its own
port, e.g. 32202, on the group's current leader). To test from the NAS:
`docker exec nasmusic python -c "import socket; socket.create_connection(('<ip>', 8009), 3)"`.

**Home Assistant.** Set `homeassistant_url` and `homeassistant_token` (HA → your profile → Security →
Long-lived access tokens) in `/data/config.toml`. Never put the token in the repo. Optionally limit the list
with `homeassistant_entities`. If HA also exposes a Chromecast the server finds directly, it appears twice.
List only the entities you want, or turn off `cast_discovery`.

**Snapcast.** Add a TCP source to `snapserver.conf` and point nasmusic at the server:

```ini
[stream]
source = tcp://0.0.0.0:4953?name=nasmusic&mode=server&sampleformat=48000:16:2
```

```toml
snapcast_host = "192.168.1.20"
```

Every Snapcast group with a connected client becomes a speaker. Playing on one switches that group to the
`nasmusic` stream. The server decodes the track with ffmpeg in real time and feeds PCM to that source. There
is a single feed, so two groups playing at once hear the same thing. Group the rooms in Snapcast for
multi-room.

**Check it.** `GET /api/v1/cast/speakers` (any signed-in device) lists what was found, plus discovery errors
for each backend. An admin can force a rediscovery with `POST /api/v1/cast/speakers/refresh`. Otherwise it
runs when the first device connects and then every 5 minutes. Set `cast_enabled = false` to turn it all off.

**Android.** The `foss` APK uses only the above. The `full` APK also has a Google Cast button in the Devices
sheet, which casts the phone's queue straight to a Chromecast. It still uses the server's signed URLs, so
port 8090 must be reachable from the Chromecast.

## Voice control (Home Assistant Assist)

"Play my Pop radio in the bedroom", "skip", "what's playing?" from Home Assistant voice satellites or the HA
app: create a voice token in nas-music's Settings → Voice control and follow
[`homeassistant/README.md`](homeassistant/README.md) (one YAML package, no add-ons).

## Radio DJ voice

In AI radio and mixes, a voice can introduce some songs ("That was Hyperballad by Björk. Radiohead got together
in Abingdon back in 1985. Here's Karma Police.") over the song's opening while the music dips. It is off until
each user switches it on (Settings → Radio DJ, synced to all their devices; "every song / every 3rd / every 5th").

- Speech is made on the NAS with [Piper](https://github.com/OHF-Voice/piper1-gpl) (GPL-3.0, CPU only). A
  two-sentence intro should take a second or two on the i5-3470, and players ask for it ~25 s ahead. The image
  includes it by default (`--build-arg WITH_DJVOICE=0` leaves it out). Nothing goes to an online speech service.
- The voice itself (about 60 MB) downloads into `/data/dj-voices` from huggingface.co/rhasspy/piper-voices the first
  time someone uses the DJ. To fetch it up front: `docker exec nasmusic nasmusic dj-voice download`; to hear it:
  `docker exec nasmusic nasmusic dj-voice say "Hello from the NAS"` (prints the .ogg it wrote).
- Pick another voice with `dj_voice = "en_GB-alan-medium"` (any Piper voice name), or point `dj_voice_model` at a
  local `.onnx` (with its `.onnx.json`) and set `dj_voice_download = false`. Each voice has its own licence (see its
  MODEL_CARD); the default `en_GB-cori-medium` was trained on a public-domain LibriVox reading.
- What it says comes from the song's tags and the artist pages cache (Wikipedia/MusicBrainz, `artist_info_enabled`).
  Clips are cached in `/data/dj/clips` (pruned at 300 MB).
- `dj_voice_enabled = false` turns it off for everyone.
