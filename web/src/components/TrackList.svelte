<script lang="ts">
  import { player } from '../lib/player.svelte';
  import type { PlayMeta } from '../lib/events';
  import type { Track } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import TrackRow from './TrackRow.svelte';

  let {
    tracks,
    queueName,
    meta = {},
    showAlbum = true,
  }: { tracks: Track[]; queueName: string; meta?: PlayMeta; showAlbum?: boolean } = $props();

  const currentId = $derived(player.shown.track?.id);
</script>

<div role="list">
  {#each tracks as t, i (t.id + ':' + i)}
    <TrackRow
      track={t}
      {showAlbum}
      current={t.id === currentId}
      onplay={() => player.playList(tracks, i, queueName, meta)}
      onmenu={() => ui.open({ kind: 'songMenu', ctx: { track: t, list: tracks } })}
    />
  {:else}
    <div class="empty">No songs</div>
  {/each}
</div>
