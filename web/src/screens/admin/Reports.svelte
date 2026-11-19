<script lang="ts">
  import { ArrowLeft, Copy, Trash2 } from '@lucide/svelte';
  import { adminApi, type BugReport, type BugReportDetail } from '../../lib/api/admin';
  import { session } from '../../lib/session.svelte';
  import { ui } from '../../lib/ui.svelte';

  const A = $derived(adminApi(session.api));
  let list = $state<BugReport[] | null>(null);
  let filter = $state<'open' | 'closed' | 'all'>('open');
  let detail = $state<BugReportDetail | null>(null);
  let error = $state<string | null>(null);

  const load = () =>
    A.reports()
      .then((r) => (list = r))
      .catch((e) => (error = e.status === 404 ? 'This server has no bug-report endpoint yet.' : e.message));
  $effect(() => void load());

  const shown = $derived((list ?? []).filter((r) => filter === 'all' || r.status === filter));

  async function open(r: BugReport) {
    try {
      detail = await A.report(r.id);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  async function setStatus(status: BugReport['status']) {
    if (!detail) return;
    try {
      await A.setReportStatus(detail.id, status);
      detail = { ...detail, status };
      await load();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  async function del() {
    if (!detail || !(await ui.confirm('Delete report', `Delete "${detail.title}"?`, 'Delete'))) return;
    try {
      await A.deleteReport(detail.id);
      detail = null;
      await load();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  function copy() {
    if (!detail) return;
    const text = `${detail.title}\n${detail.username ?? ''} · ${detail.deviceName ?? ''} · ${detail.appVersion ?? ''}\n\n${detail.description}\n\n${detail.logs ?? ''}`;
    void navigator.clipboard?.writeText(text).then(() => ui.toast('Copied'));
  }
</script>

{#if detail}
  <div class="row"><button class="btn sm" onclick={() => (detail = null)}><ArrowLeft size={14} /> All reports</button></div>
  <h2>{detail.title}</h2>
  <p class="muted small">
    {new Date(detail.createdAt).toLocaleString()} · {detail.username ?? 'unknown user'} · {detail.deviceName ?? ''} ({detail.kind ?? '?'} {detail.appVersion ?? ''})
    · <span class={detail.status === 'open' ? 'warnc' : 'ok'}>{detail.status}</span>
  </p>
  <div class="row">
    {#if detail.status === 'open'}<button class="btn sm" onclick={() => setStatus('closed')}>Mark as fixed</button>{:else}<button class="btn sm" onclick={() => setStatus('open')}>Reopen</button>{/if}
    <button class="btn sm" onclick={copy}><Copy size={14} /> Copy</button>
    <button class="btn sm danger" onclick={del}><Trash2 size={14} /> Delete</button>
  </div>
  <h3>Description</h3>
  <p class="desc">{detail.description}</p>
  {#if detail.logs}<h3>Logs</h3><pre class="mono logs">{detail.logs}</pre>{/if}
  {#if detail.context}<h3>Context</h3><pre class="mono logs">{JSON.stringify(detail.context, null, 2)}</pre>{/if}
{:else}
  <h2>Bug reports</h2>
  <div class="row tabs">
    {#each [['open', 'Open'], ['closed', 'Fixed'], ['all', 'All']] as [k, label]}
      <button class="chip" class:on={filter === k} onclick={() => (filter = k as typeof filter)}>{label}{list ? ` · ${k === 'all' ? list.length : list.filter((r) => r.status === k).length}` : ''}</button>
    {/each}
  </div>
  {#if error}<p class="bad">{error}</p>{/if}
  {#if list}
    {#each shown as r (r.id)}
      <button class="rep" onclick={() => open(r)}>
        <span class="dot" class:ok={r.status === 'closed'} class:warnc={r.status === 'open'}>●</span>
        <span class="grow"><b>{r.title}</b><span class="muted small"> · {r.username ?? '?'} · {r.deviceName ?? ''} · {new Date(r.createdAt).toLocaleString()}</span></span>
        <span class="small muted">{r.appVersion ?? ''}</span>
      </button>
    {:else}<p class="muted">No {filter === 'all' ? '' : filter === 'open' ? 'open ' : 'fixed '}reports.</p>{/each}
  {:else if !error}<p class="muted">Loading…</p>{/if}
{/if}

<style>
  .tabs {
    margin-bottom: 10px;
  }
  .chip {
    border-color: #444;
    padding: 6px 14px;
  }
  .rep {
    display: flex;
    gap: 12px;
    align-items: baseline;
    width: 100%;
    text-align: left;
    padding: 12px 10px;
    border-bottom: 1px solid var(--line);
    flex-wrap: wrap;
  }
  .rep:hover {
    background: rgba(255, 255, 255, 0.04);
  }
  .grow {
    flex: 1;
    min-width: 200px;
  }
  .desc {
    white-space: pre-wrap;
  }
  .logs {
    background: #0a0a0a;
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 12px;
    overflow-x: auto;
    white-space: pre-wrap;
  }
</style>
