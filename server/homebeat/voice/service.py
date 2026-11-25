"""Voice commands: intent -> (device, content) -> Connect action -> a sentence to speak back.

Runs in two halves. `plan()` is blocking (database, maybe the AI for radio) and runs in a worker thread; it
parses, picks the device, finds the music and, for a play, writes the queue. `run()` executes the plan on the
Connect Hub's loop as a socket-less remote (homebeat.connect.RemoteConn).

Two callers: Home Assistant (POST /voice/command, a voice token) and the app's own mic button (POST
/me/voice/command, a device token). For the app, the calling device is "here": a plan that targets it (or a
transport command with no room named) comes back with `local` set and the app does it itself, so it works
whether or not the phone has a Connect socket open. Everything that picks tracks goes
through family-mode-aware code (library queries, collections.create_queue, discover.start_radio), so voice
can never play what the user's family mode hides.
"""
from __future__ import annotations

import logging
import random
import sqlite3
from dataclasses import dataclass, field

from homebeat import auth
from homebeat.app import collections as coll
from homebeat.app import discover, mixes, playlists
from homebeat.config import Config
from homebeat.db import family
from homebeat.library import queries
from homebeat.voice import match, store
from homebeat.voice.parse import Intent, from_slots, parse

log = logging.getLogger(__name__)

CAST_PREF = {"chromecast": 0.012, "snapcast": 0.01, "dlna": 0.008, "homeassistant": 0.0}
GENERIC_NAMES = {"android": ("phone", "my phone", "mobile"), "web": ("browser", "web player", "computer"),
                 "desktop": ("computer", "desktop", "laptop", "pc"), "watch": ("watch", "my watch")}
KIND_PRIORITY = {"playlist": 0.03, "artist": 0.025, "album": 0.02, "song": 0.01, "mix": 0.015, "genre": 0.005}
NOT_FOUND = "Sorry, I couldn't find {q} in your library."


@dataclass
class Plan:
    intent: str
    ok: bool = True
    speech: str = ""
    device: dict | None = None          # the target Connect device
    queue_id: str | None = None
    item: dict | None = None            # {kind, id, title}
    volume: float | None = None
    transfer_play: bool = False         # resume/play on a device that isn't the active one
    local: bool = False                 # the calling app does it itself (POST /me/voice/command only)
    volume_step: float | None = None    # local volume: relative change for the app to apply
    errors: list[str] = field(default_factory=list)

    def response(self) -> dict:
        out: dict = {"ok": self.ok, "intent": self.intent, "speech": self.speech}
        if self.device:
            out["device"] = {"id": self.device["id"], "name": self.device["name"]}
        if self.item:
            out["item"] = self.item
        if self.queue_id:
            out["queueId"] = self.queue_id
        if self.local:
            out["local"] = True
            if self.volume is not None:
                out["volume"] = self.volume
            if self.volume_step is not None:
                out["volumeStep"] = self.volume_step
        return out


def fail(intent: str, speech: str, **kw) -> Plan:
    return Plan(intent, ok=False, speech=speech, **kw)


# ---------- devices ----------


def all_devices(conn: sqlite3.Connection, uid: str, live: list[dict]) -> list[dict]:
    """The Hub's devices (online sockets, speakers, an offline active one) + the user's other signed-in devices
    (offline), each with its voice aliases and default flag."""
    prefs = store.device_prefs(conn, uid)
    out = [dict(d) for d in live]
    seen = {d["id"] for d in out}
    for r in auth.list_devices(conn, uid):
        if r["id"] not in seen:
            out.append({"id": r["id"], "name": r["name"], "kind": r["kind"], "canPlay": r["kind"] != "other",
                        "online": False, "isActive": False})
            seen.add(r["id"])
    for d in out:
        p = prefs.get(d["id"], {})
        d["aliases"] = p.get("aliases", [])
        d["isDefault"] = p.get("isDefault", False)
    # Aliases for speakers that aren't discovered right now still show in settings (offline).
    for did, p in prefs.items():
        if did not in seen and did.startswith("spk:"):
            out.append({"id": did, "name": did.split(":", 2)[-1], "kind": "speaker", "canPlay": True,
                        "online": False, "isActive": False, "castType": did.split(":")[1], **p})
    return out


