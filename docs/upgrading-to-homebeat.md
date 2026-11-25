# Upgrading from nas-music to Homebeat

nas-music is now called **Homebeat**. The server keeps all your data: users, listening history, playlists,
queues, settings and `config.toml` stay exactly where they are, and nothing is moved or converted.

## What carries over on its own

- **Database.** A data folder that already has `nasmusic.db` keeps using it (a fresh install creates
  `homebeat.db`). Nothing is renamed on disk.
- **Backups.** Older `nasmusic-*.db` / `nasmusic.db.vN-backup-*` backups (local and off-site) are still listed
  in Admin → Backups and can be restored.
- **Environment variables.** Every `HOMEBEAT_*` variable also accepts its old `NASMUSIC_*` name
  (`NASMUSIC_CONFIG`, `NASMUSIC_LAN_HOST`, ...). The new name wins if both are set.
- **Server image.** CI publishes the same image as `ghcr.io/the-gk-og/homebeat` **and** the old
  `ghcr.io/the-gk-og/nasmusic`, so a compose file that still pulls `nasmusic:latest` keeps updating.
- **Command.** Inside the container both `homebeat ...` and the old `nasmusic ...` work.
- **Snapcast.** `snapcast_stream_id` still defaults to `nasmusic`, so an existing `snapserver.conf` keeps working.

## Server (Docker)

You can leave your stack as it is. To switch it to the new names:

**Dockhand stack (`deploy/dockhand/compose.yaml`)**
1. Take a snapshot or a manual backup (Admin → Backups) first.
2. **Keep the stack's name** in Dockhand and keep the volume key `nasmusic-data` (the new compose file does):
   Compose names the volume `<stack>_nasmusic-data`, and a different name would start with an empty volume.
3. **Stop the stack**, paste the new compose file, then deploy. (The service is now called `homebeat`; without
   stopping first, the old `nasmusic` container keeps ports 8443/8090 and the new one can't start.)
4. The `homebeat` service also answers to the old name `nasmusic` on the stack's network, so the Caddyfile in
   `/data/relay` (`reverse_proxy nasmusic:8080`) keeps working unchanged.
5. Check: sign in on the web player and look at your history and playlists. Admin → Health shows the database size.

**`deploy/docker` (`.env` installs)**: keep `STACK_NAME=nasmusic` in your `.env` (the volume is
`<STACK_NAME>_server-data`). You can change `IMAGE` to `ghcr.io/the-gk-og/homebeat:latest`. Then
`docker compose pull && docker compose up -d`.

**`deploy/full` (TrueNAS app)**: keep your existing `/mnt/POOL/apps/nasmusic` data path in the YAML.

**GitHub package visibility.** The first push creates a new `homebeat` package on GitHub; if your NAS pulls
without logging in, make it public once (GitHub → your profile → Packages → homebeat → Package settings).

## Android (phone and watch)

The app ID changed (`org.nasmusic.app` → `org.homebeat.app`, watch `org.homebeat.wear`), so Homebeat
installs as a **new app** next to nas-music:

1. Install `homebeat-full-v0.1.N.apk` (or `-foss-`) from the latest release, and the watch APK if you use it.
2. Open Homebeat and connect it to your server again, the same way you did the first time.
3. Re-download any offline music, and redo phone-only settings (alarm, EQ, app settings).
4. Uninstall nas-music.

Everything stored on the server (history, likes, playlists, mixes) comes back after sign-in.
The release signing key is unchanged.

## Desktop app

The desktop app's ID changed too (`org.homebeat.desktop`), so it starts signed out once. Install the new
build, sign in, then uninstall the old one.

## Home Assistant

The files in `deploy/homeassistant` are now `homebeat_*.yaml` and use `homebeat_*` secret names. A setup made
from the old files keeps working as is (it only calls the server's API); rename them only if you like.
