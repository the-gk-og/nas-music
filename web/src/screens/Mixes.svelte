<script lang="ts">
  import { ArrowLeft, Sparkles } from '@lucide/svelte';
  import Cover from '../components/Cover.svelte';
  import { fmtTime } from '../lib/format';
  import { session } from '../lib/session.svelte';
  import type { Mix } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let mixes = $state<Mix[] | null>(null);
  let error = $state<string | null>(null);
  $effect(() => {
    session.api.mixesToday().then((m) => (mixes = m)).catch((e) => (error = e.message));
  });
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>Mixes for you</h1>
  </div>
  {#if error}<div class="empty">{error}</div>
  {:else if !mixes}<div class="empty">Loading…</div>
  {:else}
    <div class="grid">
      {#each mixes as m (m.id)}
        <button class="mix" onclick={() => ui.go('mix', m.id)}>
          <div class="covers">
            {#each m.tracks.slice(0, 4) as t}<Cover albumId={t.track.albumId} size={128} rounded={0} />{/each}
          </div>
          <div class="t ellipsis">{m.title}</div>
          <div class="muted small">{m.tracks.length} songs · {fmtTime(m.durationS ?? 0)}</div>
        </button>
      {:else}
        <div class="empty"><Sparkles size={32} /><br />No mixes yet. They're built every night from your listening.</div>
      {/each}
    </div>
  {/if}
</div>

<style>
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
    gap: 14px;
    padding: 14px;
  }
  .mix {
    text-align: left;
  }
  .covers {
    display: grid;
    grid-template-columns: 1fr 1fr;
    border-radius: 10px;
    overflow: hidden;
  }
  .t {
    margin-top: 8px;
    font-weight: 600;
  }
  .small {
    font-size: 0.85rem;
  }
</style>
