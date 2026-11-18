<script lang="ts">
  import { ArrowLeft, ListEnd, Play, Shuffle } from '@lucide/svelte';
  import Cover from '../components/Cover.svelte';
  import TrackList from '../components/TrackList.svelte';
  import { fmtTime } from '../lib/format';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { Album, Track } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { id }: { id: string } = $props();
  let album = $state<(Album & { tracks: Track[] }) | null>(null);
  let error = $state<string | null>(null);
  $effect(() => {
    session.api.album(id).then((a) => (album = a)).catch((e) => (error = e.message));
  });
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>{album?.title ?? ''}</h1>
  </div>
  {#if error}<div class="empty">{error}</div>
  {:else if album}
    <div class="hero">
      <div class="art"><Cover albumId={album.id} size={512} /></div>
      <div class="info">
        <h2>{album.title}</h2>
        <button class="artist" onclick={() => ui.go('artist', album!.artistId)}>{album.artist}</button>
        <div class="muted">{album.year ?? ''} · {album.tracks.length} songs · {fmtTime(album.tracks.reduce((s, t) => s + t.durationS, 0))}</div>
        <div class="btns">
          <button class="btn primary" onclick={() => player.playList(album!.tracks, 0, `Album: ${album!.title}`, { source: 'library', addedFrom: 'album' })}><Play size={18} fill="currentColor" /> Play</button>
          <button class="btn" onclick={() => { if (!player.shuffle) player.toggleShuffle(); void player.playList(album!.tracks, Math.floor(Math.random() * album!.tracks.length), `Album: ${album!.title}`, { source: 'library', addedFrom: 'album' }); }}><Shuffle size={18} /> Shuffle</button>
          <button class="btn" onclick={() => player.addToQueue(album!.tracks, 'end')}><ListEnd size={18} /> Add to queue</button>
        </div>
      </div>
    </div>
    <TrackList tracks={album.tracks} queueName="Album: {album.title}" meta={{ source: 'library', addedFrom: 'album' }} showAlbum={false} />
  {:else}<div class="empty">Loading…</div>{/if}
</div>

<style>
  .hero {
    display: flex;
    gap: 20px;
    padding: 16px;
    flex-wrap: wrap;
  }
  .art {
    width: min(220px, 45vw);
  }
  .info {
    flex: 1;
    min-width: 220px;
    display: grid;
    gap: 6px;
    align-content: end;
  }
  h2 {
    margin: 0;
  }
  .artist {
    text-align: left;
    color: var(--fg-2);
    font-size: 1.05rem;
  }
  .btns {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 8px;
  }
</style>
