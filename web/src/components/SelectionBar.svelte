<script lang="ts">
  import { ListEnd, ListPlus, ListStart, Play, X } from '@lucide/svelte';
  import { player } from '../lib/player.svelte';
  import { selection } from '../lib/selection.svelte';
  import { ui } from '../lib/ui.svelte';

  const n = $derived(selection.tracks.length);

  /** Hand the picked songs to `f` and end the selection. */
  function act(f: (tracks: typeof selection.tracks) => unknown) {
    const t = selection.tracks.slice();
    selection.clear();
    void f(t);
  }

  // Leaving the page or pressing Escape drops the selection.
  let route = ui.route;
  $effect(() => {
    if (ui.route !== route) {
      route = ui.route;
      selection.clear();
    }
  });
</script>

<svelte:window onkeydown={(e) => { if (e.key === 'Escape' && selection.active && !ui.dialog) selection.clear(); }} />

{#if selection.active}
  <div class="selbar" role="toolbar" aria-label="{n} selected">
    <button class="icon-btn" aria-label="Clear selection" onclick={() => selection.clear()}><X size={20} /></button>
    <b class="count">{n} selected</b>
    <button class="act" onclick={() => act((t) => player.playList(t, 0, `${t.length} songs`))}><Play size={18} /><span>Play</span></button>
    <button class="act" onclick={() => act(async (t) => { await player.addToQueue(t, 'next'); ui.toast(`${t.length} songs will play next`); })}><ListStart size={18} /><span>Play next</span></button>
    <button class="act" onclick={() => act((t) => ui.open({ kind: 'addToQueue', tracks: t }))}><ListEnd size={18} /><span>Add to queue</span></button>
    <button class="act" onclick={() => act((t) => ui.open({ kind: 'addToPlaylist', tracks: t }))}><ListPlus size={18} /><span>Add to playlist</span></button>
  </div>
{/if}

<style>
  .selbar {
    position: fixed;
    top: calc(env(safe-area-inset-top, 0px) + 10px);
    left: 50%;
    transform: translateX(-50%);
    z-index: 90;
    display: flex;
    align-items: center;
    gap: 4px;
    max-width: calc(100vw - 24px);
    padding: 6px 10px 6px 6px;
    border-radius: 999px;
    background: var(--bg-2);
    border: 1.5px solid var(--accent);
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.5);
    animation: drop 0.15s ease-out;
  }
  .count {
    padding: 0 8px 0 2px;
    white-space: nowrap;
  }
  .act {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 12px;
    border-radius: 999px;
    white-space: nowrap;
  }
  .act:hover {
    background: rgba(255, 255, 255, 0.08);
  }
  @media (max-width: 640px) {
    .act span {
      display: none;
    }
  }
  @keyframes drop {
    from {
      opacity: 0;
      transform: translate(-50%, -8px);
    }
  }
</style>