def _labels(d: dict) -> list[tuple[str, tuple[dict, float]]]:
    out = [(a, (d, 0.03)) for a in d.get("aliases", [])]
    out.append((d["name"], (d, 0.0)))
    if d.get("kind") == "speaker" and d.get("id", "").startswith("spk:homeassistant:"):
        out.append((d["id"].split(":", 2)[2].replace("media_player.", "").replace("_", " "), (d, -0.01)))
    for g in GENERIC_NAMES.get(d.get("kind", ""), ()):
        out.append((g, (d, -0.04)))
    return out


def _dev_bonus(item: tuple[dict, float]) -> float:
    d, b = item
    return b + (0.02 if d.get("online") else 0.0) + CAST_PREF.get(d.get("castType") or "", 0.0) + \
        (0.005 if d.get("isActive") else 0.0)


def find_device(phrase: str, devices: list[dict], threshold: float = match.THRESHOLD) -> dict | None:
    cands = [lab for d in devices if d.get("canPlay", True) for lab in _labels(d)]
    m = match.best(phrase, cands, threshold, bonus=_dev_bonus)
    return m.item[0] if m else None


def area_device(devices: list[dict], area: str | None, area_devices: list[str] | None) -> dict | None:
    """The device in the room the voice satellite is in: an alias equal to the area name, one of the area's
    media players (HA entity ids or names), or a device named after the room."""
    playable = [d for d in devices if d.get("canPlay", True)]
    for ref in area_devices or []:
        ref = str(ref)
        for d in playable:
            if d["id"] == f"spk:homeassistant:{ref}" or d["id"] == ref:
                return _same_named(d, playable)
        hit = find_device(ref.replace("media_player.", "").replace("_", " ") if ref.startswith("media_player.")
                          else ref, playable, 0.9)
        if hit:
            return hit
    if area:
        alias_hits = [(match.score(area, a), d) for d in playable for a in d.get("aliases", [])]
        alias_hits = [h for h in alias_hits if h[0] >= 0.9]
        if alias_hits:
            return max(alias_hits, key=lambda h: (h[0], _dev_bonus((h[1], 0))))[1]
        return find_device(area, [d for d in playable if d.get("kind") == "speaker"], 0.84)
    return None


def _same_named(d: dict, devices: list[dict]) -> dict:
    """An HA media_player that is also discovered natively (a Nest Hub is both a Chromecast and an HA entity):
    prefer the native one."""
    best = d
    for o in devices:
        if o is not d and o.get("kind") == "speaker" and match.score(o["name"], d["name"]) >= 0.97 and \
                CAST_PREF.get(o.get("castType") or "", 0) > CAST_PREF.get(best.get("castType") or "", 0):
            best = o
    return best


def default_device(devices: list[dict]) -> dict | None:
    return next((d for d in devices if d.get("isDefault") and d.get("canPlay", True)), None)


def pick_split(it: Intent, devices: list[dict]) -> None:
    """Choose where the play query ends and the device phrase starts (see parse.device_splits)."""
    if it.device:
        return
    for before, phrase in it.splits:
        d = find_device(phrase, devices)
        if d is not None:
            it.device = phrase
            if it.name == "play":
                from homebeat.voice.parse import play_query

                it.query, it.kind, it.artist = play_query(before)
            return


# ---------- content ----------


def _artists(conn, uid):
    return [(r["name"], ("artist", r["id"], r["name"]))
            for r in conn.execute(queries.artist_select(queries.fam(conn, uid)) + " GROUP BY ar.id")]


def _albums(conn, uid, artist: str | None):
    rows = conn.execute(queries.album_select(queries.fam(conn, uid))).fetchall()
    out = []
    for r in rows:
        if artist and match.score(artist, r["artist"]) < match.THRESHOLD:
            continue
        out.append((r["title"], ("album", r["id"], r["title"], r["artist"])))
    return out


def _playlists(conn, uid):
    return [(p["name"], ("playlist", p["id"], p["name"])) for p in playlists.list_playlists(conn, uid)]


def _genres(conn, uid):
    return [(g["name"], ("genre", g["name"], g["name"])) for g in queries.genres(conn, uid)]


def _mixes(conn, uid):
    out = []
    for m in mixes.today(conn, uid):
        out.append((m["title"], ("mix", m["id"], m["title"])))
        if m.get("kind"):
            out.append((f"{m['kind'].replace('_', ' ')} mix", ("mix", m["id"], m["title"])))
    return out


