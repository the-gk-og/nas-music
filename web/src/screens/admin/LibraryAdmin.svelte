<script lang="ts">
  import { Compass, RefreshCw, RotateCcw, Sparkles } from '@lucide/svelte';
  import { adminApi, type FailedFile, type JobName, type ServerStatus } from '../../lib/api/admin';
  import { session } from '../../lib/session.svelte';
  import { ui } from '../../lib/ui.svelte';

  const A = $derived(adminApi(session.api));
  let failed = $state<FailedFile[] | null>(null);
  let st = $state<ServerStatus | null>(null);
  let error = $state<string | null>(null);

  async function load() {
    try {
      [failed, st] = await Promise.all([A.failed(), A.status().catch(() => null)]);
    } catch (e) {
      error = (e as Error).message;
    }
  }
  $effect(() => {
    void load();
    const h = setInterval(() => void load(), 5000);
    return () => clearInterval(h);
  });

  const running = (n: string) => st?.jobs.find((j) => j.name === n)?.state === 'running';

  async function run(name: JobName, label: string) {
    try {
      await A.runJob(name);
      ui.toast(`${label} started`);
    } catch (e) {
      ui.toast((e as Error).message);
    }
    void load();
  }
  async function retry(f?: FailedFile) {
    try {
      const r = await A.retryFailed(f?.id);
      ui.toast(`${r.queued} file${r.queued === 1 ? '' : 's'} queued again`);
    } catch (e) {
      ui.toast((e as Error).message);
    }
    void load();
  }
</script>

<h2>Library</h2>
<div class="row actions">
  <button class="btn" disabled={running('scan')} onclick={() => run('scan', 'Scan')}><RefreshCw size={16} /> {running('scan') ? 'Scanning…' : 'Scan now'}</button>
  <button class="btn" disabled={running('nightly')} onclick={() => run('nightly', 'Nightly mixes')}><Sparkles size={16} /> {running('nightly') ? 'Building mixes…' : 'Run nightly now'}</button>
  <button class="btn" disabled={running('discovery')} onclick={() => run('discovery', 'Discovery')}><Compass size={16} /> {running('discovery') ? 'Discovering…' : 'Run discovery now'}</button>
</div>
{#if st}
  <p class="muted small">{st.library.tracks} tracks · {st.library.albums} albums · {st.library.artists} artists · analysis {st.analysis.done}/{st.analysis.total}</p>
{/if}

<h3>Failed files</h3>
{#if error}<p class="bad">{error}</p>{/if}
{#if failed?.length}
  <div class="row"><button class="btn sm" onclick={() => retry()}><RotateCcw size={14} /> Retry all ({failed.length})</button></div>
  <div class="tablewrap" style="margin-top: 10px">
    <table>
      <thead><tr><th>File</th><th>Stage</th><th>Error</th><th>Tries</th><th></th></tr></thead>
      <tbody>
        {#each failed as f (f.id)}
          <tr>
            <td class="mono">{f.path}</td>
            <td>{f.stage}</td>
            <td class="bad small">{f.error}</td>
            <td class="small">{f.attempts}{f.lastTriedAt ? ` · ${new Date(f.lastTriedAt).toLocaleString()}` : ''}</td>
            <td><button class="btn sm" onclick={() => retry(f)}>Retry</button></td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{:else if failed}<p class="ok">No failed files.</p>{:else}<p class="muted">Loading…</p>{/if}

<style>
  .actions {
    margin-bottom: 6px;
  }
</style>
