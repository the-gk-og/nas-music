# nas-music web player

Vite + Svelte 5 + TypeScript. Built to `dist/` and served by the server at `/` (the desktop app in
`../desktop` bundles the same build). Spec: `docs/v2.md` §6–§8, §10.

```bash
npm ci
npm run dev:mock     # whole UI against the in-browser mock server (sign in: admin / admin123)
NM_SERVER=https://192.168.68.2:8443 npm run dev   # proxy /api (incl. the Connect WebSocket) to a real server
npm test             # Vitest: Connect state machine, lyrics, queue ops, EQ graph, events
npm run lint         # eslint + svelte-check
npm run build        # → dist/
```

Typing `mock` as the server address on the Connect screen also runs against the mock (in any build).
The mock includes a fake library, generated cover art and tones, invites (`WELCOME1`), and a Connect hub
with a simulated phone ("Pixel 8 (mock)") so transfer in/out and remote mode can be exercised.

## v3 layout (docs/v3-ux.md)
- **Desktop ≥ 1200 px:** sidebar (Home, For You, Search, Library, your playlists), main area with shelves and
  big headers, right panel (Queue / Lyrics / Devices), player bar; full Now Playing with the blurred cover
  and big lyrics. Search on `/` or Ctrl+K; Q toggles the queue panel, L the lyrics panel.
- **Tablet 600–1199 px:** navigation rail; landscape (≥ 900 px wide) shows Now Playing (or the Queue while
  Now Playing is open) as a side pane.
- **Phone < 600 px:** five bottom tabs (Home, For You, Search, Library, Queue) + mini player (swipe to skip,
  tap/swipe up for Now Playing). Now Playing: swipe the cover to skip, down to minimise, long-press for the
  song menu, tap for lyrics; "Up next" peek.
- Settings → Appearance: animations Off / Quick / Expressive (OS reduce-motion forces Off), default view,
  album page style, accent, Home shelf order/visibility, "Only on this device" (`device.<id>.*` overrides).
- Built against server core's v3 contract (claude/server-core-b7e7a3): typed shelves, `artUrl`, pins as
  ShelfItems, `/search` with catalogue, `/toget`, `/radio` + `/radio/{id}/more` (topped up when ≤ 5 remain).

