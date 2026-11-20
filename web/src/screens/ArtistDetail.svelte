<script lang="ts">
  // v7 artist page: photo (Wikimedia Commons, credited), bio, genres, years active, links, top songs by
  // your plays, albums / singles, appears on, similar artists in your library, artist radio, shuffle.
  // Falls back to the plain artist + albums view on servers without /page.
  import { ExternalLink } from '@lucide/svelte';
  import ItemGrid from '../components/ItemGrid.svelte';
  import PageHeader from '../components/PageHeader.svelte';
  import TrackList from '../components/TrackList.svelte';
  import ViewToggle from '../components/ViewToggle.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { Album, ArtistPage, ShelfItem, Track } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { id }: { id: string } = $props();
  let page = $state<ArtistPage | null>(null);
  let error = $state<string | null>(null);
  let showAll = $state(false);
  let bioOpen = $state(false);

  $effect(() => {
    page = null;
    error = null;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const pollInfo = (n: number) => {
      timer = setTimeout(async () => {
        try {
          const info = await session.api.artistInfo(id);
          if (!live || !page) return;
          page = { ...page, info, photoUrl: info.photoUrl ?? null, artUrl: info.photoUrl ?? page.artUrl };
          if (info.status === 'pending' && n < 6) pollInfo(n + 1);
        } catch {
          /* keep what we have */
        }
      }, 2500 + n * 1500);
    };
    session.api
      .artistPage(id)
      .then((p) => {
        if (!live) return;
        page = p;
        if (p.info.status === 'pending') pollInfo(0);
      })
      .catch(async (e) => {
        // Older server: build a minimal page from /library/artists/{id}.
        try {
          const a = await session.api.artist(id);
          const tracks: Track[] = [];
          for (const al of a.albums.slice(0, 12)) tracks.push(...(await session.api.album(al.id)).tracks);
          if (!live) return;
          page = {
            id: a.id, name: a.name, mbid: a.mbid, artUrl: null, photoUrl: null, info: { status: 'none' },
            stats: { trackCount: tracks.length, durationS: 0, playCount: 0, albumCount: a.albums.length, singleCount: 0 },
            topTracks: [...tracks.filter((t) => t.starred), ...tracks.filter((t) => !t.starred)].slice(0, 10),
            albums: a.albums, singles: [], appearsOn: [], similar: [], radioSeed: { type: 'artist', id: a.id, title: a.name },
          };
        } catch {
          error = (e as Error).message;
        }
      });
    return () => {
      live = false;
      clearTimeout(timer);
    };
  });

  const meta = { source: 'library' as const, addedFrom: 'artist' };
  const queueName = $derived(`Artist: ${page?.name ?? ''}`);
  const item = $derived<ShelfItem | null>(
    page ? { type: 'artist', id: page.id, title: page.name, artUrl: page.artUrl ?? null, albumId: page.artUrl ? null : (page.albums[0] ?? page.singles[0] ?? page.appearsOn[0])?.id ?? null } : null,
  );
  const albumItems = (list: Album[], showArtist = false) =>
    list.map((a): ShelfItem => ({ type: 'album', id: a.id, title: a.title, subtitle: [showArtist ? a.artist : '', a.year ?? ''].filter(Boolean).join(' · '), albumId: a.hasArt === false ? null : a.id }));
  const similarItems = $derived(
    (page?.similar ?? []).map((s): ShelfItem => ({ type: 'artist', id: s.id, title: s.name, subtitle: `${s.albumCount} album${s.albumCount === 1 ? '' : 's'}`, artUrl: s.artUrl ?? null })),
  );
  const info = $derived(page?.info);
  const years = $derived.by(() => {
    const y = info?.yearsActive;
    if (!y?.from) return '';
    return `${y.from}–${y.to ?? (info?.lifeSpan?.ended ? '' : 'present')}`;
  });
  const LINK_LABELS: Record<string, string> = {
    musicbrainz: 'MusicBrainz', wikipedia: 'Wikipedia', wikidata: 'Wikidata', official: 'Official site', discogs: 'Discogs',
    bandcamp: 'Bandcamp', allmusic: 'AllMusic', lastfm: 'Last.fm', songkick: 'Songkick', soundcloud: 'SoundCloud', youtube: 'YouTube',
  };

  async function playAll(shuffle: boolean) {
    if (!page) return;
    try {
      const all = await session.api.artistTracks(page.id).catch(() => page!.topTracks as Track[]);
      if (!all.length) return;
      if (shuffle && !player.shuffle) player.toggleShuffle();
      await player.playList(all, shuffle ? Math.floor(Math.random() * all.length) : 0, queueName, meta);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  const radio = () => page && player.startRadio(page.radioSeed ?? { type: 'artist', id: page.id }, `${page.name} radio`);
</script>

<div class="page artist">
  {#if error}<div class="empty">{error}</div>
  {:else if page && item}
    <PageHeader
      {item}
      kind="artist"
      label={info?.type === 'Group' ? 'Band' : 'Artist'}
      meta="{[years, info?.area ?? info?.country ?? ''].filter(Boolean).join(' · ')}{years || info?.area ? ' · ' : ''}{page.stats.trackCount} songs"
      onplay={() => playAll(false)}
      onshuffle={() => playAll(true)}
      onradio={radio}
    />

    {#if info?.genres?.length}
      <div class="genres">{#each info.genres as g}<button class="chip" onclick={() => ui.go('genre', g)}>{g}</button>{/each}</div>
    {/if}

    {#if page.topTracks.length}
      <h2 class="h">Top songs</h2>
      <TrackList tracks={showAll ? page.topTracks : page.topTracks.slice(0, 5)} {queueName} {meta} />
      {#if page.topTracks.length > 5}<button class="btn text sa" onclick={() => (showAll = !showAll)}>{showAll ? 'Show less' : 'Show more'}</button>{/if}
    {/if}

    {#if page.albums.length}
      <div class="hrow"><h2 class="h">Albums</h2><ViewToggle section="artist.albums" /></div>
      <ItemGrid items={albumItems(page.albums)} section="artist.albums" />
    {/if}
    {#if page.singles.length}
      <h2 class="h">Singles & EPs</h2>
      <ItemGrid items={albumItems(page.singles)} section="artist.singles" />
    {/if}
    {#if page.appearsOn.length}
      <h2 class="h">Appears on</h2>
      <ItemGrid items={albumItems(page.appearsOn, true)} section="artist.appears" />
    {/if}

    <button class="radio" onclick={radio}>▶ Play {page.name} radio</button>

    {#if similarItems.length}
      <h2 class="h">Similar artists in your library</h2>
      <ItemGrid items={similarItems} section="artist.similar" />
    {/if}

    {#if info?.bio || info?.links?.length}
      <section class="about">
        <h2 class="h0">About</h2>
        {#if info?.bio}
          <p class:clamp={!bioOpen}>{info.bio.text}</p>
          {#if info.bio.text.length > 320}<button class="btn text" onclick={() => (bioOpen = !bioOpen)}>{bioOpen ? 'Less' : 'More'}</button>{/if}
          <p class="credit">From <a href={info.bio.url} target="_blank" rel="noopener noreferrer">Wikipedia</a> · <a href={info.bio.licenseUrl} target="_blank" rel="noopener noreferrer">{info.bio.license}</a></p>
        {/if}
        {#if info?.links?.length}
          <div class="links">
            {#each info.links as l (l.url)}<a href={l.url} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} /> {LINK_LABELS[l.kind] ?? l.kind}</a>{/each}
          </div>
        {/if}
        {#if page.photoUrl && info?.image}
          <p class="credit">
            Photo: {info.image.author ?? 'unknown author'}{#if info.image.license}, {#if info.image.licenseUrl}<a href={info.image.licenseUrl} target="_blank" rel="noopener noreferrer">{info.image.license}</a>{:else}{info.image.license}{/if}{/if},
            {#if info.image.filePage}<a href={info.image.filePage} target="_blank" rel="noopener noreferrer">Wikimedia Commons</a>{:else}Wikimedia Commons{/if}
          </p>
        {/if}
      </section>
    {:else if info?.status === 'pending'}
      <p class="muted">Looking up {page.name} on MusicBrainz and Wikipedia…</p>
    {/if}
  {:else}<div class="empty">Loading…</div>{/if}
</div>

<style>
  .artist {
    --gutter: clamp(16px, 3vw, 32px);
    padding-bottom: 32px;
  }
  .h {
    margin: 20px var(--gutter) 8px;
    font-size: 1.4rem;
  }
  .h0 {
    font-size: 1.4rem;
    margin: 0 0 8px;
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
  .genres {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 8px var(--gutter) 0;
  }
  .radio {
    margin: 24px var(--gutter);
    padding: 12px 20px;
    border-radius: 999px;
    border: 1px solid #555;
    font-weight: 700;
  }
  .about {
    margin: 28px var(--gutter) 0;
    max-width: 760px;
  }
  .about p {
    line-height: 1.5;
    margin: 0 0 6px;
  }
  .clamp {
    display: -webkit-box;
    -webkit-line-clamp: 5;
    line-clamp: 5;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .credit,
  .muted {
    color: var(--fg-2);
    font-size: 0.8rem;
  }
  .muted {
    margin: 20px var(--gutter);
  }
  .credit a,
  .links a {
    color: inherit;
    text-decoration: underline;
  }
  .links {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    margin: 12px 0;
    font-size: 0.9rem;
  }
  .links a {
    display: inline-flex;
    gap: 4px;
    align-items: center;
  }
</style>
