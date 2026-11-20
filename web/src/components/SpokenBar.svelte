<script lang="ts">
  // Player bar for podcasts / audiobooks / radio (shown while one is loaded, above the music player).
  // Music starting pauses it; it pauses music when it starts (spokenPlayer.start).
  import { ChevronDown, ChevronUp, Moon, Pause, Play, Redo2, SkipBack, SkipForward, Undo2, X } from '@lucide/svelte';
  import { player } from '../lib/player.svelte';
  import { clock } from '../lib/spoken';
  import { SPEEDS, spoken } from '../lib/spokenPlayer.svelte';
  import { ui } from '../lib/ui.svelte';
  import SpokenArt from './SpokenArt.svelte';

  let expanded = $state(false);
  let wasPlaying = false;
  $effect(() => {
    const p = player.playing;
    if (p && !wasPlaying && spoken.playing) spoken.pause();
    wasPlaying = p;
  });

  const c = $derived(spoken.current);
  const chapters = $derived(c?.chapters ?? []);
  const ch = $derived(spoken.chapterIndex);
  const sleepLabel = $derived(
    spoken.sleepAt === 'chapter' ? 'end of chapter' : typeof spoken.sleepAt === 'number' ? `${Math.max(1, Math.ceil((spoken.sleepAt - spoken.sleepNow) / 60000))} min` : '',
  );

  function open() {
    if (!c) return;
    if (c.kind === 'episode' && c.showId) ui.go('podcast', c.showId);
    else if (c.kind === 'book') ui.go('book', c.id);
    else ui.go('spoken', 'radio');
  }
</script>

{#if c}
  <div class="sbar" class:expanded>
    <div class="main">
      <button class="who" onclick={open}>
        <SpokenArt kind={c.kind === 'episode' ? 'podcast' : c.kind} src={c.art} size={44} round={c.kind === 'station'} />
        <span class="t">
          <b class="ellipsis">{spoken.live && spoken.nowPlaying ? spoken.nowPlaying : c.title}</b>
          <span class="muted small ellipsis">
            {spoken.live ? (spoken.nowPlaying ? c.title : c.subtitle || 'Live radio') : ch >= 0 && chapters[ch] ? chapters[ch].title : c.subtitle}
          </span>
        </span>
      </button>
      <div class="ctl">
        {#if !spoken.live}
          {#if chapters.length > 1}<button class="icon-btn" aria-label="Previous chapter" onclick={() => spoken.chapter(-1)}><SkipBack size={18} /></button>{/if}
          <button class="icon-btn" aria-label="Back 15 seconds" onclick={() => spoken.skip(-15)}><Undo2 size={20} /></button>
        {/if}
        <button class="pp" aria-label={spoken.playing ? 'Pause' : 'Play'} onclick={() => spoken.toggle()}>
          {#if spoken.playing}<Pause size={22} />{:else}<Play size={22} />{/if}
        </button>
        {#if !spoken.live}
          <button class="icon-btn" aria-label="Forward 30 seconds" onclick={() => spoken.skip(30)}><Redo2 size={20} /></button>
          {#if chapters.length > 1}<button class="icon-btn" aria-label="Next chapter" onclick={() => spoken.chapter(1)}><SkipForward size={18} /></button>{/if}
        {/if}
        <button class="icon-btn" aria-label={expanded ? 'Less' : 'More'} onclick={() => (expanded = !expanded)}>
          {#if expanded}<ChevronDown size={18} />{:else}<ChevronUp size={18} />{/if}
        </button>
        <button class="icon-btn" aria-label="Close" onclick={() => spoken.close()}><X size={18} /></button>
      </div>
    </div>
    {#if !spoken.live}
      <div class="seek">
        <span class="small muted">{clock(spoken.position)}</span>
        <input type="range" min="0" max={c.duration ?? 0} step="1" value={spoken.position} aria-label="Position"
          onchange={(e) => spoken.seek(Number((e.target as HTMLInputElement).value))} />
        <span class="small muted">-{clock(Math.max(0, (c.duration ?? 0) - spoken.position) / spoken.speed)}</span>
      </div>
    {:else}
      <div class="seek small muted">{spoken.loading ? 'Connecting…' : spoken.playing ? 'Live' : 'Paused'}{spoken.error ? ` · ${spoken.error}` : ''}</div>
    {/if}
    {#if expanded}
      <div class="more">
        {#if !spoken.live}
          <label class="small">Speed
            <select value={spoken.speed} onchange={(e) => spoken.setSpeed(Number((e.target as HTMLSelectElement).value))}>
              {#each SPEEDS as s}<option value={s}>{s}×</option>{/each}
            </select>
          </label>
        {/if}
        <label class="small"><Moon size={14} /> Sleep
          <select value={spoken.sleepAt === null ? '' : spoken.sleepAt === 'chapter' ? 'chapter' : 'set'}
            onchange={(e) => {
              const v = (e.target as HTMLSelectElement).value;
              spoken.setSleep(v === '' ? null : v === 'chapter' ? 'chapter' : Number(v));
            }}>
            <option value="">Off</option>
            {#if sleepLabel && spoken.sleepAt !== 'chapter'}<option value="set">{sleepLabel}</option>{/if}
            {#each [5, 10, 15, 30, 45, 60, 90] as m}<option value={m}>{m} min</option>{/each}
            {#if chapters.length > 1}<option value="chapter">End of chapter</option>{/if}
          </select>
        </label>
        {#if c.kind === 'book'}<button class="lk" onclick={() => spoken.addBookmark()}>Add bookmark</button>{/if}
        {#if chapters.length > 1}
          <select class="chsel" value={ch} aria-label="Chapter" onchange={(e) => spoken.seek(chapters[Number((e.target as HTMLSelectElement).value)].start)}>
            {#each chapters as x, i}<option value={i}>{clock(x.start)} {x.title}</option>{/each}
          </select>
        {/if}
      </div>
    {/if}
  </div>
{/if}

<style>
  .sbar {
    background: #181818;
    border-top: 1px solid var(--line, #2a2a2a);
    padding: 6px 12px 4px;
  }
  .main {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .who {
    display: flex;
    align-items: center;
    gap: 10px;
    flex: 1;
    min-width: 0;
    text-align: left;
  }
  .t {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .ctl {
    display: flex;
    align-items: center;
    gap: 2px;
  }
  .pp {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    background: #fff;
    color: #000;
    display: grid;
    place-items: center;
  }
  .seek {
    display: flex;
    gap: 8px;
    align-items: center;
    padding: 2px 0;
  }
  .seek input {
    flex: 1;
    accent-color: var(--accent);
  }
  .more {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
    padding: 6px 0;
  }
  .more label {
    display: inline-flex;
    gap: 6px;
    align-items: center;
  }
  select {
    background: var(--card, #222);
    color: inherit;
    border: 1px solid #444;
    border-radius: 6px;
    padding: 2px 4px;
    max-width: 60vw;
  }
  .lk {
    color: #aaa;
    text-decoration: underline;
    font-size: 0.85rem;
  }
</style>
