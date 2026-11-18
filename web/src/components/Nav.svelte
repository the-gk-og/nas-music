<script lang="ts">
  // Navigation for every form (§2, §6): phone bottom tabs, tablet rail, desktop sidebar with playlists.
  import { Compass, House, Library, ListMusic, Plus, Search } from '@lucide/svelte';
  import { appearance } from '../lib/appearance.svelte';
  import { library } from '../lib/library.svelte';
  import { session } from '../lib/session.svelte';
  import { TABS, ui, type TabId } from '../lib/ui.svelte';
  import Avatar from './Avatar.svelte';

  const ICONS: Record<TabId, typeof House> = { home: House, foryou: Compass, search: Search, library: Library, queue: ListMusic };
  const form = $derived(appearance.form);
  const tabs = $derived(form === 'desktop' ? TABS.filter((t) => t.id !== 'queue') : TABS);

  async function newPlaylist() {
    const name = await ui.prompt('New playlist', 'Name', '', 'Create');
    if (!name) return;
    try {
      const p = await session.api.createPlaylist(name);
      await library.refreshPlaylists();
      ui.go('playlist', p.id);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
</script>

<nav class="nav {form}" aria-label="Main">
  {#if form === 'desktop'}<div class="brand"><span class="ring"></span>nas-music</div>{/if}
  <div class="dests">
    {#each tabs as t (t.id)}
      {@const Icon = ICONS[t.id]}
      <button
        class="tab"
        class:active={ui.tab === t.id}
        aria-current={ui.tab === t.id ? 'page' : undefined}
        aria-label={t.label}
        onclick={() => (t.id === 'search' ? ui.focusSearch() : ui.go(t.id))}
      >
        <Icon size={form === 'desktop' ? 22 : 24} strokeWidth={ui.tab === t.id ? 2.5 : 1.9} />
        <span class="label">{t.label}</span>
      </button>
    {/each}
  </div>
  {#if form === 'desktop'}
    <div class="lists">
      <div class="lh">
        <span>Your playlists</span>
        <button class="icon-btn sm" aria-label="New playlist" title="New playlist" onclick={newPlaylist}><Plus size={18} /></button>
      </div>
      <div class="scroll">
        <button class="pl" class:active={ui.route.name === 'list' && ui.route.param === 'favourites'} onclick={() => ui.go('list', 'favourites')}>Favorites</button>
        {#each library.playlists as p (p.id)}
          <button class="pl ellipsis" class:active={ui.route.name === 'playlist' && ui.route.param === p.id} onclick={() => ui.go('playlist', p.id)}>{p.name}</button>
        {/each}
      </div>
    </div>
  {/if}
  {#if form !== 'phone'}
    <button class="me" aria-label="Profile and settings" onclick={() => ui.open({ kind: 'profile' })}>
      <Avatar name={session.saved.username ?? '?'} />
      {#if form === 'desktop'}<span class="ellipsis">{session.saved.username}</span>{/if}
    </button>
  {/if}
</nav>

<style>
  .nav {
    background: var(--bg);
    display: flex;
  }
  .tab {
    display: grid;
    place-items: center;
    color: #9a9a9a;
    position: relative;
  }
  .tab.active {
    color: #fff;
  }
  .label {
    font-size: 0.72rem;
  }

  /* phone: bottom tabs */
  .phone {
    border-top: 1px solid var(--line);
    height: calc(var(--tabbar-h) + env(safe-area-inset-bottom));
    padding-bottom: env(safe-area-inset-bottom);
  }
  .phone .dests {
    display: flex;
    flex: 1;
  }
  .phone .tab {
    flex: 1;
    gap: 2px;
    align-content: center;
  }
  .phone .tab.active::before {
    content: '';
    position: absolute;
    top: -1px;
    left: 25%;
    right: 25%;
    height: 3px;
    border-radius: 0 0 3px 3px;
    background: var(--accent);
  }

  /* tablet: navigation rail */
  .tablet {
    flex-direction: column;
    width: 88px;
    border-right: 1px solid var(--line);
    padding: 16px 0;
    align-items: center;
  }
  .tablet .dests {
    display: grid;
    gap: 10px;
    flex: 1;
  }
  .tablet .tab {
    width: 72px;
    padding: 6px 0;
    gap: 4px;
  }
  .tablet .tab :global(svg) {
    padding: 4px 16px;
    box-sizing: content-box;
    border-radius: 16px;
  }
  .tablet .tab.active :global(svg) {
    background: color-mix(in srgb, var(--accent) 25%, transparent);
    color: var(--accent);
  }

  /* desktop: sidebar */
  .desktop {
    flex-direction: column;
    width: var(--sidebar-w);
    padding: 12px 8px;
    gap: 8px;
    border-right: 1px solid var(--line);
  }
  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
    font-weight: 800;
    font-size: 1.2rem;
    padding: 8px 14px 12px;
  }
  .ring {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    border: 3.5px solid var(--accent);
  }
  .desktop .dests {
    display: grid;
    gap: 2px;
    background: var(--bg-1);
    border-radius: 10px;
    padding: 6px;
  }
  .desktop .tab {
    display: flex;
    justify-content: flex-start;
    gap: 16px;
    padding: 10px 12px;
    border-radius: 8px;
    font-weight: 600;
  }
  .desktop .tab:hover {
    color: #fff;
  }
  .desktop .tab.active {
    background: rgba(255, 255, 255, 0.07);
  }
  .desktop .label {
    font-size: 0.98rem;
  }
  .lists {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    background: var(--bg-1);
    border-radius: 10px;
    padding: 8px 6px;
  }
  .lh {
    display: flex;
    align-items: center;
    justify-content: space-between;
    color: var(--fg-3);
    font-weight: 700;
    padding: 0 8px;
  }
  .sm {
    width: 32px;
    height: 32px;
  }
  .scroll {
    overflow-y: auto;
    display: grid;
    align-content: start;
  }
  .pl {
    text-align: left;
    padding: 8px 10px;
    border-radius: 6px;
    color: var(--fg-2);
  }
  .pl:hover {
    background: rgba(255, 255, 255, 0.05);
    color: #fff;
  }
  .pl.active {
    color: var(--accent);
  }
  .me {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 8px;
    border-radius: 999px;
    max-width: 100%;
  }
  .me:hover {
    background: rgba(255, 255, 255, 0.06);
  }
</style>