def _songs(conn, uid, cfg: Config, q: str, artist: str | None):
    seen, rows = set(), []
    words = sorted(match.norm(q).split(), key=len, reverse=True)
    for attempt in [q] + words[:3]:
        if not attempt:
            continue
        for t in queries.search(conn, uid, attempt, 100, cfg.data_dir)["tracks"]:
            if t["id"] not in seen:
                seen.add(t["id"])
                rows.append(t)
        if len(rows) >= 20:
            break
    out = []
    for t in rows:
        if artist and match.score(artist, t["artist"]) < match.THRESHOLD:
            continue
        out.append((t["title"], ("song", t["id"], t["title"], t["artist"])))
    return out


def find_content(conn: sqlite3.Connection, cfg: Config, uid: str, it: Intent):
    """Best (kind, id, title, [artist]) for the query, or None. kind is one of playlist, album, artist, song,
    mix, genre."""
    q, kind = it.query, it.kind
    pools = {
        "playlist": lambda: _playlists(conn, uid),
        "artist": lambda: _artists(conn, uid),
        "album": lambda: _albums(conn, uid, it.artist),
        "song": lambda: _songs(conn, uid, cfg, q, it.artist),
        "mix": lambda: _mixes(conn, uid),
        "genre": lambda: _genres(conn, uid),
    }
    if kind in ("playlist", "artist", "album", "song", "mix"):
        m = match.best(q, pools[kind]())
        return m.item if m else None
    order = ("playlist", "artist", "genre", "album", "song", "mix") if kind == "radio" else \
        ("playlist", "artist", "album", "song", "mix")
    cands = [c for k in order for c in pools[k]()]
    m = match.best(q, cands, bonus=lambda item: KIND_PRIORITY.get(item[0], 0.0))
    return m.item if m else None


def _artist_tracks(conn, uid, artist_id: str) -> list[str]:
    ids = [r[0] for r in conn.execute(
        "SELECT t.id FROM tracks t JOIN albums al ON al.id = t.album_id WHERE (t.artist_id = ? OR al.artist_id = ?) "
        f"AND t.missing = 0{queries.fam(conn, uid)}", (artist_id, artist_id))]
    random.shuffle(ids)
    return ids


def _queue(conn, uid, name, ids, protect) -> str | None:
    ids = family.filter_ids(conn, uid, ids)
    if not ids:
        return None
    return coll.create_queue(conn, uid, name, ids, 0, protect=protect, replace_same_name=True)["id"]


def _radio(conn, cfg, uid, seed: dict, protect, device_kind: str) -> dict:
    return discover.start_radio(conn, cfg, uid, seed, protect, None, device_kind)


