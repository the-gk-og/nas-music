# nasmusic.ai — the AI engine

Analyser, recommender, mix builder and album discovery. Server core calls only the functions in
[`api.py`](api.py) and shares data through the AI-owned tables in `db/schema.sql`
(`features`, `analysis_jobs`, `taste_snapshots`, `mixes`, `mix_tracks`, `suggestions`). The engine
reads `tracks`, `artists`, `albums`, `events`, `settings`, and in v2 `users`, `user_tracks` and
`user_settings`.

**v2 is multi-user** (docs/v2.md §3, §10). Audio analysis is shared. Everything else (signals, taste
model, mixes, explanations, suggestions) is computed per user, from that user's `events` and
`user_tracks` only. See [Multi-user](#multi-user-v2) and [Listening context](#listening-context-v2).

| Module | What it does |
| --- | --- |
| `api.py` | The contract (v2 signatures). `analyse_pending`, `run_nightly`, `run_nightly_for_user`, `run_weekly_discovery` never raise; `more_like` raises `KeyError` for an unknown user, or an unknown or not-yet-analysed track |
| `store.py` | Per-user reads and writes (`user_id` filters, the anonymous popularity prior, the transitional v1 path) |
| `context.py` | `events.context` parsing, collection switches, context mixes, listening-detail signals |
| `extractors.py` | Audio → features, one class per `model_version` |
| `models.py` | Downloads and SHA-256-verifies Essentia model files into `data_dir/models/<version>/` |
| `analyser.py` | Resumable process-pool analysis; `benchmark()` |
| `recommender.py` | Feedback signals, cold-start taste centres, LightGBM play-through model, scoring |
| `mixes.py` | Daily / Morning / Evening / Deep Cuts / More-like, ordering and reasons |
| `discovery.py` | Weekly album suggestions (ListenBrainz, MusicBrainz, Last.fm, Deezer previews) |
| `__main__.py` | Maintenance CLI: `python -m nasmusic.ai --help` |

## Multi-user (v2)

```python
analyse_pending(conn, cfg, limit=None) -> int                       # global
run_nightly(conn, cfg) -> dict[str, list[str]]                      # every non-disabled user
run_nightly_for_user(conn, cfg, user_id) -> list[str]
more_like(conn, cfg, user_id, track_id, minutes=40) -> str          # KeyError: unknown user/track
explain(conn, cfg, user_id, track_id) -> tuple[str, list[str]]
run_weekly_discovery(conn, cfg) -> dict[str, int]
```

* **Isolation.** `store.Store` is the only place that reads per-user tables, and every read is
  filtered by `user_id`. Mixes, taste snapshots and suggestions are written with the user's id, and
  nightly deactivation and the `more_like` cap (3 active) only touch that user's mixes. "Not
  interested" answers, and their penalty on similar artists, apply only to the user who gave them. Tests:
  `tests/ai/test_multiuser.py`.
* **New users** (< `ai.rec.new_user_plays` = 20 plays) get a library-wide cold start:
  `0.35·sound + 0.3·popularity + 0.15·recently added + 0.2·own likes`. "Sound" is the match to
  8 popularity-weighted k-means centres over the **whole library**, and mixes take tracks from those
  centres in turn, so a first Daily Mix covers the library's range. **Popularity** is the only
  cross-user input: `SUM(user_tracks.play_count)` per track over *other* users, with no ids, events,
  stars or skips. A test checks that two different histories with equal per-track play counts give a
  new user identical scores. From 20 plays on, the user's own centres take over and no cross-user
  data is read. Reasons say which signal picked a track ("Popular with listeners on this server",
  "Recently added to the library", "A taste of the library's Hard Bop").
* **Per-user settings.** A user's `user_settings` rows with `ai.*` keys from the table below override
  the global value for that user (for example a longer Daily Mix). `user_settings['timezone']`
  overrides `cfg.timezone`.
* **Transitional v1 path.** On a database without `user_tracks` (before server core's migration), the
  engine runs single-user from the v1 columns, and `run_nightly` / `run_weekly_discovery` return the
  v1 types. `more_like(conn, cfg, track_id, minutes)` and `explain(conn, cfg, track_id)` are also
  accepted, which is why `explain`'s `track_id` has a default. Remove `Store.legacy` once every
  deployment is on v2.

## Listening context (v2)

`events.context` (JSON, opt-in per signal) is parsed by `context.py`. Every field is optional, and
missing means unknown.

* **Model features.** The LightGBM model gains output, motion, place, network and weather as
  LightGBM categoricals (NaN = unknown), plus temperature. `localHour` / `weekday` from the context
  override the ones derived from `at`. Context mixes are scored with their context set (for example
  Drive → `output=car`).
* **Context mixes** (`mixes.kind = 'context'`, ~`ai.mix.context.minutes`) are built when the user has
  at least `ai.mix.context.min_plays` (30) plays in that context, at most `ai.mix.context.max` (3) a
  night, most-played first:

  | Context | When | Title |
  | --- | --- | --- |
  | `workout` | `motion=running`, or a place named like gym / fitness / workout / sport / pool / climb | Workout |
  | `drive` | `output=car` | Drive |
  | `late_night` | `place=home` and hour in `ai.mix.late_night.hours` (22–4) | Late night |
  | `place:<name>` | any user-named place (not the phone's default `place-N`) | At \<name\> |

  They may share tracks with the time-of-day mixes. Their "known" share comes from what the user plays
  in that context ("You often play this when working out"), and Workout follows a high energy curve.
* **Listening-detail signals.** `repeatOfPrevious` (or the same track replayed within 30 minutes) is
  +1.5, a strong like. Seeking through the track (forward seeks covering more than 30 % of it, or 3+
  forward seeks) is −0.5, a mild dislike. Seeking back to re-hear a part is +0.3. Turning the volume
  up is +0.2, and turning it well down is −0.2.
* **Switches.** If `user_settings[<switch>]` is `false`, the fields it covers are ignored, even in old
  events. Assumed keys: `context.listening_details`, `context.audio_output`, `context.device_network`,
  `context.movement`, `context.location`, `context.weather`, `context.time` (see Spec gaps).

### Spec gaps (assumptions the AI engine made; to settle in docs/v2.md)

1. `mixes.kind` must allow `'context'`. If the CHECK refuses it, context mixes are skipped with a warning
   and the other mixes are still written.
2. `user_settings`: primary key `(user_id, key)` and JSON `value`. The switch key names above and the
   `timezone` key are not in the spec.
3. `events.context` sub-formats:
   * `seeks[]` items `{from, to}` in seconds (or `fromMs`/`toMs`);
   * `volumeChanges[]` items `{from, to}` on a 0–1 scale, `{delta}`, or a bare number;
   * `weather` `{condition, tempC}` or a plain condition string;
   * `output` `"<kind>"` or `"bluetooth:<name>"`.
4. `user_tracks.play_count` must be kept up to date by server core: it drives the popularity prior.
   Same for `last_played`, which mixes use for "nothing played in the last 3 days".
5. An index on `events(user_id, at)`.
6. `DELETE /me/data?signal=all` should also delete that user's `taste_snapshots` (they encode taste).

## Models and `model_version`

| `model_version` | Needs | Embedding | Also gives |
| --- | --- | --- | --- |
| `effnet-discogs-1` (default) | `uv sync --extra analysis` (essentia-tensorflow) + model files | Discogs-EffNet, 1280-d, mean-pooled over patches | 400 Discogs styles (top 5 → `genre:` tags), 56 MTG-Jamendo moods/themes (top 3 → `theme:`), danceability, mood aggressive/happy/relaxed/sad/party/acoustic (`mood:`), voice/instrumental, BPM (Percival), key (KeyExtractor, `bgate` profile), energy |
| `basic-1` (fallback) | ffmpeg only | 107-d numpy statistics: log-mel means, MFCC mean/std, chroma, spectral centroid/rolloff/flatness/flux, ZCR, loudness | BPM (flux autocorrelation), key (Krumhansl), energy, a beat-strength "danceability" |

If `cfg.model_version` needs Essentia and it is not importable, or the model files can't be
downloaded, the analyser logs a warning and writes `basic-1` features instead, so dev, tests and a
half-installed server still produce mixes.

The **energy** value is 0..1: for EffNet `0.5·loudness + 0.2·aggressive + 0.2·party + 0.1·(1 − relaxed)`;
for basic-1 `0.6·loudness + 0.25·brightness + 0.15·onset density`. Mixes use its percentile rank
within the library, so the two scales never mix.

**EffNet excerpts.** EffNet costs about 1 s of CPU per 120 s of audio, so by default it analyses three
40 s excerpts (at 20 %, 50 % and 80 % of the track) instead of the whole track. That keeps per-track time
roughly constant. Set `ai.analysis.excerpts` to `0` to analyse whole tracks (centre-cropped to
`ai.analysis.max_seconds`). The new setting applies to tracks analysed from then on.

### Model files and licence

The Essentia models are **CC BY-NC-SA 4.0** (non-commercial). They are downloaded at install time,
never committed. Every file is pinned by SHA-256 in `models.py`; a mismatch deletes the download.

```bash
uv run python -m nasmusic.ai download-models --data-dir /data          # ~30 MB
```

`analyse_pending` also downloads them on first use (once, in the parent process).

### Switching `model_version` after a hardware upgrade

1. Add the new model to `extractors.py` (an extractor class with `extract_file` / `extract_audio`)
   and, if it needs files, to `MODEL_SETS` in `models.py`. Give it a new version string, for example
   `clap-1`.
2. Set `model_version` in `config.toml` and restart.
3. The next `analyse_pending` run (nightly or after a scan) analyses every track for the new version.
   The old version's rows stay in `features`.
4. Until the new version covers **80 %** of the library, the recommender keeps using the version with
   the best coverage, so mixes keep working during the re-analysis (`common.active_version`).
5. Taste snapshots are per version; the first nightly run on the new version trains a fresh one from
   the same events. Nothing else changes.

To free space afterwards: `DELETE FROM features WHERE model_version = 'old-version'`.

## Analyser

* Picks tracks with no `features` row for the version, or whose file `mtime` is newer than
  `analysed_at`.
* Marks them `pending` in `analysis_jobs`. Each `features` row and its `done` mark are written together
  in one transaction by the parent process (the only DB writer). An interrupted run just leaves
  `pending` rows, and the next run picks them up. Safe to kill at any time.
* Failures are stored as `failed` with the error and retried after `ai.analysis.retry_failed_days`,
  or as soon as the file changes.
* Extraction runs in a **spawn** process pool (`ai.analysis.workers`, default 2) at `nice 10`. Spawn is
  used because the server has threads and TensorFlow does not survive `fork`.
* The library is only ever read (ffmpeg decodes to a pipe).

### Benchmark

```bash
uv run python -m nasmusic.ai benchmark --db /data/nasmusic.db --library /music --data-dir /data -n 30
```

This times `n` random tracks with the real pool settings (nothing is written) and prints
`seconds_per_track`, `realtime_factor` and `est_full_library_h` / `est_pending_h`. Run it on the NAS
before the first full analysis. In code: `analyser.benchmark(conn, cfg, n)`.

Measured during development (Intel Core Ultra 7 165H laptop, 24 real FLACs averaging 4 min,
Python 3.12, essentia-tensorflow 2.1b6.dev1389):

| Setup | s/track | 5,000 tracks |
| --- | --- | --- |
| `basic-1`, 2 workers, all cores | 0.43 | ~0.6 h |
| EffNet, whole track (≤ 480 s), 2 workers, all cores | 2.71 | ~3.8 h |
| EffNet, 3 × 40 s excerpts, 2 workers, all cores | 1.15 | ~1.6 h |
| EffNet, 3 × 40 s excerpts, 2 workers, **pinned to 4 CPUs** (`taskset -c 0-3`) | 1.78 | ~2.5 h |
| EffNet, 3 × 40 s excerpts, 1 worker, pinned to 4 CPUs | 2.47 | ~3.4 h |

The i5-3470 has 4 cores at roughly half this laptop's per-core speed, and no AVX2/FMA, so expect
about 2–3× the 4-CPU figure: **roughly 5–8 hours** for the first full analysis of 5,000 tracks.
After that, nightly analysis only covers new files. Measure it with `benchmark` on the NAS.

### AVX without AVX2 (i5-3470)

The `essentia-tensorflow` wheel (2.1b6.dev1389) installs from PyPI on Python 3.12 and runs here. Its
bundled `libtensorflow` 2.5.0 looks AVX-baseline in a static scan: its 2,152 AVX2/FMA instructions
are all in runtime-dispatched code (oneDNN JIT, BoringSSL `*_avx2`, libjpeg-turbo SIMD), and Essentia's
own module has only FFmpeg's dispatched `*_fma3`/`*_avx2` routines. TensorFlow's startup message lists
AVX2/FMA as what oneDNN may use, not what it requires. This has **not** been run on a real
AVX-only CPU yet. To check on the NAS:

```bash
grep -o -w -e avx -e avx2 /proc/cpuinfo | sort | uniq -c      # expect avx only
uv run python -m nasmusic.ai selftest --data-dir /data         # in the container
```

`selftest` prints the CPU flags, verifies the model files and runs EffNet on a generated tone in a
child process. If the CPU is missing an instruction, it reports `SIGILL` instead of crashing. If that
happens, set `model_version = "basic-1"` until the hardware upgrade (everything else works the same),
or build essentia-tensorflow from source with `-mavx`.

## Recommender

Signals per track, decayed with a half-life of `ai.rec.half_life_days`:

| Signal | Weight |
| --- | --- |
| play ≥ 80 % (full play) | +1.0 (mild like); 50–80 % = +0.5; less = +0.1 |
| skip after 30 s | −0.6 (mild dislike) |
| skip before `ai.rec.skip_early_s` (30 s) | −1.5 (strong) |
| repeated skip within 30 days | ×1.5, ×2.0 … per earlier skip (capped at −3 per event) |
| thumbs-up (latest thumb event) | +2.0 |
| star (`user_tracks.starred_at`) | +3.0 (strong like) |
| rating r (`user_tracks.rating`) | r − 3 (1 star = −2 … 5 stars = +2) |
| thumbs-down (latest thumb event) | never in a mix |
| repeat / seek-through / seek-back / volume (context) | +1.5 / −0.5 / +0.3 / ±0.2 (see Listening context) |

For thumbs, the **latest** `thumb_up` / `thumb_down` / `thumb_clear` event per track wins, and
`thumb_clear` cancels the earlier thumb. That state overrides `user_tracks.blocked`, which may lag behind
the events. Tracks with no thumb events fall back to `user_tracks.blocked`.

* **Cold start.** Taste is a weighted spherical k-means over the embeddings of positively weighted
  tracks (up to `ai.rec.taste_clusters` centres, about one per 6 liked tracks), so liking metal and
  jazz doesn't average out to mush. Negatives are centres of disliked tracks.
  `sound = max cos(track, centre) − 0.5·max(0, max cos(track, negative))`.
* **Learned model.** After `ai.rec.learned_min_events` feedback events (500), a LightGBM
  play-through classifier is trained nightly on play/skip events. Features are 16 embedding PCA
  components, the 24 most common tags, energy/BPM/danceability, star and rating, hour of day
  (raw + sin/cos), weekday, days since last play, and play/skip counts (all as they were *before*
  each event). It switches on only if a chronological 80/20 validation AUC is ≥ 0.55; otherwise it
  stays on cold start and says why in the snapshot notes. Its probability is blended with the
  cold-start score (`ai.rec.learned_blend`).
* **Freshness.** A track played d days ago loses `0.3·(1 − d/ai.rec.recent_penalty_days)`. A
  never-played track gains `ai.rec.never_played_boost`.
* Each nightly run stores a `taste_snapshots` row: zlib-compressed JSON with the centres, PCA, the
  LightGBM model text, and `notes` such as `{"mode": "learned", "validation_auc": 0.81, ...}`. The
  last 30 per version are kept. `more_like` and `explain` load the latest one instead of retraining.

## Mixes

Nightly, in this order (no track repeats across them unless the library is too small): **Daily**
(`known_pct` known likes / `rare_pct` rarely played taste matches / `explore_pct` exploration),
**Morning** and **Evening** (same split; tracks you play in that time window get a boost, and the
learned model scores at 8:00 and 20:00), and **Deep Cuts** (≤ `deep_cuts.max_plays` plays, ranked
purely by sound match). `more_like` is on demand: 75 % similarity to the seed, 25 % taste.

Rules: no artist twice within `ai.mix.artist_gap` tracks, nothing played (or skipped) in the last
`ai.mix.recent_days` days, no blocked tracks, at most about 1 track per artist per 12 in a mix.
The order is a nearest-neighbour walk in embedding space. Each step minimises
`(1 − cos) + 1.5·|energy − target| + 0.5·|energy jump|`. The energy target follows a curve: Daily
and Deep Cuts warm up, peak around 60 % and ease off; Morning rises; Evening falls; More-like stays
near the seed. Tracks that can't be placed without breaking the artist rule are dropped.

Every track gets a reason, for example "One of your favourites", "You've played this through 4
times", "Not played yet, but sounds like *X* by *Y*", "Rarely played, close to your Hard Bop
favourites", "Something different: Baroque you haven't explored much", "Never played; sounds like
…", or "Sounds like *seed* by *artist*".

The nightly run deactivates every active nightly mix **and every `more_like` mix**. The sync manifest
includes all active mixes, so on-demand mixes must not pile up. Building a new `more_like` mix also
deactivates those older than 24 h and keeps at most 3 active (`mixes.MORE_LIKE_MAX_ACTIVE`).
Inactive mixes older than `ai.mix.keep_days` are deleted.

## Discovery (weekly)

1. **Seeds.** Your top `ai.discovery.seed_artists` artists by positive feedback and plays (or by
   how much you own, before any feedback).
2. **Name matching.** The library has no MBIDs yet, so artists are resolved through a MusicBrainz
   search with an exact match on normalised names (case, accents, "The", `&`/"and", punctuation, and
   "(Deluxe Edition)"-style suffixes for albums). Results are cached in
   `data_dir/cache/discovery.json`. `artists.mbid` is used when present.
3. **Candidates.** ListenBrainz similar artists (plus Last.fm `artist.getSimilar` when
   `lastfm_api_key` is set), and albums you don't own by your top artists. Each artist's album is
   the most popular release group on ListenBrainz not owned or answered before, falling back to
   MusicBrainz's newest studio album.
4. **Sound check.** For the top `ai.discovery.preview_candidates`, two Deezer 30-second previews are
   fetched into memory, decoded through an ffmpeg pipe, embedded with the same model, and
   discarded. Nothing touches the disk. The score is the percentile of the preview's taste match
   within your own library.
5. **Score.** `0.55·artist link + 0.45·sound − 0.6·"not interested" closeness`. An artist you
   answered "Not interested" is penalised, and so are its ListenBrainz neighbours. At most
   `ai.discovery.max_per_cluster` (2) per seed-artist cluster, `ai.discovery.per_week` (10) in total,
   one album per artist. Albums already owned (normalised name or release-group MBID) or ever
   suggested are skipped.
6. **Links.** Bandcamp and Qobuz search URLs (`ai.discovery.qobuz_locale`), a Cover Art Archive cover
   (Deezer's as a fallback), and `preview_url` = `https://api.deezer.com/track/<id>`. Deezer's signed
   MP3 links expire within minutes, so the API URL is stored instead; GET it to get a fresh
   `preview`.

HTTP is polite: User-Agent `nas-music/0.1 (open-source self-hosted music server)` with no contact
details, MusicBrainz at most 1 request/s, other hosts spaced 0.15–0.3 s, and one retry with back-off
on 429/503.

## Status key for the dashboard: `ai.status.model_version`

`settings['ai.status.model_version']` is a JSON object the engine keeps current. It is not a tunable,
and it is not seeded with the defaults.

```json
{"requested": "effnet-discogs-1",      // cfg.model_version
 "analysing": "basic-1",               // version analyse_pending is actually writing to `features`
 "fallback_reason": "Essentia is not importable",   // null when analysing == requested
 "mixes": "basic-1",                   // version the last nightly run built mixes from (see 80 % rule)
 "updated_at": "2026-11-18T03:00:00Z"}
```

Analysis progress = `features` rows with `model_version = analysing` vs non-missing tracks.
`analyse_pending` writes `requested` / `analysing` / `fallback_reason` on every call, and `run_nightly`
writes `mixes`.

## Concurrency

Server core may run `analyse_pending` (after a scan) and `run_nightly` (02:00) at the same time, each
on its own connection. Every write is a short `BEGIN IMMEDIATE` transaction:
* one per analysed track, plus one to queue the batch;
* one per mix, with reasons computed before the transaction opens;
* one for the snapshot and one for the status key.

Model training, scoring and audio decoding hold no lock (WAL readers don't block writers).
`tests/ai/test_concurrency.py` runs both together on one DB file, alongside a third writer, and checks
that it never waits more than 2 s for the lock.

## Settings

All tunables live in the `settings` table as JSON under `ai.*`. Missing keys are written with
defaults on the first `run_nightly` / `analyse_pending`, so the web UI can list them.

| Key | Default | Meaning |
| --- | --- | --- |
| `ai.analysis.workers` | 2 | analyser processes |
| `ai.analysis.max_seconds` | 480 | whole-track analysis: centre crop length |
| `ai.analysis.excerpts` | 3 | EffNet: excerpts per track (0 = whole track) |
| `ai.analysis.excerpt_seconds` | 40 | excerpt length |
| `ai.analysis.retry_failed_days` | 7 | retry failed tracks after this many days |
| `ai.rec.learned_min_events` | 500 | feedback events before the learned model |
| `ai.rec.learned_blend` | 0.6 | learned share of the final score |
| `ai.rec.taste_clusters` | 6 | max taste centres |
| `ai.rec.half_life_days` | 120 | feedback decay |
| `ai.rec.recent_penalty_days` | 14 | freshness penalty window |
| `ai.rec.never_played_boost` | 0.05 | boost for never-played tracks |
| `ai.rec.skip_early_s` | 30 | a skip before this is a strong dislike |
| `ai.rec.new_user_plays` | 20 | below this, a user gets the library-wide cold start |
| `ai.mix.daily.minutes` | 240 | Daily Mix length |
| `ai.mix.morning.minutes` / `ai.mix.evening.minutes` | 90 | Morning / Evening length |
| `ai.mix.deep_cuts.minutes` | 90 | Deep Cuts length |
| `ai.mix.known_pct` / `ai.mix.rare_pct` / `ai.mix.explore_pct` | 0.6 / 0.3 / 0.1 | Daily-style split |
| `ai.mix.rare_max_plays` | 2 | "rarely played" means at most this many plays |
| `ai.mix.deep_cuts.max_plays` | 1 | Deep Cuts play-count ceiling |
| `ai.mix.artist_gap` | 5 | no artist twice within this many tracks |
| `ai.mix.recent_days` | 3 | nothing played in the last N days |
| `ai.mix.morning.hours` / `ai.mix.evening.hours` | [5, 11] / [17, 24] | local-time windows |
| `ai.mix.keep_days` | 60 | delete inactive mixes older than this |
| `ai.mix.late_night.hours` | [22, 4] | local hours for "Late night" |
| `ai.mix.context.min_plays` | 30 | plays in a context before it gets a mix |
| `ai.mix.context.minutes` | 60 | context mix length |
| `ai.mix.context.max` | 3 | context mixes per night |
| `ai.discovery.per_week` | 10 | suggestions per run |
| `ai.discovery.max_per_cluster` | 2 | per artist cluster |
| `ai.discovery.seed_artists` | 15 | top artists to expand from |
| `ai.discovery.preview_candidates` | 25 | candidates that get a sound check |
| `ai.discovery.qobuz_locale` | "gb-en" | Qobuz store in the buy link |

## CLI

```bash
python -m nasmusic.ai selftest        --data-dir D [--model-version V]
python -m nasmusic.ai download-models --data-dir D
python -m nasmusic.ai benchmark       --db F --library L --data-dir D [-n 20] [--workers 2]
python -m nasmusic.ai analyse | nightly | discovery --db F --library L --data-dir D
```

## Tests

`uv run pytest tests/ai`. They use synthetic embeddings and events, mock all HTTP with
`httpx.MockTransport`, and run the analyser on FLACs generated by ffmpeg with the `basic-1`
extractor. Essentia is not needed.
