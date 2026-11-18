<script lang="ts">
  // YouTube-Music-style horizontal shelf with scroll buttons on wide screens.
  import { ChevronLeft, ChevronRight } from '@lucide/svelte';
  import type { Snippet } from 'svelte';

  let { title, subtitle = '', action, children }: { title: string; subtitle?: string; action?: Snippet; children: Snippet } = $props();
  let row: HTMLDivElement | undefined = $state();
  const scroll = (d: number) => row?.scrollBy({ left: d * row.clientWidth * 0.8, behavior: 'smooth' });
</script>

<section class="shelf">
  <header>
    <div>
      {#if subtitle}<div class="sub">{subtitle}</div>{/if}
      <h2>{title}</h2>
    </div>
    <div class="right">
      {@render action?.()}
      <button class="nav" aria-label="Scroll left" onclick={() => scroll(-1)}><ChevronLeft size={20} /></button>
      <button class="nav" aria-label="Scroll right" onclick={() => scroll(1)}><ChevronRight size={20} /></button>
    </div>
  </header>
  <div class="row" bind:this={row}>{@render children()}</div>
</section>

<style>
  .shelf {
    margin: 8px 0 22px;
  }
  header {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    padding: 0 var(--gutter, 16px);
    gap: 12px;
  }
  h2 {
    margin: 0;
    font-size: 1.45rem;
    font-weight: 700;
  }
  .sub {
    color: var(--fg-3);
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }
  .right {
    display: flex;
    gap: 6px;
    align-items: center;
  }
  .nav {
    width: 34px;
    height: 34px;
    border-radius: 50%;
    border: 1px solid #444;
    display: grid;
    place-items: center;
  }
  @media (hover: none), (max-width: 599px) {
    .nav {
      display: none;
    }
  }
  .row {
    display: flex;
    gap: 4px;
    overflow-x: auto;
    padding: 8px calc(var(--gutter, 16px) - 8px);
    scroll-snap-type: x proximity;
    scrollbar-width: none;
  }
  .row::-webkit-scrollbar {
    display: none;
  }
  .row > :global(*) {
    scroll-snap-align: start;
  }
</style>
