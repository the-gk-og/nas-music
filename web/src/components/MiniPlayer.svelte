<script lang="ts">
  import { Pause, Play, SkipForward, Speaker } from '@lucide/svelte';
  import { player } from '../lib/player.svelte';
  import { ui } from '../lib/ui.svelte';
  import Cover from './Cover.svelte';

  const s = $derived(player.shown);
  const pct = $derived(s.duration ? (s.position / s.duration) * 100 : 0);
</script>

{#if s.track}
  <div class="mini">
    <div class="progress" style:width="{pct}%"></div>
    <button class="info" onclick={() => ui.go('now')}>
      <div class="thumb"><Cover albumId={s.track.albumId} size={128} /></div>
      <div class="text">
        <div class="ellipsis title">{s.track.title}</div>
        {#if player.remoteDevice}
          <div class="ellipsis remote"><Speaker size={13} /> Playing on {player.remoteDevice.name}</div>
        {:else}
          <div class="ellipsis muted">{s.track.artist}</div>
        {/if}
      </div>
    </button>
    <button class="icon-btn" aria-label="Devices" onclick={() => ui.open({ kind: 'devices' })} class:on={!!player.remoteDevice}><Speaker size={22} /></button>
    <button class="icon-btn" aria-label={s.playing ? 'Pause' : 'Play'} onclick={() => player.toggle()}>
      {#if s.playing}<Pause size={26} fill="currentColor" />{:else}<Play size={26} fill="currentColor" />{/if}
    </button>
    <button class="icon-btn" aria-label="Next" onclick={() => player.next()}><SkipForward size={24} fill="currentColor" /></button>
  </div>
{/if}

<style>
  .mini {
    position: relative;
    display: flex;
    align-items: center;
    gap: 4px;
    height: var(--mini-h);
    padding: 0 8px;
    background: var(--bg-2);
    border-top: 1px solid var(--line);
  }
  .progress {
    position: absolute;
    top: 0;
    left: 0;
    height: 2px;
    background: var(--accent);
  }
  .info {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 12px;
    text-align: left;
  }
  .thumb {
    width: 46px;
  }
  .text {
    min-width: 0;
  }
  .title {
    font-weight: 600;
  }
  .remote {
    color: var(--accent);
    font-size: 0.85rem;
    display: flex;
    align-items: center;
    gap: 4px;
  }
</style>
