<script lang="ts">
  // Admin: every user's want list ("To get", schema v8). Check items off once they are in the library (with an
  // optional note); the user sees "Added" on their own list. Items a scan/upload found are suggested.
  import { Check, Pause, Play, RefreshCw, Search, Undo2 } from '@lucide/svelte';
  import Art from '../../components/Art.svelte';
  import { adminApi, filterWants, type AdminWantItem, type AdminWants, type WantQuery } from '../../lib/api/admin';
  import { samples } from '../../lib/samples.svelte';
  import { session } from '../../lib/session.svelte';
  import { ui } from '../../lib/ui.svelte';

  const A = $derived(adminApi(session.api));
  let data = $state<AdminWants | null>(null);
  let status = $state<NonNullable<WantQuery['status']>>('active');
  let userId = $state('');
  let type = $state<'' | 'album' | 'track'>('');
  let hideDone = $state(true);
  let q = $state('');
  let busy = $state(false);

  async function load() {
    try {
      data = await A.wants({ status });
    } catch (e) {
      ui.toast((e as Error).message);
      data = { items: [], users: [] };
    }
  }
  $effect(() => {
    void status;
    void load();
  });

  const shown = $derived(filterWants(data?.items ?? [], { userId, type, hideDone, q }));
  const groups = $derived.by(() => {
    const m = new Map<string, { username: string; items: AdminWantItem[] }>();
    for (const i of shown) {
      if (!m.has(i.userId)) m.set(i.userId, { username: i.username, items: [] });
      m.get(i.userId)!.items.push(i);
    }
    return [...m.entries()].sort((a, b) => a[1].username.localeCompare(b[1].username));
  });
  const found = $derived((data?.items ?? []).filter((i) => i.suggestDone && (!userId || i.userId === userId)));

  function replace(it: AdminWantItem) {
    if (data) data = { ...data, items: data.items.map((x) => (x.id === it.id ? it : x)) };
  }
  async function check(i: AdminWantItem) {
    const note = await ui.prompt('Check off', `Mark “${i.title}” as added for ${i.username}. Note (optional):`, i.doneNote ?? '', 'Check off');
    if (note === null) return;
    try {
      replace(await A.checkWant(i.id, note || null));
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  async function uncheck(i: AdminWantItem) {
    try {
      replace(await A.uncheckWant(i.id));
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  async function checkFound(ids?: string[]) {
    busy = true;
    try {
      const r = await A.checkFoundWants(ids);
      ui.toast(`Checked off ${r.checked} item${r.checked === 1 ? '' : 's'}`);
      await load();
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
  }
  const day = (s?: string | null) => (s ? new Date(s).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }) : '');
  const STATUS_LABEL = { want: 'Wants it', got: 'Has it', dismissed: 'Dismissed' } as const;
</script>

<h2>Want lists</h2>
<p class="muted small">
  Albums and songs people added to their “To get” list. Check one off when you have added it to the library; they see it as added. Items a scan or upload
  matched in the library are marked “In library”.
</p>

{#if found.length}
  <div class="banner">
    <span class="grow"><b>{found.length}</b> item{found.length === 1 ? ' is' : 's are'} now in the library but not checked off.</span>
    <button class="btn sm primary" disabled={busy} onclick={() => checkFound(found.map((i) => i.id))}><Check size={15} /> Check {found.length === 1 ? 'it' : 'them'} off</button>
  </div>
{/if}

<div class="filters">
  <label class="search">
    <Search size={16} />
    <input type="search" placeholder="Search title, artist, note, user" bind:value={q} aria-label="Search want lists" />
  </label>
  <select bind:value={userId} aria-label="User">
    <option value="">Everyone</option>
    {#each data?.users ?? [] as u (u.userId)}<option value={u.userId}>{u.username} ({u.open} open)</option>{/each}
  </select>
  <select bind:value={type} aria-label="Type">
    <option value="">Albums & songs</option>
    <option value="album">Albums</option>
    <option value="track">Songs</option>
  </select>
  <select bind:value={status} aria-label="User's status">
    <option value="active">Not dismissed</option>
    <option value="want">Still wanted</option>
    <option value="got">User has it</option>
    <option value="dismissed">Dismissed</option>
    <option value="all">All</option>
  </select>
  <label class="row small"><input type="checkbox" bind:checked={hideDone} /> Hide done</label>
  <button class="btn sm text" onclick={load}><RefreshCw size={15} /> Refresh</button>
</div>

{#if !data}<p class="muted">Loading…</p>
{:else if !shown.length}<p class="muted">{data.items.length ? 'Nothing matches.' : 'Nobody has anything on their list.'}</p>
{:else}
  <p class="muted small">{shown.length} item{shown.length === 1 ? '' : 's'}</p>
  {#each groups as [uid, g] (uid)}
    <section class="user">
      {#if !userId}<h3>{g.username} <span class="muted small">· {g.items.length}</span></h3>{/if}
      {#each g.items as i (i.id)}
        <article class="item" class:done={i.done}>
          <div class="a"><Art item={{ type: 'toget', id: i.id, title: i.title, artUrl: i.artUrl, toget: i }} size={128} /></div>
          <div class="grow">
            <div class="title"><b>{i.title}</b></div>
            <div class="muted small">{i.type === 'album' ? 'Album' : 'Song'} · {i.artist}{i.year ? ` · ${i.year}` : ''}</div>
            <div class="small meta">
              <span>{i.username} · {day(i.createdAt)}</span>
              <span class="tag" class:ok={i.status === 'got'}>{STATUS_LABEL[i.status]}</span>
              {#if i.suggestDone}<span class="tag found">In library</span>{/if}
            </div>
            {#if i.note}<div class="small note">“{i.note}”</div>{/if}
            {#if i.done}
              <div class="small doneline"><Check size={13} /> Added {day(i.doneAt)}{i.doneBy ? ` by ${i.doneBy}` : ''}{i.doneNote ? ` — ${i.doneNote}` : ''}</div>
            {/if}
            <div class="links">
              {#if i.catalogId && i.hasPreview !== false}
                <button class="lk" onclick={() => samples.toggle(i.catalogId!)}>{#if samples.playing === i.catalogId}<Pause size={13} /> Stop{:else}<Play size={13} /> Sample{/if}</button>
              {/if}
              {#each i.links ?? [] as l}<a class="lk" href={l.url} target="_blank" rel="noopener noreferrer">{l.name}</a>{/each}
            </div>
          </div>
          <div class="acts">
            {#if i.done}
              <button class="btn sm text" onclick={() => check(i)}>Edit note</button>
              <button class="btn sm" onclick={() => uncheck(i)}><Undo2 size={15} /> Un-check</button>
            {:else}
              <button class="btn sm primary" onclick={() => check(i)}><Check size={15} /> Check off</button>
            {/if}
          </div>
        </article>
      {/each}
    </section>
  {/each}
{/if}

<style>
  .row {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .banner {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    align-items: center;
    padding: 10px 14px;
    margin: 12px 0;
    border-radius: 12px;
    background: color-mix(in srgb, var(--accent) 14%, var(--bg-1));
    border: 1px solid color-mix(in srgb, var(--accent) 40%, var(--line));
  }
  .filters {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
    margin: 12px 0;
  }
  .search {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 1 1 220px;
    padding: 0 10px;
    border: 1px solid var(--line);
    border-radius: 10px;
    background: var(--bg-1);
  }
  .search input {
    flex: 1;
    min-width: 0;
    border: 0;
    background: none;
    padding: 8px 0;
    color: inherit;
  }
  select {
    max-width: 100%;
  }
  h3 {
    margin: 18px 0 4px;
    font-size: 1rem;
  }
  .item {
    display: flex;
    gap: 12px;
    align-items: flex-start;
    flex-wrap: wrap;
    padding: 10px 0;
    border-top: 1px solid var(--line);
  }
  .item.done .title {
    opacity: 0.75;
  }
  .a {
    width: 56px;
    flex: none;
  }
  .item .grow {
    min-width: 200px;
  }
  .title {
    overflow-wrap: anywhere;
  }
  .meta {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    align-items: center;
    margin-top: 2px;
    color: var(--fg-2);
  }
  .tag {
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 0 8px;
    font-size: 0.75rem;
  }
  .tag.ok {
    color: var(--ok, #4caf50);
    border-color: currentColor;
  }
  .tag.found {
    color: var(--accent);
    border-color: currentColor;
  }
  .note {
    color: var(--fg-2);
    margin-top: 2px;
  }
  .doneline {
    display: flex;
    gap: 4px;
    align-items: center;
    color: var(--ok, #4caf50);
    margin-top: 4px;
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
    font-size: 0.78rem;
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 2px 10px;
    color: inherit;
    text-decoration: none;
    background: none;
  }
  .acts {
    display: flex;
    gap: 6px;
    align-items: center;
    flex-wrap: wrap;
  }
</style>
