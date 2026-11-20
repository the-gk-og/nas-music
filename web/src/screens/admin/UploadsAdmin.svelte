<script lang="ts">
  // Admin (v7): who may upload songs, per-user quota and usage; everyone's uploaded files with delete.
  import { Trash2 } from '@lucide/svelte';
  import { formatBytes, uploadsApi, type AdminUploads, type AdminUploadUser, type UploadedFile } from '../../lib/api/uploads';
  import { session } from '../../lib/session.svelte';
  import { ui } from '../../lib/ui.svelte';

  const U = $derived(uploadsApi(session.api));
  let data = $state<AdminUploads | null>(null);
  let files = $state<UploadedFile[]>([]);
  let quotas = $state<Record<string, string>>({});

  async function load() {
    try {
      data = await U.admin();
      files = (await U.overview(true)).files;
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  $effect(() => void load());

  async function setUser(u: AdminUploadUser, b: { canUpload?: boolean | null; quotaBytes?: number | null }) {
    try {
      const r = await U.setUser(u.userId, b);
      if (data) data.users = data.users.map((x) => (x.userId === u.userId ? r : x));
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  function saveQuota(u: AdminUploadUser) {
    const raw = (quotas[u.userId] ?? '').trim();
    if (raw === '') return void setUser(u, { quotaBytes: null });
    const gb = Number(raw);
    if (!Number.isFinite(gb) || gb < 0) return ui.toast('Quota: a number of GB (0 = unlimited, empty = default)');
    void setUser(u, { quotaBytes: Math.round(gb * 1024 ** 3) });
    quotas = { ...quotas, [u.userId]: '' };
  }
  async function remove(f: UploadedFile) {
    if (!(await ui.confirm('Delete upload', `Delete “${f.title ?? f.originalName}” uploaded by ${f.username ?? 'a removed user'}? The file is removed from the upload folder.`, 'Delete'))) return;
    try {
      await U.remove(f.id);
      await load();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
</script>

<h2>Uploads</h2>
{#if !data}<p class="muted">Loading…</p>
{:else}
  <p class="muted small">
    {#if data.enabled}
      People you allow can upload songs from the web player, the desktop app or their phone. Files go to the separate upload
      folder (never the read-only library) and appear in the library within seconds. Default quota
      {data.defaultQuotaBytes ? formatBytes(data.defaultQuotaBytes) : 'unlimited'}, files up to {formatBytes(data.maxFileBytes)}.
    {:else}
      Uploading is off: set <code>upload_root</code> in config.toml to a writable folder mounted into the container (see
      deploy/README.md “Uploads”) and restart.
    {/if}
  </p>
  <div class="table">
    {#each data.users as u (u.userId)}
      <div class="item">
        <div class="grow">
          <b>{u.username}</b>{#if u.isAdmin} <span class="muted small">admin</span>{/if}
          <div class="muted small">
            {u.files} file{u.files === 1 ? '' : 's'} · {formatBytes(u.usedBytes)}{u.quotaBytes ? ` of ${formatBytes(u.quotaBytes)}` : ' · no limit'}
            {#if !u.explicit} · default ({u.isAdmin ? 'admins may upload' : 'not allowed'}){/if}
          </div>
        </div>
        <label class="small"><input type="checkbox" checked={u.canUpload} onchange={(e) => setUser(u, { canUpload: e.currentTarget.checked })} /> May upload</label>
        {#if u.explicit}<button class="btn text" onclick={() => setUser(u, { canUpload: null })}>Default</button>{/if}
        <input class="q" inputmode="decimal" placeholder="Quota GB" aria-label="Quota in GB for {u.username}" bind:value={quotas[u.userId]}
          onkeydown={(e) => e.key === 'Enter' && saveQuota(u)} />
        <button class="btn text" onclick={() => saveQuota(u)}>Set</button>
      </div>
    {/each}
  </div>

  {#if files.length}
    <h2>Uploaded files</h2>
    <div class="table">
      {#each files as f (f.id)}
        <div class="item">
          <div class="grow">
            <b>{f.title ?? f.originalName}</b>
            <div class="muted small">{f.username ?? '—'} · {f.path} · {formatBytes(f.bytes)}{f.visibility === 'private' ? ' · only the uploader' : ''}</div>
          </div>
          <button class="icon-btn" aria-label="Delete upload" title="Delete" onclick={() => remove(f)}><Trash2 size={18} /></button>
        </div>
      {/each}
    </div>
  {/if}
{/if}

<style>
  .item {
    display: flex;
    gap: 12px;
    align-items: center;
    flex-wrap: wrap;
    padding: 10px 0;
    border-bottom: 1px solid var(--line);
  }
  .grow {
    flex: 1;
    min-width: 200px;
  }
  .small {
    font-size: 0.85rem;
  }
  .q {
    width: 90px;
  }
</style>
