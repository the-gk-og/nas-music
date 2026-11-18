<script lang="ts">
  // For You (§3.2): Your mixes, Radio, Because you played…, Release Radar, Suggestions, To get, Blends, Wrapped.
  import { ChevronRight } from '@lucide/svelte';
  import Card from '../components/Card.svelte';
  import CatalogRow from '../components/CatalogRow.svelte';
  import Shelf from '../components/Shelf.svelte';
  import SuggestionCard from '../components/SuggestionCard.svelte';
  import TopBar from '../components/TopBar.svelte';
  import { appearance } from '../lib/appearance.svelte';
  import { session } from '../lib/session.svelte';
  import type { Shelf as ShelfT, ShelfItem, Suggestion, ToGetItem } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let shelves = $state<ShelfT[] | null>(null);
  let suggestions = $state<Suggestion[]>([]);
  let toget = $state<ToGetItem[]>([]);

  /** Old servers have no /foryou: build Your mixes from /mixes/today. */
  async function fallback(): Promise<ShelfT[]> {
    const mixes = await session.api.mixesToday().catch(() => []);
    return [
      {
        id: 'mixes',
        title: 'Your mixes',
        items: mixes.map((m) => ({ type: 'mix', id: m.id, title: m.title, subtitle: `${m.tracks.length} songs`, artIds: m.tracks.slice(0, 4).map((x) => x.track.albumId) })),
      },
    ];
  }

  $effect(() => {
    session.api
      .forYou()
      .then((f) => (shelves = f.shelves))
      .catch(async () => (shelves = await fallback()));
    session.api.suggestions().then((s) => (suggestions = s)).catch(() => {});
    session.api.toGet().then((t) => (toget = t)).catch(() => {});
  });

  // Suggestions + To get come from their own endpoints (richer than shelf items).
  const feed = $derived((shelves ?? []).filter((s) => s.items.length && s.id !== 'suggestions' && s.id !== 'toget'));
  const want = $derived(toget.filter((t) => t.status === 'want'));
  const toItem = (t: ToGetItem): ShelfItem => ({
    type: 'catalog',
    id: t.id,
    title: t.title,
    subtitle: t.artist,
    catalog: { id: t.catalogId ?? t.id, type: t.type, title: t.title, artist: t.artist, year: t.year, hasCover: t.hasCover, hasPreview: !!t.catalogId, owned: false, inToGet: true },
  });
</script>

<div class="page foryou">
  {#if appearance.form === 'desktop'}<TopBar />{/if}
  <header class="hero">
    <h1>For You</h1>
    <p class="muted">Mixes, radio and discoveries picked from your listening.</p>
  </header>
  {#if !shelves}<div class="empty">Loading…</div>{/if}

  {#each feed as s (s.id)}
    <Shelf title={s.title} subtitle={s.kind === 'because' ? 'Because you played' : ''}>
      {#each s.items as it (it.type + it.id)}<Card item={it} siblings={s.items} wide={s.id === 'mixes'} />{/each}
    </Shelf>
  {/each}

  {#if suggestions.length}
    <Shelf title="Suggestions" subtitle="This week">
      {#each suggestions as s (s.id)}
        <SuggestionCard {s} onchange={(n) => {
          suggestions = suggestions.map((x) => (x.id === n.id ? n : x));
          if (n.status === 'want') session.api.toGet().then((t) => (toget = t)).catch(() => {});
        }} />
      {/each}
    </Shelf>
  {/if}

  {#if want.length}
    <section class="toget">
      <button class="th" onclick={() => ui.go('toget')}><h2>To get</h2><ChevronRight size={22} /></button>
      {#each want.slice(0, 5) as t (t.id)}<CatalogRow item={toItem(t).catalog!} />{/each}
      {#if want.length > 5}<button class="btn text more" onclick={() => ui.go('toget')}>Show all {want.length}</button>{/if}
    </section>
  {/if}
</div>

<style>
  .foryou {
    --gutter: clamp(16px, 3vw, 32px);
  }
  .hero {
    padding: 18px var(--gutter) 10px;
    background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 16%, transparent), transparent);
  }
  h1 {
    margin: 0;
    font-size: clamp(1.8rem, 4vw, 3rem);
  }
  .hero p {
    margin: 4px 0 0;
  }
  .toget {
    margin: 8px 0 24px;
  }
  .th {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 0 var(--gutter);
  }
  .th h2 {
    margin: 0;
    font-size: 1.45rem;
  }
  .more {
    margin-left: calc(var(--gutter) - 18px);
  }
</style>
