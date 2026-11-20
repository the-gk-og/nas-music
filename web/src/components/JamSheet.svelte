<script lang="ts">
  // Host controls for a Jam (listen together): start, share the link/QR, approve requests, remove songs, kick
  // guests, end. Live via /api/v1/jam/ws. Guest names are plain text (Svelte escapes them).
  import { onDestroy, onMount } from 'svelte';
  import { Check, Copy, Link2Off, PartyPopper, RefreshCw, Share2, Trash2, UserX, X } from '@lucide/svelte';
  import { JamSocket, applyPlayback, type JamSnapshot } from '../lib/jam';
  import { session } from '../lib/session.svelte';
  import { ui } from '../lib/ui.svelte';
  import Sheet from './Sheet.svelte';

  let snap = $state<JamSnapshot | null>(null);
  let loading = $state(true);
  let busy = $state(false);
  let qr = $state<{ qr: string | null; lanQr: string | null } | null>(null);
  let useLan = $state(false);
  let last = $state<string>('');
  let approval = $state(false);
  let allowPlayNext = $state(false);
  let maxPerGuest = $state<number | null>(5);
  let sock: JamSocket | null = null;

  const api = () => session.api;
  const jam = $derived(snap?.jam);
  const link = $derived(useLan ? jam?.lanJoinUrl : jam?.joinUrl);

  function set(s: JamSnapshot | null) {
    const prevCode = snap?.jam.joinCode;
    snap = s;
    if (s) {
      approval = s.jam.settings.approval;
      allowPlayNext = s.jam.settings.allowPlayNext;
      maxPerGuest = s.jam.settings.maxPerGuest;
      if (s.jam.joinCode !== prevCode) void loadQr(s.jam.id);
      if (!sock) live(s.jam.id);
    }
  }

  async function loadQr(id: string) {
    try {
      qr = await api().jamQr(id);
    } catch {
      qr = null;
    }
  }

  function live(id: string) {
    sock = new JamSocket({
      url: api().jamWsUrl(id),
      onEvent: (e) => {
        if (e.type === 'snapshot') set(e.snapshot);
        else if (e.type === 'playback' && snap) snap = applyPlayback(snap, e);
        else if (e.type === 'reaction') last = `${e.emoji} ${e.name}`;
        else if (e.type === 'gone') {
          sock = null;
          snap = null;
        }
      },
    });
    sock.start();
  }

  onMount(async () => {
    try {
      set((await api().currentJam()) ?? null);
    } catch (e) {
      ui.toast(e instanceof Error ? e.message : String(e));
    } finally {
      loading = false;
    }
  });
  onDestroy(() => sock?.stop());

  async function act(fn: () => Promise<JamSnapshot | void>) {
    busy = true;
    try {
      const r = await fn();
      if (r) set(r);
    } catch (e) {
      ui.toast(e instanceof Error ? e.message : String(e));
    } finally {
      busy = false;
    }
  }

  const start = () => act(() => api().startJam({ approval, allowPlayNext, maxPerGuest: maxPerGuest ?? undefined }));
  const saveSettings = () => jam && act(() => api().patchJam(jam.id, { approval, allowPlayNext, maxPerGuest }));

  async function end() {
    if (!jam) return;
    const id = jam.id;
    // The confirm dialog replaces this sheet (it unmounts); reopen a fresh one afterwards.
    if (!(await ui.confirm('End the Jam?', 'Everyone gets disconnected. Your queue stays as it is.', 'End Jam'))) {
      ui.open({ kind: 'jam' });
      return;
    }
    try {
      await api().endJam(id);
      ui.toast('Jam ended');
    } catch (e) {
      ui.toast(e instanceof Error ? e.message : String(e));
    }
    ui.open({ kind: 'jam' });
  }

  async function copy() {
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      ui.toast('Link copied');
    } catch {
      ui.toast(link);
    }
  }
  async function share() {
    if (!link) return;
    if (navigator.share) await navigator.share({ title: 'Join my Jam', url: link }).catch(() => {});
    else void copy();
  }
  const expiresIn = (iso: string | null | undefined) => {
    if (!iso) return '';
    const m = Math.round((Date.parse(iso) - Date.now()) / 60000);
    return m <= 0 ? 'expired' : m < 90 ? `${m} min` : `${Math.round(m / 60)} h`;
  };
