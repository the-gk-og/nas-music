<script lang="ts">
  // "Podcasts & more" (Library → Podcasts & more): Podcasts · Audiobooks · Radio, plus Continue listening.
  // Spoken word and live radio are kept apart from music: no queues, no events, no recommendations.
  import { ArrowLeft, Plus, Search, Trash2 } from '@lucide/svelte';
  import SpokenArt from '../../components/SpokenArt.svelte';
  import { session } from '../../lib/session.svelte';
  import { remaining, spokenApi, type Audiobook, type ContinueItem, type Episode, type Podcast, type PodcastSearchResult, type Station, type StationSearchResult } from '../../lib/spoken';
  import { spoken } from '../../lib/spokenPlayer.svelte';
  import { ui } from '../../lib/ui.svelte';

  type Tab = 'podcasts' | 'books' | 'radio';
  const TABS: [Tab, string][] = [
    ['podcasts', 'Podcasts'],
    ['books', 'Audiobooks'],
    ['radio', 'Radio'],
  ];
  const tab = $derived<Tab>((TABS.some(([t]) => t === ui.route.param) ? ui.route.param : 'podcasts') as Tab);
  const api = $derived(spokenApi(session.api));

  let cont = $state<ContinueItem[]>([]);
  let shows = $state<Podcast[] | null>(null);
  let inbox = $state<Episode[]>([]);
  let books = $state<{ enabled: boolean; books: Audiobook[] } | null>(null);
  let stations = $state<Station[] | null>(null);
  let error = $state('');

  let q = $state('');
  let searching = $state(false);
  let podResults = $state<PodcastSearchResult[] | null>(null);
  let stResults = $state<StationSearchResult[] | null>(null);

  const fail = (e: Error) => (error = e.message);
  const isUrl = (s: string) => /^https?:\/\//i.test(s.trim());

  $effect(() => {
    const t = tab;
    error = '';
    q = '';
    podResults = null;
    stResults = null;
    api.continueListening().then((c) => (cont = c)).catch(() => (cont = []));
    if (t === 'podcasts') {
      api.podcasts().then((p) => (shows = p)).catch(fail);
      api.inbox('unplayed', 30).then((e) => (inbox = e)).catch(() => (inbox = []));
    } else if (t === 'books') api.books('recent').then((b) => (books = b)).catch(fail);
    else api.stations().then((s) => (stations = s)).catch(fail);
  });

  async function search(ev: Event) {
    ev.preventDefault();
    const term = q.trim();
    if (!term) return;
    searching = true;
    try {
      if (isUrl(term)) {
        if (tab === 'podcasts') {
          const p = await api.subscribe(term);
          ui.toast(`Subscribed to ${p.title}`);
          ui.go('podcast', p.id);
        } else {
          const s = await api.addStation({ streamUrl: term });
          stations = await api.stations();
          ui.toast(`Added ${s.name}`);
          q = '';
        }
      } else if (tab === 'podcasts') {
        const r = await api.searchPodcasts(term);
        podResults = r.results;
        if (!r.results.length && r.errors.length) ui.toast(r.errors[0]);
      } else stResults = await api.searchStations({ q: term });
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      searching = false;
    }
  }

  async function subscribe(r: PodcastSearchResult) {
    try {
      const p = await api.subscribe(r.feedUrl);
      ui.go('podcast', p.id);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async function addStation(r: StationSearchResult) {
    try {
      await api.addStation({ streamUrl: r.streamUrl, name: r.name, homepage: r.homepage, imageUrl: r.imageUrl, country: r.country,
        tags: r.tags, codec: r.codec, bitrate: r.bitrate, explicit: r.explicit, radiobrowserUuid: r.radiobrowserUuid, probe: false });
      r.favourite = true;
      stations = await api.stations();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async function removeStation(s: Station) {
    if (!(await ui.confirm('Remove station', `Remove ${s.name} from your stations?`, 'Remove'))) return;
    await api.removeStation(s.id).catch((e) => ui.toast(e.message));
    stations = await api.stations();
  }

  function resume(c: ContinueItem) {
    if (c.kind === 'episode') void spoken.playEpisode(c.episode.id);
    else void spoken.playBook(c.book.id);
  }
</script>

<div class="page spoken">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>Podcasts &amp; more</h1>
  </div>
  <div class="chips" role="tablist">
    {#each TABS as [t, label]}
      <button class="chip" class:on={tab === t} role="tab" aria-selected={tab === t} onclick={() => ui.go('spoken', t)}>{label}</button>
    {/each}
  </div>

  {#if cont.length && tab !== 'radio'}
    <h2>Continue listening</h2>
    <div class="row-scroll">
      {#each cont.filter((c) => (tab === 'books' ? c.kind === 'book' : c.kind === 'episode')) as c (c.kind + c.progress.id)}
        <button class="cont" onclick={() => resume(c)}>
          {#if c.kind === 'episode'}
            <SpokenArt kind="podcast" src={api.artSrc('podcast', c.episode.podcastId, 256)} size={120} />
            <b class="ellipsis">{c.episode.title}</b>
            <span class="muted small">{remaining(c.progress, c.episode.durationS)}</span>
          {:else}
            <SpokenArt kind="book" src={api.artSrc('book', c.book.id, 256)} size={120} />
            <b class="ellipsis">{c.book.title}</b>
            <span class="muted small">{remaining(c.progress, c.book.durationS)}</span>
          {/if}
          <span class="bar"><span style:width="{Math.min(100, (100 * c.progress.positionS) / (c.progress.durationS || 1))}%"></span></span>
        </button>
      {/each}
    </div>
  {/if}

  {#if tab !== 'books'}
    <form class="search" onsubmit={search}>
      <Search size={18} />
      <input bind:value={q} placeholder={tab === 'podcasts' ? 'Search podcasts, or paste an RSS feed URL' : 'Search stations, or paste a stream URL'} aria-label="Search" />
      <button class="btn" disabled={searching}>{isUrl(q) ? 'Add' : 'Search'}</button>
    </form>
  {/if}

  {#if error}<div class="empty">{error}</div>{/if}

  {#if tab === 'podcasts'}
    {#if podResults}
      <h2>Directory <button class="lk" onclick={() => (podResults = null)}>Close</button></h2>
      {#each podResults as r (r.feedUrl)}
        <div class="item">
          <SpokenArt kind="podcast" src={r.imageUrl ?? ''} />
          <div class="grow">
            <b class="ellipsis">{r.title}{#if r.explicit}<span class="e">E</span>{/if}</b>
            <div class="muted small ellipsis">{r.author ?? ''}{r.genre ? ` · ${r.genre}` : ''}</div>
          </div>
          {#if r.subscribed}<button class="btn ghost" onclick={() => ui.go('podcast', r.id)}>Open</button>
          {:else}<button class="btn" onclick={() => subscribe(r)}><Plus size={16} /> Subscribe</button>{/if}
        </div>
      {:else}<div class="empty">Nothing found.</div>{/each}
    {:else if shows}
      {#if shows.length}
        <div class="grid">
          {#each shows as p (p.id)}
            <button class="show" onclick={() => ui.go('podcast', p.id)}>
              <SpokenArt kind="podcast" src={api.artSrc('podcast', p.id, 256)} size={140} />
              <b class="ellipsis">{p.title}</b>
              <span class="muted small">{p.unplayed ? `${p.unplayed} unplayed` : 'All played'}</span>
            </button>
          {/each}
        </div>
        {#if inbox.length}
          <h2>New episodes</h2>
          {#each inbox as ep (ep.id)}
            <button class="item ep" onclick={() => spoken.playEpisode(ep.id)}>
              <SpokenArt kind="podcast" src={api.artSrc('podcast', ep.podcastId, 128)} size={48} />
              <div class="grow">
                <b class="ellipsis">{ep.title}</b>
                <div class="muted small ellipsis">{ep.podcastTitle} · {ep.publishedAt?.slice(0, 10) ?? ''} · {remaining(ep.progress, ep.durationS)}</div>
              </div>
            </button>
          {/each}
        {/if}
      {:else}
        <div class="empty">No podcasts yet. Search the directory above, or paste a show's RSS feed URL.</div>
      {/if}
    {:else}<div class="empty">Loading…</div>{/if}
  {:else if tab === 'books'}
    {#if !books}<div class="empty">Loading…</div>
    {:else if !books.enabled}<div class="empty">Audiobooks are not set up on this server (an admin sets <code>audiobooks_root</code>).</div>
    {:else if !books.books.length}<div class="empty">No audiobooks found yet.</div>
    {:else}
      <div class="grid">
        {#each books.books as b (b.id)}
          <button class="show" onclick={() => ui.go('book', b.id)}>
            <SpokenArt kind="book" src={api.artSrc('book', b.id, 256)} size={140} />
            <b class="ellipsis">{b.title}</b>
            <span class="muted small ellipsis">{b.author ?? ''}</span>
            <span class="muted small">{remaining(b.progress, b.durationS)}</span>
          </button>
        {/each}
      </div>
    {/if}
  {:else}
    {#if stResults}
      <h2>Radio Browser <button class="lk" onclick={() => (stResults = null)}>Close</button></h2>
      {#each stResults as r (r.id)}
        <div class="item">
          <SpokenArt kind="station" src={r.imageUrl ?? ''} size={48} />
          <div class="grow">
            <b class="ellipsis">{r.name}</b>
            <div class="muted small ellipsis">{[r.country, r.codec && `${r.codec}${r.bitrate ? ` ${r.bitrate}k` : ''}`, ...r.tags.slice(0, 3)].filter(Boolean).join(' · ')}</div>
          </div>
          {#if r.favourite}<span class="muted small">Added</span>
          {:else}<button class="btn" onclick={() => addStation(r)}><Plus size={16} /> Add</button>{/if}
        </div>
      {:else}<div class="empty">Nothing found.</div>{/each}
    {/if}
    {#if stations}
      {#if !stations.length && !stResults}<div class="empty">No stations yet. Search Radio Browser above, or paste a stream URL.</div>{/if}
      {#if stations.length}<h2>Your stations</h2>{/if}
      {#each stations as s (s.id)}
        <div class="item" class:playing={spoken.current?.kind === 'station' && spoken.current.id === s.id}>
          <button class="hit" onclick={() => spoken.playStation(s)}>
            <SpokenArt kind="station" src={s.hasImage ? api.artSrc('station', s.id, 128) : ''} size={48} />
            <div class="grow">
              <b class="ellipsis">{s.name}</b>
              <div class="muted small ellipsis">{[s.country, ...s.tags.slice(0, 3)].filter(Boolean).join(' · ')}</div>
            </div>
          </button>
          <button class="icon-btn" aria-label="Remove {s.name}" onclick={() => removeStation(s)}><Trash2 size={18} /></button>
        </div>
      {/each}
    {:else if !error}<div class="empty">Loading…</div>{/if}
  {/if}
</div>

<style>
  .spoken {
    padding-bottom: 120px;
  }
  h2 {
    font-size: 1.1rem;
    margin: 20px 0 8px;
    display: flex;
    gap: 12px;
    align-items: baseline;
  }
  .row-scroll {
    display: flex;
    gap: 12px;
    overflow-x: auto;
    padding-bottom: 4px;
  }
  .cont,
  .show {
    display: flex;
    flex-direction: column;
    gap: 4px;
    text-align: left;
    min-width: 0;
  }
  .cont {
    width: 120px;
    flex: none;
  }
  .bar {
    height: 3px;
    background: #333;
    border-radius: 2px;
    overflow: hidden;
  }
  .bar span {
    display: block;
    height: 100%;
    background: var(--accent);
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    gap: 16px;
    margin-top: 12px;
  }
  .search {
    display: flex;
    gap: 8px;
    align-items: center;
    margin: 16px 0 8px;
    background: var(--card, #1d1d1d);
    border-radius: 8px;
    padding: 4px 4px 4px 12px;
  }
  .search input {
    flex: 1;
    min-width: 0;
    background: none;
    border: 0;
    color: inherit;
    padding: 8px 0;
    font: inherit;
  }
  .item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 0;
    width: 100%;
    text-align: left;
  }
  .item .hit {
    display: flex;
    align-items: center;
    gap: 12px;
    flex: 1;
    min-width: 0;
    text-align: left;
  }
  .item.playing b {
    color: var(--accent);
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .grow b {
    display: block;
  }
  .e {
    font-size: 0.65rem;
    background: #555;
    border-radius: 2px;
    padding: 0 3px;
    margin-left: 6px;
    vertical-align: middle;
  }
  .lk {
    font-size: 0.85rem;
    color: #aaa;
    text-decoration: underline;
  }
  .btn {
    display: inline-flex;
    gap: 4px;
    align-items: center;
    padding: 6px 12px;
    border-radius: 999px;
    background: var(--accent);
    color: #000;
    font-weight: 600;
    white-space: nowrap;
  }
  .btn.ghost {
    background: none;
    color: inherit;
    border: 1px solid #555;
  }
</style>
