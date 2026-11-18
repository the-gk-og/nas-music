<script lang="ts">
  import ItemGrid from '../components/ItemGrid.svelte';
  import PageHeader from '../components/PageHeader.svelte';
  import TrackList from '../components/TrackList.svelte';
  import ViewToggle from '../components/ViewToggle.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { Album, Artist, ShelfItem, Track } from '../lib/types';

  let { id }: { id: string } = $props();
  let artist = $state<(Artist & { albums: Album[] }) | null>(null);
  let tracks = $state<Track[]>([]);
  let error = $state<string | null>(null);
  let showAll = $state(false);

  $effect(() => {
    session.api
      .artist(id)
      .then(async (a) => {
        artist = a;
        const all: Track[] = [];
        for (const al of a.albums.slice(0, 12)) all.push(...(await session.api.album(al.id)).tracks);
        tracks = all;
      })
      .catch((e) => (error = e.message));
  });

  // "Top tracks": favourites first, then by album order (no per-artist play counts in the API yet).
  const top = $derived([...tracks.filter((t) => t.starred), ...tracks.filter((t) => !t.starred)]);
  const meta = { source: 'library' as const, addedFrom: 'artist' };
  const item = $derived<ShelfItem | null>(artist ? { type: 'artist', id: artist.id, title: artist.name, albumId: artist.albums[0]?.id ?? null } : null);
  const albums = $derived((artist?.albums ?? []).map((a): ShelfItem => ({ type: 'album', id: a.id, title: a.title, subtitle: String(a.year ?? ''), albumId: a.id })));
</script>

<div class="page artist">
  {#if error}<div class="empty">{error}</div>
  {:else if artist && item}
    <PageHeader
      {item}
      kind="artist"
      label="Artist"
      meta="{artist.albums.length} album{artist.albums.length === 1 ? '' : 's'} · {tracks.length} songs"
      onplay={() => player.playList(top, 0, `Artist: ${artist!.name}`, meta)}
      onshuffle={() => {
        if (!player.shuffle) player.toggleShuffle();
        void player.playList(top, Math.floor(Math.random() * top.length), `Artist: ${artist!.name}`, meta);
      }}
      onradio={() => player.startRadio({ type: 'artist', id: artist!.id }, `${artist!.name} radio`)}
    />
    {#if top.length}
      <h2 class="h">Top tracks</h2>
      <TrackList tracks={showAll ? top : top.slice(0, 5)} queueName="Artist: {artist.name}" {meta} />
      {#if top.length > 5}<button class="btn text sa" onclick={() => (showAll = !showAll)}>{showAll ? 'Show less' : 'Show all'}</button>{/if}
    {/if}
    <div class="hrow"><h2 class="h">Albums</h2><ViewToggle section="artist.albums" /></div>
    <ItemGrid items={albums} section="artist.albums" />
    <button class="radio" onclick={() => player.startRadio({ type: 'artist', id: artist!.id }, `${artist!.name} radio`)}>▶ {artist.name} radio</button>
  {:else}<div class="empty">Loading…</div>{/if}
</div>

<style>
  .artist {
    --gutter: clamp(16px, 3vw, 32px);
  }
  .h {
    margin: 20px var(--gutter) 8px;
    font-size: 1.4rem;
  }
  .hrow {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding-right: var(--gutter);
  }
  .sa {
    margin-left: calc(var(--gutter) - 10px);
  }
  .radio {
    margin: 24px var(--gutter);
    padding: 12px 20px;
    border-radius: 999px;
    border: 1px solid #555;
    font-weight: 700;
  }
</style>
