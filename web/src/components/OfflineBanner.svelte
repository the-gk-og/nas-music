<script lang="ts">
  import { CloudOff } from '@lucide/svelte';
  import { pwa } from '../lib/pwa.svelte';
</script>

{#if pwa.reachable === false}
  <div class="offline" role="status">
    <CloudOff size={18} />
    <span class="grow">Can't reach your server. Music you play needs it, so this retries on its own.</span>
    <button onclick={() => pwa.check()} disabled={pwa.checking}>{pwa.checking ? 'Checking…' : 'Retry now'}</button>
  </div>
{/if}

<style>
  .offline {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 16px;
    padding-top: max(8px, env(safe-area-inset-top));
    background: #3a2a00;
    color: #ffe7a3;
    font-size: 0.9rem;
    z-index: 50;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  button {
    flex: none;
    padding: 4px 12px;
    border-radius: 999px;
    border: 1px solid currentColor;
    font-size: 0.85rem;
  }
  button:disabled {
    opacity: 0.6;
  }
</style>
