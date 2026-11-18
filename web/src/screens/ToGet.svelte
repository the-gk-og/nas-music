<script lang="ts">
  // To get (§4): albums/songs you want; status want / got / dismissed; note; buy links.
  import { ArrowLeft, Pause, Play, Trash2 } from '@lucide/svelte';
  import Art from '../components/Art.svelte';
  import { samples } from '../lib/samples.svelte';
  import { session } from '../lib/session.svelte';
  import type { ToGetItem, ToGetStatus } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let items = $state<ToGetItem[] | null>(null);
  let filter = $state<ToGetStatus>('want');
  let error = $state<string | null>(null);
  const load = () => session.api.toGet().then((t) => (items = t)).catch((e) => (error = e.status === 404 ? 'This server has no To get list yet.' : e.message));
  $effect(() => void load());

  const shown = $derived((items ?? []).filter((i) => i.status === filter));

  async function set(i: ToGetItem, status: ToGetStatus) {
    await session.api.patchToGet(i.id, { status }).catch((e) => ui.toast(e.message));
    await load();
  }
  async function note(i: ToGetItem) {
    const n = await ui.prompt('Note', `Note for "${i.title}"`, i.note ?? '', 'Save');
    if (n === null) return;
    await session.api.patchToGet(i.id, { note: n || null }).catch((e) => ui.toast(e.message));
    await load();
  }
  async function del(i: ToGetItem) {
    if (!(await ui.confirm('Remove', `Remove "${i.title}" from To get?`, 'Remove'))) return;
    await session.api.deleteToGet(i.id).catch((e) => ui.toast(e.message));
    await load();
  }
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>To get</h1>
  </div>
  <div class="chips">
    {#each [['want', 'Want'], ['got', 'Got'], ['dismissed', 'Dismissed']] as [k, label]}
      <button class="chip" class:on={filter === k} onclick={() => (filter = k as ToGetStatus)}>{label}{items ? ` · ${items.filter((x) => x.status === k).length}` : ''}</button>
    {/each}
  </div>
  {#if error}<div class="empty">{error}</div>
  {:else if !items}<div class="empty">Loading…</div>
  {:else}
    {#each shown as i (i.id)}
      <div class="item">
        <div class="a"><Art item={{ type: 'catalog', id: i.id, title: i.title, catalog: { id: i.catalogId ?? i.id, type: i.type, title: i.title, artist: i.artist, hasCover: i.hasCover, owned: false } }} size={128} /></div>
        <div class="grow">
          <b>{i.title}</b>
          <div class="muted small">{i.type === 'album' ? 'Album' : 'Song'} · {i.artist}{i.year ? ` · ${i.year}` : ''}</div>
          {#if i.note}<div class="small note">“{i.note}”</div>{/if}
          <div class="links">
            {#if i.catalogId}
              <button class="lk" onclick={() => samples.toggle(i.catalogId!)}>{#if samples.playing === i.catalogId}<Pause size={14} /> Stop{:else}<Play size={14} /> Sample{/if}</button>
            {/if}
            {#each i.links ?? [] as l}<a class="lk" href={l.url} target="_blank" rel="noopener noreferrer">{l.name}</a>{/each}
            <button class="lk" onclick={() => note(i)}>{i.note ? 'Edit note' : 'Add note'}</button>
          </div>
        </div>
        <div class="acts">
          {#if i.status !== 'got'}<button class="btn small" onclick={() => set(i, 'got')}>Got it</button>{/if}
          {#if i.status !== 'want'}<button class="btn small" onclick={() => set(i, 'want')}>Want</button>{/if}
          {#if i.status === 'want'}<button class="btn small" onclick={() => set(i, 'dismissed')}>Dismiss</button>{/if}
          <button class="icon-btn" aria-label="Remove" onclick={() => del(i)}><Trash2 size={18} /></button>
        </div>
      </div>
    {:else}
      <div class="empty">{filter === 'want' ? 'Nothing here yet. Search for music you don’t own and press + to add it.' : 'Nothing here.'}</div>
    {/each}
  {/if}
</div>

<style>
  .chips {
    display: flex;
    gap: 8px;
    padding: 12px 16px;
  }
  .chip {
    border-color: #444;
    padding: 6px 14px;
  }
  .item {
    display: flex;
    gap: 14px;
    align-items: flex-start;
    padding: 12px 16px;
    border-bottom: 1px solid var(--line);
    flex-wrap: wrap;
  }
  .a {
    width: 72px;
    flex: none;
  }
  .grow {
    flex: 1;
    min-width: 180px;
  }
  .small {
    font-size: 0.85rem;
  }
  .note {
    color: var(--fg-2);
    margin-top: 2px;
  }
  .links {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 6px;
  }
  .lk {
    display: inline-flex;
    gap: 4px;
    align-items: center;
    font-size: 0.8rem;
    border: 1px solid #444;
    border-radius: 999px;
    padding: 3px 10px;
    color: inherit;
    text-decoration: none;
  }
  .acts {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .btn.small {
    padding: 6px 12px;
    font-size: 0.85rem;
  }
</style>
