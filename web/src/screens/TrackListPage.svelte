<script lang="ts">
  import { ArrowLeft, Play, Shuffle } from '@lucide/svelte';
  import TrackList from '../components/TrackList.svelte';
  import type { PlayMeta } from '../lib/events';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { SmartListKind, Track } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { kind, param }: { kind: 'genre' | 'list' | 'mix'; param: string } = $props();

  const LIST_TITLES: Record<string, string> = {
    all: 'All songs',
    favourites: 'Favorites',
    recently_added: 'Recently added',
    recently_played: 'Recently played',
    most_played: 'Most played',
    not_played: 'Not played',
  };

  let tracks = $state<Track[] | null>(null);
  let title = $state('');
  let reasons = $state<Record<string, string>>({});
  let error = $state<string | null>(null);
  let meta = $state<PlayMeta>({});

  $effect(() => {
    tracks = null;
    error = null;
    void (async () => {
      try {
        if (kind === 'genre') {
          title = param;
          meta = { source: 'library', addedFrom: 'genre' };
          tracks = await session.api.genreTracks(param);
        } else if (kind === 'mix') {
          const m = await session.api.mix(param);
          title = m.title;
          meta = { source: m.kind === 'more_like' ? 'more_like' : 'mix', mixId: m.id, addedFrom: 'mix' };
          reasons = Object.fromEntries(m.tracks.map((x) => [x.track.id, x.reason]));
          tracks = m.tracks.map((x) => x.track);
        } else {
          title = LIST_TITLES[param] ?? param;
          meta = { source: param === 'favourites' ? 'favourites' : 'library', addedFrom: param };
          tracks = param === 'all' ? await session.api.allTracks('title') : await session.api.smartList(param as SmartListKind);
        }
      } catch (e) {
        error = (e as Error).message;
      }
    })();
  });
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>{title}</h1>
    {#if tracks?.length}
      <button class="icon-btn" aria-label="Shuffle" onclick={() => { if (!player.shuffle) player.toggleShuffle(); void player.playList(tracks!, Math.floor(Math.random() * tracks!.length), title, meta); }}><Shuffle size={22} /></button>
      <button class="icon-btn" aria-label="Play" onclick={() => player.playList(tracks!, 0, title, meta)}><Play size={24} fill="currentColor" /></button>
    {/if}
  </div>
  {#if error}<div class="empty">{error}</div>
  {:else if !tracks}<div class="empty">Loading…</div>
  {:else}
    {#if kind === 'mix' && tracks.length}
      <p class="why muted">{reasons[tracks[0].id] ?? ''}</p>
    {/if}
    <TrackList {tracks} queueName={title} {meta} />
  {/if}
</div>

<style>
  .why {
    margin: 8px 18px;
    font-size: 0.9rem;
  }
</style>
