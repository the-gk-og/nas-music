<script lang="ts">
  // Desktop main-area bar: back/forward, global search (/ or Ctrl+K), avatar.
  import { Bell, ChevronLeft, ChevronRight, Search } from '@lucide/svelte';
  import { notifications } from '../lib/notifications.svelte';
  import { appearance } from '../lib/appearance.svelte';
  import { session } from '../lib/session.svelte';
  import { ui } from '../lib/ui.svelte';
  import Avatar from './Avatar.svelte';

  let { solid = false }: { solid?: boolean } = $props();
</script>

<div class="topbar-v3" class:solid>
  {#if appearance.form === 'desktop'}
    <button class="round" aria-label="Back" onclick={() => history.back()}><ChevronLeft size={20} /></button>
    <button class="round" aria-label="Forward" onclick={() => history.forward()}><ChevronRight size={20} /></button>
  {/if}
  <button class="search" onclick={() => ui.focusSearch()} aria-label="Search">
    <Search size={18} /><span>What do you want to play?</span><kbd>Ctrl K</kbd>
  </button>
  <span class="sp"></span>
  <button class="round bell" aria-label={notifications.unread ? `Notifications (${notifications.unread} new)` : 'Notifications'} title="New music" onclick={() => ui.go('new')}>
    <Bell size={18} />{#if notifications.unread}<span class="badge">{notifications.unread > 9 ? '9+' : notifications.unread}</span>{/if}
  </button>
  <button class="me" aria-label="Profile and settings" onclick={() => ui.open({ kind: 'profile' })}><Avatar name={session.saved.username ?? '?'} size={32} /></button>
</div>

<style>
  .topbar-v3 {
    position: sticky;
    top: 0;
    z-index: 4;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px var(--gutter, 16px);
    transition: background 0.2s;
  }
  .topbar-v3.solid {
    background: rgba(0, 0, 0, 0.85);
    backdrop-filter: blur(10px);
  }
  .round {
    width: 34px;
    height: 34px;
    border-radius: 50%;
    background: rgba(0, 0, 0, 0.6);
    display: grid;
    place-items: center;
  }
  .search {
    display: flex;
    align-items: center;
    gap: 10px;
    background: var(--bg-2);
    border-radius: 999px;
    padding: 9px 16px;
    color: var(--fg-3);
    min-width: 0;
    flex: 0 1 420px;
    border: 1px solid transparent;
  }
  .search:hover {
    border-color: #555;
  }
  .search span {
    flex: 1;
    text-align: left;
    overflow: hidden;
    white-space: nowrap;
    text-overflow: ellipsis;
  }
  kbd {
    font: inherit;
    font-size: 0.72rem;
    border: 1px solid #444;
    border-radius: 4px;
    padding: 1px 5px;
  }
  @media (max-width: 599px) {
    kbd {
      display: none;
    }
  }
  .sp {
    flex: 1;
  }
  .bell {
    position: relative;
  }
  .badge {
    position: absolute;
    top: -3px;
    right: -3px;
    min-width: 16px;
    height: 16px;
    padding: 0 4px;
    border-radius: 8px;
    background: var(--accent, #1db954);
    color: #000;
    font-size: 0.66rem;
    font-weight: 700;
    line-height: 16px;
  }
</style>
