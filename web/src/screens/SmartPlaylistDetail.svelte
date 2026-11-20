<script lang="ts">
  // v7 smart playlist: rule summary, the (server-evaluated, auto-updating) tracks, edit / delete.
  import { Pencil, RefreshCw, Trash2 } from '@lucide/svelte';
  import PageHeader from '../components/PageHeader.svelte';
  import TrackList from '../components/TrackList.svelte';
  import { library } from '../lib/library.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import { describeDefinition, fmtDuration } from '../lib/smart';
  import type { ShelfItem, SmartFieldSpec, SmartPlaylist } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { id }: { id: string } = $props();
  let sp = $state<SmartPlaylist | null>(null);
  let fields = $state<SmartFieldSpec[]>([]);
  let error = $state<string | null>(null);

  function load() {
    session.api.smartPlaylist(id).then((p) => (sp = p)).catch((e) => (error = e.message));
  }
  $effect(load);
  $effect(() => {
    session.api.smartFields().then((f) => (fields = f.fields)).catch(() => {});
  });

  const tracks = $derived(sp?.tracks ?? []);
  const meta = { source: 'library' as const, addedFrom: 'smart_playlist' };
  const name = $derived(`Smart playlist: ${sp?.name ?? ''}`);
  const item = $derived<ShelfItem>({ type: 'smart_playlist', id, title: sp?.name ?? '', artIds: tracks.slice(0, 4).map((t) => t.albumId), albumId: tracks[0]?.albumId ?? null });

  async function del() {
    if (!(await ui.confirm('Delete smart playlist', `Delete "${sp?.name}"? The songs stay in your library.`, 'Delete'))) return;
    await session.api.deleteSmartPlaylist(id).catch((e) => ui.toast(e.message));
    void library.refreshSmart();
    void library.refreshPins();
    ui.go('library', 'playlists');
  }
  async function refresh() {
    try {
      sp = await session.api.post<SmartPlaylist>(`/smart-playlists/${encodeURIComponent(id)}/refresh`);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
</script>

<div class="page smart">
  {#if error}<div class="empty">{error}</div>
  {:else if sp}
    <PageHeader
      {item}
      kind="smart_playlist"
      label="Smart playlist"
      meta="{sp.trackCount} songs · {fmtDuration(sp.durationS)}"
      onplay={tracks.length ? () => player.playList(tracks, 0, name, meta) : undefined}
      onshuffle={tracks.length
        ? () => {
            if (!player.shuffle) player.toggleShuffle();
            void player.playList(tracks, Math.floor(Math.random() * tracks.length), name, meta);
          }
        : undefined}
      onradio={tracks.length ? () => player.startRadio({ type: 'smart_playlist', id }, `${sp!.name} radio`) : undefined}
    >
      {#snippet menu()}
        <button role="menuitem" onclick={() => ui.go('smart-edit', id)}><Pencil size={18} /> Edit rules</button>
        <button role="menuitem" onclick={refresh}><RefreshCw size={18} /> Refresh now</button>
        <button role="menuitem" onclick={del}><Trash2 size={18} /> Delete</button>
      {/snippet}
    </PageHeader>
    <div class="rules">
      <span>{fields.length ? describeDefinition(sp.definition, fields) : ''}</span>
      <button class="btn text" onclick={() => ui.go('smart-edit', id)}><Pencil size={16} /> Edit</button>
    </div>
    {#if tracks.length}
      <TrackList {tracks} queueName={name} {meta} />
    {:else}
      <div class="empty">No songs match these rules right now. The list updates by itself as you listen.</div>
    {/if}
  {:else}<div class="empty">Loading…</div>{/if}
</div>

<style>
  .rules {
    --gutter: clamp(16px, 3vw, 32px);
    display: flex;
    gap: 12px;
    align-items: center;
    justify-content: space-between;
    margin: 4px var(--gutter) 12px;
    color: var(--fg-2);
    font-size: 0.9rem;
  }
  .rules button {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    flex: none;
  }
</style>
