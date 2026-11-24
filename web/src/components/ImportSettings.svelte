<script lang="ts">
  // Settings: bring listening history and playlists in from Spotify, Last.fm and M3U files. History feeds
  // your mixes; playlists become playlists here; songs you don't have can go to your To get list.
  import { onDestroy } from 'svelte';
  import { ApiError } from '../lib/api/client';
  import { library } from '../lib/library.svelte';
  import { session } from '../lib/session.svelte';
  import type { ImportHistoryResult, ImportSource, ImportStatus, ImportedPlaylist } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  const NAMES: Record<ImportSource, string> = { listenbrainz: 'ListenBrainz', lastfm: 'Last.fm', spotify: 'Spotify' };

  let st = $state<ImportStatus | null>(null);
  let unsupported = $state(false);
  let busy = $state<string | null>(null);
  let addMissing = $state(true);
  let addPlayed = $state(false);
  let lfmUser = $state('');
  let lines = $state<string[]>([]);
  let poll: ReturnType<typeof setTimeout> | null = null;

  async function refresh() {
    try {
      st = await session.api.importStatus();
      if (!lfmUser && st.lastfmUsername) lfmUser = st.lastfmUsername;
      if (st.lastfm?.state === 'running') {
        if (poll) clearTimeout(poll);
        poll = setTimeout(refresh, 3000);
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) unsupported = true;
    }
  }
  onDestroy(() => poll && clearTimeout(poll));
  $effect(() => {
    void refresh();
  });

  const MIN_PLAYS = 5;

  function histLine(h: ImportHistoryResult, what: string): string {
    let s = `${what}: ${h.imported} plays added from ${h.scanned} listens`;
    if (h.alreadyKnown) s += `, ${h.alreadyKnown} already here`;
    if (h.unmatched) s += `, ${h.unmatched} of songs not in your library`;
    if (h.tooShort) s += `, ${h.tooShort} too short to count`;
    return s + '.';
  }
  function plLine(p: ImportedPlaylist): string {
    if (p.unchanged) return `“${p.name}”: you already have it.`;
    if (!p.playlistId) return `“${p.name}”: none of its ${p.total} songs are in your library.`;
    return `“${p.name}”: ${p.matched} of ${p.total} songs${p.toGetAdded ? `, ${p.toGetAdded} added to To get` : ''}.`;
  }

  function pick(accept: string, multiple: boolean): Promise<File[]> {
    return new Promise((resolve) => {
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = accept;
      input.multiple = multiple;
      input.onchange = () => resolve([...(input.files ?? [])]);
      input.click();
    });
  }

  async function run(key: string, fn: () => Promise<string[]>) {
    busy = key;
    lines = [];
    try {
      lines = await fn();
      void library.refreshPlaylists();
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      busy = null;
      void refresh();
    }
  }

  async function spotify() {
    const files = await pick('.zip,.json,.csv', true);
    if (!files.length) return;
    await run('spotify', async () => {
      const out: string[] = [];
      let toGet = 0;
      for (const f of files) {
        try {
          const r = await session.api.importSpotify(f, f.name, { addMissing, missingMinPlays: addMissing && addPlayed ? MIN_PLAYS : 0 });
          if (r.history) out.push(histLine(r.history, f.name));
          for (const p of r.playlists) out.push(plLine(p));
          toGet += r.toGetAdded;
        } catch (e) {
          out.push(`${f.name}: ${(e as Error).message}`);
        }
      }
      if (toGet) out.push(`${toGet} songs added to To get.`);
      return out;
    });
  }

  async function lastfmApi() {
    const u = lfmUser.trim();
    busy = 'lastfm';
    try {
      st = await session.api.importLastfm(u || undefined);
      if (poll) clearTimeout(poll);
      poll = setTimeout(refresh, 2000);
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      busy = null;
    }
  }

  async function lastfmCsv() {
    const [f] = await pick('.csv,text/csv', false);
    if (!f) return;
    await run('lastfmcsv', async () => {
      const r = await session.api.importLastfmCsv(f, addMissing && addPlayed ? MIN_PLAYS : 0);
      const out = [histLine(r, f.name)];
      if (r.toGetAdded) out.push(`${r.toGetAdded} songs added to To get.`);
      return out;
    });
  }

  async function m3u() {
    const files = await pick('.m3u,.m3u8,audio/x-mpegurl', true);
    if (!files.length) return;
    await run('m3u', async () => {
      const out: string[] = [];
      for (const f of files) {
        try {
          out.push(plLine(await session.api.importM3u(f, f.name, addMissing)));
        } catch (e) {
          out.push(`${f.name}: ${(e as Error).message}`);
        }
      }
      return out;
    });
  }

  async function remove(src: ImportSource) {
    const n = st?.imported[src] ?? 0;
    if (!(await ui.confirm(`Remove ${NAMES[src]} history`, `Removes the ${n} plays imported from ${NAMES[src]} (your own plays here stay).`, 'Remove'))) return;
    try {
      const r = await session.api.removeImportedHistory(src);
      st = r;
      ui.toast(`Removed ${r.removed} imported plays`);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  const job = $derived(st?.lastfm);
  const sources = $derived(st ? (Object.keys(st.imported) as ImportSource[]).filter((s) => st!.imported[s] > 0) : []);
</script>

<div class="section-title">Import from other services</div>
{#if unsupported}
  <p class="pad muted small">This server does not support imports yet.</p>
{:else if !st}
  <p class="pad muted small">Loading…</p>
{:else}
  <p class="pad muted small">
    Bring your history and playlists with you. Plays of songs in this library are added to your history so your mixes learn
    from them (they are marked, never scrobbled, and can be removed). Playlists are created from the songs you have.
  </p>
  <label class="sig">
    <div><div>Add songs I don't have to To get</div><div class="muted small">Songs from imported playlists that aren't in the library.</div></div>
    <input type="checkbox" class="switch" bind:checked={addMissing} />
  </label>
  {#if addMissing}
    <label class="sig">
      <div><div>Also songs I played a lot</div><div class="muted small">From imported history: songs played {MIN_PLAYS} or more times that aren't here.</div></div>
      <input type="checkbox" class="switch" bind:checked={addPlayed} />
    </label>
  {/if}

  <div class="pad svc">
    <div>Spotify</div>
    <div class="muted small">
      Request your data at <a href="https://www.spotify.com/account/privacy/" target="_blank" rel="noopener noreferrer">spotify.com/account/privacy</a>
      (“Extended streaming history” has every play; “Account data” has your playlists and the last year), then choose the zip or
      the JSON files from it. Exportify CSV playlists (<a href="https://exportify.net" target="_blank" rel="noopener noreferrer">exportify.net</a>) work too.
    </div>
    <div class="btns"><button class="btn" disabled={!!busy} onclick={spotify}>{busy === 'spotify' ? 'Importing…' : 'Choose Spotify files'}</button></div>
  </div>

  <div class="pad svc">
    <div>Last.fm</div>
    {#if st.lastfmAvailable}
      <form class="row" onsubmit={(e) => { e.preventDefault(); void lastfmApi(); }}>
        <input placeholder="Last.fm user name" aria-label="Last.fm user name" autocomplete="off" bind:value={lfmUser} />
        <button class="btn" disabled={!!busy || job?.state === 'running' || !lfmUser.trim()}>Import scrobbles</button>
      </form>
      {#if job}
        <div class="small {job.state === 'error' ? 'err' : 'muted'}">
          {#if job.state === 'running'}Importing {job.username ?? ''}… {job.page && job.pages ? `page ${job.page} of ${job.pages}, ` : ''}{job.imported ?? 0} plays added so far
          {:else if job.state === 'error'}Import failed: {job.error ?? ''}
          {:else}Last import ({job.username}): {job.imported ?? 0} plays added from {job.scanned ?? 0} scrobbles, {job.alreadyKnown ?? 0} already here, {job.unmatched ?? 0} not in the library{/if}
        </div>
      {/if}
      <div class="muted small">Your profile must show its recent listening. Or import a CSV of your scrobbles instead:</div>
    {:else}
      <div class="muted small">Import a CSV of your scrobbles (made with a tool such as lastfm-to-csv: artist, album, title, date).</div>
    {/if}
    <div class="btns"><button class="btn" disabled={!!busy} onclick={lastfmCsv}>{busy === 'lastfmcsv' ? 'Importing…' : 'Choose CSV'}</button></div>
  </div>

  <div class="pad svc">
    <div>M3U playlists</div>
    <div class="muted small">From another player (foobar2000, VLC, Musicolet, MusicBee…). Songs are found by file path or by “Artist - Title”. To export a playlist, use Export as M3U in its menu.</div>
    <div class="btns"><button class="btn" disabled={!!busy} onclick={m3u}>{busy === 'm3u' ? 'Importing…' : 'Choose .m3u files'}</button></div>
  </div>

  {#if lines.length}
    <ul class="pad small result">
      {#each lines as l, i (i)}<li>{l}</li>{/each}
    </ul>
  {/if}

  {#if sources.length}
    <div class="pad svc">
      <div class="muted small">Imported history</div>
      <div class="btns">
        {#each sources as s (s)}
          <button class="btn danger" disabled={!!busy} onclick={() => remove(s)}>Remove {st.imported[s]} from {NAMES[s]}</button>
        {/each}
      </div>
    </div>
  {/if}
{/if}

<style>
  /* same row styles as Settings.svelte (its styles are scoped) */
  .pad {
    padding: 4px 18px 10px;
  }
  .small {
    font-size: 0.85rem;
  }
  .sig {
    display: flex;
    gap: 16px;
    align-items: center;
    justify-content: space-between;
    padding: 12px 18px;
    border-bottom: 1px solid var(--line);
    cursor: pointer;
  }
  .svc {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 14px;
  }
  .row {
    display: flex;
    gap: 8px;
  }
  .row input {
    flex: 1;
    min-width: 0;
  }
  .btns {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .result {
    margin: 0 0 12px;
    padding-left: 36px;
  }
  .err {
    color: #f87171;
  }
</style>
