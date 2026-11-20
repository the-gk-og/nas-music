<script lang="ts">
  import { ArrowUpDown, ChevronDown, Ellipsis, PartyPopper, Play, Save, Timer, X } from '@lucide/svelte';
  import TrackRow from '../components/TrackRow.svelte';
  import { fmtTime } from '../lib/format';
  import { player } from '../lib/player.svelte';
  import { queueLabel, totalDurationS, type SortKey } from '../lib/queue';
  import { ui } from '../lib/ui.svelte';

  let { compact = false }: { compact?: boolean } = $props();
  const q = $derived(player.viewQueue);
  const isPlaying = $derived(!!q && q.id === player.queue?.id);
  let pickerOpen = $state(false);
  let sortOpen = $state(false);
  let moreOpen = $state(false);

  $effect(() => {
    if (!player.viewQueue && player.queues.length) void player.showQueue(player.queues[0].id);
  });

  // ---- drag reorder (pointer events, works with mouse and touch) ----
  let listEl: HTMLDivElement | undefined = $state();
  let dragFrom = $state<number | null>(null);
  let dragOver = $state<number | null>(null);
  let dragY = $state(0);

  function startDrag(i: number, ev: PointerEvent) {
    ev.preventDefault();
    dragFrom = dragOver = i;
    dragY = ev.clientY;
    const move = (e: PointerEvent) => {
      dragY = e.clientY;
      const rows = [...(listEl?.querySelectorAll<HTMLElement>('[data-row]') ?? [])];
      let over = rows.length;
      for (let k = 0; k < rows.length; k++) {
        const r = rows[k].getBoundingClientRect();
        if (e.clientY < r.top + r.height / 2) {
          over = k;
          break;
        }
      }
      dragOver = over;
      autoscroll(e.clientY);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
      const from = dragFrom;
      let to = dragOver;
      dragFrom = dragOver = null;
      if (q && from != null && to != null) {
        if (to > from) to--; // dropping "before row k" after removal
        if (to !== from) void player.moveInQueue(q.id, from, to);
      }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
  }

  function autoscroll(y: number) {
    const sc = listEl?.closest('.page');
    if (!sc) return;
    const r = sc.getBoundingClientRect();
    if (y < r.top + 60) sc.scrollTop -= 12;
    else if (y > r.bottom - 60) sc.scrollTop += 12;
  }

  async function save() {
    if (!q) return;
    const name = await ui.prompt('Save as playlist', 'Playlist name', q.name.replace(/^[^:]+:\s*/, ''), 'Save');
    if (name) await player.saveQueueAsPlaylist(q.id, name);
  }
  async function rename() {
    moreOpen = false;
    if (!q) return;
    const name = await ui.prompt('Rename queue', 'Name', q.name, 'Rename');
    if (name) await player.renameQueue(q.id, name);
  }
  async function remove() {
    moreOpen = false;
    if (q && (await ui.confirm('Delete queue', `Delete "${queueLabel(q)}"?`, 'Delete'))) await player.deleteQueue(q.id);
  }
  async function clear() {
    moreOpen = false;
    if (q && (await ui.confirm('Clear queue', 'Remove every song from this queue?', 'Clear'))) await player.clearQueue(q.id);
  }
  function sort(k: SortKey) {
    sortOpen = false;
    if (q) void player.sortQueue(q.id, k);
  }

  const position = $derived(q && q.tracks.length ? `${q.index + 1} / ${q.tracks.length}` : '0 / 0');
</script>

<div class="page" class:compact>
  <div class="head">
    <div class="sel-row">
      <button class="selector" onclick={() => (pickerOpen = !pickerOpen)} aria-expanded={pickerOpen}>
        <span class="ellipsis">{q ? queueLabel(q) : 'No queue'}</span><ChevronDown size={22} />
      </button>
      {#if !compact}<button class="icon-btn" aria-label="Open Now Playing" onclick={() => ui.go('now')}><X size={26} /></button>{/if}
    </div>
    {#if pickerOpen}
      <div class="picker" role="listbox">
        {#each player.queues as x (x.id)}
          <button
            role="option"
            aria-selected={x.id === q?.id}
            class:sel={x.id === q?.id}
            onclick={() => {
              pickerOpen = false;
              void player.showQueue(x.id);
            }}
          >
            <span class="ellipsis">{queueLabel(x)}</span>
            <span class="muted">{x.trackCount ?? ''}{x.id === player.queue?.id ? ' · playing' : ''}</span>
          </button>
        {:else}
          <div class="empty">No queues yet. Tap a song anywhere to start one.</div>
        {/each}
      </div>
    {/if}
    <div class="tools">
      <button class="icon-btn" aria-label="Play this queue" disabled={!q?.tracks.length} onclick={() => q && player.playQueue(q.id)}><Play size={26} fill="currentColor" /></button>
      <div class="rel">
        <button class="icon-btn" aria-label="Sort" onclick={() => (sortOpen = !sortOpen)}><ArrowUpDown size={24} /></button>
        {#if sortOpen}
          <div class="menu">
            <button onclick={() => sort('title')}>By title</button>
            <button onclick={() => sort('artist')}>By artist</button>
            <button onclick={() => sort('album')}>By album</button>
            <button onclick={() => sort('duration')}>By duration</button>
            <button onclick={() => sort('reverse')}>Reverse</button>
          </div>
        {/if}
      </div>
      <div class="count">
        <div><b>{position.split(' / ')[0]}</b> / {position.split(' / ')[1]}</div>
        <div class="time"><Timer size={15} /> {fmtTime(totalDurationS(q?.tracks ?? []))}</div>
      </div>
      <button class="icon-btn" aria-label="Jam: listen together" title="Jam: let friends add songs" onclick={() => ui.open({ kind: 'jam' })}><PartyPopper size={24} /></button>
      <button class="icon-btn" aria-label="Save as playlist" disabled={!q?.tracks.length} onclick={save}><Save size={26} /></button>
      <div class="rel">
        <button class="icon-btn" aria-label="Queue options" onclick={() => (moreOpen = !moreOpen)}><Ellipsis size={26} /></button>
        {#if moreOpen}
          <div class="menu right">
            <button onclick={clear}>Clear</button>
            <button onclick={rename}>Rename</button>
            <button onclick={remove}>Delete queue</button>
          </div>
        {/if}
      </div>
    </div>
  </div>

  <div bind:this={listEl} role="list" class:dragging={dragFrom != null}>
    {#if q}
      {#each q.tracks as t, i (t.id + ':' + i)}
        <div
          data-row
          class="slot"
          class:lifted={dragFrom === i}
          class:drop-before={dragOver === i && dragFrom != null && dragFrom !== i && dragFrom !== i - 1}
          class:drop-after={dragOver === q.tracks.length && i === q.tracks.length - 1 && dragFrom != null && dragFrom !== i}
          style:transform={dragFrom === i && listEl ? `translateY(${dragY - (listEl.querySelectorAll('[data-row]')[i]?.getBoundingClientRect().top ?? dragY) - 36}px)` : undefined}
        >
          <TrackRow
            track={t}
            handle
            current={isPlaying && i === q.index}
            onplay={() => player.playQueue(q.id, i)}
            onmenu={() => ui.open({ kind: 'songMenu', ctx: { track: t, queueId: q.id, position: i, list: q.tracks } })}
            onhandledown={(e) => startDrag(i, e)}
          />
        </div>
      {:else}
        <div class="empty">This queue is empty.</div>
      {/each}
    {:else}
      <div class="empty">Nothing queued. Tap a song in Albums, Playlists or Search to start a queue.</div>
    {/if}
  </div>
</div>

<style>
  .head {
    position: sticky;
    top: 0;
    z-index: 3;
    background: var(--bg);
    border-bottom: 1px solid var(--line);
    padding: 10px 8px 4px;
  }
  .sel-row {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .selector {
    flex: 1;
    min-width: 0;
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 8px;
    border: 1.5px solid #444;
    border-radius: 12px;
    padding: 12px 14px;
    font-size: 1.25rem;
    font-weight: 700;
    text-align: left;
  }
  .picker {
    margin: 6px 0;
    background: var(--bg-2);
    border-radius: 12px;
    max-height: 50vh;
    overflow-y: auto;
  }
  .picker button {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    width: 100%;
    padding: 12px 16px;
    text-align: left;
  }
  .picker button.sel {
    color: var(--accent);
  }
  .picker button:hover {
    background: rgba(255, 255, 255, 0.05);
  }
  .tools {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 4px 0;
  }
  .count {
    text-align: center;
    font-size: 1.05rem;
  }
  .time {
    display: flex;
    align-items: center;
    gap: 4px;
    justify-content: center;
  }
  .rel {
    position: relative;
  }
  .menu {
    position: absolute;
    top: 46px;
    left: 0;
    z-index: 10;
    background: var(--bg-2);
    border-radius: 10px;
    min-width: 170px;
    padding: 6px 0;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.6);
  }
  .menu.right {
    left: auto;
    right: 0;
  }
  .menu button {
    display: block;
    width: 100%;
    padding: 10px 16px;
    text-align: left;
  }
  .menu button:hover {
    background: rgba(255, 255, 255, 0.06);
  }
  .slot {
    position: relative;
  }
  .slot.lifted {
    z-index: 5;
    opacity: 0.9;
    pointer-events: none;
    box-shadow: 0 8px 30px rgba(0, 0, 0, 0.7);
  }
  .slot.drop-before::before,
  .slot.drop-after::after {
    content: '';
    position: absolute;
    left: 12px;
    right: 12px;
    height: 3px;
    background: var(--accent);
    border-radius: 2px;
  }
  .slot.drop-before::before {
    top: -2px;
  }
  .slot.drop-after::after {
    bottom: -2px;
  }
  .dragging {
    user-select: none;
  }
  .compact .selector {
    font-size: 1rem;
    padding: 8px 12px;
  }
  .compact :global(.thumb) {
    width: 48px;
  }
  .compact :global(.title) {
    font-size: 0.98rem;
  }
</style>
