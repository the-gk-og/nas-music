<script lang="ts">
  import { ArrowLeft } from '@lucide/svelte';
  import { session } from '../lib/session.svelte';
  import type { MyDevice } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let devices = $state<MyDevice[] | null>(null);
  let current = $state('');
  let next = $state('');
  let next2 = $state('');
  let msg = $state<string | null>(null);
  let pair = $state<{ pairCode: string; qrPayload: string } | null>(null);

  function load() {
    session.api.myDevices().then((d) => (devices = d)).catch(() => (devices = []));
  }
  $effect(load);

  async function changePw(e: Event) {
    e.preventDefault();
    msg = null;
    if (next.length < 8) return (msg = 'New password must be at least 8 characters.');
    if (next !== next2) return (msg = 'Passwords do not match.');
    try {
      await session.api.changePassword(current, next);
      current = next = next2 = '';
      ui.toast('Password changed');
    } catch (err) {
      msg = (err as Error).message;
    }
  }

  async function remove(d: MyDevice) {
    if (!(await ui.confirm('Remove device', `Sign out "${d.name}"?`, 'Remove'))) return;
    await session.api.removeDevice(d.id).catch((e) => ui.toast(e.message));
    load();
  }
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>Account</h1>
  </div>
  <div class="pad"><b>{session.saved.username}</b>{#if session.isAdmin}<span class="muted"> · admin</span>{/if}</div>

  <div class="section-title">Your devices</div>
  {#each devices ?? [] as d (d.id)}
    <div class="dev">
      <div>
        <div>{d.name}{#if d.id === session.saved.deviceId}<span class="muted"> (this device)</span>{/if}</div>
        <div class="muted small">{d.kind}{d.online ? ' · online' : d.lastSeen ? ` · last seen ${new Date(d.lastSeen).toLocaleString()}` : ''}</div>
      </div>
      {#if d.id !== session.saved.deviceId}<button class="btn danger" onclick={() => remove(d)}>Remove</button>{/if}
    </div>
  {/each}
  <div class="pad">
    <button class="btn" onclick={async () => (pair = await session.api.pairCode().catch((e) => (ui.toast(e.message), null)))}>Pair a phone with a code</button>
    {#if pair}<p>Code: <b class="code">{pair.pairCode}</b> <span class="muted">(enter it in the Android app; valid 10 minutes)</span></p>{/if}
  </div>

  <div class="section-title">Change password</div>
  <form class="pad form" onsubmit={changePw}>
    <label>Current password <input type="password" bind:value={current} autocomplete="current-password" required /></label>
    <label>New password <input type="password" bind:value={next} autocomplete="new-password" required /></label>
    <label>Repeat new password <input type="password" bind:value={next2} autocomplete="new-password" required /></label>
    {#if msg}<div class="error">{msg}</div>{/if}
    <div><button class="btn primary" type="submit">Change password</button></div>
  </form>
</div>

<style>
  .pad {
    padding: 8px 18px;
  }
  .dev {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 10px 18px;
    border-bottom: 1px solid var(--line);
  }
  .small {
    font-size: 0.85rem;
  }
  .code {
    font-family: monospace;
    font-size: 1.3rem;
    letter-spacing: 0.15em;
  }
</style>
