<script lang="ts">
  import { Cast, Laptop, Monitor, Smartphone, Speaker } from '@lucide/svelte';
  import type { ConnectDevice } from '../lib/types';
  import { player } from '../lib/player.svelte';

  let { onpick }: { onpick?: () => void } = $props();
  const kindIcon = (d: ConnectDevice) =>
    d.castType === 'chromecast' ? Cast : d.kind === 'android' || d.kind === 'watch' ? Smartphone : d.kind === 'desktop' ? Laptop : d.kind === 'web' ? Monitor : Speaker;
  const CAST_LABEL: Record<string, string> = { chromecast: 'Google Cast', dlna: 'DLNA', homeassistant: 'Home Assistant', snapcast: 'Snapcast' };
  const isSpeaker = (d: ConnectDevice) => d.kind === 'speaker';
  // Your own devices first, then speakers.
  const list = $derived(player.devices.filter((x) => x.online || x.isActive).sort((a, b) => Number(isSpeaker(a)) - Number(isSpeaker(b))));
</script>

{#if player.connectStatus !== 'open'}
  <p class="muted small">{player.connectStatus === 'reconnecting' ? 'Connecting to the server…' : 'Not connected'}</p>
{/if}
{#each list as dev (dev.id)}
  {@const Icon = kindIcon(dev)}
  {@const active = player.remoteState?.activeDeviceId === dev.id}
  {@const connecting = active && isSpeaker(dev) && !!player.remoteState?.connecting}
  <div class="row">
  <button
    class="dev"
    class:active
    disabled={!dev.canPlay}
    onclick={() => {
      player.transfer(dev.id);
      onpick?.();
    }}
  >
    <Icon size={26} />
    <div class="grow">
      <div class="ellipsis">{dev.name}{dev.id === player.myDeviceId ? ' (this device)' : ''}</div>
      <div class="sub">
        {connecting ? 'Connecting…' : active ? (player.remoteState?.playing ? 'Playing' : 'Active') : dev.canPlay ? (isSpeaker(dev) ? 'Tap to play on this speaker' : 'Tap to play here') : 'Remote only'}{#if isSpeaker(dev)}
          · {CAST_LABEL[dev.castType ?? ''] ?? 'Speaker'}{/if}
      </div>
    </div>
    {#if connecting}<span class="spin" aria-hidden="true"></span>{:else if active && player.remoteState?.playing}<span class="eq"><i></i><i></i><i></i></span>{/if}
  </button>
  {#if active && isSpeaker(dev)}
    <button class="stop" onclick={() => player.stopCasting()}>{connecting ? 'Cancel' : 'Stop casting'}</button>
  {/if}
  </div>
{:else}
  {#if player.connectStatus === 'open'}<p class="muted small">No other devices online.</p>{/if}
{/each}

<style>
  .row {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .stop {
    flex: none;
    padding: 6px 12px;
    margin-right: 8px;
    border-radius: 999px;
    border: 1px solid var(--fg-3);
    font-size: 0.82rem;
  }
  .stop:hover {
    background: rgba(255, 255, 255, 0.06);
  }
  .spin {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 2px solid var(--accent);
    border-right-color: transparent;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  .dev {
    display: flex;
    align-items: center;
    gap: 14px;
    width: 100%;
    padding: 12px 14px;
    border-radius: 10px;
    text-align: left;
  }
  .dev:hover:not(:disabled) {
    background: rgba(255, 255, 255, 0.06);
  }
  .dev.active {
    color: var(--accent);
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .sub {
    font-size: 0.82rem;
    color: var(--fg-3);
  }
  .small {
    font-size: 0.85rem;
    padding: 0 14px;
  }
  .eq {
    display: flex;
    gap: 2px;
    align-items: flex-end;
    height: 16px;
  }
  .eq i {
    width: 3px;
    background: var(--accent);
    animation: bar 0.9s ease-in-out infinite;
  }
  .eq i:nth-child(2) {
    animation-delay: 0.2s;
  }
  .eq i:nth-child(3) {
    animation-delay: 0.4s;
  }
  @keyframes bar {
    0%,
    100% {
      height: 4px;
    }
    50% {
      height: 16px;
    }
  }
</style>
