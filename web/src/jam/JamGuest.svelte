<script lang="ts">
  // Jam guest page: join with a name, see what's playing and coming up, search the host's library, ask for songs,
  // vote and react. Everything guest-supplied is rendered as text (Svelte escapes it; no {@html} here).
  import { onDestroy, onMount } from 'svelte';
  import { Headphones, ListMusic, LogOut, Plus, Search, ThumbsDown, ThumbsUp, Undo2, X } from '@lucide/svelte';
  import {
    applyPlayback,
    clockOffset,
    codeFromPath,
    fmtMs,
    JamSocket,
    positionNow,
    REACTIONS,
    type JamEntry,
    type JamPreview,
    type JamSnapshot,
    type JamTrack,
  } from '../lib/jam';
  import { guestApi, JamHttpError } from './guestApi';

  const code = codeFromPath(location.pathname);
  const storeKey = `nm.jam.${code}`;

  let phase = $state<'loading' | 'join' | 'in' | 'gone' | 'invalid'>('loading');
  let goneText = $state('');
  let canRejoin = $state(false);
  let preview = $state<JamPreview | null>(null);
  let name = $state('');
  let busy = $state(false);
  let error = $state('');
  let token = $state<string | null>(null);
  let snap = $state<JamSnapshot | null>(null);
  let offset = $state(0);
  let now = $state(Date.now());
  let tab = $state<'next' | 'add'>('next');
  let q = $state('');
  let results = $state<JamTrack[]>([]);
  let searching = $state(false);
  let toasts = $state<{ id: number; text: string }[]>([]);
  let floats = $state<{ id: number; emoji: string; name: string; x: number }[]>([]);
  let listening = $state(false);
  let audio: HTMLAudioElement | undefined = $state();
  let sock: JamSocket | null = null;
  let tick: ReturnType<typeof setInterval> | undefined;
  let searchTimer: ReturnType<typeof setTimeout> | undefined;
  let nextId = 0;

  const np = $derived(snap?.nowPlaying ?? null);
  const pos = $derived(np ? Math.min(positionNow(np, offset, now), np.track.durationS * 1000) : 0);
  const me = $derived(snap?.me);
  const settings = $derived(snap?.jam.settings);
  const queuedIds = $derived(new Set([np?.track.id, ...(snap?.upcoming ?? []).map((e) => e.track.id), ...(snap?.pending ?? []).map((p) => p.trackId)]));

  function readToken(): string | null {
    try {
      return localStorage.getItem(storeKey);
    } catch {
      return null;
    }
  }
  function saveToken(t: string | null) {
    try {
      if (t) localStorage.setItem(storeKey, t);
      else localStorage.removeItem(storeKey);
    } catch {
      /* private mode: the session lasts as long as the tab */
    }
  }

  function toast(text: string) {
    const id = ++nextId;
    toasts = [...toasts.slice(-2), { id, text }];
    setTimeout(() => (toasts = toasts.filter((t) => t.id !== id)), 3500);
  }

  function setSnap(s: JamSnapshot) {
    snap = s;
    offset = clockOffset(s.serverTime);
    syncAudio();
  }

  function leaveWith(text: string) {
    sock?.stop();
    sock = null;
    saveToken(null);
    token = null;
    stopListening();
    goneText = text;
    phase = 'gone';
  }

  function connect(t: string) {
    sock?.stop();
    sock = new JamSocket({
      url: guestApi.wsUrl(t),
      onEvent: (e) => {
        if (e.type === 'snapshot') setSnap(e.snapshot);
        else if (e.type === 'playback' && snap) {
          snap = applyPlayback(snap, e);
          offset = clockOffset(e.serverTime);
          syncAudio();
        } else if (e.type === 'reaction') float(e.emoji, e.name);
        else if (e.type === 'gone') {
          canRejoin = e.reason !== 'ended';
          leaveWith(e.reason === 'kicked' ? 'The host removed you from this Jam.' : e.reason === 'ended' ? 'The Jam has ended. Thanks for listening!' : 'Your Jam session expired.');
        }
      },
    });
    sock.start();
  }

  async function enter(t: string, s?: JamSnapshot) {
    token = t;
    saveToken(t);
    setSnap(s ?? (await guestApi.me(t)));
    phase = 'in';
    connect(t);
  }

  onMount(async () => {
    tick = setInterval(() => (now = Date.now()), 500);
    if (!code) {
      phase = 'invalid';
      goneText = "This isn't a Jam link.";
      return;
    }
    const saved = readToken();
    if (saved) {
      try {
        await enter(saved);
        return;
      } catch {
        saveToken(null);
      }
    }
    try {
      preview = await guestApi.preview(code);
      phase = 'join';
    } catch (e) {
      phase = 'invalid';
      goneText = e instanceof JamHttpError ? e.message : 'Could not reach the server.';
    }
  });

  onDestroy(() => {
    sock?.stop();
    clearInterval(tick);
    clearTimeout(searchTimer);
  });

  async function join(asMember = false) {
    if (!code || busy) return;
    error = '';
    busy = true;
    try {
      const r = await guestApi.join(code, asMember ? null : name.trim(), asMember);
      const { token: t, ...s } = r;
      await enter(t, s as JamSnapshot);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  async function guarded(fn: () => Promise<unknown>, ok?: string) {
    if (!token) return;
    try {
      await fn();
      if (ok) toast(ok);
    } catch (e) {
      if (e instanceof JamHttpError && e.status === 401) leaveWith('You are no longer in this Jam.');
      else toast(e instanceof Error ? e.message : String(e));
    }
  }

  function onSearchInput() {
    clearTimeout(searchTimer);
    const term = q.trim();
    if (term.length < 2) {
      results = [];
      return;
    }
    searchTimer = setTimeout(async () => {
      searching = true;
      await guarded(async () => (results = (await guestApi.search(token!, term)).tracks));
      searching = false;
    }, 350);
  }

  const add = (t: JamTrack, playNext: boolean) =>
    guarded(async () => {
      const r = await guestApi.add(token!, t.id, playNext);
      toast(r.queued ? `Added “${t.title}”` : `Asked the host for “${t.title}”`);
    });

  const vote = (e: Pick<JamEntry, 'track' | 'votes'>, v: -1 | 1) =>
    guarded(() => guestApi.vote(token!, e.track.id, e.votes.mine === v ? 0 : v));

  const react = (emoji: string) => guarded(() => guestApi.react(token!, emoji));

  async function leave() {
    if (token) await guestApi.leave(token).catch(() => {});
    canRejoin = true;
    leaveWith('You left the Jam.');
  }

  function float(emoji: string, who: string) {
    const id = ++nextId;
    floats = [...floats.slice(-12), { id, emoji, name: who, x: 10 + Math.random() * 75 }];
    setTimeout(() => (floats = floats.filter((f) => f.id !== id)), 2600);
  }

  // ---- signed-in members: listen along on this device (stream with their own session, synced to the host) ----
  function syncAudio() {
    if (!listening || !audio || !snap) return;
    const cur = snap.nowPlaying;
    if (!cur) {
      audio.pause();
      return;
    }
    const src = `/api/v1/stream/${encodeURIComponent(cur.track.id)}?format=opus`;
    if (!audio.src.endsWith(src)) audio.src = src;
    const target = positionNow(cur, offset) / 1000;
    if (Math.abs(audio.currentTime - target) > 1.5) audio.currentTime = target;
    if (cur.playing && audio.paused) void audio.play().catch(() => toast('Tap “Listen along” again to start audio.'));
    if (!cur.playing && !audio.paused) audio.pause();
  }
  function startListening() {
    listening = true;
    syncAudio();
  }
  function stopListening() {
    listening = false;
    audio?.pause();
  }
  $effect(() => {
    // Re-check drift every few seconds while listening.
    if (!listening) return;
    const t = setInterval(syncAudio, 4000);
    return () => clearInterval(t);
  });
</script>

<svelte:head><title>{snap ? `Jam with ${snap.jam.hostName}` : 'Jam'} · Homebeat</title></svelte:head>

<main class="page">
  {#if phase === 'loading'}
    <div class="center muted">Loading…</div>
  {:else if phase === 'invalid' || phase === 'gone'}
    <div class="center card">
      <div class="big">♫</div>
      <h1>{phase === 'gone' ? 'See you next time' : 'Jam'}</h1>
      <p class="muted">{goneText}</p>
      {#if phase === 'gone' && canRejoin}<button class="btn" onclick={() => location.reload()}>Rejoin</button>{/if}
    </div>
  {:else if phase === 'join' && preview}
    <form class="center card" onsubmit={(e) => { e.preventDefault(); void join(); }}>
      <div class="big">♫</div>
      <h1>{preview.hostName} invited you to a Jam</h1>
      <p class="muted">Add songs to the queue and vote on what plays next. {preview.guestCount ? `${preview.guestCount} already here.` : ''}</p>
      <label class="field">
        <span>Your name</span>
        <!-- svelte-ignore a11y_autofocus -->
        <input bind:value={name} maxlength="32" autocomplete="nickname" autofocus placeholder="e.g. Sam" />
      </label>
      <button class="btn" type="submit" disabled={busy || !name.trim()}>Join</button>
      {#if preview.signedInAs}
        <button class="btn ghost" type="button" disabled={busy} onclick={() => join(true)}>Join as {preview.signedInAs}</button>
      {/if}
      {#if error}<p class="err">{error}</p>{/if}
      <p class="fine muted">No account or app needed. You can only suggest songs; the music plays on the host's speakers.</p>
    </form>
  {:else if phase === 'in' && snap}
    <header class="top">
      <div>
        <div class="title">Jam with {snap.jam.hostName}</div>
        <div class="muted small">{snap.guests.length + 1} listening · you're {me?.name}</div>
      </div>
      <button class="icon" aria-label="Leave the Jam" onclick={leave}><LogOut size={20} /></button>
    </header>

    <section class="now">
      {#if np}
        <img class="art" src={guestApi.artUrl(token!, np.track.albumId, 320)} alt="" onerror={(e) => ((e.currentTarget as HTMLImageElement).style.visibility = 'hidden')} />
        <div class="np-meta">
          <div class="np-title">{np.track.title}</div>
          <div class="muted">{np.track.artist}</div>
          <div class="muted small">{np.addedBy.isHost ? `Picked by ${np.addedBy.name}` : `Added by ${np.addedBy.name}`}{np.playing ? '' : ' · paused'}</div>
          <div class="bar"><div style:width={`${Math.min(100, (pos / Math.max(1, np.track.durationS * 1000)) * 100)}%`}></div></div>
          <div class="times muted small"><span>{fmtMs(pos)}</span><span>{fmtMs(np.track.durationS * 1000)}</span></div>
          <div class="votes">
            <button class="chip" class:on={np.votes.mine === 1} aria-label="Vote up" onclick={() => vote(np, 1)}><ThumbsUp size={16} /> {np.votes.up}</button>
            <button class="chip" class:on={np.votes.mine === -1} aria-label="Vote down" onclick={() => vote(np, -1)}><ThumbsDown size={16} /> {np.votes.down}</button>
            {#if me?.isMember}
              {#if listening}
                <button class="chip on" onclick={stopListening}><Headphones size={16} /> Listening</button>
              {:else}
                <button class="chip" onclick={startListening}><Headphones size={16} /> Listen along</button>
              {/if}
            {/if}
          </div>
        </div>
      {:else}
        <div class="muted">Nothing playing yet. Add a song to get things going.</div>
      {/if}
    </section>

    <div class="reactions" role="group" aria-label="Reactions">
      {#each REACTIONS as r (r)}<button onclick={() => react(r)} aria-label={`React ${r}`}>{r}</button>{/each}
    </div>

    <nav class="tabs">
      <button class:sel={tab === 'next'} onclick={() => (tab = 'next')}><ListMusic size={18} /> Up next</button>
      <button class:sel={tab === 'add'} onclick={() => (tab = 'add')}><Search size={18} /> Add songs</button>
    </nav>

    {#if tab === 'next'}
      {#if snap.pending.length}
        <h2>Waiting for the host</h2>
        <ul class="list">
          {#each snap.pending as p (p.id)}
            <li>
              <div class="meta"><div class="t">{p.track?.title ?? 'Unknown'}</div><div class="muted small">{p.track?.artist}{p.playNext ? ' · play next' : ''}</div></div>
              <button class="icon" aria-label="Withdraw request" onclick={() => guarded(() => guestApi.withdraw(token!, p.id), 'Request withdrawn')}><Undo2 size={18} /></button>
            </li>
          {/each}
        </ul>
      {/if}
      <h2>Up next</h2>
      <ul class="list">
        {#each snap.upcoming as e (e.position + ':' + e.track.id)}
          <li class:mine={e.addedBy.guestId === me?.guestId}>
            <img class="thumb" src={guestApi.artUrl(token!, e.track.albumId, 96)} alt="" loading="lazy" onerror={(ev) => ((ev.currentTarget as HTMLImageElement).style.visibility = 'hidden')} />
            <div class="meta">
              <div class="t">{e.track.title}</div>
              <div class="muted small">{e.track.artist} · {e.addedBy.guestId === me?.guestId ? 'you' : e.addedBy.name}</div>
            </div>
            <button class="chip sm" class:on={e.votes.mine === 1} aria-label="Vote up" onclick={() => vote(e, 1)}><ThumbsUp size={14} />{e.votes.up || ''}</button>
            <button class="chip sm" class:on={e.votes.mine === -1} aria-label="Vote down" onclick={() => vote(e, -1)}><ThumbsDown size={14} />{e.votes.down || ''}</button>
          </li>
        {:else}
          <li class="muted empty">The queue is empty. Add something!</li>
        {/each}
      </ul>
    {:else}
      <div class="search">
        <Search size={18} />
        <input bind:value={q} oninput={onSearchInput} placeholder="Search the host's library" enterkeyhint="search" maxlength="100" />
        {#if q}<button class="icon" aria-label="Clear" onclick={() => { q = ''; results = []; }}><X size={18} /></button>{/if}
      </div>
      <p class="muted small">
        {settings?.approval ? 'The host approves songs before they play. ' : ''}
        {me?.remaining != null ? `You can add ${me.remaining} more right now.` : ''}
      </p>
      <ul class="list">
        {#each results as t (t.id)}
          <li>
            <div class="meta"><div class="t">{t.title}</div><div class="muted small">{t.artist} · {t.album}</div></div>
            {#if queuedIds.has(t.id)}
              <span class="muted small">in the queue</span>
            {:else}
              {#if settings?.allowPlayNext}<button class="chip sm" onclick={() => add(t, true)}>Next</button>{/if}
              <button class="chip sm on" aria-label={`Add ${t.title}`} onclick={() => add(t, false)}><Plus size={14} /> Add</button>
            {/if}
          </li>
        {:else}
          {#if q.trim().length >= 2 && !searching}<li class="muted empty">No songs found.</li>{/if}
        {/each}
      </ul>
    {/if}
    <audio bind:this={audio} preload="auto"></audio>
  {/if}

  <div class="floats" aria-hidden="true">
    {#each floats as f (f.id)}<div class="float" style:left={`${f.x}%`}><span>{f.emoji}</span><small>{f.name}</small></div>{/each}
  </div>
  <div class="toasts" role="status">
    {#each toasts as t (t.id)}<div class="toast">{t.text}</div>{/each}
  </div>
</main>

<style>
  .page {
    max-width: 560px;
    margin: 0 auto;
    padding: 12px 16px calc(24px + env(safe-area-inset-bottom));
    min-height: 100%;
    overflow-y: auto;
  }
  :global(#app) {
    overflow-y: auto;
  }
  .center {
    margin: 12vh auto 0;
    text-align: center;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .card {
    background: var(--bg-1);
    border-radius: 18px;
    padding: 24px 20px;
  }
  .big {
    font-size: 44px;
    color: var(--accent);
  }
  h1 {
    font-size: 22px;
    margin: 0;
    overflow-wrap: anywhere;
  }
  h2 {
    font-size: 14px;
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
  .fine {
    font-size: 12px;
  }
  .err {
    color: var(--danger);
    margin: 0;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 6px;
    text-align: left;
  }
  input {
    background: var(--bg-3);
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 12px;
    color: var(--fg);
    font-size: 16px;
    width: 100%;
  }
  .btn {
    background: var(--accent);
    color: #000;
    font-weight: 600;
    border-radius: 999px;
    padding: 12px 18px;
    font-size: 16px;
  }
  .btn.ghost {
    background: transparent;
    color: var(--fg);
    border: 1px solid var(--line);
  }
  .top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
  }
  .title {
    font-weight: 700;
    font-size: 18px;
    overflow-wrap: anywhere;
  }
  .icon {
    padding: 8px;
    border-radius: 999px;
    color: var(--fg-2);
    display: grid;
    place-items: center;
  }
  .now {
    margin-top: 14px;
    background: var(--bg-1);
    border-radius: 16px;
    padding: 14px;
    display: flex;
    gap: 14px;
    align-items: flex-start;
  }
  .art {
    width: 112px;
    height: 112px;
    border-radius: 10px;
    object-fit: cover;
    background: var(--bg-3);
    flex: none;
  }
  .np-meta {
    flex: 1;
    min-width: 0;
  }
  .np-title {
    font-weight: 700;
    font-size: 17px;
    overflow-wrap: anywhere;
  }
  .bar {
    height: 4px;
    background: var(--bg-3);
    border-radius: 2px;
    margin-top: 10px;
    overflow: hidden;
  }
  .bar div {
    height: 100%;
    background: var(--accent);
    transition: width 0.5s linear;
  }
  .times {
    display: flex;
    justify-content: space-between;
    margin-top: 4px;
  }
  .votes {
    display: flex;
    gap: 8px;
    margin-top: 8px;
    flex-wrap: wrap;
  }
  .chip {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 6px 12px;
    font-size: 14px;
    white-space: nowrap;
  }
  .chip.sm {
    padding: 5px 10px;
    font-size: 13px;
    gap: 4px;
  }
  .chip.on {
    background: var(--accent-dim);
    border-color: var(--accent-dim);
    color: var(--fg);
  }
  .reactions {
    display: flex;
    justify-content: space-between;
    margin: 12px 0 4px;
    gap: 4px;
  }
  .reactions button {
    font-size: 24px;
    padding: 6px;
    border-radius: 12px;
    flex: 1;
  }
  .reactions button:active {
    background: var(--bg-2);
  }
  .tabs {
    display: flex;
    gap: 8px;
    margin-top: 10px;
    border-bottom: 1px solid var(--line);
  }
  .tabs button {
    flex: 1;
    display: inline-flex;
    gap: 6px;
    justify-content: center;
    align-items: center;
    padding: 10px;
    color: var(--fg-3);
    border-bottom: 2px solid transparent;
  }
  .tabs button.sel {
    color: var(--fg);
    border-color: var(--accent);
  }
  .list {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .list li {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 0;
    border-bottom: 1px solid var(--bg-2);
  }
  .list li.mine .t {
    color: var(--accent);
  }
  .list li.empty {
    justify-content: center;
    padding: 20px 0;
    border: 0;
  }
  .thumb {
    width: 44px;
    height: 44px;
    border-radius: 6px;
    object-fit: cover;
    background: var(--bg-3);
    flex: none;
  }
  .meta {
    flex: 1;
    min-width: 0;
  }
  .t {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .meta .small {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .search {
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--bg-3);
    border-radius: 10px;
    padding: 0 10px;
    margin-top: 12px;
    color: var(--fg-3);
  }
  .search input {
    border: 0;
    background: transparent;
    padding: 12px 0;
  }
  .search input:focus {
    outline: none;
  }
  .floats {
    position: fixed;
    inset: auto 0 0 0;
    height: 60vh;
    pointer-events: none;
    overflow: hidden;
  }
  .float {
    position: absolute;
    bottom: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    animation: rise 2.6s ease-out forwards;
  }
  .float span {
    font-size: 34px;
  }
  .float small {
    font-size: 11px;
    color: var(--fg-2);
    max-width: 90px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  @keyframes rise {
    from {
      transform: translateY(0);
      opacity: 1;
    }
    to {
      transform: translateY(-55vh);
      opacity: 0;
    }
  }
  .toasts {
    position: fixed;
    left: 50%;
    transform: translateX(-50%);
    bottom: calc(16px + env(safe-area-inset-bottom));
    display: flex;
    flex-direction: column;
    gap: 6px;
    width: min(92vw, 420px);
  }
  .toast {
    background: var(--bg-3);
    border-radius: 10px;
    padding: 10px 14px;
    text-align: center;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
  }
</style>
