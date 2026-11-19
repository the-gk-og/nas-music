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
  import type { CatalogItem, Shelf as ShelfT, Suggestion, ToGetItem } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  const KNOWN = new Set(['mixes', 'radio', 'because_you_played', 'release_radar', 'suggestions', 'to_get', 'blends', 'wrapped']);
  let shelves = $state<ShelfT[] | null>(null);

  /** Older servers have no /foryou: Your mixes from /mixes/today, Suggestions, To get. */
  async function fallback(): Promise<ShelfT[]> {
    const [mixes, sugg, toget] = await Promise.all([
      session.api.mixesToday().catch(() => []),
      session.api.suggestions().catch(() => []),
      session.api.toGet('want').catch(() => []),
    ]);
    return [
      {
        id: 'mixes',
        kind: 'mixes',
        title: 'Your mixes',
        items: mixes.map((m) => ({ type: 'mix', id: m.id, title: m.title, subtitle: `${m.tracks.length} songs`, artIds: m.tracks.slice(0, 4).map((x) => x.track.albumId) })),
      },
      { id: 'suggestions', kind: 'suggestions', title: 'Suggestions', items: sugg.map((x) => ({ type: 'suggestion', id: x.id, title: x.album, subtitle: x.artist, suggestion: x })) },
      { id: 'to_get', kind: 'to_get', title: 'To get', items: toget.map((x) => ({ type: 'toget', id: x.id, title: x.title, subtitle: x.artist, artUrl: x.artUrl, toget: x })) },
    ];
  }

  const load = () =>
    session.api
      .forYou()
      .then((f) => (shelves = f.shelves))
      .catch(async () => (shelves = await fallback()));
  $effect(() => void load());

  // Unknown kinds are skipped (contract rule); phase-2 kinds render once the server sends them.
  const feed = $derived((shelves ?? []).filter((s) => KNOWN.has(s.kind) && s.items.length));
  const asCatalog = (t: ToGetItem): CatalogItem => ({
    id: t.catalogId ?? t.id,
    type: t.type,
    title: t.title,
    artist: t.artist,
    year: t.year,
    artUrl: t.artUrl,
    hasPreview: !!t.catalogId && t.hasPreview !== false,
    owned: t.status === 'got',
    ownedId: t.ownedId,
    toGet: { id: t.id, status: t.status },
  });
  function suggestionChanged(n: Suggestion) {
    shelves = (shelves ?? []).map((s) => (s.kind === 'suggestions' ? { ...s, items: s.items.map((it) => (it.id === n.id ? { ...it, suggestion: n } : it)) } : s));
    if (n.status === 'want') void load(); // lands in To get
  }
</script>

<div class="page foryou">
  {#if appearance.form === 'desktop'}<TopBar />{/if}
  <header class="hero">
    <h1>For You</h1>
    <p class="muted">Mixes, radio and discoveries picked from your listening.</p>
  </header>
  {#if !shelves}<div class="empty">Loading…</div>{/if}

  {#each feed as s (s.id)}
    {#if s.kind === 'suggestions'}
      <Shelf title={s.title} subtitle={s.subtitle ?? 'This week'}>
        {#each s.items.filter((x) => x.suggestion) as it (it.id)}<SuggestionCard s={it.suggestion!} onchange={suggestionChanged} />{/each}
      </Shelf>
    {:else if s.kind === 'to_get'}
      {@const items = s.items.filter((x) => x.toget)}
      <section class="toget">
        <button class="th" onclick={() => ui.go('toget')}><h2>{s.title}</h2><ChevronRight size={22} /></button>
        {#each items.slice(0, 5) as it (it.id)}<CatalogRow item={asCatalog(it.toget!)} />{/each}
        {#if items.length > 5}<button class="btn text more" onclick={() => ui.go('toget')}>Show all {items.length}</button>{/if}
      </section>
    {:else}
      <Shelf title={s.title} subtitle={s.subtitle ?? (s.kind === 'because_you_played' ? 'Because you played' : '')}>
        {#each s.items as it (it.type + it.id)}<Card item={it} siblings={s.items} wide={s.kind === 'mixes'} />{/each}
      </Shelf>
    {/if}
  {/each}
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
