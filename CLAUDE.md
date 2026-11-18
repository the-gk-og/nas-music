# nas-music

Self-hosted, AI-curated music streaming: a custom server on a TrueNAS box plus a custom Android app.
Replaces LatentJam. Design doc: https://claude.ai/artifact/PAiW16UTjDX9JSseBPeqnt

## Hard rules
- Everything must be free and open source; the repo is AGPL-3.0. Do not add closed-source or
  paid dependencies (no Google ML Kit, no Firebase, no Play Services). Check licences of new deps.
- `api/openapi.yaml` is THE contract between server and app. Change it only deliberately, and
  update both sides (or note the change for the other side in the PR/commit message).
- `server/nasmusic/db/schema.sql` is the shared database contract between server core and the AI.
- The music library is mounted read-only. Never write to it.
- No secrets in the repo (Cloudflare tokens, Last.fm keys go in `config.toml`, which is gitignored).

## Layout and ownership
| Path | What | Owner |
| --- | --- | --- |
| `api/openapi.yaml` | HTTP API contract | shared |
| `server/nasmusic/db/` | SQLite schema + migrations, connection helper | server core |
| `server/nasmusic/{app,library,stream,auth,sync,web}` | FastAPI app, library scanner, streaming/transcode, pairing, sync manifest, web UI | server core |
| `server/nasmusic/ai/` | Analyser, recommender, mix builder, discovery, nightly jobs | AI engine |
| `android/` | Kotlin/Compose app | Android |
| `deploy/` | Docker Compose, cloudflared, TrueNAS notes | deploy |

Server core talks to the AI only through `server/nasmusic/ai/api.py` (functions listed there) and
through the tables in `schema.sql`. The AI never imports from server-core modules except `nasmusic.db`.

## Stack
- Server: Python 3.12, FastAPI, uvicorn, SQLite (WAL), mutagen (tags), ffmpeg (Opus), APScheduler.
  Managed with `uv` (`cd server && uv sync && uv run pytest`).
- AI: numpy, LightGBM, Essentia + Discogs-EffNet (optional extra `analysis`; models downloaded at
  install time, never committed — they are CC BY-NC-SA).
- Android: Kotlin, Jetpack Compose, Media3 (ExoPlayer + MediaSession + download cache), WorkManager,
  Room, OkHttp/Retrofit + kotlinx.serialization, ZXing for QR. minSdk 26.
- Target hardware today: Intel i5-3470 (AVX, **no AVX2**), 16 GB RAM, no GPU. Must run there.

## Networking
App holds two base URLs: LAN (`https://<NAS static IP>:8443`, self-signed cert pinned by SHA-256)
and tunnel (`https://music.<domain>` via Cloudflare Tunnel + Access service token headers
`CF-Access-Client-Id` / `CF-Access-Client-Secret`). It probes `GET /api/v1/health` on LAN with a
1 s timeout on every network change. Night sync uses LAN only unless the user enables tunnel sync.
