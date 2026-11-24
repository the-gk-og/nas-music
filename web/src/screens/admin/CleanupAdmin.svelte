<script lang="ts">
  // Admin: library cleanup (v10). Pruning reports, find-and-remove, and the trash. Removing is undoable until
  // the trash is purged; purging deletes files only when the server allows it (config library_delete_files).
  import { Download, Pause, Play, RefreshCw, RotateCcw, Search, ShieldCheck, Trash2 } from '@lucide/svelte';
  import {
    cleanupApi,
    qualityText,
    selectionText,
    type CleanupItem,
    type CleanupReport,
    type CleanupSearch,
    type CleanupSummary,
    type TrashBatch,
  } from '../../lib/api/admin';
  import { fmtBytes } from '../../lib/format';
  import { library } from '../../lib/library.svelte';
  import { preview } from '../../lib/preview.svelte';
  import { session } from '../../lib/session.svelte';
  import { ui } from '../../lib/ui.svelte';

  const C = $derived(cleanupApi(session.api));
  let tab = $state<'reports' | 'find' | 'trash'>('reports');
  let summary = $state<CleanupSummary | null>(null);
  let error = $state<string | null>(null);
  let busy = $state(false);

  // reports
  let report = $state<CleanupReport | null>(null);
  let threshold = $state<number | null>(null);
  let items = $state<CleanupItem[] | null>(null);
  let total = $state(0);
  let picked = $state<Set<string>>(new Set());

  // find
  let q = $state('');
  let found = $state<CleanupSearch | null>(null);

  // trash
  let batches = $state<TrashBatch[] | null>(null);

  $effect(() => () => preview.stop());

  async function loadSummary() {
    try {
      summary = await C.summary();
      error = null;
    } catch (e) {
      error = (e as { status?: number }).status === 404 ? 'This server has no cleanup tools yet (update it).' : (e as Error).message;
    }
  }
  $effect(() => void loadSummary());

  async function openReport(r: CleanupReport, th: number | null = r.threshold) {
    report = r;
    threshold = th;
    items = null;
    picked = new Set();
    try {
      const res = await C.report(r.id, { threshold: th ?? undefined, limit: 2000 });
      items = res.items;
      total = res.total;
    } catch (e) {
      ui.toast((e as Error).message);
      items = [];
    }
  }

  const pickedItems = $derived((items ?? []).filter((i) => picked.has(i.id)));

  function pick(id: string, on: boolean) {
    const s = new Set(picked);
    if (on) s.add(id);
    else s.delete(id);
    picked = s;
  }

  const days = $derived(summary?.trashDays ?? 30);
  const afterTrash = $derived(
    summary?.deleteFiles
      ? `After ${days} days in the trash the files are deleted from the NAS.`
      : `After ${days} days in the trash they stay hidden for good and go on the delete list (deleting files is turned off on the server).`,
  );

  async function removeTracks(trackIds: string[], what: string, reason: string | null) {
    if (!trackIds.length) return;
    const ok = await ui.confirm('Move to trash', `Remove ${what} from the library? ${afterTrash} You can restore them from the trash until then.`, 'Move to trash');
    if (!ok) return;
    await doRemove({ trackIds }, reason);
  }

  async function doRemove(b: { trackIds?: string[]; albumIds?: string[]; artistIds?: string[] }, reason: string | null) {
    busy = true;
    try {
      if (preview.trackId && b.trackIds?.includes(preview.trackId)) preview.stop();
      const r = await C.remove({ ...b, reason });
      ui.toast(`Moved ${r.removed} song${r.removed === 1 ? '' : 's'} to the trash (${fmtBytes(r.bytes)})`);
      library.tagsChanged(null, null);
      await loadSummary();
      if (report) await openReport(report, threshold);
      if (found && q.trim()) await runSearch();
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
  }

  async function removePicked() {
    if (!report) return;
    await removeTracks(
      pickedItems.flatMap((i) => i.trackIds),
      selectionText(pickedItems, size),
      report.title,
    );
  }

  async function dismiss(ids: string[]) {
    try {
      await C.dismissLossless(ids);
      ui.toast('Marked as fine');
      await loadSummary();
      if (report) await openReport(report, threshold);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async function runSearch() {
    if (!q.trim()) return;
    try {
      found = await C.search(q.trim());
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async function removeAlbum(a: CleanupSearch['albums'][number]) {
    const ok = await ui.confirm('Remove album', `Remove "${a.title}" by ${a.artist} (${a.trackCount} songs, ${fmtBytes(a.bytes)})? ${afterTrash}`, 'Move to trash');
    if (ok) await doRemove({ albumIds: [a.id] }, `album: ${a.title}`);
  }

  async function removeArtist(a: CleanupSearch['artists'][number]) {
    const typed = await ui.prompt(
      'Remove artist',
      `This removes every song by ${a.name} (${a.trackCount} songs, ${fmtBytes(a.bytes)}). ${afterTrash} Type REMOVE to confirm.`,
      '',
      'Move to trash',
    );
    if (typed?.trim().toUpperCase() !== 'REMOVE') {
      if (typed !== null) ui.toast('Not removed: type REMOVE to confirm');
      return;
    }
    await doRemove({ artistIds: [a.id] }, `artist: ${a.name}`);
  }

  async function loadTrash() {
    try {
      batches = (await C.trash()).batches;
    } catch (e) {
      ui.toast((e as Error).message);
      batches = [];
    }
  }
  $effect(() => {
    if (tab === 'trash') void loadTrash();
  });

  async function restore(b: TrashBatch) {
    busy = true;
    try {
      const r = await C.restore({ batchIds: [b.batchId] });
      ui.toast(`Restored ${r.restored} song${r.restored === 1 ? '' : 's'}`);
      library.tagsChanged(null, null);
      await Promise.all([loadTrash(), loadSummary()]);
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
  }

  async function purgeNow(b: TrashBatch) {
    const n = `${b.count} song${b.count === 1 ? '' : 's'} (${fmtBytes(b.bytes)})`;
    if (summary?.deleteFiles) {
      const typed = await ui.prompt('Delete files now', `Permanently delete ${n} from the NAS? This cannot be undone. Type DELETE to confirm.`, '', 'Delete forever');
      if (typed?.trim().toUpperCase() !== 'DELETE') {
        if (typed !== null) ui.toast('Nothing deleted: type DELETE to confirm');
        return;
      }
    } else if (!(await ui.confirm('Empty from trash', `Hide ${n} for good? The files stay on the NAS and go on the delete list.`, 'Empty'))) return;
    busy = true;
    try {
      const r = await C.purge([b.batchId]);
      ui.toast(
        r.filesDeleted
          ? `Deleted ${r.filesDeleted} file${r.filesDeleted === 1 ? '' : 's'}, freed ${fmtBytes(r.freedBytes)}${r.errors ? ` (${r.errors} could not be deleted)` : ''}`
          : `${r.kept} song${r.kept === 1 ? '' : 's'} hidden for good${r.errors ? ` (${r.errors} could not be deleted)` : ''}`,
      );
      await Promise.all([loadTrash(), loadSummary()]);
    } catch (e) {
      ui.toast((e as Error).message);
    }
    busy = false;
  }

  async function downloadScript() {
    try {
      const url = await C.deleteScriptUrl();
      const a = document.createElement('a');
      a.href = url;
      a.download = 'delete-list.sh';
      a.click();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  const size = (n: number | null | undefined) => (n === 0 ? '0 B' : fmtBytes(n));
  const listen = (id: string) => preview.toggle(id, C.previewUrl(id, 'opus'));
  const date = (s: string) => new Date(s).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
  const dur = (s: number | null) => (s == null ? '' : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`);
  const unitLabel: Record<string, string> = { days: 'Older than (days)', skips: 'At least (skips)', kbps: 'Below (kbps)', seconds: 'Shorter than (seconds)' };
</script>

<h2>Cleanup</h2>
<p class="muted small">
  Prune songs nobody listens to, low-quality copies and junk. Removed songs leave the library at once and wait in the trash for {days} days, so a mistake
  is one click to undo.
</p>

{#if error}<p class="bad">{error}</p>
{:else if summary}
  <div class="banner small" class:on={summary.deleteFiles}>
    {#if summary.deleteFiles}
      <Trash2 size={16} /><span class="grow">Deleting files is on: when a removal leaves the trash, its files are deleted from the NAS.</span>
    {:else}
      <ShieldCheck size={16} />
      <span class="grow"
        >Deleting files is off, so the music folder is never touched: emptied songs stay hidden and go on the delete list. To let the server delete them, set
        <code>library_delete_files = true</code> in config.toml and mount the music folder without <code>:ro</code>.</span
      >
    {/if}
  </div>

  <div class="tabs row">
    <button class="chip" class:on={tab === 'reports'} onclick={() => (tab = 'reports')}>Reports</button>
    <button class="chip" class:on={tab === 'find'} onclick={() => (tab = 'find')}>Find &amp; remove</button>
    <button class="chip" class:on={tab === 'trash'} onclick={() => (tab = 'trash')}>
      Trash{summary.inTrash ? ` (${summary.inTrash})` : ''}{summary.deleteList ? ` · ${summary.deleteList} to delete` : ''}
    </button>
    <button class="btn sm text" onclick={loadSummary}><RefreshCw size={15} /> Refresh</button>
  </div>

  {#if preview.error}<p class="small bad">{preview.error}</p>{/if}

  {#if tab === 'reports'}
    <div class="grid">
      {#each summary.reports as r (r.id)}
        <button class="tile pick" class:on={report?.id === r.id} onclick={() => openReport(r)}>
          <div class="k">{r.title}</div>
          <div class="v">{r.count}</div>
          <div class="s">{r.kind === 'album' ? 'albums' : 'songs'} · {size(r.bytes)}</div>
        </button>
      {/each}
    </div>
    {#if summary.lossless.lossless}
      <p class="muted small">
        Fake-lossless check: {summary.lossless.checked} of {summary.lossless.lossless} lossless files checked{summary.lossless.checked < summary.lossless.lossless
          ? ' (the rest are checked a bit each night)'
          : ''}.
      </p>
    {/if}

    {#if report}
      <h3>{report.title}</h3>
      <p class="muted small">{report.description}</p>
      <div class="row">
        {#if report.unit}
          <label class="row small"
            >{unitLabel[report.unit]}
            <input class="num" type="number" min="0" bind:value={threshold} onchange={() => report && openReport(report, threshold)} />
          </label>
        {/if}
        {#if items?.length}
          <button class="btn sm text" onclick={() => (picked = picked.size === items!.length ? new Set() : new Set(items!.map((i) => i.id)))}>
            {picked.size === items.length ? 'Select none' : 'Select all'}
          </button>
          <button class="btn sm danger" disabled={busy || !picked.size} onclick={removePicked}>
            <Trash2 size={15} /> Move {picked.size ? selectionText(pickedItems, size) : 'selected'} to trash
          </button>
          {#if report.id === 'fake_lossless'}
            <button class="btn sm text" disabled={busy || !picked.size} onclick={() => dismiss([...picked])}>Mark selected as fine</button>
          {/if}
        {/if}
      </div>
      {#if !items}<p class="muted">Loading…</p>
      {:else if !items.length}<p class="muted">Nothing here.</p>
      {:else}
        {#if total > items.length}<p class="muted small">Showing the first {items.length} of {total}.</p>{/if}
        <div class="list">
          {#each items as it (it.id)}
            <label class="item">
              <input type="checkbox" checked={picked.has(it.id)} onchange={(e) => pick(it.id, e.currentTarget.checked)} />
              {#if it.kind === 'track'}
                <button
                  class="icon-btn"
                  aria-label={preview.trackId === it.id && preview.playing ? 'Pause' : 'Listen'}
                  onclick={(e) => {
                    e.preventDefault();
                    listen(it.id);
                  }}
                >
                  {#if preview.trackId === it.id && preview.playing}<Pause size={16} />{:else}<Play size={16} />{/if}
                </button>
              {/if}
              <div class="grow">
                <div class="ellipsis"><b>{it.title}</b> <span class="muted">— {it.artist}{it.kind === 'track' ? ` · ${it.album}` : ''}</span></div>
                <div class="small muted ellipsis">
                  {#if it.kind === 'track'}{qualityText(it)} · {dur(it.durationS)} ·{/if}
                  {fmtBytes(it.bytes)}
                  {#if it.kind === 'track'}· {it.plays} play{it.plays === 1 ? '' : 's'}{/if}
                  {#if it.note}· <span class:bad={report.id === 'fake_lossless'}>{it.note}</span>{/if}
                </div>
                <div class="mono small muted ellipsis" title={it.path ?? ''}>{it.path ?? ''}</div>
              </div>
            </label>
          {/each}
        </div>
      {/if}
    {/if}
  {:else if tab === 'find'}
    <form
      class="row"
      onsubmit={(e) => {
        e.preventDefault();
        void runSearch();
      }}
    >
      <input class="grow" type="search" placeholder="Artist, album, song or file path" bind:value={q} />
      <button class="btn sm primary" type="submit"><Search size={15} /> Find</button>
    </form>
    {#if found}
      {#if !found.artists.length && !found.albums.length && !found.tracks.length}<p class="muted">No matches.</p>{/if}
      {#if found.artists.length}
        <h3>Artists</h3>
        {#each found.artists as a (a.id)}
          <div class="item">
            <div class="grow"><b>{a.name}</b> <span class="small muted">{a.trackCount} songs · {fmtBytes(a.bytes)}</span></div>
            <button class="btn sm danger" disabled={busy} onclick={() => removeArtist(a)}><Trash2 size={14} /> Remove artist</button>
          </div>
        {/each}
      {/if}
      {#if found.albums.length}
        <h3>Albums</h3>
        {#each found.albums as a (a.id)}
          <div class="item">
            <div class="grow">
              <div class="ellipsis"><b>{a.title}</b> <span class="muted">— {a.artist}{a.year ? ` (${a.year})` : ''}</span></div>
              <div class="small muted">{a.trackCount} songs · {fmtBytes(a.bytes)}</div>
            </div>
            <button class="btn sm danger" disabled={busy} onclick={() => removeAlbum(a)}><Trash2 size={14} /> Remove album</button>
          </div>
        {/each}
      {/if}
      {#if found.tracks.length}
        <h3>Songs</h3>
        {#each found.tracks as t (t.id)}
          <div class="item">
            <button class="icon-btn" aria-label={preview.trackId === t.id && preview.playing ? 'Pause' : 'Listen'} onclick={() => listen(t.id)}>
              {#if preview.trackId === t.id && preview.playing}<Pause size={16} />{:else}<Play size={16} />{/if}
            </button>
            <div class="grow">
              <div class="ellipsis"><b>{t.title}</b> <span class="muted">— {t.artist} · {t.album}</span></div>
              <div class="small muted ellipsis">{qualityText(t)} · {fmtBytes(t.bytes)} · {t.plays} plays · <span class="mono">{t.path}</span></div>
            </div>
            <button class="btn sm danger" disabled={busy} onclick={() => removeTracks([t.id], `"${t.title}"`, null)}><Trash2 size={14} /> Remove</button>
          </div>
        {/each}
      {/if}
    {/if}
  {:else}
    {#if summary.deleteList}
      <div class="banner small">
        <Download size={16} />
        <span class="grow">{summary.deleteList} song{summary.deleteList === 1 ? ' is' : 's are'} hidden for good but still on the NAS.</span>
        <button class="btn sm" onclick={downloadScript}><Download size={14} /> Download delete script</button>
      </div>
      <p class="muted small">
        Run it on the NAS with your music folder, e.g. <span class="mono">sh delete-list.sh /mnt/tank/music</span>. The server forgets the songs once the
        files are gone.
      </p>
    {/if}
    {#if !batches}<p class="muted">Loading…</p>
    {:else if !batches.length}<p class="muted">The trash is empty.</p>
    {:else}
      {#each batches as b (b.batchId)}
        <article class="group">
          <div class="row">
            <b class="grow">{b.count} song{b.count === 1 ? '' : 's'} · {fmtBytes(b.bytes)}</b>
            <span class="small muted">removed {date(b.removedAt)}{b.removedBy ? ` by ${b.removedBy}` : ''}</span>
          </div>
          {#if b.reason}<div class="small muted">{b.reason}</div>{/if}
          <ul class="small sample">
            {#each b.sample as s (s.id)}<li class="ellipsis">{s.title} <span class="muted">— {s.artist} · {s.album}</span></li>{/each}
            {#if b.count > b.sample.length}<li class="muted">…and {b.count - b.sample.length} more</li>{/if}
          </ul>
          {#if b.purgedCount}
            <div class="small warnc">Emptied: hidden for good{b.purgeError ? `, could not delete (${b.purgeError})` : ', on the delete list'}.</div>
          {:else}
            <div class="small muted">{summary.deleteFiles ? 'Files deleted' : 'Emptied'} on {date(b.purgeAfter)}.</div>
          {/if}
          <div class="row acts">
            <button class="btn sm" disabled={busy} onclick={() => restore(b)}><RotateCcw size={14} /> Restore</button>
            {#if !b.purgedCount}
              <button class="btn sm danger" disabled={busy} onclick={() => purgeNow(b)}>
                <Trash2 size={14} />
                {summary.deleteFiles ? 'Delete files now' : 'Empty now'}
              </button>
            {/if}
          </div>
        </article>
      {/each}
    {/if}
  {/if}
{:else}<p class="muted">Loading…</p>{/if}

<style>
  .banner {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
    background: var(--bg-1);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 10px 14px;
    margin: 8px 0 14px;
  }
  .banner code {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.85em;
  }
  .banner.on {
    border-color: var(--danger);
  }
  .tabs {
    margin-bottom: 14px;
  }
  .chip {
    padding: 6px 14px;
    border-radius: 999px;
    background: var(--bg-2);
    color: var(--fg-2);
    font-size: 0.9rem;
  }
  .chip.on {
    background: #fff;
    color: #000;
  }
  .tile.pick {
    text-align: left;
    cursor: pointer;
    color: inherit;
  }
  .tile.pick.on {
    border-color: var(--accent);
  }
  .num {
    width: 90px;
  }
  .list {
    border: 1px solid var(--line);
    border-radius: 12px;
    overflow: hidden;
  }
  .item {
    display: flex;
    gap: 10px;
    align-items: center;
    padding: 8px 12px;
    border-bottom: 1px solid var(--line);
    min-width: 0;
  }
  .list .item:last-child {
    border-bottom: 0;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .ellipsis {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .group {
    background: var(--bg-1);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 12px 14px;
    margin: 12px 0;
  }
  .sample {
    margin: 6px 0;
    padding-left: 18px;
  }
  .acts {
    margin-top: 8px;
  }
  form.row input {
    min-width: 0;
  }
</style>
