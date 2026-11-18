<script lang="ts">
  import { ArrowLeft, Pause, Play } from '@lucide/svelte';
  import { session } from '../lib/session.svelte';
  import type { Suggestion } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let items = $state<Suggestion[] | null>(null);
  let covers = $state<Record<string, string>>({});
  let error = $state<string | null>(null);
  let playingId = $state<string | null>(null);
  const audio = typeof Audio !== 'undefined' ? new Audio() : null;
  audio?.addEventListener('ended', () => (playingId = null));

  $effect(() => {
    session.api
      .suggestions()
      .then((s) => {
        items = s;
        for (const x of s)
          if (x.hasCover) session.api.suggestionCoverSrc(x.id, 256).then((u) => (covers = { ...covers, [x.id]: u })).catch(() => {});
      })
      .catch((e) => (error = e.message));
    return () => audio?.pause();
  });

  async function preview(s: Suggestion) {
    if (!audio) return;
    if (playingId === s.id) {
      audio.pause();
      playingId = null;
      return;
    }
    try {
      audio.src = await session.api.suggestionPreviewSrc(s.id);
      await audio.play();
      playingId = s.id;
    } catch {
      ui.toast('Preview unavailable');
    }
  }

  async function set(s: Suggestion, status: Suggestion['status']) {
    try {
      await session.api.setSuggestion(s.id, status);
      items = items!.map((x) => (x.id === s.id ? { ...x, status } : x));
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>Discover</h1>
  </div>
  {#if error}<div class="empty">{error}</div>
  {:else if !items}<div class="empty">Loading…</div>
  {:else}
    {#each items as s (s.id)}
      <div class="sug" class:dim={s.status === 'not_interested'}>
        <div class="art">{#if covers[s.id]}<img src={covers[s.id]} alt="" />{/if}</div>
        <div class="body">
          <div class="t">{s.album} <span class="muted">{s.year ?? ''}</span></div>
          <div>{s.artist}</div>
          <div class="muted small">{s.reason}</div>
          <div class="btns">
            {#if s.hasPreview}<button class="btn" onclick={() => preview(s)}>{#if playingId === s.id}<Pause size={16} />{:else}<Play size={16} />{/if} Preview</button>{/if}
            <button class="btn" class:primary={s.status === 'want'} onclick={() => set(s, 'want')}>Want</button>
            <button class="btn" class:primary={s.status === 'have'} onclick={() => set(s, 'have')}>Have it</button>
            <button class="btn" class:primary={s.status === 'not_interested'} onclick={() => set(s, 'not_interested')}>Not interested</button>
            {#each s.buyLinks ?? [] as l}<a class="btn" href={l.url} target="_blank" rel="noopener noreferrer">{l.name}</a>{/each}
          </div>
        </div>
      </div>
    {:else}<div class="empty">No suggestions this week.</div>{/each}
  {/if}
</div>

<style>
  .sug {
    display: flex;
    gap: 14px;
    padding: 14px 16px;
    border-bottom: 1px solid var(--line);
  }
  .dim {
    opacity: 0.5;
  }
  .art {
    width: 96px;
    height: 96px;
    background: #1a1a1a;
    flex: none;
    border-radius: 6px;
    overflow: hidden;
  }
  .art img {
    width: 100%;
    height: 100%;
    object-fit: cover;
  }
  .body {
    display: grid;
    gap: 3px;
    min-width: 0;
  }
  .t {
    font-weight: 700;
  }
  .small {
    font-size: 0.85rem;
  }
  .btns {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 6px;
  }
  .btns .btn {
    padding: 6px 10px;
    font-size: 0.85rem;
    text-decoration: none;
    color: inherit;
  }
</style>
