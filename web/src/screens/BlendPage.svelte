<script lang="ts">
  // Blend: a shared, auto-updating playlist. id "new" = start one (pick people, name it).
  // Invitees see who's in it and Accept / Decline; members see the tracks with "because Alex plays…".
  import { ArrowLeft, LogOut, Play, RefreshCw, Shuffle, UserPlus } from '@lucide/svelte';
  import TrackRow from '../components/TrackRow.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { Blend } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import { blendReason } from '../lib/wrapped';

  let { id }: { id: string } = $props();
  let blend = $state<Blend | null>(null);
  let error = $state<string | null>(null);
  let candidates = $state<{ userId: string; username: string }[]>([]);
  let picked = $state<string[]>([]);
  let name = $state('');
  let busy = $state(false);

  const isNew = $derived(id === 'new');
  $effect(() => {
    if (isNew) {
      session.api.blendCandidates().then((c) => (candidates = c)).catch((e) => (error = e.message));
      return;
    }
    session.api
      .blend(id)
      .then((b) => (blend = b))
      .catch((e) => (error = e.status === 404 ? 'This blend is gone, or you are no longer in it.' : e.message));
  });

  const names = $derived(Object.fromEntries((blend?.members ?? []).map((m) => [m.userId, m.username])));
  const tracks = $derived((blend?.tracks ?? []).map((t) => t.track));
  const me = $derived(session.saved.userId);

  async function act<T>(fn: () => Promise<T>): Promise<T | undefined> {
    busy = true;
    try {
      return await fn();
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      busy = false;
    }
  }
  async function create() {
    const b = await act(() => session.api.createBlend(picked, name.trim() || null));
    if (b) ui.go('blend', b.id);
  }
  async function accept() {
    const b = await act(() => session.api.acceptBlend(id));
    if (b) blend = b;
  }
  async function decline() {
    await act(() => session.api.declineBlend(id));
    ui.go('foryou');
  }
  async function leave() {
    if (!(await ui.confirm('Leave blend', `Leave "${blend?.name}"? Your listening stops being used in it right away.`, 'Leave'))) return;
    await act(() => session.api.leaveBlend(id));
    ui.go('foryou');
  }
  async function invite() {
    const others = await session.api.blendCandidates().catch(() => []);
    const inBlend = new Set(blend?.members.map((m) => m.userId));
    const avail = others.filter((o) => !inBlend.has(o.userId)).map((o) => o.username);
    const who = await ui.prompt('Invite', avail.length ? `Username (${avail.join(', ')})` : 'Username', '', 'Invite');
    if (!who) return;
    const b = await act(() => session.api.inviteToBlend(id, [who]));
    if (b) blend = b;
  }
  async function rename() {
    const n = await ui.prompt('Rename blend', 'Name', blend?.name ?? '', 'Save');
    if (!n) return;
    const b = await act(() => session.api.renameBlend(id, n));
    if (b) blend = b;
  }
  async function refresh() {
    const b = await act(() => session.api.refreshBlend(id));
    if (b) blend = b;
  }
  const play = (i = 0, shuffle = false) => {
    if (!tracks.length) return;
    const list = shuffle ? [...tracks].sort(() => Math.random() - 0.5) : tracks;
    void player.playList(list, i, `Blend: ${blend?.name}`, { source: 'mix', addedFrom: 'blend' });
  };
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>{isNew ? 'Start a blend' : (blend?.name ?? 'Blend')}</h1>
  </div>

  {#if error}<div class="empty">{error}</div>
  {:else if isNew}
    <div class="form">
      <p class="muted">A blend is a playlist that mixes your taste with other people’s on this server and updates every night. Each person you invite has to say yes before any of their listening is used, and anyone can leave at any time.</p>
      {#if !candidates.length}<div class="empty">Nobody here can be invited yet.</div>{/if}
      {#each candidates as c (c.userId)}
        <label class="who"><input type="checkbox" value={c.username} bind:group={picked} /> {c.username}</label>
      {/each}
      <label class="name">Name (optional)<input bind:value={name} maxlength="80" placeholder="e.g. Car trips" /></label>
      <button class="btn primary" disabled={!picked.length || busy} onclick={create}>Send invite{picked.length > 1 ? 's' : ''}</button>
    </div>
  {:else if !blend}<div class="empty">Loading…</div>
  {:else}
    <div class="head">
      <div class="match">
        {#if blend.matchPct != null}<b>{blend.matchPct}%</b><span>taste match</span>{:else}<span class="muted">{blend.myStatus === 'invited' ? 'Invitation' : 'Waiting for others to join'}</span>{/if}
      </div>
      <div class="members">
        {#each blend.members as m (m.userId)}<span class="mem" class:inv={m.status === 'invited'}>{m.userId === me ? 'You' : m.username}{m.status === 'invited' ? ' (invited)' : ''}</span>{/each}
      </div>
      {#if blend.pairs && blend.pairs.length > 1}
        <div class="muted small">{blend.pairs.map((p) => `${p.aName} & ${p.bName}: ${p.pct ?? '–'}%`).join(' · ')}</div>
      {/if}
    </div>

    {#if blend.myStatus === 'invited'}
      <div class="invite">
        <p><b>{blend.invitedBy ?? blend.createdBy?.username}</b> invited you to this blend. If you accept, your plays and favourites are used to build it, and the other members see which songs came from your taste.</p>
        <button class="btn primary" disabled={busy} onclick={accept}>Accept</button>
        <button class="btn" disabled={busy} onclick={decline}>Decline</button>
      </div>
    {:else}
      <div class="actions">
        <button class="btn primary" disabled={!tracks.length} onclick={() => play()}><Play size={18} /> Play</button>
        <button class="btn" disabled={!tracks.length} onclick={() => play(0, true)}><Shuffle size={18} /> Shuffle</button>
        <button class="icon-btn" aria-label="Invite" title="Invite" onclick={invite}><UserPlus size={20} /></button>
        <button class="icon-btn" aria-label="Refresh" title="Refresh now" disabled={busy} onclick={refresh}><RefreshCw size={20} /></button>
        <button class="btn text" onclick={rename}>Rename</button>
        <button class="btn text danger" onclick={leave}><LogOut size={16} /> Leave</button>
      </div>
      {#each blend.tracks ?? [] as t, i (t.track.id + ':' + i)}
        <div class="trk">
          <TrackRow track={t.track} current={t.track.id === player.shown.track?.id} onplay={() => play(i)} onmenu={() => ui.open({ kind: 'songMenu', ctx: { track: t.track, list: tracks } })} />
          <div class="why small muted">{blendReason(t.reason, t.forUsers, names, me ?? undefined)}</div>
        </div>
      {:else}
        <div class="empty">No songs yet — they appear once at least two people have joined.</div>
      {/each}
    {/if}
  {/if}
</div>

<style>
  .form,
  .head,
  .invite,
  .actions {
    padding: 0 16px 12px;
  }
  .form {
    display: flex;
    flex-direction: column;
    gap: 10px;
    max-width: 560px;
  }
  .who {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .name {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .match b {
    font-size: 2.6rem;
    margin-right: 8px;
  }
  .members {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 8px 0;
  }
  .mem {
    padding: 3px 10px;
    border-radius: 99px;
    background: rgba(255, 255, 255, 0.08);
  }
  .mem.inv {
    opacity: 0.6;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .actions .btn {
    display: inline-flex;
    gap: 6px;
    align-items: center;
  }
  .why {
    padding: 0 16px 6px 72px;
    margin-top: -6px;
  }
</style>