## Installable app (PWA)
`public/manifest.webmanifest` + a service worker (`src/sw.ts`, built to `/sw.js` by the plugin in
`vite.config.ts`, which fills in this build's precache list and version). Chromium/Edge/Android offer
"Install" (also Settings → This device → Install as an app). The worker keeps the app shell (index.html, the
hashed build, icons) so the player opens when the server can't be reached; a banner says so and retries
`/health` (3 s → 1 min backoff), then reloads data when it's back. It never caches `/api/*` (no audio, no
JSON), `/admin` or other origins (`src/lib/swroutes.ts`). Navigations are network-first with a 4 s fallback.
Service workers need a secure context with a trusted certificate: the tunnel address works; the LAN address
works only if the NAS certificate is trusted by the OS (an "accept the risk" exception isn't enough). Not
registered in dev, in the desktop app, or on the Jam guest page.

## Now Playing visualiser
The bars button in full-screen Now Playing (off by default, remembered per device) draws a spectrum behind
the player from an `AnalyserNode` tapped after the deck mix, before EQ and volume (`player.analyser()`,
`components/Visualiser.svelte`, maths in `lib/visualiser.ts`). It only runs while music plays on this device,
and is hidden when the OS asks for reduced motion.

## Admin console (`#/admin/<section>`)
Dashboard (health, now playing, analysis ETA, nightly, jobs), Users & invites (devices, last activity, admin
roles with last-admin protection), Library (scan / nightly / discovery now, failed files + retry), Health &
pruning (counts first, confirm before any removal), Logs (live tail, level/text filter) & job history, Bug
reports, Backups, Server settings (validated URLs incl. custom ports, time zone, cache size, LRCLIB; restart
notice). All calls go through `src/lib/api/admin.ts` — **its shapes are a proposal until api/openapi.yaml
defines the admin API**; the mock (`mockAdmin.ts`) implements them.

## Layout
- `src/lib/api/client.ts` – typed client for every v1/v2 endpoint; cookie (same-origin) or bearer auth.
- `src/lib/api/mock.ts`, `mockData.ts` – mock HTTP API + Connect hub.
- `src/lib/connect.ts` – Connect WebSocket client (hello/welcome, rev-ordered state, takeover/release,
  commands, 25 s ping + pong timeout, exponential reconnect).
- `src/lib/player.svelte.ts` – `<audio>` → Web Audio graph, queues mirrored to the server, Connect roles,
  listening events, Media Session, A-B repeat, speed/pitch, per-output EQ profiles.
- `src/lib/eq.ts` – 10-band EQ, presets, preamp, bass boost, widening/mono matrix, balance; pitch worklet.
- `src/lib/events.ts` – play (≥ 80 % or end) / skip detection, batching to `/events`, opt-in context.
- `src/lib/lyrics.ts`, `queue.ts` – pure helpers.
- `src/screens/*`, `src/components/*` – Musicolet-style UI (tabs: Queues, Now Playing, Folders, Albums,
  Artists, Genres, Playlists, Search, More).

Keyboard: Space play/pause · ←/→ seek 5 s · Shift+←/→ previous/next · L lyrics · M mute · S shuffle ·
R repeat · / search.

## Contract notes (checked against server core's v2 `api/openapi.yaml`, branch claude/server-core-b7e7a3)
Aligned: settings envelope `{settings}` with `context.<signal>` keys, `DataSummary`, `DELETE /me/data?signal=…`
(`all` sends `confirm=true`), `EventContext` (`seeks [{fromMs,toMs}]`, `volumeChanges [{atMs,volume}]`,
`network` wifi/cellular/ethernet/none, ISO weekday), lyrics `?source=user|embedded|lrc_file`, `Device.current`,
Connect `error {detail}`, Tauri CORS and the `/join/<code>` SPA fallback.

Still open:
1. **`/stream` and `/art` don't accept `?token=`.** `<audio>`/`<img>` can't send `Authorization`, so the
   desktop app (cross-origin, bearer) fetches art with the header into blob URLs and, for audio, tries
   `?token=` first and then downloads the whole track with the header into a blob. That works but loses
   streaming/Range for the first seconds of each track. Please accept `?token=` on `/stream` (as on `/connect`).
2. **Connect `state` shape**: the client accepts flat `{"type":"state", ...PlaybackState}` and wrapped
   `{"type":"state","state":{...}}`, and sends flat.
3. **Transfer to self**: the client sends `transfer{toDeviceId: self}` before local playback and ignores a
   `takeover` for the same track arriving within 5 s.
4. **Per-device switches** (`device.<deviceId>.context.<signal>`) aren't offered in the web UI yet; the web
   writes the user-level keys.
5. **No bulk queue edit.** Sorting replays `POST /queues/{id}/move`; "Clear" deletes one position at a time.
   Suggest `PUT /queues/{id}/tracks {trackIds, currentIndex}`.
6. **Share link** has no track deep link; it copies `/#/album/<albumId>?track=<id>`.
7. **Pair code** is shown as text (no QR rendering on web; `qrPayload` is ignored).
8. **Cloudflare Access on the tunnel.** Browsers and the desktop webview can't attach the Access service
   token to `<audio>`, `<img>` or WebSockets; using the player through the tunnel needs an Access policy that
   lets browsers in (an Access login, or a bypass for `/` and `/api` since the app has its own auth).
9. **CSP**: the player CSP has `script-src 'self'`, so the pitch-shifter AudioWorklet ships as
   `dist/pitch-worklet.js` (no blob: modules). Keep serving files from `dist/` root.
