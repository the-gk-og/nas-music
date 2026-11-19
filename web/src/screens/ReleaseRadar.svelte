<script lang="ts">
  // Release Radar: new releases by artists you play — "New from artists you play" (not owned: sample,
  // add to To get, buy links) and "In your library" (recently added / already got them).
  import { ArrowLeft } from '@lucide/svelte';
  import Card from '../components/Card.svelte';
  import CatalogRow from '../components/CatalogRow.svelte';
  import { session } from '../lib/session.svelte';
  import type { ReleaseRadar } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import { releaseDate } from '../lib/wrapped';

  let rr = $state<ReleaseRadar | null>(null);
  let error = $state<string | null>(null);
  $effect(() => {
    session.api
      .releaseRadar()
      .then((r) => (rr = r))
      .catch((e) => (error = e.status === 404 ? 'This server has no Release Radar yet.' : e.message));
  });
  const updated = $derived(rr?.updatedAt ? new Date(rr.updatedAt).toLocaleDateString(undefined, { day: 'numeric', month: 'long' }) : null);
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>Release Radar</h1>
  </div>
  {#if error}<div class="empty">{error}</div>
  {:else if !rr}<div class="empty">Loading…</div>
  {:else}
    <p class="muted intro">
      New albums, EPs and singles from the artists you play{updated ? ` · updated ${updated}` : ' · refreshed every Friday'}.
    </p>
    <section>
      <h2>New from artists you play</h2>
      {#each rr.notOwned as it (it.id)}
        <div class="rel">
          {#if it.catalog}<CatalogRow item={it.catalog} />{/if}
          <div class="meta muted small">
            {it.release?.type ?? 'Release'}{it.release?.date ? ` · ${releaseDate(it.release.date)}` : ''}{#if it.release?.upcoming}<span class="tag">Upcoming</span>{/if}
          </div>
        </div>
      {:else}
        <div class="empty">Nothing new right now{rr.updatedAt ? '' : ' — the first scan runs on Friday'}.</div>
      {/each}
    </section>
    {#if rr.inLibrary.length}
      <section>
        <h2>In your library</h2>
        <div class="grid">
          {#each rr.inLibrary as it (it.id)}<Card item={{ ...it, subtitle: [it.subtitle, it.release?.date ? releaseDate(it.release.date) : null].filter(Boolean).join(' · ') }} />{/each}
        </div>
      </section>
    {/if}
  {/if}
</div>

<style>
  .intro {
    padding: 0 16px;
    margin: 0 0 12px;
  }
  section {
    margin-bottom: 24px;
  }
  h2 {
    padding: 0 16px;
    font-size: 1.3rem;
  }
  .meta {
    padding: 0 16px 8px 88px;
    margin-top: -6px;
  }
  .tag {
    margin-left: 8px;
    padding: 1px 8px;
    border-radius: 99px;
    background: var(--accent);
    color: #000;
    font-weight: 600;
  }
  .grid {
    display: flex;
    flex-wrap: wrap;
    padding: 0 8px;
  }
</style>
