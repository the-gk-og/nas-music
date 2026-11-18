<script lang="ts">
  import { ApiError } from '../lib/api/client';
  import { session, defaultServerAddress } from '../lib/session.svelte';

  let { inviteCode = '', ondone }: { inviteCode?: string; ondone: () => void } = $props();

  let address = $state(session.lastAddress || defaultServerAddress());
  let username = $state('');
  let password = $state('');
  let password2 = $state('');
  let code = $state('');
  let joining = $state(false);
  let deviceName = $state(session.saved.deviceName);
  let busy = $state(false);
  let error = $state<string | null>(null);

  $effect(() => {
    if (inviteCode) {
      code = inviteCode;
      joining = true;
    }
  });

  function explain(e: unknown): string {
    if (e instanceof ApiError) {
      if (e.status === 401) return 'Wrong username or password.';
      if (e.status === 403) return joining ? 'That invite code is invalid, used or expired.' : e.detail || 'Not allowed.';
      if (e.status === 409) return 'That username is taken.';
      if (e.status === 429) return 'Too many tries. Wait a minute and try again.';
      if (e.status === 404) return 'This server does not support accounts yet (it is still on v1).';
      return e.detail || `Error ${e.status}`;
    }
    return (e as Error).message;
  }

  async function submit(ev: Event) {
    ev.preventDefault();
    error = null;
    if (joining) {
      if (!/^[A-Za-z0-9_.-]{3,32}$/.test(username)) return (error = 'Username: 3–32 characters, letters, digits, _ . -');
      if (password.length < 8) return (error = 'Password must be at least 8 characters.');
      if (password !== password2) return (error = 'Passwords do not match.');
    }
    busy = true;
    try {
      session.setDeviceName(deviceName.trim() || session.saved.deviceName);
      if (joining) await session.register(address, code.trim(), username.trim(), password, deviceName);
      else await session.login(address, username.trim(), password, deviceName);
      if (location.pathname.startsWith('/join/')) history.replaceState(null, '', '/#/now');
      ondone();
    } catch (e) {
      error = explain(e);
    } finally {
      busy = false;
    }
  }
</script>

<div class="wrap">
  <form class="card form" onsubmit={submit}>
    <div class="logo"><span class="ring"></span><h1>nas-music</h1></div>
    <p class="muted">{joining ? 'Create your account with an invite code.' : 'Connect to your music server.'}</p>
    <label>Server address
      <input bind:value={address} placeholder="192.168.68.2:8443 or https://music.example.com" autocomplete="url" required />
    </label>
    {#if joining}
      <label>Invite code <input bind:value={code} required autocomplete="off" /></label>
    {/if}
    <label>Username <input bind:value={username} required autocomplete="username" /></label>
    <label>Password
      <input type="password" bind:value={password} required autocomplete={joining ? 'new-password' : 'current-password'} />
    </label>
    {#if joining}
      <label>Repeat password <input type="password" bind:value={password2} required autocomplete="new-password" /></label>
    {/if}
    <label>This device's name <input bind:value={deviceName} /></label>
    {#if error}<div class="error" role="alert">{error}</div>{/if}
    <button class="btn primary" type="submit" disabled={busy}>{busy ? 'Connecting…' : joining ? 'Create account' : 'Sign in'}</button>
    <button class="btn text" type="button" onclick={() => ((joining = !joining), (error = null))}>
      {joining ? 'I already have an account' : 'Join with invite code'}
    </button>
    <p class="hint muted">
      Tip: type <code>mock</code> as the address to try the player with a built-in demo library
      (admin / admin123).
    </p>
  </form>
</div>

<style>
  .wrap {
    min-height: 100%;
    display: grid;
    place-items: center;
    padding: 24px 16px;
  }
  form {
    width: min(420px, 100%);
  }
  .logo {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .ring {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    border: 5px solid var(--accent);
  }
  h1 {
    margin: 0;
    font-size: 1.6rem;
  }
  .hint {
    font-size: 0.8rem;
    margin: 0;
  }
  .btn {
    justify-content: center;
  }
</style>
