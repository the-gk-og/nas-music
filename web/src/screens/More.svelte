<script lang="ts">
  import { Compass, Settings, Shield, SlidersVertical, Sparkles, Speaker, UserRound, LogOut } from '@lucide/svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import { ui } from '../lib/ui.svelte';

  async function logout() {
    if (!(await ui.confirm('Sign out', 'Sign out of this device?', 'Sign out'))) return;
    player.reset();
    await session.logout();
    location.hash = '';
    location.reload();
  }
</script>

<div class="page">
  <div class="topbar"><h1>More</h1></div>
  <button class="row-item" onclick={() => ui.go('settings')}><Settings size={24} />Settings</button>
  <button class="row-item" onclick={() => ui.go('equalizer')}><SlidersVertical size={24} />Equalizer</button>
  <button class="row-item" onclick={() => ui.open({ kind: 'devices' })}><Speaker size={24} />Devices</button>
  <button class="row-item" onclick={() => ui.go('mixes')}><Sparkles size={24} />Mixes for you</button>
  <button class="row-item" onclick={() => ui.go('discover')}><Compass size={24} />Discover</button>
  <button class="row-item" onclick={() => ui.go('account')}><UserRound size={24} />Account <span class="muted">· {session.saved.username}</span></button>
  {#if session.isAdmin}
    <button class="row-item" onclick={() => ui.go('admin')}><Shield size={24} />Admin</button>
  {/if}
  <button class="row-item" onclick={logout}><LogOut size={24} />Sign out</button>
  <p class="muted keys">
    Keyboard: Space play/pause · ←/→ seek 5 s · Shift+←/→ previous/next · L lyrics · M mute · S shuffle · R repeat · / search
  </p>
</div>

<style>
  .keys {
    padding: 16px 20px;
    font-size: 0.85rem;
  }
</style>
