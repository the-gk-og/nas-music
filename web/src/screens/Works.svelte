<script lang="ts">
  // v10 classical: a composer's works (cards), and one work with its movements ("play whole work").
  import Cover from '../components/Cover.svelte';
  import PageHeader from '../components/PageHeader.svelte';
  import TrackRow from '../components/TrackRow.svelte';
  import { fmtTime } from '../lib/format';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { ShelfItem, Track, Work } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { kind, param }: { kind: 'composer' | 'work'; param: string } = $props();

  let works = $state<Work[] | null>(null);
  let work = $state<(Work & { tracks: Track[] }) | null>(null);
  let error = $state<string | null>(null);

  $effect(() => {
    works = null;
    work = null;
    error = null;
    const p = kind === 'composer' ? session.api.works({ composer: param }).then((w) => (works = w)) : session.api.work(param).then((w) => (work = w));
    p.catch((e) => (error = (e as Error).message));
  });

  const currentId = $derived(player.shown.track?.id);
  const meta = { source: 'library' as const, addedFrom: 'work' };
  const header = $derived<ShelfItem | null>(
    work ? { type: 'album', id: work.id, title: work.title, subtitle: [work.composer, work.artist].filter(Boolean).join(' · '), albumId: work.hasArt ? work.albumId : null } : null,
  );
</script>

<div class="page">
  {#if error}<div class="empty">{error}</div>
  {:else if kind === 'composer'}
    <div class="head">
      <button class="btn text" onclick={() => ui.back()}>← Back</button>
      <h1>{param}</h1>
      {#if works}<div class="muted">{works.length} work{works.length === 1 ? '' : 's'}</div>{/if}
    </div>
    {#if !works}<div class="empty">Loading…</div>
    {:else}
      <div class="works">
        {#each works as w (w.id)}
          <button class="wk" onclick={() => ui.go('work', w.id)}>
            <span class="art"><Cover albumId={w.hasArt ? w.albumId : null} size={128} /></span>
            <span class="txt">
              <span class="t ellipsis">{w.title}</span>
              <span class="muted small ellipsis">{w.artist}{w.year ? ` · ${w.year}` : ''} · {w.trackCount} movement{w.trackCount === 1 ? '' : 's'} · {fmtTime(w.durationS)}</span>
            </span>
          </button>
        {/each}
      </div>
    {/if}
  {:else if work && header}
    <PageHeader
      item={header}
      kind="work"
      label="Work"
      meta="{work.tracks.length} movement{work.tracks.length === 1 ? '' : 's'} · {fmtTime(work.durationS)}"
      onplay={() => player.playList(work!.tracks, 0, `Work: ${work!.title}`, meta)}
    >
      {#snippet menu()}
        <button onclick={() => ui.go('album', work!.albumId)}>Go to album</button>
      {/snippet}
    </PageHeader>
    <div role="list">
      {#each work.tracks as t, i (t.id + ':' + i)}
        <TrackRow
          track={t}
          movement
          showAlbum={false}
          current={t.id === currentId}
          onplay={() => player.playList(work!.tracks, i, `Work: ${work!.title}`, meta)}
          onmenu={() => ui.open({ kind: 'songMenu', ctx: { track: t, list: work!.tracks } })}
        />
      {/each}
    </div>
  {:else}<div class="empty">Loading…</div>{/if}
</div>

<style>
  .head {
    margin: 10px clamp(16px, 3vw, 32px) 6px;
  }
  h1 {
    margin: 6px 0 2px;
    font-size: clamp(1.6rem, 3vw, 2.2rem);
  }
  .works {
    display: grid;
    margin: 8px 0 16px;
  }
  .wk {
    display: flex;
    gap: 12px;
    align-items: center;
    text-align: left;
    padding: 10px clamp(16px, 3vw, 32px);
  }
  .wk:hover {
    background: rgba(255, 255, 255, 0.04);
  }
  .art {
    width: 48px;
    flex: none;
  }
  .txt {
    display: grid;
    gap: 2px;
    min-width: 0;
  }
  .t {
    font-weight: 600;
  }
</style>
