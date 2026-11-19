<script lang="ts">
  // Phone/tablet mini player (§2): swipe left/right to skip, tap or swipe up for Now Playing.
  import { Pause, Play, SkipForward, Speaker } from '@lucide/svelte';
  import { withTransition } from '../lib/appearance.svelte';
  import { gesture } from '../lib/gesture';
  import { player } from '../lib/player.svelte';
  import { ui } from '../lib/ui.svelte';
  import Cover from './Cover.svelte';

  const s = $derived(player.shown);
  const pct = $derived(s.duration ? (s.position / s.duration) * 100 : 0);
  const openNow = () => withTransition(() => ui.go('now'));
</script>

{#if s.track}
  <div class="mini">
    <div class="progress" style:width="{pct}%"></div>
    <div
      class="info"
      role="button"
      tabindex="0"
      aria-label="Open Now Playing"
      onkeydown={(e) => e.key === 'Enter' && openNow()}
      use:gesture={{
        follow: true,
        ontap: openNow,
        onswipe: (d) => (d === 'left' ? player.next() : d === 'right' ? player.prev() : d === 'up' ? openNow() : undefined),
      }}
    >
      <div class="thumb" style:view-transition-name="np-cover"><Cover albumId={s.track.albumId} size={128} /></div>
      <div class="text">
        <div class="ellipsis title">{s.track.title}</div>
        {#if player.remoteDevice}
          <div class="ellipsis remote"><Speaker size={13} /> {player.remoteLabel}</div>
        {:else}
          <div class="ellipsis muted">{s.track.artist}</div>
        {/if}
      </div>
    </div>
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
    margin: 0 8px 6px;
    padding: 0 6px 0 8px;
    background: var(--bg-2);
    border-radius: 10px;
    overflow: hidden;
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
    cursor: pointer;
    transition: transform 0.15s;
  }
  .thumb {
    width: 46px;
    flex: none;
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
