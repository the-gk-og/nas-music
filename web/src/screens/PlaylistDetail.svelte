<script lang="ts">
  import { Pencil, Trash2, Users, X } from '@lucide/svelte';
  import PageHeader from '../components/PageHeader.svelte';
  import { fmtTime } from '../lib/format';
  import type { ShelfItem } from '../lib/types';
  import TrackRow from '../components/TrackRow.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { Playlist } from '../lib/types';
  import { library } from '../lib/library.svelte';
  import { ui } from '../lib/ui.svelte';
  import { selection } from '../lib/selection.svelte';

  let { id }: { id: string } = $props();
  let pl = $state<Playlist | null>(null);
  let error = $state<string | null>(null);
  function load() {
    session.api.playlist(id).then((p) => (pl = p)).catch((e) => (error = e.message));
  }
  $effect(load);
  // v5: live updates from other members (Connect `playlist`), and admin tag edits
  $effect(() => {
    const c = library.changed;
    if (!c || c.id !== id) return;
    if (c.deleted) {
      ui.toast('This playlist was deleted or is no longer shared with you');
      ui.go('library', 'playlists');
    } else if (c.rev == null || c.rev !== pl?.rev) load();
  });
  $effect(() => {
    if (library.tagsRev) load();
  });

  const canEdit = $derived(!pl?.role || pl.role !== 'viewer');
  const isOwner = $derived(!pl?.role || pl.role === 'owner');
  const entries = $derived(pl?.entries ?? []);

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
  // Entry-based edits (v5) keep their meaning if others changed the list meanwhile; index-based fallback
  // for servers without entries. A 409 means our view was stale: just reload.
  async function run(p: Promise<Playlist | void>): Promise<boolean> {
    try {
      const r = await p;
      if (r && 'id' in r) pl = r;
      else load();
      return true;
    } catch (e) {
      ui.toast((e as Error).message);
      load();
      return false;
    }
  }
  function removeAt(i: number) {
    const e = entries[i];
    const t = tracks[i];
    const after = i > 0 ? entries[i - 1]?.id : '';
    void run(e ? session.api.removePlaylistEntry(id, e.id) : session.api.removeFromPlaylist(id, i)).then((ok) => {
      if (!ok || !t) return;
      ui.undoable(`Removed "${t.title}"`, () =>
        run(e && after != null ? session.api.addToPlaylistAfter(id, [t.id], after) : session.api.addToPlaylist(id, [t.id], i)),
      );
    });
  }
  function move(i: number, d: number) {
    const to = i + d;
    if (to < 0 || to >= tracks.length) return;
    const e = entries[i];
    if (!e) return void run(session.api.movePlaylistTrack(id, i, to));
    // after the entry that will precede it: moving up -> the one two above; down -> the next one
    const after = d < 0 ? (to > 0 ? entries[to - 1].id : null) : entries[to].id;
    void run(session.api.movePlaylistEntry(id, e.id, after));
  }
</script>

<div class="page">
  {#if error}<div class="empty">{error}</div>
  {:else if !pl}<div class="empty">Loading…</div>
  {:else}
    <PageHeader
      {item}
      kind="playlist"
      label={pl.role && pl.role !== 'owner' ? `Playlist by ${pl.owner ?? '?'} · you're ${pl.role === 'editor' ? 'an editor' : 'a viewer'}` : pl.collaborative ? 'Collaborative playlist' : 'Playlist'}
      meta="{tracks.length} songs · {fmtTime(tracks.reduce((s, t) => s + t.durationS, 0))}"
      onplay={tracks.length ? () => player.playList(tracks, 0, `Playlist: ${pl!.name}`, { source: 'library', addedFrom: 'playlist' }) : undefined}
      onshuffle={tracks.length ? () => {
        if (!player.shuffle) player.toggleShuffle();
        void player.playList(tracks, Math.floor(Math.random() * tracks.length), `Playlist: ${pl!.name}`, { source: 'library', addedFrom: 'playlist' });
      } : undefined}
      onradio={tracks.length ? () => player.startRadio({ type: 'playlist', id }, `${pl!.name} radio`) : undefined}
    >
      {#snippet menu()}
        <button onclick={() => ui.open({ kind: 'playlistShare', playlistId: id })}><Users size={18} /> {isOwner ? 'Share / members' : 'Members & activity'}</button>
        {#if isOwner}
          <button onclick={rename}><Pencil size={18} /> Rename</button>
          <button onclick={del}><Trash2 size={18} /> Delete playlist</button>
        {/if}
      {/snippet}
    </PageHeader>
    {#each tracks as t, i (entries[i]?.id ?? t.id + ':' + i)}
      <div class="r">
        <div class="grow">
          <TrackRow
            track={t}
            current={t.id === currentId}
            onplay={() => player.playList(tracks, i, `Playlist: ${pl!.name}`, { source: 'library', addedFrom: 'playlist' })}
            onmenu={() => ui.open({ kind: 'songMenu', ctx: { track: t, list: tracks } })}
            selected={selection.active && selection.has(t.id)}
            selecting={selection.active}
            onselect={(range) => selection.pick(tracks, i, range)}
          />
        </div>
        {#if pl.collaborative && entries[i]?.addedBy}<span class="by muted small" title="Added {new Date(entries[i].addedAt).toLocaleString()}">{entries[i].addedBy}</span>{/if}
        {#if canEdit}
          <div class="ord">
            <button aria-label="Move up" disabled={i === 0} onclick={() => move(i, -1)}>▲</button>
            <button aria-label="Move down" disabled={i === tracks.length - 1} onclick={() => move(i, 1)}>▼</button>
          </div>
          <button class="icon-btn" aria-label="Remove from playlist" onclick={() => removeAt(i)}><X size={18} /></button>
        {/if}
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
  .by {
    padding: 0 8px;
    white-space: nowrap;
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
