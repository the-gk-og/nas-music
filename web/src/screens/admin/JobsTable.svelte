<script lang="ts">
  import { Play } from '@lucide/svelte';
  import { adminApi, type JobName, type JobStatus } from '../../lib/api/admin';
  import { session } from '../../lib/session.svelte';
  import { ui } from '../../lib/ui.svelte';

  let { jobs, onchange }: { jobs: JobStatus[]; onchange?: () => void } = $props();
  const A = $derived(adminApi(session.api));
  const LABELS: Record<string, string> = { scan: 'Library scan', analyse: 'Audio analysis', loudness: 'Volume levelling', nightly: 'Nightly mixes', discovery: 'Weekly discovery', pretranscode: 'Pre-transcode', backup: 'Backup' };
  const RUNNABLE = new Set(['scan', 'nightly', 'discovery', 'analyse', 'loudness', 'pretranscode', 'backup']);
  let busy = $state<string | null>(null);

  async function run(name: string) {
    busy = name;
    try {
      await A.runJob(name as JobName);
      ui.toast(`${LABELS[name] ?? name} started`);
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      busy = null;
      onchange?.();
    }
  }
</script>

<div class="tablewrap">
  <table>
    <thead><tr><th>Job</th><th>Status</th><th>Last run</th><th>Next</th><th></th></tr></thead>
    <tbody>
      {#each jobs as j (j.name)}
        <tr>
          <td>{LABELS[j.name] ?? j.name}</td>
          <td>
            {#if j.state === 'running'}<span class="warnc">● running</span>
            {:else if j.state === 'failed' || j.ok === false}<span class="bad">● failed</span>
            {:else}<span class="ok">● ok</span>{/if}
            {#if j.error}<div class="small bad">{j.error}</div>{/if}
          </td>
          <td class="small">{j.startedAt ? new Date(j.startedAt).toLocaleString() : '—'}{j.durationS ? ` · ${Math.round(j.durationS)} s` : ''}</td>
          <td class="small">{j.nextRunAt ? new Date(j.nextRunAt).toLocaleString() : '—'}</td>
          <td>
            {#if RUNNABLE.has(j.name)}
              <button class="btn sm" disabled={j.state === 'running' || busy === j.name} onclick={() => run(j.name)}><Play size={14} /> Run now</button>
            {/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>
