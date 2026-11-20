# Voice control through Home Assistant Assist

Say things like these to a Home Assistant voice satellite, the HA phone app, or the Assist dialog:

| You say | What happens |
| --- | --- |
| "Play my Pop radio in the bedroom" | starts a radio from your playlist/artist/genre called Pop on the bedroom speaker |
| "Play something chill in Stacey's room" | a "describe a vibe" radio for *chill* on Stacey's speaker |
| "Play AJR" | AJR's songs, shuffled, where you are (or on whatever's playing) |
| "Play the album Neotheater" / "Play Bang by AJR" / "Play my road trip playlist" / "Play my daily mix" | that album / song (then a radio from it) / playlist / mix |
| "Play some music" | the smart shuffle for right now |
| "Skip", "Previous song", "Pause the music", "Resume the music" | controls whatever you're playing |
| "Set the music volume to 40", "Turn the music up" | volume of the device that's playing |
| "What's playing?" | "This is Bang by AJR on Elijah bedroom display." |

Everything is free and open source: Home Assistant's own Assist (with any FOSS speech-to-text/text-to-speech you
already use, e.g. Whisper + Piper) and plain YAML. Nothing is installed into Home Assistant.

**How it works.** Home Assistant only recognises that a sentence is about music and forwards what you said to
your nas-music server (`POST /api/v1/voice/command`). nas-music understands it: it matches names fuzzily
against *your* library, playlists and mixes, and rooms against your speakers and the room names you give them,
plays through Connect (exactly as if you'd picked it in the app), and replies with the sentence Home Assistant
speaks. Family mode applies: anything it hides from you can't be played by voice either.

Requirements: Home Assistant **2024.6 or newer** (needs `trigger.device_id` for "the room I'm in" and
`rest_command` responses), English, and a nas-music server with this feature (API 0.7, schema v7).

---

## Step 1 — Create a voice token in nas-music

1. Open nas-music (web player, desktop app or Android app) → **Settings → Voice control**.
2. Type a name (e.g. `Home Assistant`) and press **Create voice token**.
3. **Copy the token now** (it starts with `nmv_`). It is shown only once.

A voice token belongs to *you* and can do one thing only: send voice commands as you. It can't sign in, browse,
stream or change settings, so it's safe to keep in Home Assistant. Revoke it in the same place at any time
(it stops working immediately). Everyone in the house who wants their own music, history and family-mode rules
should make their own token (see "Several people" below).

## Step 2 — Name your rooms (optional but recommended)

Still in **Settings → Voice control**, every speaker and device is listed. Add the names you'll say, separated
by commas:

| Device | Voice names |
| --- | --- |
| Elijah bedroom display | `bedroom, Elijah's room` |
| Stacey's room speaker | `Stacey's room` |
| Living Room TV | `lounge, living room` |

Names already work without aliases when they're part of the device's name ("bedroom" finds "Elijah bedroom
display"), but aliases make it exact. Mark one device as **Default**: it's used when you don't name a room,
the satellite's room has no speaker, and nothing is playing.

> Speakers that show up twice (a Nest Hub is both a Chromecast and a Home Assistant `media_player`) are fine:
> nas-music prefers the native Chromecast one.

## Step 3 — Add the token to Home Assistant's secrets

Edit `secrets.yaml` in your Home Assistant config folder (File editor / Studio Code Server add-on, or Samba):

```yaml
nasmusic_voice_url: https://192.168.1.50:8443/api/v1/voice/command   # your NAS's LAN address and HTTPS port
nasmusic_voice_auth: Bearer nmv_PASTE_YOUR_TOKEN_HERE                 # keep the word "Bearer " in front
```

Home Assistant and the NAS are on the same network, so use the LAN address (`verify_ssl: false` in the package
accepts nas-music's self-signed certificate; the request never leaves your network). If Home Assistant can
only reach nas-music through the Cloudflare tunnel, use `https://music.<your domain>/api/v1/voice/command`,
set `verify_ssl: true`, and uncomment the two `CF-Access-Client-*` headers in the package with your Access
service token in `secrets.yaml` (`nasmusic_cf_id`, `nasmusic_cf_secret`).

## Step 4 — Install the package

1. Turn on packages, if you haven't, in `configuration.yaml`:

   ```yaml
   homeassistant:
     packages: !include_dir_named packages
   ```

2. Create the folder `packages/` next to `configuration.yaml` and copy
   [`nasmusic_voice.yaml`](nasmusic_voice.yaml) into it.
3. **Developer tools → YAML → Check configuration**, then **Restart** (the first time; later edits only need
   **Reload: automations, scripts, REST commands**).

That's all. The package adds:

- `rest_command.nasmusic_voice` — the HTTP call to nas-music.
- `script.nasmusic_voice` — "send this sentence (for this room)"; usable from dashboards and your own automations.
- the automation **nas-music voice control** — catches the music sentences from Assist and speaks the answer.

## Step 5 — Try it

1. **Developer tools → Actions**, pick **nas-music voice command**, text `what's playing`, **Perform action**:
   the response shows `speech: Nothing is playing right now.` (or what is). An `I couldn't reach nas-music`
   means the URL is wrong or the NAS isn't reachable; `doesn't accept the voice token` means the token is wrong
   or revoked (check the `Bearer ` prefix).
2. Open **Assist** (the speech-bubble icon top right, or the phone app) and type `play some music in the bedroom`.
3. Then talk to a voice satellite.

### "The room I'm in"

When you don't name a room, nas-music plays in the room of the satellite you're talking to: Home Assistant
sends the satellite's **area** and the `media_player`s in it. For that, put each satellite *and* its speaker in
the same area (**Settings → Areas**), or give the speaker a voice name equal to the area's name (Step 2).
"Skip", "pause" and the other controls always act on whatever is playing for you, wherever it is.

## Several people

Each person creates their own voice token in their own nas-music account. Home Assistant decides whose music it
is by which token it sends, so the simplest set-up is per room (Stacey's satellite uses Stacey's token): copy the
package once per person with a different `rest_command`/`script` name and secret, and give each automation's
triggers to that person's satellites with a condition such as
`condition: template` / `value_template: "{{ trigger.device_id == 'abc123…' }}"`.

## Alternative: Home Assistant does the intent recognition

If you'd rather have Home Assistant recognise intents and slots itself (e.g. to combine them with your own
sentences), use [`custom_sentences/en/nasmusic.yaml`](custom_sentences/en/nasmusic.yaml) (copy to
`<config>/custom_sentences/en/`) together with [`nasmusic_intents.yaml`](nasmusic_intents.yaml) (into
`packages/`), and **delete the `automation:` block** from `nasmusic_voice.yaml` so sentences aren't handled
twice. nas-music then receives `{"intent": "NasPlay", "slots": {"query": "..."}}` instead of the raw sentence.
Room-of-the-satellite targeting isn't available this way (intent scripts don't see which satellite spoke), but
"... in the bedroom" in the sentence still works.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| Assist answers "Sorry, I couldn't understand that" | The sentence didn't match a trigger. Start with "play", or say "pause **the music**" (plain "pause" is left to Home Assistant's own media controls). |
| "I couldn't find a speaker called …" | Add that word as a voice name for the speaker (Step 2). Speakers are discovered by nas-music (Chromecast, DLNA, Snapcast, Home Assistant media players); check they're listed under Settings → Voice control. |
| "Sorry, I couldn't find … in your library" | Speech-to-text misheard the name, or family mode hides it. Try the Assist dialog with the exact name. |
| "… is offline" | The phone/browser named isn't connected; speakers must be reachable from the NAS. |
| "Radio isn't available on the server yet" | The AI engine isn't installed/analysed yet; playlists, albums, artists and songs still work. |
| Nothing at all, automation trace shows an error | Check the URL in `secrets.yaml` and that HA can reach the NAS on that port. |

The nas-music server log has one line per voice command (`voice <user> (<token name>): '<text>' -> <intent> ok=…`).

## API reference

`POST /api/v1/voice/command` with `Authorization: Bearer nmv_…`:

```json
{"text": "play my pop radio in the bedroom", "area": "Bedroom",
 "areaDevices": ["media_player.elijah_bedroom_display"], "dryRun": false}
```

or with Home Assistant's intent instead of text: `{"intent": "NasPlay", "slots": {"query": "pop radio"}}`
(intents: NasPlay, NasPause, NasResume, NasNext, NasPrevious, NasVolume, NasVolumeUp, NasVolumeDown,
NasNowPlaying, and HA's HassMediaPause/Unpause/Next/Previous, HassSetVolume). Answer:

```json
{"ok": true, "intent": "play", "speech": "Playing Pop radio on Elijah bedroom display.",
 "device": {"id": "spk:chromecast:…", "name": "Elijah bedroom display"},
 "item": {"kind": "radio", "id": "…", "title": "Pop radio"}, "queueId": "…"}
```

`ok: false` answers still come with a `speech` to say. `dryRun: true` only resolves (nothing plays). Full schema:
`api/openapi.yaml` (tag `voice`).
