<script lang="ts">
  // Admin: explicit-content flags (review + bulk set) and per-user family mode with lock / PIN (v6).
  import { Lock, LockOpen, Search } from '@lucide/svelte';
  import { familyApi, type AdminFamilyUser, type ExplicitReview, type ExplicitSource } from '../../lib/api/admin';
  import { family } from '../../lib/family.svelte';
  import { library } from '../../lib/library.svelte';
  import { session } from '../../lib/session.svelte';
  import type { ExplicitStatus } from '../../lib/types';
  import { ui } from '../../lib/ui.svelte';
  import ExplicitBadge from '../../components/ExplicitBadge.svelte';

  const F = $derived(familyApi(session.api));
  const STATUSES: ExplicitStatus[] = ['probable', 'unknown', 'explicit', 'clean'];
  const SOURCE_LABEL: Record<ExplicitSource, string> = { admin: 'admin', tags: 'file tags', deezer: 'Deezer', lyrics: 'lyrics word list' };

  let users = $state<AdminFamilyUser[] | null>(null);
  let pins = $state<Record<string, string>>({});
  let review = $state<ExplicitReview | null>(null);
  let filter = $state<Set<ExplicitStatus>>(new Set(['probable', 'unknown']));
  let q = $state('');
  let offset = $state(0);
  let selected = $state<Set<string>>(new Set());
  let busy = $state(false);
  const PAGE = 100;

  async function loadUsers() {
    try {
      users = await F.users();
    } catch (e) {
      ui.toast((e as Error).message);
      users = [];
    }
  }
  async function loadReview() {
    try {
      review = await F.review({ status: [...filter], q: q.trim() || undefined, limit: PAGE, offset });
      selected = new Set();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  $effect(() => {
    void library.tagsRev;
    void loadUsers();
  });
  $effect(() => {
    void library.tagsRev;
    void loadReview();
  });

  async function setUser(u: AdminFamilyUser, b: Parameters<typeof F.setUser>[1]) {
    try {
      const r = await F.setUser(u.userId, b);
      users = (users ?? []).map((x) => (x.userId === u.userId ? r : x));
      if (b.pin) pins = { ...pins, [u.userId]: '' };
      if (u.userId === session.saved.userId) void family.load();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  function lock(u: AdminFamilyUser) {
    const pin = (pins[u.userId] ?? '').trim();
    if (pin && !/^[0-9]{4,12}$/.test(pin)) return ui.toast('The PIN must be 4–12 digits');
    void setUser(u, pin ? { locked: true, pin } : { locked: true });
  }

  function toggleStatus(s: ExplicitStatus) {
    const n = new Set(filter);
    if (n.has(s)) n.delete(s);
    else n.add(s);
    filter = n;
    offset = 0;
    void loadReview();
  }
  function toggle(id: string) {
    const s = new Set(selected);
    if (s.has(id)) s.delete(id);
    else s.add(id);
    selected = s;
  }
  const allOn = $derived(!!review?.items.length && review.items.every((i) => selected.has(i.trackId)));
  const toggleAll = () => (selected = allOn ? new Set() : new Set(review?.items.map((i) => i.trackId)));

  async function mark(value: 'explicit' | 'clean' | null, ids = [...selected], albumIds: string[] = []) {
    if (!ids.length && !albumIds.length) return;
    busy = true;
    try {
      const r = await F.set({ trackIds: ids, albumIds, value });
      ui.toast(value ? `Marked ${r.tracks} as ${value}` : `${r.tracks} back to automatic`);
      library.tagsRev++;
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
  }
  function evidence(i: ExplicitReview['items'][number]): string {
    const parts: string[] = [];
    if (i.admin) parts.push(`admin: ${i.admin}`);
    if (i.tags) parts.push(`tags: ${i.tags}`);
    if (i.deezer) parts.push(`Deezer: ${i.deezer.replace('_', ' ')}`);
    if (i.lyrics) parts.push(i.lyrics === 'none' ? 'no lyrics' : `lyrics: ${i.lyrics}${i.lyricsHits ? ` (${i.lyricsHits} hits)` : ''}`);
    return parts.join(' · ') || 'not checked yet';
  }
</script>

<h2>Family mode</h2>
<p class="muted small">
  Family mode hides explicit songs from browsing, search, mixes, radio and recommendations, and skips them in queues — enforced
  by the server. Lock it on for an account (e.g. a child's) so that user can't turn it off; with a PIN, someone who knows it can
  unlock it on their device, without one only an admin can.
</p>
{#if !users}<p class="muted">Loading…</p>
{:else}
  <div class="table">
    {#each users as u (u.userId)}
      <div class="item">
        <div class="grow">
          <b>{u.username}</b>{#if u.isAdmin} <span class="muted small">admin</span>{/if}
          <div class="muted small">
            {u.enabled ? 'On' : 'Off'}{u.enabled ? ` · hides explicit${u.hideProbable ? ', probable' : ''}${u.hideUnknown ? ', unchecked' : ''}` : ''}
            {#if u.locked} · locked{u.lockedBy ? ` by ${u.lockedBy}` : ''}{u.hasPin ? ' (PIN)' : ' (admin-only)'}{/if}
          </div>
        </div>
        <label class="small"><input type="checkbox" checked={u.enabled} disabled={u.locked} onchange={(e) => setUser(u, { enabled: e.currentTarget.checked })} /> On</label>
        <label class="small"><input type="checkbox" checked={u.hideProbable} onchange={(e) => setUser(u, { hideProbable: e.currentTarget.checked })} /> Probable</label>
        <label class="small"><input type="checkbox" checked={u.hideUnknown} onchange={(e) => setUser(u, { hideUnknown: e.currentTarget.checked })} /> Unchecked</label>
        {#if u.locked}
          {#if u.hasPin}<button class="btn text" onclick={() => setUser(u, { clearPin: true })}>Remove PIN</button>{/if}
          <button class="btn text" onclick={() => setUser(u, { locked: false })}><LockOpen size={16} /> Unlock</button>
        {:else}
          <input class="pin" inputmode="numeric" placeholder="PIN (optional)" aria-label="Unlock PIN for {u.username}" bind:value={pins[u.userId]} />
          <button class="btn text" onclick={() => lock(u)}><Lock size={16} /> Lock on</button>
        {/if}
      </div>
    {/each}
  </div>
{/if}

<h2>Explicit flags</h2>
<p class="muted small">
  Each song is checked automatically by the <b>explicit</b> job (after scans and nightly): file tags first, then Deezer, then a
  swear-word list over its lyrics, which only ever says “probably explicit”. Your own setting beats all of them. Counts:
  {#if review}{review.counts.explicit} explicit · {review.counts.probable} probable · {review.counts.clean} clean · {review.counts.unknown} unchecked{/if}
</p>
<div class="row">
  {#each STATUSES as s (s)}
    <button class="chip" class:on={filter.has(s)} aria-pressed={filter.has(s)} onclick={() => toggleStatus(s)}>{s === 'unknown' ? 'unchecked' : s}</button>
  {/each}
  <form class="row grow" onsubmit={(e) => { e.preventDefault(); offset = 0; void loadReview(); }}>
    <input type="search" placeholder="Filter by song, artist, album…" bind:value={q} aria-label="Filter the review list" />
    <button class="btn"><Search size={16} /></button>
  </form>
</div>
{#if review}
  <div class="row bar">
    <label><input type="checkbox" checked={allOn} onchange={toggleAll} /> Select all ({review.items.length} of {review.total})</label>
    <span class="row">
      <button class="btn" disabled={!selected.size || busy} onclick={() => mark('explicit')}>Mark explicit</button>
      <button class="btn" disabled={!selected.size || busy} onclick={() => mark('clean')}>Mark clean</button>
      <button class="btn text" disabled={!selected.size || busy} onclick={() => mark(null)}>Automatic</button>
    </span>
  </div>
  {#each review.items as i (i.trackId)}
    <label class="item">
      <input type="checkbox" checked={selected.has(i.trackId)} onchange={() => toggle(i.trackId)} />
      <div class="grow">
        <div><ExplicitBadge status={i.status} /><b>{i.title}</b> <span class="muted">— {i.artist} · {i.album}</span></div>
        <div class="muted small">{i.status === 'unknown' ? 'unchecked' : i.status}{i.source ? ` (${SOURCE_LABEL[i.source]})` : ''} · {evidence(i)}</div>
      </div>
      <button class="btn text small" title="Mark the whole album explicit" onclick={(e) => { e.preventDefault(); void mark('explicit', [], [i.albumId]); }}>Album: E</button>
      <button class="btn text small" title="Mark the whole album clean" onclick={(e) => { e.preventDefault(); void mark('clean', [], [i.albumId]); }}>Album: clean</button>
    </label>
  {:else}
    <p class="muted">Nothing to review.</p>
  {/each}
  {#if review.total > PAGE}
    <div class="row">
      <button class="btn text" disabled={offset === 0} onclick={() => { offset = Math.max(0, offset - PAGE); void loadReview(); }}>Previous</button>
      <span class="muted small">{offset + 1}–{Math.min(offset + PAGE, review.total)} of {review.total}</span>
      <button class="btn text" disabled={offset + PAGE >= review.total} onclick={() => { offset += PAGE; void loadReview(); }}>Next</button>
    </div>
  {/if}
{:else}<p class="muted">Loading…</p>{/if}

<style>
  .row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    align-items: center;
    margin: 10px 0;
  }
  .row.grow {
    flex: 1;
    margin: 0;
  }
  .row input[type='search'] {
    flex: 1;
    min-width: 180px;
  }
  .bar {
    justify-content: space-between;
  }
  .item {
    display: flex;
    gap: 10px;
    align-items: center;
    flex-wrap: wrap;
    padding: 8px 0;
    border-bottom: 1px solid var(--line, #8882);
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .pin {
    width: 120px;
  }
  .chip {
    border: 1px solid var(--line, #8884);
    border-radius: 999px;
    padding: 4px 12px;
    background: none;
    color: inherit;
    cursor: pointer;
  }
  .chip.on {
    background: var(--accent);
    color: var(--bg);
    border-color: var(--accent);
  }
</style>
