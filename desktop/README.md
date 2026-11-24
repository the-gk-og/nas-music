# nas-music desktop (Tauri v2)

Wraps the web player (`../web`, built to `../web/dist`) in a native window. On first run it shows the
Connect screen: enter the server address, then sign in. The app talks to the server cross-origin with a
bearer token kept in the webview's storage (the server must allow the Tauri origins in CORS:
`tauri://localhost`, `http://tauri.localhost`).

Desktop extras (all in `src-tauri/src/lib.rs`):
- tray icon: Play/Pause, Next, Previous, Show, Quit (closing the window hides it to the tray; Quit exits)
- global media keys (Play/Pause, Next, Previous, Stop) via `tauri-plugin-global-shortcut`
- a native notification on track change while the window isn't focused
- single instance (a second launch focuses the running window), window size/position remembered
- optional Discord "Listening to" status (`src-tauri/src/discord.rs`, off by default; see below)
- the web player's Now Playing visualiser works here too (bars button in full-screen Now Playing)

## Discord status
Settings → Desktop app → "Show what I'm playing in Discord". It talks only to the Discord app on the same
computer, over Discord's local IPC socket (crate `discord-rich-presence`, MIT); nothing is sent while
paused, while another device is playing, or when the setting is off. Discord needs an application ID:
create an application at https://discord.com/developers/applications (its name is what profiles show),
paste the Application ID into the setting, and optionally upload an image named `logo` under Rich
Presence → Art Assets. Application IDs aren't secret, so a shared one could be built in later. If Discord
isn't running, the app retries every 15 s. Works with the Discord desktop client on Linux (incl. Flatpak
paths the crate knows), Windows and macOS.

## Build

Prerequisites: Node 20+, Rust (`rustup`), and the platform webview dev packages.

```bash
# Arch / Manjaro
sudo pacman -S --needed webkit2gtk-4.1 base-devel curl wget file openssl appmenu-gtk-module libappindicator-gtk3 librsvg gst-plugins-base gst-plugins-good
# Debian / Ubuntu
sudo apt install libwebkit2gtk-4.1-dev build-essential curl wget file libxdo-dev libssl-dev libayatana-appindicator3-dev librsvg2-dev patchelf
```

```bash
npm ci --prefix ../web && npm ci
npm run build            # → src-tauri/target/release/bundle/{appimage,deb}/
npm run dev              # dev window against the Vite dev server (web: npm run dev)
```

Opus playback on Linux goes through GStreamer (WebKitGTK); the AppImage bundles the media framework and
the deb depends on `gstreamer1.0-plugins-good`. macOS/WebKit can't play Ogg Opus, so the player asks the
server for FLAC there automatically.

## Known limits
- A LAN address with a self-signed certificate is rejected by the system webview unless the
  certificate is trusted by the OS. Use the tunnel address, or trust the NAS certificate system-wide.
- Global media keys don't register under most Wayland compositors; the in-window keys and MPRIS from
  the webview still work.
- Release bundles are unsigned (Windows SmartScreen / macOS Gatekeeper will warn).
