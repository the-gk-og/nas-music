# nas-music

A self-hosted, AI-curated music streaming service: a custom server on your NAS that learns what you
like and builds daily mixes, plus an Android app that downloads them overnight and keeps your
favourites offline. Free and open source (AGPL-3.0).

- `server/` — Python server: library, streaming, API, recommender (`server/nasmusic/ai`)
- `android/` — Kotlin/Compose app
- `deploy/` — Docker Compose for TrueNAS SCALE, Cloudflare Tunnel

**Install:** [`deploy/docker/README.md`](deploy/docker/README.md) sets up the server and web player with Docker
Compose on any NAS (Synology, Unraid, QNAP, OpenMediaVault, TrueNAS) or Linux machine.
- `api/openapi.yaml` — the server ↔ app contract

Design doc: https://claude.ai/artifact/PAiW16UTjDX9JSseBPeqnt
