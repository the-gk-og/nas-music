<script lang="ts">
  import type { Snippet } from 'svelte';
  import { ui } from '../lib/ui.svelte';

  let { children, wide = false, label = 'Dialog' }: { children: Snippet; wide?: boolean; label?: string } = $props();
</script>

<div
  class="backdrop"
  role="presentation"
  onclick={(e) => e.target === e.currentTarget && ui.close()}
  onkeydown={(e) => e.key === 'Escape' && ui.close()}
>
  <div class="sheet" class:wide role="dialog" aria-modal="true" aria-label={label}>
    {@render children()}
  </div>
</div>

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 50;
    background: rgba(0, 0, 0, 0.6);
    display: grid;
    place-items: center;
    padding: 16px;
    animation: fade 0.12s ease-out;
  }
  .sheet {
    background: var(--bg-2);
    border-radius: 18px;
    width: min(440px, 100%);
    max-height: calc(100dvh - 32px);
    overflow-y: auto;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.6);
  }
  .sheet.wide {
    width: min(640px, 100%);
  }
  @keyframes fade {
    from {
      opacity: 0;
    }
  }
</style>
