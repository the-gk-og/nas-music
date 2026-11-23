<script lang="ts">
  // Desktop right panel (§6): Queue / Lyrics / Devices.
  import { X } from '@lucide/svelte';
  import { appearance } from '../lib/appearance.svelte';
  import { player } from '../lib/player.svelte';
  import { ui, type PanelId } from '../lib/ui.svelte';
  import Lyrics from '../screens/Lyrics.svelte';
  import Queues from '../screens/Queues.svelte';
  import DevicesList from './DevicesList.svelte';

  const TABS: { id: PanelId; label: string }[] = [
    { id: 'queue', label: 'Queue' },
    { id: 'lyrics', label: 'Lyrics' },
    { id: 'devices', label: 'Devices' },
  ];
  const track = $derived(player.shown.track);
</script>

<aside class="panel" class:stacked={appearance.portrait} aria-label="Side panel">
  <div class="tabs">
    {#each TABS as t (t.id)}
      <button class:on={ui.panel === t.id} onclick={() => (ui.panel = t.id)}>{t.label}</button>
    {/each}
    <span class="sp"></span>
    <button class="icon-btn x" aria-label="Close panel" onclick={() => ui.togglePanel(ui.panel!)}><X size={18} /></button>
  </div>
  <div class="body">
    {#if ui.panel === 'queue'}
      <Queues compact />
    {:else if ui.panel === 'lyrics'}
      {#if track}<div class="lyr"><Lyrics {track} embedded /></div>{:else}<p class="empty">Nothing playing</p>{/if}
    {:else}
      <h3>Connect to a device</h3>
      <DevicesList />
    {/if}
  </div>
</aside>

<style>
  .panel {
    width: 360px;
    flex: none;
    display: flex;
    flex-direction: column;
    background: var(--bg-1);
    border-left: 1px solid var(--line);
    min-height: 0;
  }
  /* tall desktop window: a pane under the page, full width */
  .panel.stacked {
    width: auto;
    height: clamp(260px, 36vh, 760px);
    border-left: 0;
    border-top: 1px solid var(--line);
  }
  .tabs {
    display: flex;
    gap: 4px;
    align-items: center;
    padding: 8px 8px 4px 12px;
  }
  .tabs button:not(.x) {
    padding: 6px 12px;
    border-radius: 999px;
    color: var(--fg-2);
    font-weight: 600;
    font-size: 0.9rem;
  }
  .tabs button.on {
    background: #fff;
    color: #000;
  }
  .sp {
    flex: 1;
  }
  .x {
    width: 32px;
    height: 32px;
  }
  .body {
    flex: 1;
    min-height: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  .body > :global(.page) {
    flex: 1;
  }
  .lyr {
    flex: 1;
    min-height: 0;
    padding: 6px 14px;
    display: flex;
  }
  h3 {
    margin: 10px 14px;
    font-size: 1rem;
  }
</style>
