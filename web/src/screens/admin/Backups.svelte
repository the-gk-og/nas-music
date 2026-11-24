<script lang="ts">
  import { CloudUpload, DatabaseBackup, Download, History, RefreshCw, Trash2 } from '@lucide/svelte';
  import { adminApi, type Backup, type OffsiteBackup, type OffsiteStatus } from '../../lib/api/admin';
  import { fmtBytes } from '../../lib/format';
  import { session } from '../../lib/session.svelte';
  import { ui } from '../../lib/ui.svelte';

  const A = $derived(adminApi(session.api));
  let list = $state<Backup[] | null>(null);
  let offsite = $state<OffsiteStatus | null>(null);
  let busy = $state<string | null>(null);
  let error = $state<string | null>(null);

  const KIND: Record<string, string> = {
    manual: 'Manual',
    scheduled: 'Nightly',
    migration: 'Before upgrade',
    prune: 'Before clean-up',
    prerestore: 'Before restore',
  };
  const when = (iso: string) => new Date(iso).toLocaleString();

  const load = () =>
    A.backups()
      .then((b) => (list = b))
      .catch((e) => (error = e.status === 404 ? 'This server has no backup endpoint yet.' : e.message));
  const loadOffsite = () =>
    A.offsiteBackups()
      .then((o) => (offsite = o))
      .catch(() => (offsite = null)); // older server: no off-site section
  $effect(() => {
    void load();
    void loadOffsite();
  });

  async function create() {
    const note = await ui.prompt('Create backup', 'Note (optional)', '', 'Create');
    if (note === null) return;
    busy = 'create';
    try {
      await A.createBackup(note || undefined);
      ui.toast('Backup created');
      await load();
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      busy = null;
    }
  }
  async function download(b: Backup) {
    try {
      const url = await A.downloadBackup(b.id);
      const a = document.createElement('a');
      a.href = url;
      a.download = `nas-music-backup-${b.createdAt.slice(0, 10)}.db`;
      a.click();
      if (url.startsWith('blob:')) setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  async function del(b: Backup) {
    if (!(await ui.confirm('Delete backup', `Delete the backup from ${when(b.createdAt)} (${fmtBytes(b.bytes)})? This can’t be undone.`, 'Delete'))) return;
    try {
      await A.deleteBackup(b.id);
      await load();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  async function copyOffsite(b: Backup) {
    busy = 'copy:' + b.id;
    try {
      await A.copyBackupOffsite(b.id);
      ui.toast('Copied off-site');
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      busy = null;
      await loadOffsite();
    }
  }
  async function restore(id: string, createdAt: string, source: 'local' | 'offsite') {
    const ok = await ui.confirm(
      'Restore this backup?',
      `The database goes back to how it was on ${when(createdAt)}: users, playlists, likes, history and settings made since then are replaced. ` +
        'A copy of the database as it is now is saved first, so you can undo this by restoring that copy. ' +
        'Phones and browsers signed in after the backup was made will need to sign in again.',
      'Restore',
    );
    if (!ok) return;
    busy = 'restore:' + id;
    try {
      const r = await A.restoreBackup(id, source);
      ui.toast(`Restored. The previous database was saved as “${KIND.prerestore}” (${r.safetyBackup}).`);
      // Restored settings, users and sessions: start the console afresh (this may ask to sign in again).
      setTimeout(() => location.reload(), 1500);
    } catch (e) {
      ui.toast((e as Error).message);
      busy = null;
    }
  }
  const restoreLocal = (b: Backup) => restore(b.id, b.createdAt, 'local');
  const restoreOffsite = (o: OffsiteBackup) => restore(o.name, o.createdAt, 'offsite');
  const canCopy = (b: Backup) => offsite?.configured && b.kind !== 'migration';
</script>

<h2>Backups</h2>
<p class="muted small">A backup holds the database (users, playlists, queues, history, settings), not the music library. One is made every night at 04:15.</p>
<div class="row"><button class="btn primary" disabled={busy !== null} onclick={create}><DatabaseBackup size={16} /> {busy === 'create' ? 'Creating…' : 'Create backup now'}</button></div>
{#if error}<p class="bad">{error}</p>{/if}
{#if list}
  <div class="tablewrap" style="margin-top: 14px">
    <table>
      <thead><tr><th>Created</th><th>Size</th><th>Type</th><th>Note</th><th></th></tr></thead>
      <tbody>
        {#each list as b (b.id)}
          <tr>
            <td>{when(b.createdAt)}</td>
            <td>{fmtBytes(b.bytes)}</td>
            <td class="small">{KIND[b.kind ?? ''] ?? b.kind ?? ''}</td>
            <td class="small muted">{b.note ?? ''}</td>
            <td>
              <div class="row">
                <button class="btn sm" disabled={busy !== null} onclick={() => restoreLocal(b)}><History size={14} /> {busy === 'restore:' + b.id ? 'Restoring…' : 'Restore'}</button>
                {#if canCopy(b)}
                  <button class="btn sm" disabled={busy !== null} onclick={() => copyOffsite(b)} title="Copy to {offsite?.destination}"><CloudUpload size={14} /> {busy === 'copy:' + b.id ? 'Copying…' : 'Copy off-site'}</button>
                {/if}
                <button class="btn sm" onclick={() => download(b)}><Download size={14} /> Download</button>
                <button class="btn sm danger" aria-label="Delete backup" disabled={busy !== null} onclick={() => del(b)}><Trash2 size={14} /></button>
              </div>
            </td>
          </tr>
        {:else}<tr><td colspan="5" class="muted">No backups yet.</td></tr>{/each}
      </tbody>
    </table>
  </div>
{:else if !error}<p class="muted">Loading…</p>{/if}

{#if offsite}
  <h3 style="margin-top: 28px">Off-site copies</h3>
  {#if !offsite.configured}
    <p class="muted small">
      Backups are only on the same disk as the database. To keep a copy somewhere else, set <code>backup_copy_to</code> in
      <code>config.toml</code> to a folder on another disk or dataset (for example <code>"/backup"</code>, mounted into the container) or to an
      rclone remote (for example <code>"b2:my-bucket/music"</code>), then restart. The nightly backup is then copied there too.
    </p>
  {:else}
    <p class="small">
      Copied to <code>{offsite.destination}</code> ({offsite.kind === 'rclone' ? 'rclone remote' : 'folder'}) every night, keeping the newest {offsite.keep}{offsite.includeConfig ? ', with config.toml and the HTTPS certificate' : ''}.
      <button class="btn sm" onclick={loadOffsite} aria-label="Refresh off-site list"><RefreshCw size={14} /></button>
    </p>
    {#if offsite.last}
      <p class="small {offsite.last.ok ? 'muted' : 'bad'}">
        Last copy {when(offsite.last.at)}: {offsite.last.ok ? 'OK' : `failed: ${offsite.last.error}`}
      </p>
    {/if}
    {#if offsite.error}<p class="bad small">Can’t list the copies: {offsite.error}</p>{/if}
    <div class="tablewrap" style="margin-top: 10px">
      <table>
        <thead><tr><th>Created</th><th>Size</th><th>Type</th><th></th></tr></thead>
        <tbody>
          {#each offsite.items as o (o.name)}
            <tr>
              <td>{when(o.createdAt)}</td>
              <td>{fmtBytes(o.bytes)}</td>
              <td class="small">{KIND[o.kind] ?? o.kind}</td>
              <td><button class="btn sm" disabled={busy !== null} onclick={() => restoreOffsite(o)}><History size={14} /> {busy === 'restore:' + o.name ? 'Restoring…' : 'Restore'}</button></td>
            </tr>
          {:else}<tr><td colspan="4" class="muted">No off-site copies yet. The first one is made tonight, or use “Copy off-site” above.</td></tr>{/each}
        </tbody>
      </table>
    </div>
  {/if}
{/if}
