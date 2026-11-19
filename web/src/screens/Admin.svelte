<script lang="ts">
  import { ArrowLeft, Copy } from '@lucide/svelte';
  import { session } from '../lib/session.svelte';
  import type { AdminUser, Invite } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let invites = $state<Invite[] | null>(null);
  let users = $state<AdminUser[] | null>(null);
  let note = $state('');
  let days = $state(7);
  let error = $state<string | null>(null);

  function load() {
    session.api.invites().then((i) => (invites = i)).catch((e) => (error = e.message));
    session.api.users().then((u) => (users = u)).catch((e) => (error = e.message));
  }
  $effect(load);

  const inviteUrl = (i: Invite) => i.url ?? `${session.saved.origin || location.origin}/join/${i.code}`;

  async function create(e: Event) {
    e.preventDefault();
    try {
      const inv = await session.api.createInvite(note || undefined, days);
      note = '';
      await navigator.clipboard?.writeText(inviteUrl(inv)).catch(() => {});
      ui.toast('Invite created — link copied');
      load();
    } catch (err) {
      ui.toast((err as Error).message);
    }
  }
  async function revoke(i: Invite) {
    if (!(await ui.confirm('Delete invite', `Delete invite ${i.code}?`, 'Delete'))) return;
    await session.api.deleteInvite(i.code).catch((e) => ui.toast(e.message));
    load();
  }
  async function patch(u: AdminUser, b: { disabled?: boolean; isAdmin?: boolean }) {
    try {
      await session.api.patchUser(u.id, b);
    } catch (e) {
      ui.toast((e as Error).message);
    }
    load();
  }
  const adminCount = $derived((users ?? []).filter((u) => u.isAdmin && !u.disabled).length);

  async function setAdmin(u: AdminUser, on: boolean) {
    if (!on && u.isAdmin && adminCount <= 1) return ui.toast("Can't remove the last admin");
    const self = u.id === session.saved.userId;
    const ok = await ui.confirm(
      on ? 'Make admin' : 'Remove admin',
      on
        ? `${u.username} will be able to create invites, manage users and make other admins.`
        : self
          ? 'You will lose access to this Admin screen.'
          : `${u.username} will no longer be an admin.`,
      on ? 'Make admin' : 'Remove admin',
    );
    if (!ok) return;
    await patch(u, { isAdmin: on });
    if (self && !on) {
      session.saved = { ...session.saved, isAdmin: false };
      ui.go('home');
    }
  }

  async function reset(u: AdminUser) {
    const pw = await ui.prompt(`Reset password for ${u.username}`, 'New password (min 8 characters)', '', 'Reset');
    if (!pw) return;
    if (pw.length < 8) return ui.toast('Password must be at least 8 characters');
    await session.api.resetPassword(u.id, pw).then(() => ui.toast('Password reset')).catch((e) => ui.toast(e.message));
  }
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>Admin</h1>
  </div>
  {#if error}<div class="pad error">{error}</div>{/if}

  <div class="section-title">Invites</div>
  <form class="pad inline" onsubmit={create}>
    <input placeholder="Note (who is it for?)" bind:value={note} aria-label="Note" />
    <label class="muted">Expires in <input type="number" min="1" max="90" bind:value={days} class="days" aria-label="Days" /> days</label>
    <button class="btn primary" type="submit">Create invite</button>
  </form>
  {#each invites ?? [] as i (i.code)}
    <div class="item">
      <div class="grow">
        <div><b class="code">{i.code}</b> {#if i.note}<span class="muted">· {i.note}</span>{/if}</div>
        <div class="muted small">
          {#if i.usedBy}used {i.usedAt ? new Date(i.usedAt).toLocaleDateString() : ''}{:else}expires {new Date(i.expiresAt).toLocaleDateString()}{/if}
        </div>
      </div>
      {#if !i.usedBy}
        <button class="icon-btn" aria-label="Copy link" title={inviteUrl(i)} onclick={() => navigator.clipboard?.writeText(inviteUrl(i)).then(() => ui.toast('Link copied'))}><Copy size={18} /></button>
      {/if}
      <button class="btn danger" onclick={() => revoke(i)}>Delete</button>
    </div>
  {:else}<div class="pad muted">No invites.</div>{/each}

  <div class="section-title">Users</div>
  {#each users ?? [] as u (u.id)}
    <div class="item">
      <div class="grow">
        <div>{u.username}{#if u.id === session.saved.userId}<span class="muted"> (you)</span>{/if}</div>
        <div class="muted small">{#if u.isAdmin}<span class="role">Admin</span>{:else}Listener{/if}{u.disabled ? ' · disabled' : ''}{u.lastLogin ? ` · last login ${new Date(u.lastLogin).toLocaleDateString()}` : ''}</div>
      </div>
      {#if u.isAdmin}
        <button class="btn" disabled={adminCount <= 1} title={adminCount <= 1 ? 'The last admin can’t be removed' : ''} onclick={() => setAdmin(u, false)}>Remove admin</button>
      {:else}
        <button class="btn" disabled={u.disabled} onclick={() => setAdmin(u, true)}>Make admin</button>
      {/if}
      <label class="small"><input type="checkbox" class="switch" checked={!u.disabled} disabled={u.id === session.saved.userId} onchange={(e) => patch(u, { disabled: !e.currentTarget.checked })} /> enabled</label>
      <button class="btn" onclick={() => reset(u)}>Reset password</button>
    </div>
  {/each}
</div>

<style>
  .pad {
    padding: 8px 18px;
  }
  .inline {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    align-items: center;
  }
  .days {
    width: 70px;
  }
  .item {
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
    padding: 10px 18px;
    border-bottom: 1px solid var(--line);
  }
  .grow {
    flex: 1;
    min-width: 160px;
  }
  .small {
    font-size: 0.85rem;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .role {
    color: var(--accent);
    font-weight: 700;
  }
  .code {
    font-family: monospace;
    letter-spacing: 0.08em;
  }
</style>
