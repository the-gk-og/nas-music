<script lang="ts">
  import { DatabaseBackup, Download, Trash2 } from '@lucide/svelte';
  import { adminApi, type Backup } from '../../lib/api/admin';
  import { fmtBytes } from '../../lib/format';
  import { session } from '../../lib/session.svelte';
  import { ui } from '../../lib/ui.svelte';

  const A = $derived(adminApi(session.api));
  let list = $state<Backup[] | null>(null);
  let busy = $state(false);
  let error = $state<string | null>(null);

  const load = () =>
    A.backups()
      .then((b) => (list = b))
      .catch((e) => (error = e.status === 404 ? 'This server has no backup endpoint yet.' : e.message));
  $effect(() => void load());

  async function create() {
    const note = await ui.prompt('Create backup', 'Note (optional)', '', 'Create');
    if (note === null) return;
    busy = true;
    try {
      await A.createBackup(note || undefined);
      ui.toast('Backup created');
      await load();
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      busy = false;
    }
  }
  async function download(b: Backup) {
    try {
      const url = await A.downloadBackup(b.id);
      const a = document.createElement('a');
      a.href = url;
      a.download = `nas-music-backup-${b.createdAt.slice(0, 10)}.tar.gz`;
      a.click();
      if (url.startsWith('blob:')) setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  async function del(b: Backup) {
    if (!(await ui.confirm('Delete backup', `Delete the backup from ${new Date(b.createdAt).toLocaleString()} (${fmtBytes(b.bytes)})? This can’t be undone.`, 'Delete'))) return;
    try {
      await A.deleteBackup(b.id);
      await load();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
</script>

<h2>Backups</h2>
<p class="muted small">A backup holds the database (users, playlists, queues, history, settings) — not the music library.</p>
<div class="row"><button class="btn primary" disabled={busy} onclick={create}><DatabaseBackup size={16} /> {busy ? 'Creating…' : 'Create backup now'}</button></div>
{#if error}<p class="bad">{error}</p>{/if}
{#if list}
  <div class="tablewrap" style="margin-top: 14px">
    <table>
      <thead><tr><th>Created</th><th>Size</th><th>Type</th><th>Note</th><th></th></tr></thead>
      <tbody>
        {#each list as b (b.id)}
          <tr>
            <td>{new Date(b.createdAt).toLocaleString()}</td>
            <td>{fmtBytes(b.bytes)}</td>
            <td class="small">{b.kind ?? ''}</td>
            <td class="small muted">{b.note ?? ''}</td>
            <td>
              <div class="row">
                <button class="btn sm" onclick={() => download(b)}><Download size={14} /> Download</button>
                <button class="btn sm danger" aria-label="Delete backup" onclick={() => del(b)}><Trash2 size={14} /></button>
              </div>
            </td>
          </tr>
        {:else}<tr><td colspan="5" class="muted">No backups yet.</td></tr>{/each}
      </tbody>
    </table>
  </div>
{:else if !error}<p class="muted">Loading…</p>{/if}
