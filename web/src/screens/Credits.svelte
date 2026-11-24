<script lang="ts">
  // v9 song credits: producers, songwriters, musicians and engineers of one song (MusicBrainz + the file's
  // composer/lyricist tags). Tapping a person opens everything else in the library they worked on.
  import { ArrowLeft, ChevronRight, ExternalLink, User } from '@lucide/svelte';
  import Cover from '../components/Cover.svelte';
  import { session } from '../lib/session.svelte';
  import type { CreditEntry, Track, TrackCredits } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { id }: { id: string } = $props();
  let track = $state<Track | null>(null);
  let credits = $state<TrackCredits | null>(null);
  let error = $state<string | null>(null);

  $effect(() => {
    track = null;
    credits = null;
    error = null;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    session.api.track(id).then((t) => live && (track = t)).catch(() => undefined);
    const load = (n: number) =>
      session.api
        .trackCredits(id)
        .then((c) => {
          if (!live) return;
          credits = c;
          if (c.status === 'pending' && n < 8) timer = setTimeout(() => load(n + 1), 2000 + n * 1000);
        })
        .catch((e) => live && (error = e.status === 404 ? 'This server has no song credits yet.' : e.message));
    void load(0);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  });

  function others(p: CreditEntry): string {
    if (!p.otherTracks) return '';
    return `${p.otherTracks} other song${p.otherTracks === 1 ? '' : 's'}`;
  }
</script>

<div class="page credits">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>Credits</h1>
  </div>
  {#if track}
    <div class="head">
      <div class="thumb"><Cover albumId={track.albumId} size={128} /></div>
      <div class="ellipsis">
        <b class="ellipsis">{track.title}</b>
        <div class="muted ellipsis">{track.artist} · {track.album}</div>
      </div>
    </div>
  {/if}
  {#if error}<div class="empty">{error}</div>
  {:else if !credits}<div class="empty">Loading…</div>
  {:else}
    {#each credits.groups as g (g.id)}
      <section>
        <h2>{g.label}</h2>
        {#each g.people as p (p.personId)}
          <div class="person">
            <button class="main" onclick={() => ui.go('person', p.personId)}>
              <span class="name">{p.name}</span>
              <span class="muted small">{p.roles.join(', ')}{p.albumWide ? ' (album)' : ''}</span>
              {#if p.otherTracks}<span class="more small">{others(p)} in your library</span>{/if}
            </button>
            {#if p.artistId}
              <button class="icon-btn" aria-label="Go to artist {p.name}" title="Artist page" onclick={() => ui.go('artist', p.artistId!)}><User size={20} /></button>
            {/if}
            <ChevronRight size={20} class="chev" />
          </div>
        {/each}
      </section>
    {/each}
    {#if credits.status === 'pending'}
      <p class="muted note">Looking this song up on MusicBrainz…</p>
    {:else if !credits.groups.length}
      <div class="empty">
        {credits.status === 'not_found'
          ? "MusicBrainz doesn't know this song yet, and its tags name no composer or lyricist."
          : credits.status === 'error'
            ? "Couldn't reach MusicBrainz. Try again later."
            : 'No credits for this song.'}
      </div>
    {/if}
    {#if credits.musicbrainzUrl}
      <p class="muted note small">
        Credits from <a href={credits.musicbrainzUrl} target="_blank" rel="noopener noreferrer">MusicBrainz <ExternalLink size={12} /></a>.
        Missing someone? Anyone can add credits there.
      </p>
    {/if}
  {/if}
</div>

<style>
  .head {
    display: flex;
    gap: 14px;
    align-items: center;
    padding: 4px 16px 8px;
    min-width: 0;
  }
  .thumb {
    width: 64px;
    height: 64px;
    flex: none;
    border-radius: 6px;
    overflow: hidden;
  }
  section {
    padding: 0 16px;
    max-width: 760px;
  }
  h2 {
    font-size: 1.15rem;
    margin: 20px 0 6px;
  }
  .person {
    display: flex;
    align-items: center;
    gap: 4px;
    border-radius: 8px;
  }
  .person:hover {
    background: rgba(255, 255, 255, 0.06);
  }
  .main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    text-align: left;
    padding: 8px 6px;
    gap: 2px;
  }
  .name {
    font-weight: 600;
  }
  .more {
    color: var(--accent);
  }
  .small {
    font-size: 0.85rem;
  }
  .note {
    padding: 0 16px;
    margin-top: 20px;
  }
  .note a {
    color: inherit;
    text-decoration: underline;
  }
  :global(.credits .chev) {
    opacity: 0.5;
    flex: none;
  }
</style>