def play_content(conn: sqlite3.Connection, cfg: Config, uid: str, it: Intent, protect: str | None,
                 device: dict, dry_run: bool = False) -> Plan:
    dk = "speaker" if device.get("kind") == "speaker" else {"android": "phone", "watch": "watch",
                                                             "desktop": "desktop"}.get(device.get("kind"), "web")
    say_q = it.query or "that"
    try:
        if it.kind == "moment" or (not it.query and it.kind in ("vibe", "radio", "any")):
            if dry_run:
                return Plan("play", speech="Smart shuffle", item={"kind": "moment", "id": None, "title": "Smart shuffle"})
            q = _radio(conn, cfg, uid, {"type": "moment"}, protect, dk)
            return Plan("play", queue_id=q["id"], item={"kind": "moment", "id": None, "title": q["name"]},
                        speech="some music for right now")
        if it.kind == "vibe":
            return _vibe(conn, cfg, uid, it.query, protect, dk, dry_run)
        found = find_content(conn, cfg, uid, it)
        if found is None:
            if it.kind in ("radio", "any"):
                # Not a name in the library: maybe a mood / genre / era ("play chill radio", "play 80s rock").
                try:
                    vibe = discover.parse_vibe(conn, cfg, uid, it.query)
                except Exception:  # noqa: BLE001 - AI engine unavailable
                    vibe = {}
                if vibe.get("constraints"):
                    return _vibe(conn, cfg, uid, it.query, protect, dk, dry_run)
            return fail("play", NOT_FOUND.format(q=say_q))
        kind, ident, title = found[0], found[1], found[2]
        item = {"kind": kind, "id": ident, "title": title}
        if it.kind == "radio":
            if kind == "genre":
                return _vibe(conn, cfg, uid, title, protect, dk, dry_run, title=f"{title} radio")
            if kind == "mix":
                kind = "mix"   # a mix is already a radio-ish list: just play it
            else:
                item = {"kind": "radio", "id": ident, "title": f"{title} radio", "seed": kind}
                if dry_run:
                    return Plan("play", item=item, speech=item["title"])
                seed_type = "track" if kind == "song" else kind
                q = _radio(conn, cfg, uid, {"type": seed_type, "id": ident}, protect, dk)
                item["title"] = q["name"]
                return Plan("play", queue_id=q["id"], item=item, speech=q["name"])
        if dry_run:
            return Plan("play", item=item, speech=title)
        if kind == "playlist":
            ids = [t["id"] for t in playlists.get_playlist(conn, uid, ident)["tracks"]]
            qid, speech = _queue(conn, uid, title, ids, protect), f"your playlist {title}"
        elif kind == "album":
            alb = queries.album(conn, uid, ident, cfg.data_dir)
            ids = [t["id"] for t in (alb or {}).get("tracks", [])]
            artist = found[3] if len(found) > 3 else None
            qid, speech = _queue(conn, uid, title, ids, protect), f"{title} by {artist}" if artist else title
        elif kind == "artist":
            qid, speech = _queue(conn, uid, title, _artist_tracks(conn, uid, ident), protect), title
        elif kind == "mix":
            m = mixes.get(conn, uid, ident) or {"tracks": []}
            qid, speech = _queue(conn, uid, title, [x["track"]["id"] for x in m["tracks"]], protect), f"your {title}"
        elif kind == "genre":
            return _vibe(conn, cfg, uid, title, protect, dk, dry_run)
        else:  # song: the song, then a radio from it so the music doesn't stop after three minutes
            artist = found[3] if len(found) > 3 else None
            speech = f"{title} by {artist}" if artist else title
            qid = None
            try:
                q = _radio(conn, cfg, uid, {"type": "track", "id": ident}, protect, dk)
                rest = [t["id"] for t in q["tracks"] if t["id"] != ident] if q.get("tracks") else \
                    [i for i in coll.queue_track_ids(conn, uid, q["id"]) if i != ident]
                coll.replace_queue_tracks(conn, uid, q["id"], [ident] + rest, 0)
                qid = q["id"]
            except (NotImplementedError, discover.NotFound, coll.NotFound, KeyError, ValueError):
                qid = _queue(conn, uid, title, [ident], protect)
        if qid is None:
            return fail("play", f"There's nothing in {title} you can play here.", item=item)
        return Plan("play", queue_id=qid, item=item, speech=speech)
    except NotImplementedError:
        return fail("play", "Radio isn't available on the server yet.")
    except (discover.NotFound, discover.BadSeed, coll.NotFound, KeyError):
        return fail("play", NOT_FOUND.format(q=say_q))


def _vibe(conn, cfg, uid, text, protect, dk, dry_run, title: str | None = None) -> Plan:
    item = {"kind": "vibe", "id": None, "title": title or text}
    if dry_run:
        return Plan("play", item=item, speech=f"{text} music")
    q = _radio(conn, cfg, uid, {"type": "vibe", "text": text}, protect, dk)
    return Plan("play", queue_id=q["id"], item=item, speech=title or f"{text} music")


# ---------- the whole plan ----------


def intent_for(body: dict) -> Intent:
    if body.get("intent"):
        return from_slots(str(body["intent"]), body.get("slots") or {})
    return parse(str(body.get("text") or ""))


def now_playing(conn, uid, state: dict, devices: list[dict]) -> Plan:
    tid = state.get("trackId")
    t = queries.track(conn, uid, tid) if tid else None
    if t is None:
        return Plan("now_playing", speech="Nothing is playing right now.")
    dev = next((d for d in devices if d["id"] == state.get("activeDeviceId")), None)
    where = f" on {dev['name']}" if dev else ""
    if state.get("playing"):
        speech = f"This is {t['title']} by {t['artist']}{where}."
    else:
        speech = f"{t['title']} by {t['artist']} is paused{where}."
    return Plan("now_playing", speech=speech, device=dev, item={"kind": "song", "id": t["id"], "title": t["title"]})


