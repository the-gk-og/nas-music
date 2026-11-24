<script lang="ts">
  import { Ellipsis, Equal } from '@lucide/svelte';
  import { appearance } from '../lib/appearance.svelte';
  import { artistLine, movementTitle } from '../lib/classical';
  import { fmtTime } from '../lib/format';
  import type { Track } from '../lib/types';
  import Cover from './Cover.svelte';
  import ExplicitBadge from './ExplicitBadge.svelte';

  let {
    track,
    current = false,
    handle = false,
    showAlbum = true,
    movement = false,
    onplay,
    onmenu,
    onhandledown,
  }: {
    track: Track;
    current?: boolean;
    handle?: boolean;
    showAlbum?: boolean;
    /** v9: under a work heading, show the movement name instead of the full title. */
    movement?: boolean;
    onplay: () => void;
    onmenu: (ev: MouseEvent) => void;
    onhandledown?: (ev: PointerEvent) => void;
  } = $props();

  let pressTimer: ReturnType<typeof setTimeout> | null = null;
  let longPressed = false;

  function down(ev: PointerEvent) {
    if (ev.pointerType !== 'touch') return;
    longPressed = false;
    pressTimer = setTimeout(() => {
      longPressed = true;
      onmenu(ev as unknown as MouseEvent);
    }, 550);
  }
  function up() {
    if (pressTimer) clearTimeout(pressTimer);
    pressTimer = null;
  }
  function click() {
    if (longPressed) {
      longPressed = false;
      return;
    }
    onplay();
  }
</script>

<div class="row" class:current class:hidden={track.familyHidden} role="listitem" title={track.familyHidden ? 'Hidden by family mode — skipped' : undefined}>
  {#if handle}
    <button class="handle" aria-label="Drag to reorder" onpointerdown={onhandledown}><Equal size={24} /></button>
  {/if}
  <button
    class="main"
    onclick={click}
    onpointerdown={down}
    onpointerup={up}
    onpointercancel={up}
    onpointerleave={up}
    oncontextmenu={(e) => {
      e.preventDefault();
      onmenu(e);
    }}
  >
    <div class="thumb"><Cover albumId={track.albumId} size={128} /></div>
    <div class="text">
      <div class="title ellipsis"><ExplicitBadge status={track.explicit} />{movement ? movementTitle(track) : track.title}</div>
      <div class="artist ellipsis">{artistLine(track, appearance.prefs.classical)}</div>
      {#if showAlbum}
        <div class="album"><span class="ellipsis">{track.album}</span><span class="dur">{fmtTime(track.durationS)}</span></div>
      {:else}
        <div class="album"><span></span><span class="dur">{fmtTime(track.durationS)}</span></div>
      {/if}
    </div>
  </button>
  <button class="more" aria-label="Song menu" onclick={onmenu}><Ellipsis size={20} strokeWidth={3} /></button>
</div>

<style>
  .row {
    display: flex;
    align-items: center;
    gap: 4px;
    margin: 4px 8px;
    border: 1.5px solid transparent;
    border-radius: 12px;
    padding: 4px 6px 4px 0;
    background: var(--bg);
  }
  .row.hidden {
    opacity: 0.4;
  }
  .row.current {
    border-color: var(--accent);
    color: var(--accent);
  }
  .row:hover {
    background: rgba(255, 255, 255, 0.03);
  }
  .handle {
    width: 40px;
    height: 56px;
    display: grid;
    place-items: center;
    cursor: grab;
    touch-action: none;
    flex: none;
  }
  .main {
    flex: 1;
    min-width: 0;
    display: flex;
    gap: 14px;
    align-items: center;
    text-align: left;
    padding: 2px 0 2px 8px;
  }
  .row:has(.handle) .main {
    padding-left: 0;
  }
  .thumb {
    width: 64px;
    flex: none;
  }
  .text {
    flex: 1;
    min-width: 0;
  }
  .title {
    font-size: 1.08rem;
    font-weight: 500;
  }
  .current .title {
    font-weight: 700;
  }
  .artist,
  .album {
    font-size: 0.92rem;
    color: var(--fg-2);
  }
  .current .artist,
  .current .album {
    color: var(--accent);
  }
  .album {
    display: flex;
    justify-content: space-between;
    gap: 12px;
  }
  .dur {
    flex: none;
  }
  .more {
    width: 40px;
    height: 40px;
    display: grid;
    place-items: center;
    flex: none;
  }
  .more :global(svg) {
    background: currentColor;
    color: inherit;
    border-radius: 8px;
    padding: 3px;
    stroke: var(--bg);
    width: 30px;
    height: 30px;
  }
</style>
