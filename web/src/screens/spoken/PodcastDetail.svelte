<script lang="ts">
  // One show: episodes (all / unplayed / in progress / on server), per-show speed, server downloads, unsubscribe.
  import { ArrowLeft, Check, CircleCheck, Pause, Play, RefreshCw, Server } from '@lucide/svelte';
  import SpokenArt from '../../components/SpokenArt.svelte';
  import { session } from '../../lib/session.svelte';
  import { clock, remaining, safeNotes, spokenApi, type Episode, type EpisodeDetail, type EpisodeFilter, type Podcast } from '../../lib/spoken';
  import { SPEEDS, spoken } from '../../lib/spokenPlayer.svelte';
  import { ui } from '../../lib/ui.svelte';

  let { id }: { id: string } = $props();
  const api = $derived(spokenApi(session.api));
  let show = $state<Podcast | null>(null);
  let eps = $state<Episode[] | null>(null);
  let filter = $state<EpisodeFilter>('all');
  let open = $state<EpisodeDetail | null>(null);
  let error = $state('');

  const load = () => {
    api.podcast(id).then((p) => (show = p)).catch((e) => (error = e.message));
    api.episodes(id, filter, 0, 300).then((e) => (eps = e)).catch((e) => (error = e.message));
  };
  $effect(() => {
    void filter;
    load();
  });

  async function setPlayed(ep: Episode, played: boolean) {
    await api.putProgress([{ kind: 'episode', id: ep.id, played, updatedAt: new Date().toISOString() }]).catch((e) => ui.toast(e.message));
    load();
  }
  async function patch(b: Parameters<typeof api.patchPodcast>[1]) {
    try {
      show = await api.patchPodcast(id, b);
      if (b.speed && spoken.current?.kind === 'episode' && spoken.current.showId === id) spoken.setSpeed(b.speed);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  async function refresh() {
    const r = await api.refresh(id).catch((e) => ({ ok: false, error: e.message, new: 0 }));
    ui.toast(r.ok ? `${r.new ?? 0} new episode(s)` : (r.error ?? 'Feed failed'));
    load();
  }
  async function unsubscribe() {
    if (!show || !(await ui.confirm('Unsubscribe', `Unsubscribe from ${show.title}?`, 'Unsubscribe'))) return;
    await api.unsubscribe(id).catch((e) => ui.toast(e.message));
    ui.go('spoken', 'podcasts');
  }
  async function allPlayed() {
    if (!(await ui.confirm('Mark all played', 'Mark every episode of this show as played?', 'Mark played'))) return;
    await api.markShowPlayed(id).catch((e) => ui.toast(e.message));
    load();
  }
  async function details(ep: Episode) {
    open = open?.id === ep.id ? null : await api.episode(ep.id).catch(() => null);
  }
  const isCurrent = (ep: Episode) => spoken.current?.kind === 'episode' && spoken.current.id === ep.id;
</script>

<div class="page pod">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
  </div>
  {#if error}<div class="empty">{error}</div>{/if}
  {#if show}
    <header>
      <SpokenArt kind="podcast" src={api.artSrc('podcast', show.id, 512)} size={160} />
      <div class="grow">
        <h1>{show.title}</h1>
        <div class="muted">{show.author ?? ''}</div>
        {#if show.summary}<p class="small muted desc">{show.summary}</p>{/if}
        {#if show.lastError}<p class="small warn">Last check failed: {show.lastError}</p>{/if}
        <div class="tools">
          <label class="small">Speed
            <select value={show.speed} onchange={(e) => patch({ speed: Number((e.target as HTMLSelectElement).value) })}>
              {#each SPEEDS as s}<option value={s}>{s}×</option>{/each}
            </select>
          </label>
          <label class="small" title="How many of the newest episodes the server keeps downloaded (shared by all listeners)">
            <Server size={14} /> Keep on server
            <select value={show.serverKeepLast ?? 0} onchange={(e) => patch({ serverKeepLast: Number((e.target as HTMLSelectElement).value) })}>
              {#each [0, 1, 3, 5, 10, 25] as n}<option value={n}>{n === 0 ? 'none (stream)' : n}</option>{/each}
            </select>
          </label>
          <button class="icon-btn" aria-label="Check for new episodes" title="Check for new episodes" onclick={refresh}><RefreshCw size={18} /></button>
          <button class="lk" onclick={allPlayed}>Mark all played</button>
          {#if show.subscribed}<button class="lk" onclick={unsubscribe}>Unsubscribe</button>{/if}
        </div>
      </div>
    </header>
  {/if}
  <div class="chips">
    {#each [['all', 'All'], ['unplayed', 'Unplayed'], ['in_progress', 'In progress'], ['downloaded', 'On server']] as [k, label]}
      <button class="chip" class:on={filter === k} onclick={() => (filter = k as EpisodeFilter)}>{label}</button>
    {/each}
  </div>
  {#if !eps}<div class="empty">Loading…</div>
  {:else if !eps.length}<div class="empty">No episodes here.</div>
  {:else}
    {#each eps as ep (ep.id)}
      <div class="ep" class:done={ep.progress?.played}>
        <button class="play" aria-label={isCurrent(ep) && spoken.playing ? 'Pause' : 'Play'}
          onclick={() => (isCurrent(ep) ? spoken.toggle() : spoken.playEpisode(ep.id))}>
          {#if isCurrent(ep) && spoken.playing}<Pause size={18} />{:else}<Play size={18} />{/if}
        </button>
        <button class="grow txt" onclick={() => details(ep)}>
          <b>{ep.title}{#if ep.explicit}<span class="e">E</span>{/if}</b>
          <span class="muted small">{ep.publishedAt?.slice(0, 10) ?? ''} · {remaining(ep.progress, ep.durationS)}{ep.onServer ? ' · on server' : ''}{ep.hasChapters ? ' · chapters' : ''}</span>
        </button>
        <button class="icon-btn" aria-label={ep.progress?.played ? 'Mark unplayed' : 'Mark played'} title={ep.progress?.played ? 'Mark unplayed' : 'Mark played'}
          onclick={() => setPlayed(ep, !ep.progress?.played)}>
          {#if ep.progress?.played}<CircleCheck size={18} />{:else}<Check size={18} />{/if}
        </button>
      </div>
      {#if open?.id === ep.id}
        <div class="notes small">
          {#if open.chapters.length}
            <ol class="chapters">
              {#each open.chapters as c}
                <li><button onclick={async () => { if (!isCurrent(ep)) await spoken.playEpisode(ep.id, open ?? undefined); void spoken.seek(c.start); }}>{clock(c.start)} {c.title}</button></li>
              {/each}
            </ol>
          {/if}
          <!-- eslint-disable-next-line svelte/no-at-html-tags -- feed HTML is sanitised by safeNotes -->
          {@html safeNotes(open.description)}
        </div>
      {/if}
    {/each}
  {/if}
</div>

<style>
  .pod {
    padding-bottom: 120px;
  }
  header {
    display: flex;
    gap: 20px;
    align-items: flex-start;
    flex-wrap: wrap;
    margin-bottom: 12px;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  h1 {
    margin: 0 0 4px;
  }
  .desc {
    max-width: 70ch;
  }
  .warn {
    color: #e0a040;
  }
  .tools {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    align-items: center;
    margin-top: 8px;
  }
  .tools label {
    display: inline-flex;
    gap: 6px;
    align-items: center;
  }
  select {
    background: var(--card, #222);
    color: inherit;
    border: 1px solid #444;
    border-radius: 6px;
    padding: 2px 4px;
  }
  .lk {
    color: #aaa;
    text-decoration: underline;
    font-size: 0.85rem;
  }
  .ep {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 0;
    border-bottom: 1px solid var(--line, #2a2a2a);
  }
  .ep.done .txt b {
    color: #888;
  }
  .play {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    border: 1px solid #555;
    display: grid;
    place-items: center;
    flex: none;
  }
  .txt {
    text-align: left;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .e {
    font-size: 0.65rem;
    background: #555;
    border-radius: 2px;
    padding: 0 3px;
    margin-left: 6px;
  }
  .notes {
    padding: 8px 0 16px 46px;
    color: #ccc;
    overflow-wrap: anywhere;
  }
  .notes :global(a) {
    color: var(--accent);
  }
  .chapters {
    padding-left: 18px;
    margin: 0 0 12px;
  }
  .chapters button {
    text-align: left;
    color: #ddd;
  }
</style>
