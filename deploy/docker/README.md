# Installing nas-music with Docker

This guide sets up the nas-music server (with the web player built in) on a NAS or any Linux machine that runs
Docker: Synology, Unraid, QNAP, OpenMediaVault, TrueNAS SCALE or a plain server. It takes about 15 minutes.

If you already run nas-music from `deploy/full` or `deploy/dockhand`, you don't need this: those keep working.
The TrueNAS-specific notes (datasets, ACLs, Cloudflare, uploads, casting, scrobbling) are in
[`deploy/README.md`](../README.md).

## What you need

- A **64-bit Intel or AMD** machine (x86-64). ARM boxes, such as many low-end Synology and QNAP models, aren't
  supported yet. 4 GB of RAM is enough; 8 GB or more is comfortable while the first audio analysis runs.
- **Docker with Compose v2** (`docker compose version` should print 2.x or newer). On Synology this is
  *Container Manager*, on Unraid the *Compose Manager* plugin, on QNAP *Container Station*, on TrueNAS SCALE
  it is built in.
- Your **music folder** on the NAS. The server only reads it; it never changes, moves or deletes your files.
- A **fixed LAN IP** for the NAS. Set a DHCP reservation in your router. The apps remember this address, and it
  goes into the server's HTTPS certificate, so it shouldn't change.

## 1. Get the files

You need two files from this repository's `deploy/docker` folder: `compose.yaml` and `.env.example`
(`compose.host-network.yaml` too if you want speakers found automatically, see step 7).

Put them in a folder on the NAS, for example `/volume1/docker/nasmusic` (Synology) or
`/mnt/user/appdata/nasmusic` (Unraid). Using git:

```bash
git clone https://github.com/the-gk-og/nas-music.git
cd nas-music/deploy/docker
```

Then make your settings file:

```bash
cp .env.example .env
```

## 2. Fill in `.env`

Open `.env` in any text editor. Every setting is explained in the file. Two are required:

| Setting | What to put | Example |
| --- | --- | --- |
| `MUSIC_DIR` | Path of your music folder on the NAS | `/volume1/music` |
| `LAN_HOST` | The NAS's fixed LAN IP address | `192.168.1.50` |

Worth setting now: `SERVER_NAME` (what the apps call this server), `TIMEZONE` (nightly mixes run at 02:00 in it),
and `ADMIN_PASSWORD` (leave it empty to have one generated; see step 4).

## 3. Permissions

The server runs as the user and group in `PUID` / `PGID` (568:568 by default, TrueNAS's `apps` user). That user
must be able to **read** your music and **write** the data folder.

1. Find a suitable user on the NAS with `id <username>`. It prints `uid=…` and `gid=…`; put those in `PUID` and
   `PGID`. Typical values: Synology's first user `1026` / `100`; Unraid `99` / `100`; TrueNAS `568` / `568`.
2. Check that user can read the music folder (most NASes give the `users` group read access already).
   If only some group can read it, put that group's id in `MUSIC_GID`.
3. Data folder. You have two choices:
   - **Leave `DATA_DIR` unset.** Docker keeps the data in a volume it manages. This works straight away with
     the default `PUID=568`, but the volume is tucked away inside Docker, so it's harder to back up.
   - **Recommended: a folder of your own.** Create it and give it to `PUID:PGID`, then set `DATA_DIR`:
     ```bash
     mkdir -p /volume1/docker/nasmusic/data
     sudo chown 1026:100 /volume1/docker/nasmusic/data   # your PUID:PGID
     ```
     If you change `PUID` away from 568, you need this option.

If the data folder isn't writable, the server stops at startup with
`can't write /data/config.toml: Permission denied` in the log. Fix the owner and start it again.

## 4. Get the server image and start

The image is built by GitHub Actions and published as `ghcr.io/the-gk-og/nasmusic:latest`. Choose one way:

**Pull the published image.** While the repository is private, log in once with a GitHub token that has only
the `read:packages` scope (GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)):

```bash
docker login ghcr.io -u <your GitHub username>     # paste the token as the password
docker compose pull
```

**Or build it yourself** from the cloned repository (needs the whole repo, takes 10–20 minutes the first time):

```bash
docker compose build
```

Then start it:

```bash
docker compose up -d
docker compose logs server
```

On the very first start, the server writes its settings to `config.toml` in the data folder and prints the
admin account:

```
================================================================
  First admin user:  admin
  Password:          wcQ6ljInbEDOhRPj
  ...
================================================================
```

Write the password down. It is shown only on that first start. If you lose it, reset it with
`docker compose exec server nasmusic user passwd admin`.

