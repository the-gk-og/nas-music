<script lang="ts">
  import { Pencil, Trash2, X } from '@lucide/svelte';
  import PageHeader from '../components/PageHeader.svelte';
  import { fmtTime } from '../lib/format';
  import type { ShelfItem } from '../lib/types';
  import TrackRow from '../components/TrackRow.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { Playlist } from '../lib/types';
  import { library } from '../lib/library.svelte';
  import { ui } from '../lib/ui.svelte';

  let { id }: { id: string } = $props();
  let pl = $state<Playlist | null>(null);
  let error = $state<string | null>(null);
  function load() {
    session.api.playlist(id).then((p) => (pl = p)).catch((e) => (error = e.message));
  }
  $effect(load);

  const tracks = $derived(pl?.tracks ?? []);
  const currentId = $derived(player.shown.track?.id);
  const item = $derived<ShelfItem>({ type: 'playlist', id, title: pl?.name ?? '', artIds: tracks.slice(0, 4).map((t) => t.albumId), albumId: tracks[0]?.albumId ?? null });

  async function rename() {
    const name = await ui.prompt('Rename playlist', 'Name', pl?.name ?? '', 'Rename');
    if (!name) return;
    await session.api.renamePlaylist(id, name).catch((e) => ui.toast(e.message));
    load();
    void library.refreshPlaylists();
  }
  async function del() {
    if (!(await ui.confirm('Delete playlist', `Delete "${pl?.name}"? The songs stay in your library.`, 'Delete'))) return;
    await session.api.deletePlaylist(id).catch((e) => ui.toast(e.message));
    void library.refreshPlaylists();
    ui.go('library', 'playlists');
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
  {#if error}<div class="empty">{error}</div>
  {:else if !pl}<div class="empty">Loading…</div>
  {:else}
    <PageHeader
      {item}
      kind="playlist"
      label="Playlist"
      meta="{tracks.length} songs · {fmtTime(tracks.reduce((s, t) => s + t.durationS, 0))}"
      onplay={tracks.length ? () => player.playList(tracks, 0, `Playlist: ${pl!.name}`, { source: 'library', addedFrom: 'playlist' }) : undefined}
      onshuffle={tracks.length ? () => {
        if (!player.shuffle) player.toggleShuffle();
        void player.playList(tracks, Math.floor(Math.random() * tracks.length), `Playlist: ${pl!.name}`, { source: 'library', addedFrom: 'playlist' });
      } : undefined}
      onradio={tracks.length ? () => player.startRadio({ type: 'playlist', id }, `${pl!.name} radio`) : undefined}
    >
      {#snippet menu()}
        <button onclick={rename}><Pencil size={18} /> Rename</button>
        <button onclick={del}><Trash2 size={18} /> Delete playlist</button>
      {/snippet}
    </PageHeader>
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
</style>