def add_to_playlist(conn: sqlite3.Connection, uid: str, it: Intent, track_id: str | None, dry: bool) -> Plan:
    """"add this to Gym": the playing song onto one of the user's playlists (owned or shared with edit rights)."""
    t = queries.track(conn, uid, track_id) if track_id else None
    if t is None:
        return fail("add_to_playlist", "Nothing is playing to add.")
    m = match.best(it.query, _playlists(conn, uid)) if it.query else None
    if m is None:
        return fail("add_to_playlist", f"I couldn't find a playlist called {it.query or 'that'}.")
    _, pid, name = m.item
    item = {"kind": "playlist", "id": pid, "title": name}
    if t["id"] in playlists._playlist_ids(conn, pid):
        return Plan("add_to_playlist", item=item, speech=f"{t['title']} is already in {name}.")
    if not dry:
        try:
            playlists.add_tracks(conn, uid, pid, [t["id"]])
        except coll.Forbidden:
            return fail("add_to_playlist", f"You can only listen to {name}, not add to it.", item=item)
        except (coll.NotFound, coll.BadRequest):
            return fail("add_to_playlist", f"I couldn't add that to {name}.", item=item)
    return Plan("add_to_playlist", item=item, speech=f"Added {t['title']} to {name}.")


def plan(conn: sqlite3.Connection, cfg: Config, uid: str, body: dict, live: list[dict], state: dict,
         caller: dict | None = None) -> Plan:
    """`caller`: the app device asking (POST /me/voice/command), which is "here" and is online by definition."""
    it = intent_for(body)
    devices = all_devices(conn, uid, live)
    me = None
    if caller is not None:
        me = next((d for d in devices if d["id"] == caller["id"]), None)
        if me is None:
            me = {**caller, "canPlay": True, "isActive": False}
            devices.append(me)
        me["online"] = True
    dry = bool(body.get("dryRun"))
    if it.name == "unknown":
        return fail("unknown", "Sorry, I didn't understand that.")
    if it.name == "add_to_playlist":
        return add_to_playlist(conn, uid, it, body.get("trackId") or state.get("trackId"), dry)
    if it.name == "now_playing":
        p = now_playing(conn, uid, state, devices)
        if me is not None and (p.device is None or p.device["id"] == me["id"]):
            p.local = True   # the app knows best what it is playing
        return p
    pick_split(it, devices)
    if it.name == "play" and it.splits and not it.device:
        # "... in the bedroom" but no device is called that: say so, unless the whole phrase is a name in the
        # library ("Songs in the Key of Life").
        whole = it.kind not in ("vibe", "moment", "radio") and find_content(conn, cfg, uid, it) is not None
        if not whole:
            return fail("play", f"I couldn't find a speaker called {it.splits[0][1]}.")
    active = next((d for d in devices if d["id"] == state.get("activeDeviceId")), None)
    named = None
    if it.device:
        named = find_device(it.device, devices)
        if named is None:
            return fail(it.name, f"I couldn't find a speaker called {it.device}.")
    if me is not None:
        return _plan_for_app(conn, cfg, uid, it, state, me, named, active, dry)
    here = area_device(devices, body.get("area"), body.get("areaDevices"))
    fallback = here or default_device(devices)

    if it.name == "play":
        target = named or here or active or default_device(devices)
        if target is None:
            return fail("play", "Where should I play it? Say the room or speaker name, or pick a default "
                                "speaker in Homebeat's voice settings.")
        if not target.get("online"):
            return fail("play", f"{target['name']} is offline.", device=target)
        p = play_content(conn, cfg, uid, it, (active and state.get("queueId")) or None, target, dry)
        p.device = target
        if p.ok:
            p.speech = f"Playing {p.speech} on {target['name']}."
        return p

    # transport controls: they act on what's playing
    if it.name == "resume":
        if named is not None and named["id"] != state.get("activeDeviceId") or active is None:
            target = named or fallback
            if target is None:
                return fail("resume", "Nothing is playing. Say the room to play in.")
            if not state.get("queueId"):
                return fail("resume", "There's nothing to resume.", device=target)
            if not target.get("online"):
                return fail("resume", f"{target['name']} is offline.", device=target)
            return Plan("resume", device=target, transfer_play=True, speech=f"Resuming on {target['name']}.")
        return Plan("resume", device=active, speech="Resuming.")
    if active is None or (named is not None and named["id"] != active["id"]):
        where = f" on {named['name']}" if named is not None else ""
        return fail(it.name, f"Nothing is playing{where}.")
    if it.name == "volume":
        cur = float(state.get("volume") if state.get("volume") is not None else 1.0)
        v = it.volume if it.volume is not None else cur + (it.volume_step or 0)
        v = round(max(0.0, min(1.0, v)), 2)
        return Plan("volume", device=active, volume=v, speech=f"Volume {round(v * 100)} percent.")
    speech = {"pause": "Paused.", "next": "Skipping.", "previous": "Going back."}[it.name]
    return Plan(it.name, device=active, speech=speech)