The server then scans your library. About 5,000 songs take a few minutes. Audio analysis (for mixes and radio)
runs in the background afterwards and can take several hours the first time on an older CPU.

### On a NAS user interface instead of a shell

- **Synology Container Manager**: Project → Create → choose the folder with `compose.yaml` and `.env`, then
  Build. Logs are under Container → `nasmusic-server-1` → Log.
- **Unraid Compose Manager**: Add New Stack, paste `compose.yaml` into the compose file and `.env` into the
  env file, then Compose Up.
- **TrueNAS SCALE**: Apps → Discover → ⋮ → Install via YAML doesn't read `.env`. Use
  [`deploy/full/compose.yaml`](../full/compose.yaml) with the steps in [`deploy/README.md`](../README.md),
  or run this folder from the TrueNAS shell with `docker compose`.
- **Portainer**: Stacks → Add stack → Repository (this repo, compose path `deploy/docker/compose.yaml`) or Web
  editor, and add the `.env` values under Environment variables.

## 5. Open the web player

Go to **`https://<LAN_HOST>:8443`**, for example `https://192.168.1.50:8443`.

Your browser warns that the connection isn't private. That is expected: the server makes its own certificate,
and the apps check it by its fingerprint instead. Choose *Advanced → Continue*. Then sign in as the admin.

Change the generated password on the **Account** page. Add family and friends in the admin console at
`https://<LAN_HOST>:8443/admin` → **Users → Invite someone**; each person gets their own history, likes and mixes.

## 6. Connect the phone app

1. Install the Android app from the repository's **Releases** page (the latest `nas-music-v0.1.N.apk`).
2. In the web player, open **Account → Pair a phone with a code**. A QR code appears.
3. In the app, scan it. It works at home straight away, and away from home once you've set up remote access.

You can also print a pairing QR in the terminal with `docker compose exec server nasmusic pair-code`.

## 7. Optional extras

### Remote access

At home the apps use `https://<LAN_HOST>:8443`. To listen away from home you need a public address. Choose one:

- **Cloudflare Tunnel** (free, needs a domain on Cloudflare, no ports opened on your router):
  1. Cloudflare Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared. On the install page choose
     Docker and copy the long token after `--token`.
  2. Add a public hostname, for example `music.example.com`, service type **HTTP**, URL **`server:8080`**.
  3. In `.env`, uncomment `COMPOSE_PROFILES=tunnel` and paste the token into `TUNNEL_TOKEN`. Then
     `docker compose up -d`. A second container, `cloudflared`, starts.
- **Your own reverse proxy** (Caddy, Nginx Proxy Manager, Traefik…) on the same machine: proxy your public
  hostname to `http://127.0.0.1:8080`. That port is only reachable from the NAS itself.
- **A relay through a friend's server**, if your internet provider doesn't give you a public IP:
  [`deploy/relay/README.md`](../relay/README.md).

Then tell the server its public address so the pairing QR includes it: `tunnel_url = "https://music.example.com"`
in `config.toml` (see "Changing settings"). Pair the phone again afterwards.

### Speakers

Chromecast, DLNA, Home Assistant and Snapcast speakers appear in every user's Devices list. Home Assistant and
Snapcast work as they are. Chromecast and DLNA speakers are *found* with multicast, which doesn't pass Docker's
normal network, so either:

- Use host networking: in `.env`, uncomment `COMPOSE_FILE=compose.yaml:compose.host-network.yaml` and run
  `docker compose up -d`. Ports 8443, 8080 and 8090 must then be free on the NAS.
- Or list the speakers' IPs by hand in `config.toml`. Details are in [`deploy/README.md`](../README.md)
  under "Casting to speakers".

### Uploads and audiobooks

People you allow can upload songs from the apps. Uploads go to a **separate, writable** folder, never the music
folder. Create the folder, owned by `PUID:PGID`, set `UPLOADS_DIR` and `UPLOAD_ROOT=/uploads` in `.env`, and
remove the `#` in front of the `/uploads` line in `compose.yaml`. Audiobooks work the same way with
`AUDIOBOOKS_DIR` and `AUDIOBOOKS_ROOT=/audiobooks`. If the server has already started once, put
`upload_root = "/uploads"` (or `audiobooks_root = "/audiobooks"`) in `config.toml` instead.

### Scrobbling, tag fixing and other API keys

Last.fm scrobbling, automatic tag fixing (AcoustID) and better podcast search need free API keys. They go in
`config.toml`; [`deploy/README.md`](../README.md) says where to get each one.

## Changing settings

