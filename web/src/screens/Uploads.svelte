<script lang="ts">
  // Upload music (v7): drag and drop or pick files; per-file progress, retry, cancel and a tag preview of where
  // each song will land. Below: your uploads, with "only me" visibility and delete.
  import { ArrowLeft, Eye, EyeOff, Play, RotateCcw, Trash2, Upload, X } from '@lucide/svelte';
  import { ACCEPT, formatBytes, type UploadedFile } from '../lib/api/uploads';
  import { library } from '../lib/library.svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import { ui } from '../lib/ui.svelte';
  import { uploader, type UploadItem } from '../lib/uploader.svelte';

  let error = $state<string | null>(null);
  let dragging = $state(false);
  let picker = $state<HTMLInputElement | null>(null);
  const ov = $derived(uploader.overview);

  $effect(() => {
    void uploader.rev;
    uploader.load().catch((e) => (error = (e as Error).message));
  });
  // Tracks appear a few seconds after the upload finishes (the server scans them in): poll while any isn't in yet.
  $effect(() => {
    if (!ov?.files.some((f) => !f.indexed)) return;
    const t = setTimeout(() => void uploader.load().catch(() => {}), 3000);
    return () => clearTimeout(t);
  });

  function drop(e: DragEvent) {
    e.preventDefault();
    dragging = false;
    if (e.dataTransfer?.files?.length) void uploader.add(e.dataTransfer.files);
  }
  function picked(e: Event) {
    const input = e.currentTarget as HTMLInputElement;
    if (input.files?.length) void uploader.add(input.files);
    input.value = '';
  }

  const STATE: Record<UploadItem['state'], string> = {
    checking: 'Checking…', queued: 'Waiting', uploading: 'Uploading', done: 'Uploaded', failed: 'Failed', rejected: 'Not uploaded',
    cancelled: 'Cancelled',
  };
  const pct = (i: UploadItem) => (i.file.size ? Math.round((100 * i.sent) / i.file.size) : 0);

  async function toggleVisibility(f: UploadedFile) {
    try {
      await uploader.api.setVisibility(f.id, f.visibility === 'private' ? 'everyone' : 'private');
      await uploader.load();
      library.tagsRev++;
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  async function remove(f: UploadedFile) {
    const name = f.title ?? f.originalName;
    if (!(await ui.confirm('Delete upload', `Delete “${name}” from the server? This removes the file for everyone.`, 'Delete'))) return;
    try {
      await uploader.api.remove(f.id);
      await uploader.load();
      library.tagsRev++;
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  function play(f: UploadedFile) {
    void session.api.get<import('../lib/types').Track>(`/library/tracks/${encodeURIComponent(f.trackId)}`)
      .then((t) => player.playList([t], 0, 'Uploads'))
      .catch((e) => ui.toast((e as Error).message));
  }
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>Upload music</h1>
  </div>

  {#if error}<div class="empty">{error}</div>
  {:else if !ov}<div class="empty">Loading…</div>
  {:else if !ov.enabled}<div class="empty">Uploading isn’t set up on this server. An admin needs to add an upload folder (<code>upload_root</code>).</div>
  {:else if !ov.canUpload}<div class="empty">You can’t upload to this server yet. Ask an admin to allow it for you.</div>
  {:else}
    <div class="pad">
      <div
        class="drop"
        class:on={dragging}
        role="button"
        tabindex="0"
        aria-label="Drop audio files here or choose files"
        ondragover={(e) => { e.preventDefault(); dragging = true; }}
        ondragleave={() => (dragging = false)}
        ondrop={drop}
        onclick={() => picker?.click()}
        onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); picker?.click(); } }}
      >
        <Upload size={32} />
        <b>Drop songs here</b>
        <span class="muted small">or click to choose · FLAC, MP3, M4A/AAC, Ogg/Opus, WAV · up to {formatBytes(ov.maxFileBytes)} each</span>
      </div>
      <input bind:this={picker} type="file" accept={ACCEPT} multiple hidden onchange={picked} />
      <div class="row">
        <div class="seg" role="radiogroup" aria-label="Who sees new uploads">
          <button role="radio" aria-checked={uploader.visibility === 'everyone'} class:on={uploader.visibility === 'everyone'} onclick={() => (uploader.visibility = 'everyone')}><Eye size={16} />Everyone</button>
          <button role="radio" aria-checked={uploader.visibility === 'private'} class:on={uploader.visibility === 'private'} onclick={() => (uploader.visibility = 'private')}><EyeOff size={16} />Only me</button>
        </div>
        <span class="muted small">{formatBytes(ov.usedBytes)} used{ov.quotaBytes ? ` of ${formatBytes(ov.quotaBytes)}` : ''}</span>
      </div>
      {#if ov.quotaBytes}
        <div class="bar quota" aria-label="Quota used"><span style="width: {Math.min(100, (100 * ov.usedBytes) / ov.quotaBytes)}%"></span></div>
      {/if}
    </div>

    {#if uploader.items.length}
      <div class="sechead"><h2>This session</h2><button class="btn text small" onclick={() => uploader.clearFinished()}>Clear finished</button></div>
      {#each uploader.items as i (i.key)}
        <div class="item">
          <div class="grow">
            <b class="ellip">{i.tags.title ?? i.file.name}</b>
            <div class="muted small ellip">{i.target || i.file.name} · {formatBytes(i.file.size)}{i.visibility === 'private' ? ' · only me' : ''}</div>
            {#if i.state === 'uploading' || i.state === 'queued'}
              <div class="bar" role="progressbar" aria-valuenow={pct(i)} aria-valuemin={0} aria-valuemax={100}><span style="width: {pct(i)}%"></span></div>
            {/if}
            <div class="small" class:err={i.state === 'failed' || i.state === 'rejected'}>
              {STATE[i.state]}{i.state === 'uploading' ? ` · ${pct(i)}%` : ''}{i.error ? ` — ${i.error}` : ''}
            </div>
          </div>
          <div class="acts">
            {#if i.state === 'failed' || i.state === 'cancelled'}<button class="icon-btn" aria-label="Retry" title="Retry" onclick={() => uploader.retry(i.key)}><RotateCcw size={18} /></button>{/if}
            {#if i.state === 'uploading' || i.state === 'queued' || i.state === 'checking'}<button class="icon-btn" aria-label="Cancel" title="Cancel" onclick={() => void uploader.cancel(i.key)}><X size={18} /></button>{/if}
          </div>
        </div>
      {/each}
    {/if}
  {/if}

  {#if ov && ov.files.length}
    <div class="sechead"><h2>Your uploads</h2><span class="muted small">{ov.files.length}</span></div>
    {#each ov.files as f (f.id)}
      <div class="item">
        <div class="grow">
          <b class="ellip">{f.title ?? f.originalName}</b>
          <div class="muted small ellip">{f.artist ?? '—'}{f.album ? ` · ${f.album}` : ''} · {f.format.toUpperCase()} · {formatBytes(f.bytes)}</div>
          <div class="muted small">{f.indexed ? (f.visibility === 'private' ? 'Only you can see this' : 'In the library for everyone') : 'Adding to the library…'}</div>
        </div>
        <div class="acts">
          {#if f.indexed}<button class="icon-btn" aria-label="Play" title="Play" onclick={() => play(f)}><Play size={18} /></button>{/if}
          <button class="icon-btn" aria-label={f.visibility === 'private' ? 'Show to everyone' : 'Only me'} title={f.visibility === 'private' ? 'Show to everyone' : 'Make it only me'} onclick={() => toggleVisibility(f)}>
            {#if f.visibility === 'private'}<EyeOff size={18} />{:else}<Eye size={18} />{/if}
          </button>
          <button class="icon-btn" aria-label="Delete upload" title="Delete" onclick={() => remove(f)}><Trash2 size={18} /></button>
        </div>
      </div>
    {/each}
  {/if}
</div>

<style>
  .pad {
    padding: 12px 16px;
  }
  .drop {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 6px;
    padding: 32px 16px;
    border: 2px dashed var(--line);
    border-radius: 16px;
    cursor: pointer;
    text-align: center;
  }
  .drop.on,
  .drop:hover,
  .drop:focus-visible {
    border-color: var(--accent, #8ab4f8);
    background: color-mix(in srgb, var(--accent, #8ab4f8) 8%, transparent);
  }
  .row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    flex-wrap: wrap;
    margin-top: 12px;
  }
  .seg {
    display: inline-flex;
    border: 1px solid var(--line);
    border-radius: 999px;
    overflow: hidden;
  }
  .seg button {
    display: inline-flex;
    gap: 6px;
    align-items: center;
    padding: 6px 14px;
    background: none;
    border: 0;
    color: inherit;
    cursor: pointer;
  }
  .seg button.on {
    background: color-mix(in srgb, var(--accent, #8ab4f8) 22%, transparent);
  }
  .sechead {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 16px 16px 4px;
  }
  .sechead h2 {
    font-size: 1.05rem;
    margin: 0;
  }
  .item {
    display: flex;
    gap: 12px;
    align-items: center;
    padding: 10px 16px;
    border-bottom: 1px solid var(--line);
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .ellip {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .small {
    font-size: 0.85rem;
  }
  .err {
    color: #f28b82;
  }
  .acts {
    display: flex;
    gap: 4px;
  }
  .bar {
    height: 6px;
    border-radius: 3px;
    background: var(--line);
    overflow: hidden;
    margin: 6px 0 4px;
  }
  .bar span {
    display: block;
    height: 100%;
    background: var(--accent, #8ab4f8);
    transition: width 0.2s;
  }
  .bar.quota {
    margin-top: 8px;
  }
  .btn.small {
    padding: 6px 12px;
    font-size: 0.85rem;
  }
</style>
