<script lang="ts">
  import { ListEnd, ListPlus, Play, Tags } from '@lucide/svelte';
  import Card from '../components/Card.svelte';
  import PageHeader from '../components/PageHeader.svelte';
  import Shelf from '../components/Shelf.svelte';
  import TrackList from '../components/TrackList.svelte';
  import TrackRow from '../components/TrackRow.svelte';
  import { appearance } from '../lib/appearance.svelte';
  import { groupByWork, hasWorks } from '../lib/classical';
  import { fmtTime } from '../lib/format';
  import { library } from '../lib/library.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { Album, ShelfItem, Track } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { id }: { id: string } = $props();
  let album = $state<(Album & { tracks: Track[] }) | null>(null);
  let more = $state<Album[]>([]);
  let error = $state<string | null>(null);
  $effect(() => {
    void library.tagsRev; // reload after an admin tag edit
    session.api
      .album(id)
      .then((a) => {
        album = a;
        return session.api.albums('year', 0, 50, a.artistId);
      })
      .then((m) => (more = m.filter((x) => x.id !== id)))
      .catch((e) => (error = e.message));
  });

  const name = $derived(album ? `Album: ${album.title}` : '');
  const meta = { source: 'library' as const, addedFrom: 'album' };
  const item = $derived<ShelfItem | null>(album ? { type: 'album', id: album.id, title: album.title, subtitle: album.artist, albumId: album.id, album } : null);
  // v10 classical mode: movements grouped under their work, each work playable on its own.
  const groups = $derived(album && appearance.prefs.classical && hasWorks(album.tracks) ? groupByWork(album.tracks) : null);
  const currentId = $derived(player.shown.track?.id);
  function playWork(tracks: Track[], work: string) {
    void player.playList(tracks, 0, `Work: ${work}`, { source: 'library', addedFrom: 'work' });
  }
  const moreItems = $derived(more.map((a): ShelfItem => ({ type: 'album', id: a.id, title: a.title, subtitle: String(a.year ?? ''), albumId: a.id })));
</script>

<div class="page">
  {#if error}<div class="empty">{error}</div>
  {:else if album && item}
    <PageHeader
      {item}
      kind="album"
      label="Album"
      meta="{album.year ?? ''}{album.year ? ' · ' : ''}{album.tracks.length} songs · {fmtTime(album.tracks.reduce((s, t) => s + t.durationS, 0))}"
      onplay={() => player.playList(album!.tracks, 0, name, meta)}
      onshuffle={() => {
        if (!player.shuffle) player.toggleShuffle();
        void player.playList(album!.tracks, Math.floor(Math.random() * album!.tracks.length), name, meta);
      }}
      onradio={() => player.startRadio({ type: 'album', id: album!.id }, `${album!.title} radio`)}
    >
      {#snippet menu()}
        <button onclick={() => player.addToQueue(album!.tracks, 'next')}><ListEnd size={18} /> Play next</button>
        <button onclick={() => player.addToQueue(album!.tracks, 'end')}><ListEnd size={18} /> Add to queue</button>
        <button onclick={() => ui.open({ kind: 'addToPlaylist', tracks: album!.tracks })}><ListPlus size={18} /> Add to playlist</button>
        {#if session.isAdmin}
          <button onclick={() => ui.open({ kind: 'tagEdit', trackIds: album!.tracks.map((t) => t.id), albumId: album!.id, title: album!.title })}><Tags size={18} /> Edit album tags</button>
        {/if}
      {/snippet}
    </PageHeader>
    {#if groups}
      <div role="list">
        {#each groups as g (g.start)}
          {#if g.workId}
            <div class="work">
              <div class="grow">
                <div class="wt">{g.work}</div>
                <div class="muted small">{g.composer ? `${g.composer} · ` : ''}{g.tracks.length} movement{g.tracks.length === 1 ? '' : 's'} · {fmtTime(g.tracks.reduce((s, t) => s + t.durationS, 0))}</div>
              </div>
              <button class="icon-btn" aria-label="Play {g.work}" title="Play whole work" onclick={() => playWork(g.tracks, g.work ?? '')}><Play size={20} fill="currentColor" /></button>
            </div>
          {/if}
          {#each g.tracks as t, j (t.id + ':' + (g.start + j))}
            <TrackRow
              track={t}
              showAlbum={false}
              movement={!!g.workId}
              current={t.id === currentId}
              onplay={() => player.playList(album!.tracks, g.start + j, name, meta)}
              onmenu={() => ui.open({ kind: 'songMenu', ctx: { track: t, list: album!.tracks } })}
            />
          {/each}
        {/each}
      </div>
    {:else}
      <TrackList tracks={album.tracks} queueName={name} {meta} showAlbum={false} />
    {/if}
    {#if moreItems.length}
      <div class="more"><Shelf title="More by {album.artist}">{#each moreItems as it (it.id)}<Card item={it} />{/each}</Shelf></div>
    {/if}
  {:else}<div class="empty">Loading…</div>{/if}
</div>

<style>
  .work {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 18px var(--gutter, 16px) 4px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--line);
  }
  .wt {
    font-weight: 700;
  }
  .more {
    --gutter: clamp(16px, 3vw, 32px);
    margin-top: 24px;
  }
</style>
