<script lang="ts">
  import { Ban, Flame, Heart, History, ListMusic, ClockPlus, LibraryBig, Plus } from '@lucide/svelte';
  import { session } from '../lib/session.svelte';
  import type { Playlist } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  const SMART = [
    { k: 'all', label: 'All songs', icon: LibraryBig },
    { k: 'favourites', label: 'Favorites', icon: Heart },
    { k: 'recently_added', label: 'Recently added', icon: ClockPlus },
    { k: 'recently_played', label: 'Recently played', icon: History },
    { k: 'most_played', label: 'Most played', icon: Flame },
    { k: 'not_played', label: 'Not played', icon: Ban },
  ];
  let lists = $state<Playlist[] | null>(null);
  let error = $state<string | null>(null);
  function load() {
    session.api.playlists().then((p) => (lists = p)).catch((e) => (error = e.message));
  }
  $effect(load);

  async function create() {
    const name = await ui.prompt('New playlist', 'Name', '', 'Create');
    if (!name) return;
    try {
      const p = await session.api.createPlaylist(name);
      ui.go('playlist', p.id);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
</script>

<div class="page">
  {#each SMART as s (s.k)}
    {@const Icon = s.icon}
    <button class="row-item big" onclick={() => ui.go('list', s.k)}><Icon size={30} fill={s.k === 'favourites' ? 'currentColor' : 'none'} />{s.label}</button>
  {/each}
  <div class="section-title">Your playlists</div>
  {#if error}<div class="muted pad">{error}</div>{/if}
  {#each lists ?? [] as p (p.id)}
    <button class="row-item big" onclick={() => ui.go('playlist', p.id)}>
      <ListMusic size={28} />
      <div><div>{p.name}</div>{#if p.trackCount != null}<div class="sub">{p.trackCount} songs</div>{/if}</div>
    </button>
  {/each}
  <div class="pad"><button class="btn" onclick={create}><Plus size={20} color="var(--accent)" /> New Playlist</button></div>
</div>

<style>
  .big {
    font-size: 1.2rem;
    padding: 14px 22px;
    gap: 24px;
  }
  .pad {
    padding: 12px 22px;
  }
</style>
