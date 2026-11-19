<script lang="ts">
  // Admin: tag editing (v5). Find tracks or albums, multi-select, edit in bulk; recent edits log.
  import { Disc3, Search, Tags } from '@lucide/svelte';
  import { tagsApi, type TagEditLogEntry } from '../../lib/api/admin';
  import { library } from '../../lib/library.svelte';
  import { session } from '../../lib/session.svelte';
  import type { Album, Track } from '../../lib/types';
  import { ui } from '../../lib/ui.svelte';

  const T = $derived(tagsApi(session.api));
  let q = $state('');
  let tracks = $state<Track[]>([]);
  let albums = $state<Album[]>([]);
  let selected = $state<Set<string>>(new Set());
  let searching = $state(false);
  let log = $state<TagEditLogEntry[] | null>(null);

  async function search() {
    if (!q.trim()) return;
    searching = true;
    try {
      const r = await session.api.search(q.trim(), 300);
      tracks = r.tracks;
      albums = r.albums;
      selected = new Set();
    } catch (e) {
      ui.toast((e as Error).message);
    }
    searching = false;
  }
  async function loadLog() {
    try {
      log = await T.history({ limit: 100 });
    } catch {
      log = [];
    }
  }
  $effect(() => {
    void library.tagsRev;
    void loadLog();
    if (q.trim()) void search();
  });

  function toggle(id: string) {
    const s = new Set(selected);
    if (s.has(id)) s.delete(id);
    else s.add(id);
    selected = s;
  }
  const allOn = $derived(tracks.length > 0 && tracks.every((t) => selected.has(t.id)));
  const toggleAll = () => (selected = allOn ? new Set() : new Set(tracks.map((t) => t.id)));
  function editSelected() {
    const ids = tracks.filter((t) => selected.has(t.id)).map((t) => t.id);
    if (!ids.length) return;
    ui.open({ kind: 'tagEdit', trackIds: ids, title: ids.length === 1 ? tracks.find((t) => t.id === ids[0])!.title : `${ids.length} tracks` });
  }
  async function editAlbum(a: Album) {
    try {
      const full = await session.api.album(a.id);
      ui.open({ kind: 'tagEdit', trackIds: full.tracks.map((t) => t.id), albumId: a.id, title: a.title });
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  const fmtV = (v: unknown) => (v === null || v === undefined || v === '' ? '∅' : String(v));
</script>

<h2>Tags</h2>
<p class="muted small">The music library is read-only, so edits are stored on the server as overrides on top of the files' tags. They survive rescans, show up everywhere (search, mixes, every app) and can be reverted. Tip: any admin can also use “Edit tags” in a song's menu or “Edit album tags” on an album.</p>
<form class="row" onsubmit={(e) => { e.preventDefault(); void search(); }}>
  <input type="search" placeholder="Find songs, albums, artists…" bind:value={q} aria-label="Search the library" />
  <button class="btn" disabled={searching}><Search size={16} /> Search</button>
</form>

{#if albums.length}
  <h3>Albums</h3>
  {#each albums.slice(0, 20) as a (a.id)}
    <div class="item"><Disc3 size={18} /><div class="grow"><b>{a.title}</b> <span class="muted">— {a.artist}{a.year ? ` · ${a.year}` : ''}</span></div><button class="btn text" onclick={() => editAlbum(a)}><Tags size={16} /> Edit album</button></div>
  {/each}
{/if}
{#if tracks.length}
  <div class="row bar">
    <label><input type="checkbox" checked={allOn} onchange={toggleAll} /> Select all ({tracks.length})</label>
    <button class="btn" disabled={!selected.size} onclick={editSelected}><Tags size={16} /> Edit {selected.size || ''} selected</button>
  </div>
  {#each tracks as t (t.id)}
    <label class="item">
      <input type="checkbox" checked={selected.has(t.id)} onchange={() => toggle(t.id)} />
      <div class="grow"><b>{t.title}</b> <span class="muted">— {t.artist} · {t.album}{t.trackNo ? ` · #${t.trackNo}` : ''}{t.genre ? ` · ${t.genre}` : ''}</span></div>
    </label>
  {/each}
{:else if q && !searching}<p class="muted">No songs found.</p>{/if}

<h3>Recent edits</h3>
{#if !log}<p class="muted">Loading…</p>
{:else if !log.length}<p class="muted">No tag edits yet.</p>
{:else}
  {#each log as h (h.id)}
    <div class="h small">
      <span class="muted">{new Date(h.at).toLocaleString()} · {h.user ?? '?'}</span>
      <button class="link" onclick={() => ui.open({ kind: 'tagEdit', trackIds: [h.trackId], title: h.trackTitle })}>{h.trackTitle}</button>
      {h.action === 'revert' ? 'reverted' : 'set'} <b>{h.field}</b>: {fmtV(h.oldValue)} → {fmtV(h.newValue)}
    </div>
  {/each}
{/if}

<style>
  .row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
    margin: 12px 0;
  }
  .row input[type='search'] {
    flex: 1;
    min-width: 200px;
  }
  .bar {
    justify-content: space-between;
  }
  .item {
    display: flex;
    gap: 10px;
    align-items: center;
    padding: 6px 0;
    border-bottom: 1px solid var(--line, #8882);
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .h {
    padding: 3px 0;
  }
  .link {
    background: none;
    border: 0;
    color: var(--accent);
    cursor: pointer;
    padding: 0;
    font: inherit;
  }
</style>
