<script lang="ts">
  import { Check, ChevronDown, ListEnd, ListPlus, Plus, Tags } from '@lucide/svelte';
  import Card from '../components/Card.svelte';
  import PageHeader from '../components/PageHeader.svelte';
  import Shelf from '../components/Shelf.svelte';
  import TrackList from '../components/TrackList.svelte';
  import { fmtTime } from '../lib/format';
  import { library } from '../lib/library.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { Album, AlbumCompleteness, ShelfItem, Track } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { id }: { id: string } = $props();
  let album = $state<(Album & { tracks: Track[] }) | null>(null);
  let more = $state<Album[]>([]);
  let error = $state<string | null>(null);
  // "9 of 12" from MusicBrainz: loaded after the album, silently absent when unknown or offline.
  let complete = $state<AlbumCompleteness | null>(null);
  let gapsOpen = $state(false);
  let adding = $state(false);
  $effect(() => {
    void library.tagsRev; // reload after an admin tag edit
    complete = null;
    gapsOpen = false;
    session.api
      .album(id)
      .then((a) => {
        album = a;
        session.api
          .albumCompleteness(id)
          .then((c) => (complete = c.albumId === id && c.total ? c : null))
          .catch(() => (complete = null));
        return session.api.albums('year', 0, 50, a.artistId);
      })
      .then((m) => (more = m.filter((x) => x.id !== id)))
      .catch((e) => (error = e.message));
  });

  const gaps = $derived(complete?.missing ?? []);
  const unlisted = $derived(gaps.filter((m) => !m.toget || m.toget.status === 'dismissed'));
  const where = (m: AlbumCompleteness['missing'][number]) => ((complete?.discCount ?? 1) > 1 ? `${m.disc}.${m.position}` : String(m.position));

  async function addGaps(rec?: string) {
    if (!complete || adding) return;
    adding = true;
    try {
      complete = await session.api.addMissingToGet(id, rec ? [rec] : undefined);
      ui.toast(rec ? 'Added to To-get' : 'Missing tracks added to To-get');
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      adding = false;
    }
  }

  const name = $derived(album ? `Album: ${album.title}` : '');
  const meta = { source: 'library' as const, addedFrom: 'album' };
  const item = $derived<ShelfItem | null>(album ? { type: 'album', id: album.id, title: album.title, subtitle: album.artist, albumId: album.id, album } : null);
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
    {#if complete}
      <div class="complete">
        {#if complete.status === 'complete'}
          <span class="pill ok" title="Matches {complete.releaseTitle ?? 'the release'} on MusicBrainz"><Check size={14} /> Complete album · {complete.total} of {complete.total}</span>
        {:else}
          <button class="pill gap" aria-expanded={gapsOpen} onclick={() => (gapsOpen = !gapsOpen)} title="Compared with {complete.releaseTitle ?? 'the release'} on MusicBrainz">
            {complete.have} of {complete.total} tracks<ChevronDown size={14} />
          </button>
          {#if unlisted.length}
            <button class="pill add" disabled={adding} onclick={() => addGaps()}><Plus size={14} /> Add {unlisted.length === 1 ? 'the missing track' : `${unlisted.length} missing`} to To-get</button>
          {/if}
        {/if}
      </div>
      {#if gapsOpen && gaps.length}
        <ul class="gaps" aria-label="Missing tracks">
          {#each gaps as m (m.disc + '.' + m.position)}
            <li>
              <span class="no">{where(m)}</span>
              <span class="title">{m.title}</span>
              {#if m.durationS}<span class="muted dur">{fmtTime(m.durationS)}</span>{/if}
              {#if m.toget && m.toget.status !== 'dismissed'}
                <button class="muted small" onclick={() => ui.go('toget')}>{m.toget.status === 'got' ? 'Got it' : 'On To-get'}</button>
              {:else if m.recordingMbid}
                <button class="icon-btn" aria-label="Add {m.title} to To-get" disabled={adding} onclick={() => addGaps(m.recordingMbid!)}><Plus size={18} /></button>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
    {/if}
    <TrackList tracks={album.tracks} queueName={name} {meta} showAlbum={false} />
    {#if moreItems.length}
      <div class="more"><Shelf title="More by {album.artist}">{#each moreItems as it (it.id)}<Card item={it} />{/each}</Shelf></div>
    {/if}
  {:else}<div class="empty">Loading…</div>{/if}
</div>

<style>
  .complete {
    --gutter: clamp(16px, 3vw, 32px);
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 0 var(--gutter) 10px;
  }
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 4px 10px;
    border-radius: 999px;
    background: var(--bg-2);
    font-size: 0.85rem;
    color: var(--fg-2);
  }
  .pill.ok {
    color: var(--fg-2);
  }
  .pill.add {
    color: var(--accent);
  }
  .pill:disabled {
    opacity: 0.6;
  }
  .gaps {
    --gutter: clamp(16px, 3vw, 32px);
    list-style: none;
    margin: 0 var(--gutter) 14px;
    padding: 4px 0;
    border-radius: 10px;
    background: var(--bg-2);
  }
  .gaps li {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 4px 12px;
    min-height: 40px;
  }
  .gaps .no {
    width: 2.5em;
    color: var(--fg-3);
    font-variant-numeric: tabular-nums;
  }
  .gaps .title {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--fg-2);
  }
  .gaps .dur {
    font-variant-numeric: tabular-nums;
  }
  .more {
    --gutter: clamp(16px, 3vw, 32px);
    margin-top: 24px;
  }
</style>
