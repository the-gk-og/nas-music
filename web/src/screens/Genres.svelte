<script lang="ts">
  import { Tag } from '@lucide/svelte';
  import { session } from '../lib/session.svelte';
  import { ui } from '../lib/ui.svelte';

  let genres = $state<{ name: string; trackCount?: number }[] | null>(null);
  let error = $state<string | null>(null);
  $effect(() => {
    session.api
      .genres()
      .then((g) => (genres = g.map((x) => (typeof x === 'string' ? { name: x } : x))))
      .catch((e) => (error = e.message));
  });
</script>

<div class="page">
  <div class="topbar"><h1>Genres</h1></div>
  {#if error}<div class="empty">{error}</div>
  {:else if !genres}<div class="empty">Loading…</div>
  {:else}
    {#each genres as g (g.name)}
      <button class="row-item" onclick={() => ui.go('genre', g.name)}>
        <Tag size={24} />
        <div><div>{g.name}</div>{#if g.trackCount != null}<div class="sub">{g.trackCount} songs</div>{/if}</div>
      </button>
    {:else}<div class="empty">No genres</div>{/each}
  {/if}
</div>
