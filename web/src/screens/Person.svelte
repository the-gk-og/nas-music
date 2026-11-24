<script lang="ts">
  // v9: one credited person (producer, songwriter, musician, ...) and every song in the library they are
  // credited on, with their role on each. Play all / shuffle; link to their artist page when they have one.
  import { ArrowLeft, ExternalLink, Play, Shuffle, User } from '@lucide/svelte';
  import TrackList from '../components/TrackList.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { CreditPerson } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { id }: { id: string } = $props();
  let p = $state<CreditPerson | null>(null);
  let error = $state<string | null>(null);

  $effect(() => {
    p = null;
    error = null;
    session.api
      .person(id)
      .then((x) => (p = x))
      .catch((e) => (error = e.status === 404 ? 'Nothing in your library credits this person.' : e.message));
  });

  /** Songs grouped by their role on them ("Producer", "Guitar, Backing vocals"), biggest group first. */
  const sections = $derived.by(() => {
    const m = new Map<string, CreditPerson['tracks']>();
    for (const t of p?.tracks ?? []) {
      const k = t.credits.join(', ');
      m.set(k, [...(m.get(k) ?? []), t]);
    }
    return [...m.entries()].sort((a, b) => b[1].length - a[1].length);
  });
  const meta = { source: 'library' as const, addedFrom: 'person' };
  const queueName = $derived(`Credits: ${p?.name ?? ''}`);

  async function play(shuffle: boolean) {
    if (!p?.tracks.length) return;
    if (shuffle && !player.shuffle) player.toggleShuffle();
    await player.playList(p.tracks, shuffle ? Math.floor(Math.random() * p.tracks.length) : 0, queueName, meta);
  }
</script>

<div class="page person">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1 class="ellipsis">{p?.name ?? 'Credits'}</h1>
  </div>
  {#if error}<div class="empty">{error}</div>
  {:else if !p}<div class="empty">Loading…</div>
  {:else}
    <div class="intro">
      <p class="muted">{p.roles.join(' · ')}</p>
      <p class="muted">Credited on {p.trackCount} song{p.trackCount === 1 ? '' : 's'} in your library</p>
      <div class="actions">
        <button class="btn primary" onclick={() => play(false)}><Play size={18} /> Play</button>
        <button class="btn" onclick={() => play(true)}><Shuffle size={18} /> Shuffle</button>
        {#if p.artistId}<button class="btn" onclick={() => ui.go('artist', p!.artistId!)}><User size={18} /> Artist page</button>{/if}
        {#if p.musicbrainzUrl}<a class="btn" href={p.musicbrainzUrl} target="_blank" rel="noopener noreferrer"><ExternalLink size={16} /> MusicBrainz</a>{/if}
      </div>
    </div>
    {#each sections as [role, tracks] (role)}
      {#if sections.length > 1}<div class="role muted small">{role}</div>{/if}
      <TrackList {tracks} {queueName} {meta} />
    {/each}
  {/if}
</div>

<style>
  .intro {
    padding: 0 16px 8px;
  }
  .intro p {
    margin: 0 0 4px;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 12px;
  }
  .actions .btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    text-decoration: none;
  }
  .role {
    padding: 14px 16px 2px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  .small {
    font-size: 0.8rem;
  }
</style>
