# Deploying on TrueNAS SCALE

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

Every push to `main` that changes `android/` builds a debug APK. Download it from GitHub:
**Actions → android → the latest run → Artifacts → nas-music-debug-apk** (a zip containing `app-debug.apk`).
Pushing a tag such as `v0.1.0` also attaches the APK to a GitHub Release.

To install it, allow "Install unknown apps" for your browser or file manager on the phone.