The `.env` values like `LAN_HOST`, `SERVER_NAME` and `ADMIN_PASSWORD` are used **only on the first start**, to
write `config.toml` in the data folder. After that, edit `config.toml` and restart:

```bash
docker compose exec server sh -c 'cat /data/config.toml'   # look at it
# edit the file in your data folder (or: docker compose cp server:/data/config.toml . ; edit ; docker compose cp config.toml server:/data/)
docker compose restart server
```

Every option is explained in [`server/config.example.toml`](../../server/config.example.toml). The ports,
paths, `PUID`/`PGID` and the tunnel token stay in `.env`, because Docker uses them, not the server.

To start completely fresh, stop the stack, delete the data folder's contents (or `docker compose down -v` for
the Docker volume), and start again. Your music is never touched.

## Updating

```bash
docker compose pull          # or: git pull && docker compose build
docker compose up -d
```

The database is upgraded automatically on start (a backup copy is made before each upgrade). Your settings, users and
history are kept.

## Backups

The server backs up its database (users, listening history, likes, playlists, settings) every night at 04:15
into `backups/` in the data folder, keeping the last 3 nightly ones. You can also make one any time, and restore
any of them, in **Admin → Backups**.

Those copies sit on the same disk as the database, so also send them somewhere else with `backup_copy_to` in
`config.toml`. Each nightly backup is then gzipped there too, keeping the newest `backup_keep` (default 14):

- **Another disk or dataset** (simplest). Mount it into the container and point at it:
  in `.env` set `BACKUP_DIR=/mnt/tank2/music-backups`, uncomment the `/backup` line in `compose.yaml`, and in
  `config.toml` set `backup_copy_to = "/backup"`. The folder must be writable by `PUID`. A copy of `config.toml`
  and the HTTPS certificate is kept in `config/` there as well.
- **A cloud or SFTP remote with rclone** (Backblaze B2, S3, Google Drive, another NAS over SFTP, …; rclone is in
  the image). Create the remote once:
  `docker compose exec -it server rclone config` (it is saved as `/data/rclone.conf`), then set
  `backup_copy_to = "myremote:music-backups"`. `config.toml` holds tokens, so it is **not** copied to a remote
  unless you set `backup_include_config = true`; use an rclone `crypt` remote if you do.

Restart after changing `config.toml`. Admin → Backups shows where copies go, whether last night's worked, and the
copies there. **Copy off-site** sends any backup straight away.

**Restoring.** In Admin → Backups, press **Restore** on a backup (local or off-site). The server checks the file,
saves the current database first (type "Before restore", so you can undo), swaps it in and upgrades it if it came
from an older version. No restart is needed; phones paired after that backup was made have to pair again.

If the server won't start, or on a new machine: put `config.toml` back in the data folder (from `config/` in the
backup folder), then

```bash
docker compose run --rm server nasmusic restore                 # lists local and off-site backups
docker compose run --rm server nasmusic restore --offsite nasmusic-scheduled-20261124T041500Z.db.gz
docker compose up -d
```

Uploads (if you use them) are not in these backups: back that folder up separately, it is the only copy of
those songs. The Opus and art caches never need backing up; they are rebuilt.

## Troubleshooting

| What you see | What to do |
| --- | --- |
| `set LAN_HOST in .env` or `set MUSIC_DIR in .env` | `.env` is missing or not next to `compose.yaml`. |
| `can't write /data/config.toml: Permission denied` | The data folder isn't owned by `PUID:PGID`. See step 3. |
| The library stays empty | The server can't read `MUSIC_DIR`. Check with `docker compose exec server ls /music`. If it says "Permission denied", fix `PUID`/`PGID`/`MUSIC_GID`. |
| `denied` or `unauthorized` when pulling | Log in to `ghcr.io` with a `read:packages` token (step 4), or build the image yourself. |
| `port is already allocated` | Something else uses 8443, 8080 or 8090. Change `LAN_PORT`, `HTTP_PORT` or `CAST_PORT` in `.env`. |
| The phone app can't reach the server at home | Check `https://<LAN_HOST>:8443` opens in the phone's browser. If `LAN_HOST` was wrong, fix `lan_host` in `config.toml`, delete the `tls` folder in the data folder (so the certificate is remade), restart, and pair again. |
| Health check | `curl -k https://<LAN_HOST>:8443/api/v1/health` should print `{"status":"ok",…}`. |

## Names used here

If the project is renamed, these are the places the name appears in a Docker install: `STACK_NAME` and `IMAGE`
in `.env` (container and volume names come from `STACK_NAME`), the `NASMUSIC_*` variable names in
`compose.yaml`, and the `nasmusic` command inside the container.
