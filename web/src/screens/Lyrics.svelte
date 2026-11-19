<script lang="ts">
  import { ChevronDown, Pencil, Type, X } from '@lucide/svelte';
  import { ApiError } from '../lib/api/client';
  import { currentLineIndex, lyricsAttribution, noLyricsMessage, SOURCE_LABEL, toLrc } from '../lib/lyrics';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { Lyrics, LyricsSource, Track } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { track, embedded = false, big = false }: { track: Track; embedded?: boolean; big?: boolean } = $props();

  let lyrics = $state<Lyrics | null>(null);
  let missing = $state(false);
  let fileName = $state('');
  let loading = $state(true);
  let srcOpen = $state(false);
  let box: HTMLDivElement | undefined = $state();
  let userScrolledAt = 0;

  let source = $state<LyricsSource | undefined>(undefined);
  // The server lists the sources it has (v2+); otherwise offer the file sources and your edit.
  const sourceOptions = $derived<LyricsSource[]>(
    lyrics?.sources?.length ? [...new Set<LyricsSource>([...lyrics.sources, 'user'])] : ['embedded', 'lrc_file', 'user'],
  );
  const attribution = $derived(lyricsAttribution(lyrics?.source));

  async function load(id: string) {
    loading = true;
    missing = false;
    lyrics = null;
    try {
      lyrics = await session.api.lyrics(id, source);
    } catch (e) {
      missing = true;
      if (!(e instanceof ApiError) || e.status !== 404) ui.toast("Couldn't load lyrics");
    }
    if (missing && !fileName) {
      try {
        fileName = (await session.api.trackInfo(id)).fileName;
      } catch {
        fileName = `${track.title}.flac`;
      }
    }
    loading = false;
  }

  $effect(() => {
    fileName = '';
    void load(track.id);
  });

  const posMs = $derived(player.shown.position * 1000);
  const active = $derived(lyrics?.synced ? currentLineIndex(lyrics.lines, posMs) : -1);

  // Auto-scroll the active line to the middle, unless the user scrolled in the last 4 s.
  $effect(() => {
    const i = active;
    if (i < 0 || !box || Date.now() - userScrolledAt < 4000) return;
    const el = box.querySelector<HTMLElement>(`[data-line="${i}"]`);
    if (el) box.scrollTo({ top: el.offsetTop - box.clientHeight / 2 + el.clientHeight / 2, behavior: 'smooth' });
  });

  function edit() {
    const raw = lyrics ? (lyrics.synced ? toLrc(lyrics.lines) : lyrics.text) : '';
    ui.open({ kind: 'lyricsEditor', track, raw });
  }

  function pick(k: LyricsSource) {
    srcOpen = false;
    source = k;
    void load(track.id);
  }

  async function revert() {
    srcOpen = false;
    try {
      await session.api.deleteLyrics(track.id);
      source = undefined;
      await load(track.id);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  // Reload when the editor dialog closes (it saves via the API).
  let wasEditing = false;
  $effect(() => {
    const editing = ui.dialog?.kind === 'lyricsEditor';
    if (wasEditing && !editing) void load(track.id);
    wasEditing = editing;
  });
</script>

<div class="lyrics" class:embedded class:big style:--size={ui.lyricsSize * (big ? 1.45 : 1)}>
  <div class="head">
    <div class="src">
      <button class="srcbtn" onclick={() => (srcOpen = !srcOpen)}>
        {SOURCE_LABEL[lyrics?.source ?? source ?? 'embedded']}<ChevronDown size={16} />
      </button>
      {#if srcOpen}
        <div class="menu">
          {#each sourceOptions as k}
            <button class="item" class:sel={(lyrics?.source ?? source ?? 'embedded') === k} onclick={() => pick(k)}>{SOURCE_LABEL[k]}</button>
          {/each}
          {#if lyrics?.source === 'user'}
            <button class="item" onclick={revert}>Delete your edit</button>
          {/if}
        </div>
      {/if}
    </div>
    <button class="icon-btn" aria-label="Edit lyrics" onclick={edit}><Pencil size={20} /></button>
    <button class="icon-btn" aria-label="Text size" onclick={() => (ui.lyricsSize = ui.lyricsSize >= 1.5 ? 0.8 : +(ui.lyricsSize + 0.175).toFixed(3))}><Type size={20} /></button>
    {#if !embedded}<button class="icon-btn" aria-label="Close lyrics" onclick={() => (ui.lyricsOpen = false)}><X size={22} /></button>{/if}
  </div>

  <div
    class="body"
    bind:this={box}
    onwheel={() => (userScrolledAt = Date.now())}
    ontouchmove={() => (userScrolledAt = Date.now())}
    role="presentation"
    ondblclick={() => (ui.lyricsOpen = false)}
  >
    {#if loading}
      <p class="muted">…</p>
    {:else if missing || !lyrics || !lyrics.lines.length}
      <p class="none">{noLyricsMessage(fileName || track.title)}</p>
      <button class="btn" onclick={edit}><Pencil size={18} /> Add lyrics</button>
    {:else if lyrics.synced}
      <div class="pad"></div>
      {#each lyrics.lines as l, i}
        <button
          class="line synced"
          class:active={i === active}
          class:past={i < active}
          data-line={i}
          onclick={() => {
            userScrolledAt = 0;
            player.seek((l.timeMs ?? 0) / 1000);
          }}
        >{l.text || '♪'}</button>
      {/each}
      {#if attribution}<p class="attr">{attribution}</p>{/if}
      <div class="pad"></div>
    {:else}
      {#each lyrics.lines as l}
        <p class="line">{l.text || ' '}</p>
      {/each}
      {#if attribution}<p class="attr">{attribution}</p>{/if}
    {/if}
  </div>
</div>

<style>
  .lyrics {
    width: 100%;
    height: 100%;
    max-height: 62vh;
    display: flex;
    flex-direction: column;
    align-self: stretch;
  }
  .head {
    display: flex;
    align-items: center;
    gap: 2px;
  }
  .src {
    flex: 1;
    position: relative;
  }
  .srcbtn {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--fg-2);
    letter-spacing: 0.06em;
    font-size: 0.95rem;
  }
  .menu {
    position: absolute;
    top: 30px;
    left: 0;
    z-index: 5;
    background: var(--bg-2);
    border-radius: 10px;
    padding: 6px 0;
    min-width: 220px;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.6);
  }
  .item {
    display: block;
    width: 100%;
    text-align: left;
    padding: 10px 14px;
  }
  .item.sel {
    color: var(--accent);
  }
  .body {
    flex: 1;
    overflow-y: auto;
    padding: 6px 4px;
    scroll-behavior: smooth;
  }
  .none {
    font-size: calc(1.6rem * var(--size));
    color: var(--fg-2);
    line-height: 1.3;
    margin: 4px 0 18px;
  }
  .line {
    display: block;
    width: 100%;
    text-align: left;
    font-size: calc(1.35rem * var(--size));
    line-height: 1.35;
    margin: 0 0 10px;
    color: var(--fg-2);
  }
  .synced {
    color: rgba(255, 255, 255, 0.45);
    transition: color 0.2s;
  }
  .synced.past {
    color: rgba(255, 255, 255, 0.6);
  }
  .synced.active {
    color: #fff;
    font-weight: 700;
  }
  .synced:hover {
    color: #fff;
  }
  .pad {
    height: 30%;
  }
  .attr {
    color: var(--fg-3);
    font-size: 0.85rem;
    margin: 18px 0 8px;
  }
  .embedded {
    max-height: none;
  }
  /* Expressive motion: the active line grows in. */
  :global([data-motion='expressive']) .synced {
    transition:
      color 0.3s,
      transform 0.35s;
    transform-origin: left center;
  }
  :global([data-motion='expressive']) .synced.active {
    transform: scale(1.06);
  }
</style>