def _plan_for_app(conn, cfg, uid, it: Intent, state: dict, me: dict, named: dict | None, active: dict | None,
                  dry: bool) -> Plan:
    """The app's mic button. Plays on the named room, else on whatever this app is already controlling (the
    active device), else on the phone itself. Anything aimed at the phone itself, and transport commands with
    no room named (the app's player already routes those to whatever it controls), come back `local`."""
    if it.name == "play":
        target = named or active or me
        if not target.get("online"):
            return fail("play", f"{target['name']} is offline.", device=target)
        p = play_content(conn, cfg, uid, it, (active and state.get("queueId")) or None, target, dry)
        p.device = target
        p.local = target["id"] == me["id"]
        if p.ok:
            p.speech = f"Playing {p.speech}" + ("." if p.local else f" on {target['name']}.")
        return p
    if named is not None and named["id"] != me["id"]:
        # "pause in the kitchen": as for Home Assistant, through Connect.
        if it.name == "resume" and named["id"] != state.get("activeDeviceId"):
            if not state.get("queueId"):
                return fail("resume", "There's nothing to resume.", device=named)
            if not named.get("online"):
                return fail("resume", f"{named['name']} is offline.", device=named)
            return Plan("resume", device=named, transfer_play=True, speech=f"Resuming on {named['name']}.")
        if active is None or named["id"] != active["id"]:
            return fail(it.name, f"Nothing is playing on {named['name']}.")
        if it.name == "volume":
            cur = float(state.get("volume") if state.get("volume") is not None else 1.0)
            v = round(max(0.0, min(1.0, it.volume if it.volume is not None else cur + (it.volume_step or 0))), 2)
            return Plan("volume", device=active, volume=v, speech=f"Volume {round(v * 100)} percent.")
        return Plan(it.name, device=active, speech=_CONTROL_SPEECH[it.name])
    p = Plan(it.name, device=me, local=True, speech=_CONTROL_SPEECH.get(it.name, ""))
    if it.name == "volume":
        p.volume, p.volume_step = it.volume, it.volume_step
        p.speech = f"Volume {round(it.volume * 100)} percent." if it.volume is not None else \
            ("Louder." if (it.volume_step or 0) > 0 else "Quieter.")
    return p


_CONTROL_SPEECH = {"pause": "Paused.", "resume": "Resuming.", "next": "Skipping.", "previous": "Going back."}

ACTION = {"pause": "pause", "resume": "play", "next": "next", "previous": "prev", "volume": "setVolume"}


async def run(hub, p: Plan, uid: str, requester) -> Plan:
    """Execute a plan on the Hub (the Hub's loop). Hub errors turn the plan into a failure."""
    if not p.ok or p.local or p.intent in ("now_playing", "unknown", "add_to_playlist"):
        return p
    d = p.device or {}
    errs: list[dict] = []
    if p.intent == "play":
        if p.queue_id is None:
            return p   # dry run
        hub.notify_queue(uid, p.queue_id)
        state = await hub.state_now(uid)
        if d.get("kind") != "speaker" and d["id"] != state.get("activeDeviceId"):
            errs += await hub.remote(uid, {"type": "transfer", "toDeviceId": d["id"], "play": False}, requester)
        if not errs:
            errs += await hub.remote(uid, {"type": "command", "action": "playQueue", "targetDeviceId": d["id"],
                                           "args": {"queueId": p.queue_id, "index": 0}}, requester)
    elif p.transfer_play:
        errs += await hub.remote(uid, {"type": "transfer", "toDeviceId": d["id"], "play": True}, requester)
    else:
        args = {"volume": p.volume} if p.intent == "volume" else {}
        errs += await hub.remote(uid, {"type": "command", "action": ACTION[p.intent], "args": args}, requester)
    if errs:
        p.ok = False
        detail = str(errs[0].get("detail") or "")
        p.errors = [str(e.get("detail")) for e in errs]
        if "offline" in detail:
            p.speech = f"{d.get('name') or 'Your player'} is offline."
        elif "no active device" in detail:
            p.speech = "Nothing is playing."
        else:
            p.speech = f"Sorry, that didn't work: {detail}."
    return p
