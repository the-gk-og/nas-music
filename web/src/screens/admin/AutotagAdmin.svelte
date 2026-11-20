<script lang="ts">
  // Admin: automatic tag fixing (v6). The autotag job fingerprints tracks, asks AcoustID + MusicBrainz and
  // leaves suggestions here; nothing applies until an admin accepts. Accepting writes tag overrides (the same
  // mechanism as the Tags section), so everything is logged and revertible; files are never touched.
  import { Check, Play, RefreshCw, Undo2, X } from '@lucide/svelte';
  import {
    AUTOTAG_FIELDS,
    adminApi,
    autotagApi,
    confidenceClass,
    confidenceText,
    countAbove,
    groupChanges,
    type AutotagField,
    type AutotagGroup,
    type AutotagStatus,
    type AutotagStatusName,
    type AutotagSuggestion,
    type AutotagValues,
  } from '../../lib/api/admin';
  import { library } from '../../lib/library.svelte';
  import { session } from '../../lib/session.svelte';
  import { ui } from '../../lib/ui.svelte';

  const T = $derived(autotagApi(session.api));
  const A = $derived(adminApi(session.api));
  const PAGE = 30;

  let st = $state<AutotagStatus | null>(null);
  let tab = $state<AutotagStatusName>('pending');
  let q = $state('');
  let groups = $state<AutotagGroup[]>([]);
  let total = $state(0);
  let offset = $state(0);
  let loading = $state(false);
  let busy = $state(false);
  let threshold = $state(90);
  let coverMode = $state<'auto' | 'always' | 'never'>('auto');
  let artistIds = $state(false);
  let open = $state<Set<string>>(new Set());
  /** Per-track field choices / edits the admin made (absent = the server's defaults / suggested values). */
  let picked = $state<Record<string, AutotagField[]>>({});
  let edited = $state<Record<string, AutotagValues>>({});
  let covers = $state<Record<string, string>>({});

  async function loadStatus() {
    try {
      st = await T.status();
    } catch {
      st = null;
    }
  }
  async function load(reset = true) {
    if (reset) offset = 0;
    loading = true;
    try {
      const r = await T.groups({ status: tab, q: q.trim() || undefined, limit: PAGE, offset });
      groups = reset ? r.groups : [...groups, ...r.groups];
      total = r.total;
      for (const g of r.groups) void loadCover(g);
    } catch (e) {
      ui.toast((e as Error).message);
    }
    loading = false;
  }
  async function loadCover(g: AutotagGroup) {
    const id = g.release?.mbid;
    if (!id || !g.release?.hasCover || covers[id]) return;
    const path = T.coverPath(id);
    try {
      covers[id] = session.api.mode === 'cookie' ? session.api.base + path : await session.api.blobUrlPublic(path);
    } catch {
      /* no cover */
    }
  }
  $effect(() => {
    void tab;
    void loadStatus();
    void load();
  });

  async function runNow() {
    try {
      await A.runJob('autotag');
      ui.toast('Tag fixing started — it runs in the background (up to 50 min per run)');
      setTimeout(loadStatus, 1500);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  const fieldsOf = (t: AutotagSuggestion) => picked[t.trackId] ?? t.defaultFields;
  const custom = (t: AutotagSuggestion) => t.trackId in picked || t.trackId in edited;
  function toggleField(t: AutotagSuggestion, f: AutotagField) {
    const cur = fieldsOf(t);
    picked = { ...picked, [t.trackId]: cur.includes(f) ? cur.filter((x) => x !== f) : [...cur, f] };
  }
  function edit(t: AutotagSuggestion, f: AutotagField, v: string) {
    const num = f === 'trackNo' || f === 'discNo' || f === 'year';
    const val = v.trim() === '' ? null : num ? Number(v) : v;
    edited = { ...edited, [t.trackId]: { ...(edited[t.trackId] ?? {}), [f]: val } };
    if (!fieldsOf(t).includes(f)) toggleField(t, f);
  }
  const shown = (t: AutotagSuggestion, f: AutotagField) => (edited[t.trackId] && f in edited[t.trackId] ? edited[t.trackId][f as keyof AutotagValues] : t.suggested[f as keyof AutotagValues]);
  const fmt = (v: unknown) => (v === null || v === undefined || v === '' ? '∅' : String(v));
  const cover = () => (coverMode === 'auto' ? null : coverMode === 'always');

  /** Artist MBIDs are opt-in: passed as edits so the server applies them on top of the defaults. */
  function artistEdits(t: AutotagSuggestion): AutotagValues {
    if (!artistIds) return {};
    const e: AutotagValues = {};
    if (t.suggested.mbArtistId) e.mbArtistId = t.suggested.mbArtistId;
    if (t.suggested.mbAlbumArtistId) e.mbAlbumArtistId = t.suggested.mbAlbumArtistId;
    return e;
  }

  async function acceptTracks(ts: AutotagSuggestion[]) {
    busy = true;
    let n = 0;
    const errors: string[] = [];
    try {
      const plain = ts.filter((t) => !custom(t));
      if (plain.length) {
        const edits = Object.fromEntries(plain.map((t) => [t.trackId, artistEdits(t)]).filter(([, e]) => Object.keys(e).length));
        const r = await T.accept({ trackIds: plain.map((t) => t.trackId), edits, cover: cover() });
        n += r.accepted;
        errors.push(...r.coverErrors);
      }
      for (const t of ts.filter(custom)) {
        const r = await T.accept({ trackIds: [t.trackId], fields: fieldsOf(t), edits: { [t.trackId]: { ...artistEdits(t), ...(edited[t.trackId] ?? {}) } }, cover: cover() });
        n += r.accepted;
        errors.push(...r.coverErrors);
      }
      ui.toast(`Accepted ${n} track${n === 1 ? '' : 's'}${errors.length ? ` · cover failed (${errors.length})` : ''}`);
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
    library.tagsChanged(null, null);
    await Promise.all([load(), loadStatus()]);
  }

  async function acceptAbove() {
    const thr = threshold / 100;
    const ok = await ui.confirm(
      'Accept suggestions',
      `Accept every pending suggestion at ${threshold}% confidence or more (${countAbove(groups, thr)} on this page, maybe more on others)? Default fields only; you can undo each one under "Accepted".`,
      'Accept all',
    );
    if (!ok) return;
    busy = true;
    try {
      const r = await T.accept({ minConfidence: thr, cover: cover() });
      ui.toast(`Accepted ${r.accepted} track${r.accepted === 1 ? '' : 's'}`);
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
    library.tagsChanged(null, null);
    await Promise.all([load(), loadStatus()]);
  }

  async function reject(ts: AutotagSuggestion[]) {
    busy = true;
    try {
      await T.reject({ trackIds: ts.map((t) => t.trackId) });
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
    await Promise.all([load(), loadStatus()]);
  }

  async function reopen(ts: AutotagSuggestion[]) {
    busy = true;
    try {
      const r = await T.reopen(ts.map((t) => t.trackId));
      ui.toast(`Back to pending: ${r.reopened}${r.reverted ? ` · ${r.reverted} tag change${r.reverted === 1 ? '' : 's'} reverted` : ''}`);
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
    library.tagsChanged(null, null);
    await Promise.all([load(), loadStatus()]);
  }

  async function rematch(ts: AutotagSuggestion[]) {
    try {
      const r = await T.rematch(ts.map((t) => t.trackId));
      ui.toast(`${r.queued} track${r.queued === 1 ? '' : 's'} will be looked up again on the next run`);
      await load();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  function toggleOpen(id: string) {
    const s = new Set(open);
    if (s.has(id)) s.delete(id);
    else s.add(id);
    open = s;
  }
  const label = (f: AutotagField) => AUTOTAG_FIELDS.find((x) => x.key === f)?.label ?? f;
  const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0);
</script>

<h2>Tag fixing</h2>
<p class="muted small">
  The server fingerprints your songs (Chromaprint), identifies them with AcoustID and fetches the matching MusicBrainz release. Suggestions wait here
  until you accept them; accepting stores tag overrides like the Tags section does — logged, revertible, and the music files are never changed.
</p>

{#if st}
  {#if !st.fpcalcAvailable}<p class="bad small">fpcalc (Chromaprint) is not installed on the server, so nothing can be fingerprinted.</p>{/if}
  {#if !st.apiKeyConfigured}
    <p class="warnc small">
      No AcoustID API key: songs are fingerprinted (the duplicate finder works) but not identified. Get a free key at acoustid.org/new-application and set
      <span class="mono">acoustid_api_key</span> in config.toml, then restart.
    </p>
  {/if}
  <div class="grid">
    <div class="tile">
      <div class="k">Fingerprinted</div>
      <div class="v">{st.fingerprinted} / {st.tracks}</div>
      <div class="meter"><span style:width="{pct(st.fingerprinted, st.tracks)}%"></span></div>
      {#if st.fingerprintFailed}<div class="s">{st.fingerprintFailed} failed</div>{/if}
    </div>
    <div class="tile">
      <div class="k">Identified</div>
      <div class="v">{st.matched}</div>
      <div class="s">{st.noMatch} not found{st.lookupErrors ? ` · ${st.lookupErrors} errors` : ''}</div>
    </div>
    <div class="tile">
      <div class="k">Suggestions</div>
      <div class="v">{st.pending}</div>
      <div class="s">{st.accepted} accepted · {st.rejected} rejected</div>
    </div>
  </div>
  <div class="row" style="margin-top:12px">
    <button class="btn sm" onclick={runNow} disabled={st.running}><Play size={15} /> {st.running ? 'Running…' : 'Run now'}</button>
    <button class="btn sm text" onclick={() => { void loadStatus(); void load(); }}><RefreshCw size={15} /> Refresh</button>
  </div>
{/if}

<div class="tabs" role="tablist">
  {#each ['pending', 'accepted', 'rejected'] as const as s (s)}
    <button role="tab" class:on={tab === s} aria-selected={tab === s} onclick={() => (tab = s)}>{s[0].toUpperCase() + s.slice(1)}</button>
  {/each}
</div>

<form class="row" onsubmit={(e) => { e.preventDefault(); void load(); }}>
  <input type="search" placeholder="Filter by album, artist or title…" bind:value={q} aria-label="Filter suggestions" />
  <button class="btn sm">Filter</button>
</form>

{#if tab === 'pending'}
  <div class="opts">
    <label class="row">Accept all ≥ <input type="range" min="50" max="100" step="1" bind:value={threshold} aria-label="Confidence threshold" /> <b>{threshold}%</b>
      <button class="btn sm primary" disabled={busy || !groups.length} onclick={acceptAbove}><Check size={15} /> Accept all ≥ {threshold}%</button>
    </label>
    <label class="row">Cover art
      <select bind:value={coverMode} aria-label="Cover art">
        <option value="auto">only for albums without art</option>
        <option value="always">always use Cover Art Archive</option>
        <option value="never">never</option>
      </select>
    </label>
    <label class="row" title="Artist ids regroup artists by MusicBrainz id; until all of an artist's albums carry it, the artist may show up twice.">
      <input type="checkbox" bind:checked={artistIds} /> Also apply MusicBrainz artist ids
    </label>
  </div>
{/if}

{#if loading && !groups.length}<p class="muted">Loading…</p>
{:else if !groups.length}<p class="muted">{tab === 'pending' ? 'No suggestions waiting. New ones appear after the tag-fixing job runs.' : `Nothing ${tab} yet.`}</p>
{:else}
  <p class="muted small">{total} album group{total === 1 ? '' : 's'}</p>
  {#each groups as g (g.groupId)}
    {@const changes = groupChanges(g)}
    <article class="group">
      <header>
        {#if g.release?.mbid && covers[g.release.mbid]}<img class="cov" src={covers[g.release.mbid]} alt="" />{:else}<div class="cov ph"></div>{/if}
        <div class="grow">
          <div class="title">
            {#if g.release}<b>{g.release.title}</b> <span class="muted">— {g.release.artist}{g.release.year ? ` · ${g.release.year}` : ''}</span>
            {:else}<b>Recording matches</b>{/if}
          </div>
          <div class="small muted">
            Now: {g.currentAlbum ?? '∅'} — {g.currentAlbumArtist ?? '∅'} · {g.tracks.length} of {g.albumTrackCount} track{g.albumTrackCount === 1 ? '' : 's'}
            {#if g.release}· {[g.release.type, g.release.format, g.release.country, g.release.trackCount ? `${g.release.trackCount} tracks` : null].filter(Boolean).join(' · ')}{/if}
          </div>
          {#if changes.length}<div class="small">Changes: {changes.map(label).join(', ')}</div>{/if}
        </div>
        <span class="conf {confidenceClass(g.confidence)}" title="Album confidence">{confidenceText(g.confidence)}</span>
      </header>
      <div class="row acts">
        {#if tab === 'pending'}
          <button class="btn sm primary" disabled={busy} onclick={() => acceptTracks(g.tracks)}><Check size={15} /> Accept album</button>
          <button class="btn sm" disabled={busy} onclick={() => reject(g.tracks)}><X size={15} /> Reject album</button>
        {:else}
          <button class="btn sm" disabled={busy} onclick={() => reopen(g.tracks)}><Undo2 size={15} /> {tab === 'accepted' ? 'Undo (revert tags)' : 'Back to pending'}</button>
        {/if}
        <button class="btn sm text" onclick={() => toggleOpen(g.groupId)}>{open.has(g.groupId) ? 'Hide tracks' : 'Show tracks'}</button>
        {#if g.release}<a class="small" href="https://musicbrainz.org/release/{g.release.mbid}" target="_blank" rel="noopener noreferrer">MusicBrainz ↗</a>{/if}
      </div>
      {#if open.has(g.groupId)}
        {#each g.tracks as t (t.trackId)}
          <div class="track">
            <div class="row">
              <span class="conf {confidenceClass(t.confidence)}">{confidenceText(t.confidence)}</span>
              <span class="grow mono small" title={t.path}>{t.path}</span>
              {#if tab === 'pending'}
                <button class="btn sm" disabled={busy} onclick={() => acceptTracks([t])}><Check size={14} /> Accept</button>
                <button class="btn sm text" disabled={busy} onclick={() => reject([t])}><X size={14} /> Reject</button>
                <button class="btn sm text" onclick={() => rematch([t])} title="Look this track up again on the next run"><RefreshCw size={14} /></button>
              {:else}
                <button class="btn sm text" disabled={busy} onclick={() => reopen([t])}><Undo2 size={14} /> Undo</button>
              {/if}
            </div>
            <table class="diff">
              <tbody>
                {#each AUTOTAG_FIELDS.filter((f) => f.key in t.suggested || t.changes.includes(f.key)) as f (f.key)}
                  {@const changed = t.changes.includes(f.key)}
                  <tr class:same={!changed}>
                    <td class="chk">
                      {#if tab === 'pending' && changed}
                        <input type="checkbox" checked={fieldsOf(t).includes(f.key)} onchange={() => toggleField(t, f.key)} aria-label="Apply {f.label}" />
                      {/if}
                    </td>
                    <td class="k">{f.label}</td>
                    <td class="old" class:mono={f.mb}>{fmt(t.current[f.key as keyof AutotagValues])}</td>
                    <td class="arrow">{changed ? '→' : ''}</td>
                    <td class="new" class:mono={f.mb}>
                      {#if tab === 'pending' && changed && !f.mb}
                        <input value={fmt(shown(t, f.key)) === '∅' ? '' : String(shown(t, f.key))} onchange={(e) => edit(t, f.key, (e.currentTarget as HTMLInputElement).value)} aria-label="{f.label} value" />
                      {:else}{fmt(shown(t, f.key))}{/if}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
            {#if t.detail}
              <div class="small muted">
                AcoustID {Math.round(Number(t.detail.acoustidScore ?? 0) * 100)}%{t.detail.durationDiffS != null ? ` · duration off by ${t.detail.durationDiffS}s` : ''}{t.detail.onRelease === false ? ' · no release match (recording only)' : ''}
              </div>
            {/if}
          </div>
        {/each}
      {/if}
    </article>
  {/each}
  {#if groups.length < total}
    <button class="btn sm" disabled={loading} onclick={() => { offset += PAGE; void load(false); }}>Load more</button>
  {/if}
{/if}

<style>
  .row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
  }
  form.row {
    margin: 12px 0;
  }
  form.row input[type='search'] {
    flex: 1;
    min-width: 200px;
  }
  .tabs {
    display: flex;
    gap: 6px;
    margin: 20px 0 6px;
  }
  .tabs button {
    padding: 6px 14px;
    border-radius: 999px;
    background: var(--bg-2);
    color: var(--fg-2);
  }
  .tabs button.on {
    background: #fff;
    color: #000;
  }
  .opts {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin: 6px 0 16px;
    font-size: 0.92rem;
  }
  .group {
    background: var(--bg-1);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 12px 14px;
    margin-bottom: 12px;
  }
  header {
    display: flex;
    gap: 12px;
    align-items: flex-start;
  }
  .cov {
    width: 64px;
    height: 64px;
    border-radius: 6px;
    object-fit: cover;
    flex: none;
  }
  .cov.ph {
    background: var(--bg-2);
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .title {
    overflow-wrap: anywhere;
  }
  .conf {
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }
  .acts {
    margin-top: 10px;
  }
  .track {
    border-top: 1px solid var(--line);
    margin-top: 10px;
    padding-top: 10px;
  }
  .track .mono {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .diff {
    margin: 6px 0;
    font-size: 0.88rem;
  }
  .diff td {
    padding: 3px 6px;
    border-bottom: 0;
    overflow-wrap: anywhere;
  }
  .diff .k {
    color: var(--fg-3);
    white-space: nowrap;
  }
  .diff .chk {
    width: 22px;
  }
  .diff .arrow {
    color: var(--fg-3);
  }
  .diff .old {
    color: var(--fg-2);
  }
  .diff tr:not(.same) .new {
    color: var(--accent);
  }
  .diff tr.same {
    opacity: 0.55;
  }
  .diff input {
    width: 100%;
    min-width: 120px;
    font: inherit;
    padding: 4px 8px;
  }
  .opts input[type='range'] {
    width: 200px;
    flex: none;
  }
</style>
