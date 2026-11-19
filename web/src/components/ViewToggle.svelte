<script lang="ts">
  import { LayoutGrid, List } from '@lucide/svelte';
  import { appearance } from '../lib/appearance.svelte';

  let { section }: { section: string } = $props();
  const v = $derived(appearance.view(section));
</script>

<div class="vt" role="group" aria-label="View">
  <button class:on={v === 'grid'} aria-label="Grid view" aria-pressed={v === 'grid'} onclick={() => appearance.setView(section, 'grid')}><LayoutGrid size={18} /></button>
  <button class:on={v === 'list'} aria-label="List view" aria-pressed={v === 'list'} onclick={() => appearance.setView(section, 'list')}><List size={18} /></button>
</div>

<style>
  .vt {
    display: flex;
    border: 1px solid #444;
    border-radius: 999px;
    overflow: hidden;
  }
  button {
    padding: 6px 10px;
    color: var(--fg-3);
    display: grid;
  }
  button.on {
    background: #fff;
    color: #000;
  }
</style>
