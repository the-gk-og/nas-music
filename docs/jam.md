# Jam: listen together

A signed-in **host** starts a Jam from the queue screen (Android: the people icon; web/desktop: the party
icon). Friends scan the QR code or open the link on any phone: no account and no app. They type a name and
can then:

- see what's playing (with the position) and what's coming up, and who added each song;
- search the host's library (read-only) and add songs, to the end or "play next" if the host allows it;
- vote 👍/👎 on songs and send reactions (🔥 ❤️ 😂 👏 🕺 🙌 😮 👎).

Guests only ever change the **host's queue**. Audio plays wherever the host is playing (phone, browser,
speaker, Cast). Guests never get streams, downloads, account data or admin access.

Host controls: approve-before-add, allow "play next", max songs waiting per guest (or no cap), remove songs,
kick guests, new link / close link, end the Jam.

Signed-in users of the same server who open the link see **Join as <username>**; their entries show their
username. On the guest page they also get **Listen along**, which streams the current song with their own
session and keeps it in sync with the host (Connect position extrapolation, corrected for clock skew,
re-seeking when it drifts more than 1.5 s).

## How it works

| Piece | Where |
| --- | --- |
| Tables `jams`, `jam_guests`, `jam_requests`, `jam_votes` (schema **v6**, additive) | `server/nasmusic/db/schema.sql`, `migrations.migrate_v5_to_v6` |
| Database logic | `server/nasmusic/jam/__init__.py` |
| Guest WebSocket hub | `server/nasmusic/jam/hub.py` |
| HTTP + WebSocket | `server/nasmusic/app/jam_api.py`, `api/openapi.yaml` tag `jam` |
| Guest page | `web/jam.html` + `web/src/jam/` (its own ~22 kB gzip bundle, served at `/jam/<code>`) |
| Host UI | `web/src/components/JamSheet.svelte`, `android/.../ui/screens/JamSheet.kt` |

- **Which queue.** At start: the queue the host is playing (Connect state), else a new empty "Jam" queue.
  The Jam follows whichever of the host's queues is playing later on. Guest adds are ordinary queue edits,
  so the host's devices hear about them via the usual Connect `queues` message.
- **Who added what.** `jam_requests` rows with status `queued` are matched to queue entries by track id, in
  the order they were queued. Removing an entry through the Jam marks its request `removed`, so a later add
  of the same song isn't credited to the wrong person. Entries without a request show the host.
- **Live updates.** `/api/v1/jam/ws` is separate from Connect and scoped to one Jam. The Connect hub has a
  small listener hook (`Hub.listeners`) that tells the Jam hub when the host's state or a queue changes.
  Guests get a full `snapshot` after each change (debounced 100 ms) and light `playback` messages for
  position/pause changes.

## Security

- Join codes: 12 random URL-safe characters (~72 bits), expire after 60 min by default (5 min to 24 h),
  can be regenerated (the old link dies, guests already in stay) or closed.
- Guest tokens: `jg_` + 32 random bytes, stored as SHA-256, scoped to one Jam, accepted only on `/jam/*`
  (they are not device tokens: `/stream`, `/library`, `/me`… all return 401). They die when the guest leaves
  or is kicked, and when the Jam ends (host, or the hard 12 h limit).
- Kick: token dies, pending requests withdrawn, socket gets `kicked` and closes with 4403. A kicked guest
  could rejoin while the link is valid; make a new link to stop that.
- Rate limits (in memory, per process): join 10/min and preview 60/min per client IP; per guest: search
  20/min, add 10/min, votes 60/min, reactions 10 per 30 s, art 300/min; host link changes 20/min.
  Max 50 guests per Jam, 200 sockets per Jam.
- Names: NFC, control/format characters (incl. bidi overrides) removed, whitespace collapsed, max 32.
  Every client renders them as text, never HTML. The guest page runs under the player CSP
  (`script-src 'self'`) with `referrer: no-referrer`.

## Links and networks

The public link uses `tunnel_url` (e.g. `https://music.example.com/jam/<code>`), falling back to the URL
the host's request came in on. The LAN link uses the LAN address; guests on it get the self-signed
certificate warning, so the public link is the default. `/jam/<code>`, `/api/v1/jam/join/*`, `/api/v1/jam/*`
and `/api/v1/jam/ws` must be reachable without Cloudflare Access / relay authentication for guests.
