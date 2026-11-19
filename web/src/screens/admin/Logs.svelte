<script lang="ts">
  // Live log tail (polls for entries after the last seq) with level/text filter, plus job history.
  import { Pause, Play, Trash2 } from '@lucide/svelte';
  import { adminApi, filterLogs, type JobRun, type LogEntry } from '../../lib/api/admin';
  import { session } from '../../lib/session.svelte';

  const A = $derived(adminApi(session.api));
  let tab = $state<'logs' | 'jobs'>('logs');
  let entries = $state<LogEntry[]>([]);
  let level = $state('INFO');
  let q = $state('');
  let live = $state(true);
  let follow = $state(true);
  let error = $state<string | null>(null);
  let history = $state<JobRun[] | null>(null);
  let box: HTMLDivElement | undefined = $state();
  let lastSeq = 0;
  const MAX = 3000;

  async function poll() {
    try {
      const fresh = await A.logs({ after: lastSeq, limit: 500 });
      if (fresh.length) {
        lastSeq = fresh[fresh.length - 1].seq;
        entries = [...entries, ...fresh].slice(-MAX);
      }
      error = null;
    } catch (e) {
      error = (e as { status?: number }).status === 404 ? 'This server has no log endpoint yet.' : (e as Error).message;
    }
  }

  $effect(() => {
    if (tab !== 'logs' || !live) return;
    void poll();
    const h = setInterval(() => document.visibilityState === 'visible' && void poll(), 2000);
    return () => clearInterval(h);
  });

  $effect(() => {
    if (tab === 'jobs') A.jobHistory(100).then((h) => (history = h)).catch((e) => (error = e.message));
  });

  const shown = $derived(filterLogs(entries, level, q));

  // Keep the newest line in view unless the user scrolled up.
  $effect(() => {
    void shown.length;
    if (follow && box) queueMicrotask(() => box && (box.scrollTop = box.scrollHeight));
  });
  function onScroll() {
    if (!box) return;
    follow = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  }
  const time = (s: string) => new Date(s).toLocaleTimeString([], { hour12: false });
</script>

<h2>Logs &amp; jobs</h2>
<div class="row tabs">
  <button class="chip" class:on={tab === 'logs'} onclick={() => (tab = 'logs')}>Live log</button>
  <button class="chip" class:on={tab === 'jobs'} onclick={() => (tab = 'jobs')}>Job history</button>
</div>
{#if error}<p class="bad">{error}</p>{/if}

{#if tab === 'logs'}
  <div class="row filters">
    <select bind:value={level} aria-label="Minimum level">
      <option value="DEBUG">Debug and up</option>
      <option value="INFO">Info and up</option>
      <option value="WARNING">Warnings and errors</option>
      <option value="ERROR">Errors only</option>
    </select>
    <input type="search" placeholder="Filter text…" bind:value={q} aria-label="Filter logs" />
    <button class="btn sm" onclick={() => (live = !live)} aria-pressed={live}>{#if live}<Pause size={14} /> Pause{:else}<Play size={14} /> Resume{/if}</button>
    <button class="btn sm" onclick={() => (entries = [])}><Trash2 size={14} /> Clear view</button>
    <span class="muted small">{shown.length} of {entries.length} lines</span>
  </div>
  <div class="log mono" bind:this={box} onscroll={onScroll} role="log" aria-live="off">
    {#each shown as e (e.seq)}
      <div class="ln lv-{e.level.toLowerCase()}"><span class="t">{time(e.at)}</span> <span class="l">{e.level.padEnd(7)}</span> <span class="lg">{e.logger}</span> {e.message}</div>
    {:else}<div class="muted">No log lines{q ? ' match' : ' yet'}.</div>{/each}
  </div>
  {#if !follow}<button class="btn sm jump" onclick={() => { follow = true; if (box) box.scrollTop = box.scrollHeight; }}>Jump to newest</button>{/if}
{:else if history}
  <div class="tablewrap">
    <table>
      <thead><tr><th>Job</th><th>Started</th><th>Duration</th><th>Result</th><th>Trigger</th></tr></thead>
      <tbody>
        {#each history as r (r.id)}
          <tr>
            <td>{r.name}</td>
            <td class="small">{new Date(r.startedAt).toLocaleString()}</td>
            <td class="small">{r.durationS != null ? `${Math.round(r.durationS)} s` : r.finishedAt ? '' : 'running'}</td>
            <td class="small">{#if r.ok === false}<span class="bad">{r.error ?? 'failed'}</span>{:else if r.ok}<span class="ok">ok</span>{r.summary ? ` · ${r.summary}` : ''}{:else}—{/if}</td>
            <td class="small muted">{r.trigger ?? ''}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{:else}<p class="muted">Loading…</p>{/if}

<style>
  .tabs {
    margin-bottom: 10px;
  }
  .chip {
    border-color: #444;
    padding: 6px 14px;
  }
  .filters {
    margin-bottom: 8px;
  }
  .filters input {
    flex: 1;
    min-width: 140px;
  }
  .log {
    height: min(62vh, 640px);
    overflow: auto;
    background: #0a0a0a;
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 10px 12px;
    font-size: 0.8rem;
    line-height: 1.5;
  }
  .ln {
    white-space: pre-wrap;
    word-break: break-word;
    color: #cfcfcf;
  }
  .t {
    color: #6b6b6b;
  }
  .lg {
    color: #7aa2c7;
  }
  .lv-debug {
    color: #8a8a8a;
  }
  .lv-warning .l,
  .lv-warning {
    color: #fbbf24;
  }
  .lv-error,
  .lv-critical {
    color: var(--danger);
  }
  .lv-info .l {
    color: var(--accent);
  }
  .jump {
    margin-top: 8px;
  }
</style>
