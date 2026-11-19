<script lang="ts">
  // Genre, smart list (Favourites, Recently added, …, All songs) and mix pages.
  import PageHeader from '../components/PageHeader.svelte';
  import TrackList from '../components/TrackList.svelte';
  import type { PlayMeta } from '../lib/events';
  import { fmtTime } from '../lib/format';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { ShelfItem, SmartListKind, Track } from '../lib/types';

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
  let reason = $state('');
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
          reason = m.tracks[0]?.reason ?? '';
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

  const item = $derived<ShelfItem>({
    type: kind === 'mix' ? 'mix' : kind === 'genre' ? 'genre' : 'smart_list',
    id: param,
    title,
    artIds: (tracks ?? []).slice(0, 4).map((t) => t.albumId),
    albumId: tracks?.[0]?.albumId ?? null,
  });
</script>

<div class="page">
  {#if error}<div class="empty">{error}</div>
  {:else if !tracks}<div class="empty">Loading…</div>
  {:else}
    <PageHeader
      {item}
      kind={kind === 'mix' ? 'mix' : kind === 'genre' ? 'genre' : 'smart_list'}
      label={kind === 'genre' ? 'Genre' : kind === 'mix' ? 'Mix' : 'Playlist'}
      meta="{tracks.length} songs · {fmtTime(tracks.reduce((s, t) => s + t.durationS, 0))}"
      onplay={tracks.length ? () => player.playList(tracks!, 0, title, meta) : undefined}
      onshuffle={tracks.length ? () => {
        if (!player.shuffle) player.toggleShuffle();
        void player.playList(tracks!, Math.floor(Math.random() * tracks!.length), title, meta);
      } : undefined}
    />
    {#if reason}<p class="why muted">{reason}</p>{/if}
    <TrackList {tracks} queueName={title} {meta} />
  {/if}
</div>

<style>
  .why {
    margin: 0 clamp(16px, 3vw, 32px) 8px;
    font-size: 0.9rem;
  }
</style>
