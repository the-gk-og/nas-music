<script lang="ts">
  import { CirclePlay, Disc3, EllipsisVertical, Folder, LibraryBig, ListMusic, Search, Tag, User } from '@lucide/svelte';
  import { TABS, ui, type TabId } from '../lib/ui.svelte';

  const ICONS: Record<TabId, typeof ListMusic> = {
    queues: ListMusic,
    now: CirclePlay,
    folders: Folder,
    albums: Disc3,
    artists: User,
    genres: Tag,
    playlists: LibraryBig,
    search: Search,
    more: EllipsisVertical,
  };
</script>

<nav class="nav" aria-label="Main">
  <div class="brand"><span class="dot"></span>nas-music</div>
  {#each TABS as t (t.id)}
    {@const Icon = ICONS[t.id]}
    <button class="tab" class:active={ui.tab === t.id} aria-label={t.label} title={t.label} aria-current={ui.tab === t.id ? 'page' : undefined} onclick={() => ui.go(t.id)}>
      <Icon size={26} strokeWidth={ui.tab === t.id ? 2.4 : 1.9} />
      <span class="label">{t.label}</span>
    </button>
  {/each}
</nav>

<style>
  .nav {
    display: flex;
    background: var(--bg-1);
    border-top: 1px solid var(--line);
    height: var(--tabbar-h);
    padding-bottom: env(safe-area-inset-bottom);
  }
  .brand {
    display: none;
  }
  .tab {
    flex: 1;
    display: grid;
    place-items: center;
    color: #9a9a9a;
    position: relative;
  }
  .tab.active {
    color: #fff;
  }
  .tab.active::before {
    content: '';
    position: absolute;
    top: -1px;
    left: 0;
    right: 0;
    height: 3px;
    background: var(--accent);
  }
  .label {
    display: none;
  }

  /* Wide screens: a left sidebar of tabs. */
  @media (min-width: 900px) {
    .nav {
      flex-direction: column;
      width: var(--sidebar-w);
      height: 100%;
      border-top: 0;
      border-right: 1px solid var(--line);
      padding: 12px 0;
      gap: 2px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      font-weight: 700;
      font-size: 1.15rem;
      padding: 8px 20px 18px;
    }
    .dot {
      width: 14px;
      height: 14px;
      border-radius: 50%;
      border: 3px solid var(--accent);
    }
    .tab {
      flex: none;
      display: flex;
      gap: 16px;
      justify-content: flex-start;
      padding: 10px 20px;
      font-size: 0.98rem;
    }
    .tab :global(svg) {
      width: 22px;
      height: 22px;
    }
    .tab.active {
      background: rgba(255, 255, 255, 0.05);
    }
    .tab.active::before {
      top: 6px;
      bottom: 6px;
      left: 0;
      right: auto;
      width: 3px;
      height: auto;
    }
    .label {
      display: inline;
    }
  }
</style>
