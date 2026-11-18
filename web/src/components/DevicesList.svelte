<script lang="ts">
  import { Laptop, Monitor, Smartphone, Speaker } from '@lucide/svelte';
  import { player } from '../lib/player.svelte';

  let { onpick }: { onpick?: () => void } = $props();
  const kindIcon = (k: string) => (k === 'android' || k === 'watch' ? Smartphone : k === 'desktop' ? Laptop : k === 'web' ? Monitor : Speaker);
  const list = $derived(player.devices.filter((x) => x.online || x.isActive));
</script>

{#if player.connectStatus !== 'open'}
  <p class="muted small">{player.connectStatus === 'reconnecting' ? 'Connecting to the server…' : 'Not connected'}</p>
{/if}
{#each list as dev (dev.id)}
  {@const Icon = kindIcon(dev.kind)}
  {@const active = player.remoteState?.activeDeviceId === dev.id}
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
      <div class="sub">{active ? (player.remoteState?.playing ? 'Playing' : 'Active') : dev.canPlay ? 'Tap to play here' : 'Remote only'}</div>
    </div>
    {#if active && player.remoteState?.playing}<span class="eq"><i></i><i></i><i></i></span>{/if}
  </button>
{:else}
  {#if player.connectStatus === 'open'}<p class="muted small">No other devices online.</p>{/if}
{/each}

<style>
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
