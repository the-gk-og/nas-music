<script lang="ts">
  import { ArrowLeft, Ellipsis, Play, X } from '@lucide/svelte';
  import TrackRow from '../components/TrackRow.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { Playlist } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { id }: { id: string } = $props();
  let pl = $state<Playlist | null>(null);
  let error = $state<string | null>(null);
  let menu = $state(false);
  function load() {
    session.api.playlist(id).then((p) => (pl = p)).catch((e) => (error = e.message));
  }
  $effect(load);

  const tracks = $derived(pl?.tracks ?? []);
  const currentId = $derived(player.shown.track?.id);

  async function rename() {
    menu = false;
    const name = await ui.prompt('Rename playlist', 'Name', pl?.name ?? '', 'Rename');
    if (!name) return;
    await session.api.renamePlaylist(id, name).catch((e) => ui.toast(e.message));
    load();
  }
  async function del() {
    menu = false;
    if (!(await ui.confirm('Delete playlist', `Delete "${pl?.name}"? The songs stay in your library.`, 'Delete'))) return;
    await session.api.deletePlaylist(id).catch((e) => ui.toast(e.message));
    ui.go('playlists');
  }
  async function removeAt(i: number) {
    await session.api.removeFromPlaylist(id, i).catch((e) => ui.toast(e.message));
    load();
  }
  async function move(i: number, d: number) {
    const to = i + d;
    if (to < 0 || to >= tracks.length) return;
    await session.api.movePlaylistTrack(id, i, to).catch((e) => ui.toast(e.message));
    load();
  }
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>{pl?.name ?? ''}</h1>
    {#if tracks.length}<button class="icon-btn" aria-label="Play" onclick={() => player.playList(tracks, 0, `Playlist: ${pl!.name}`, { source: 'library', addedFrom: 'playlist' })}><Play size={24} fill="currentColor" /></button>{/if}
    <div class="rel">
      <button class="icon-btn" aria-label="Playlist options" onclick={() => (menu = !menu)}><Ellipsis size={24} /></button>
      {#if menu}
        <div class="menu"><button onclick={rename}>Rename</button><button onclick={del}>Delete playlist</button></div>
      {/if}
    </div>
  </div>
  {#if error}<div class="empty">{error}</div>
  {:else if !pl}<div class="empty">Loading…</div>
  {:else}
    {#each tracks as t, i (t.id + ':' + i)}
      <div class="r">
        <div class="grow">
          <TrackRow
            track={t}
            current={t.id === currentId}
            onplay={() => player.playList(tracks, i, `Playlist: ${pl!.name}`, { source: 'library', addedFrom: 'playlist' })}
            onmenu={() => ui.open({ kind: 'songMenu', ctx: { track: t, list: tracks } })}
          />
        </div>
        <div class="ord">
          <button aria-label="Move up" disabled={i === 0} onclick={() => move(i, -1)}>▲</button>
          <button aria-label="Move down" disabled={i === tracks.length - 1} onclick={() => move(i, 1)}>▼</button>
        </div>
        <button class="icon-btn" aria-label="Remove from playlist" onclick={() => removeAt(i)}><X size={18} /></button>
      </div>
    {:else}<div class="empty">Empty playlist. Use “Add to playlists” in a song's menu.</div>{/each}
  {/if}
</div>

<style>
  .r {
    display: flex;
    align-items: center;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .ord {
    display: grid;
    font-size: 0.7rem;
    color: var(--fg-3);
  }
  .ord button {
    padding: 2px 6px;
  }
  .rel {
    position: relative;
  }
  .menu {
    position: absolute;
    right: 0;
    top: 46px;
    z-index: 5;
    background: var(--bg-2);
    border-radius: 10px;
    padding: 6px 0;
    min-width: 180px;
  }
  .menu button {
    display: block;
    width: 100%;
    padding: 10px 16px;
    text-align: left;
  }
</style>