</script>

<Sheet label="Jam" wide>
  <div class="pad">
    <h2><PartyPopper size={24} /> Jam</h2>
    {#if loading}
      <p class="muted">Loading…</p>
    {:else if !snap || !jam}
      <p class="muted">
        Friends open a link on their phone, no account or app needed, and add songs to the queue you're playing.
        Music keeps playing wherever you play it.
      </p>
      <label class="row"><input type="checkbox" bind:checked={approval} /> Approve songs before they're added</label>
      <label class="row"><input type="checkbox" bind:checked={allowPlayNext} /> Guests may use "play next"</label>
      <label class="row">Max songs waiting per guest
        <select bind:value={maxPerGuest}>
          {#each [1, 2, 3, 5, 10, 20] as n (n)}<option value={n}>{n}</option>{/each}
          <option value={null}>No limit</option>
        </select>
      </label>
      <button class="btn primary" disabled={busy} onclick={start}>Start a Jam</button>
    {:else}
      <div class="share">
        {#if jam.joinCode && (useLan ? qr?.lanQr : qr?.qr)}
          <img class="qr" src={useLan ? qr?.lanQr : qr?.qr} alt="QR code for the join link" />
        {/if}
        <div class="share-meta">
          {#if jam.joinCode}
            <div class="link">{link ?? 'No link for this network'}</div>
            <div class="muted small">Link works for {expiresIn(jam.joinExpiresAt)}{useLan ? ' · home Wi-Fi only (guests see a certificate warning)' : ''}</div>
            <div class="btns">
              <button class="btn" onclick={copy}><Copy size={16} /> Copy</button>
              <button class="btn" onclick={share}><Share2 size={16} /> Share</button>
              <button class="btn" onclick={() => (useLan = !useLan)}>{useLan ? 'Public link' : 'LAN link'}</button>
            </div>
          {:else}
            <div class="muted">The join link is closed: nobody new can join.</div>
          {/if}
          <div class="btns">
            <button class="btn" disabled={busy} onclick={() => act(() => api().jamLink(jam.id))}><RefreshCw size={16} /> New link</button>
            {#if jam.joinCode}<button class="btn" disabled={busy} onclick={() => act(() => api().jamLink(jam.id, { close: true }))}><Link2Off size={16} /> Close link</button>{/if}
          </div>
        </div>
      </div>

      <h3>Settings</h3>
      <label class="row"><input type="checkbox" bind:checked={approval} onchange={saveSettings} /> Approve songs before they're added</label>
      <label class="row"><input type="checkbox" bind:checked={allowPlayNext} onchange={saveSettings} /> Guests may use "play next"</label>
      <label class="row">Max songs waiting per guest
        <select bind:value={maxPerGuest} onchange={saveSettings}>
          {#each [1, 2, 3, 5, 10, 20] as n (n)}<option value={n}>{n}</option>{/each}
          <option value={null}>No limit</option>
        </select>
      </label>

      {#if snap.pending.length}
        <h3>Waiting for approval</h3>
        <ul class="list">
          {#each snap.pending as p (p.id)}
            <li>
              <div class="meta"><div class="t">{p.track?.title ?? p.trackId}</div><div class="muted small">{p.track?.artist} · {p.by?.name}{p.playNext ? ' · play next' : ''}</div></div>
              <button class="icon-btn" aria-label="Approve" disabled={busy} onclick={() => act(() => api().decideJamRequest(jam.id, p.id, true))}><Check size={20} /></button>
              <button class="icon-btn" aria-label="Reject" disabled={busy} onclick={() => act(() => api().decideJamRequest(jam.id, p.id, false))}><X size={20} /></button>
            </li>
          {/each}
        </ul>
      {/if}

      <h3>Up next {last ? `· ${last}` : ''}</h3>
      <ul class="list">
        {#if snap.nowPlaying}
          <li class="now"><div class="meta"><div class="t">▶ {snap.nowPlaying.track.title}</div><div class="muted small">{snap.nowPlaying.track.artist} · {snap.nowPlaying.addedBy.name}</div></div></li>
        {/if}
        {#each snap.upcoming as e (e.position + ':' + e.track.id)}
          <li>
            <div class="meta">
              <div class="t">{e.track.title}</div>
              <div class="muted small">{e.track.artist} · {e.addedBy.isHost ? 'you' : e.addedBy.name}{e.votes.up || e.votes.down ? ` · 👍 ${e.votes.up} 👎 ${e.votes.down}` : ''}</div>
            </div>
            <button class="icon-btn" aria-label="Remove from queue" disabled={busy} onclick={() => act(() => api().removeJamEntry(jam.id, e.position, e.track.id))}><Trash2 size={18} /></button>
          </li>
        {:else}
          <li class="muted">Nothing queued yet.</li>
        {/each}
      </ul>

      <h3>Guests ({snap.guests.length})</h3>
      <ul class="list">
        {#each snap.guests as g (g.id)}
          <li>
            <div class="meta"><div class="t">{g.name}</div>{#if g.isMember}<div class="muted small">signed-in user</div>{/if}</div>
            <button class="icon-btn" aria-label={`Remove ${g.name}`} disabled={busy} onclick={() => act(() => api().kickJamGuest(jam.id, g.id))}><UserX size={18} /></button>
          </li>
        {:else}
          <li class="muted">No one has joined yet.</li>
        {/each}
      </ul>
    {/if}
  </div>
  <div class="foot">
    {#if snap}<button class="btn danger" disabled={busy} onclick={end}>End Jam</button>{:else}<span></span>{/if}
    <button class="btn text" onclick={() => ui.close()}>Close</button>
  </div>
</Sheet>

<style>
  .pad {
    padding: 20px;
  }
  h2 {
    display: flex;
    gap: 8px;
    align-items: center;
    margin: 0 0 12px;
  }
  h3 {
    font-size: 13px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--fg-3);
    margin: 18px 0 6px;
  }
  .muted {
    color: var(--fg-3);
  }
  .small {
    font-size: 13px;
  }
  .row {
    display: flex;
    gap: 10px;
    align-items: center;
    padding: 8px 0;
  }
  select {
    margin-left: auto;
    background: var(--bg-3);
    color: var(--fg);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 4px 8px;
  }
  .btn {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 7px 14px;
  }
  .btn.primary {
    margin-top: 12px;
    background: var(--accent);
    color: #000;
    font-weight: 600;
    border: 0;
  }
  .btn.danger {
    color: var(--danger);
  }
  .btn.text {
    border: 0;
  }
  .share {
    display: flex;
    gap: 16px;
    flex-wrap: wrap;
    align-items: flex-start;
  }
  .qr {
    width: 168px;
    height: 168px;
    border-radius: 10px;
    background: #fff;
    image-rendering: pixelated;
  }
  .share-meta {
    flex: 1;
    min-width: 220px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .link {
    font-family: ui-monospace, monospace;
    font-size: 13px;
    overflow-wrap: anywhere;
    background: var(--bg-3);
    padding: 8px 10px;
    border-radius: 8px;
  }
  .btns {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .list {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .list li {
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 6px 0;
    border-bottom: 1px solid var(--bg-3);
  }
  .list li.now .t {
    color: var(--accent);
  }
  .meta {
    flex: 1;
    min-width: 0;
  }
  .t,
  .meta .small {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .icon-btn {
    padding: 6px;
    border-radius: 999px;
    color: var(--fg-2);
  }
  .foot {
    display: flex;
    justify-content: space-between;
    padding: 12px 20px 16px;
    border-top: 1px solid var(--line);
  }
</style>
