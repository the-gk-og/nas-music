<script lang="ts">
  // Library health and pruning: counts first; details on demand; every removal is confirmed.
  import { ChevronDown, ChevronRight } from '@lucide/svelte';
  import { adminApi, type HealthCheck, type HealthItem } from '../../lib/api/admin';
  import { fmtBytes } from '../../lib/format';
  import { session } from '../../lib/session.svelte';
  import { ui } from '../../lib/ui.svelte';

  const A = $derived(adminApi(session.api));
  let checks = $state<HealthCheck[] | null>(null);
  let open = $state<Record<string, HealthItem[] | null>>({});
  let selected = $state<Record<string, Set<string>>>({});
  let error = $state<string | null>(null);
  let busy = $state<string | null>(null);

  const load = () =>
    A.health()
      .then((c) => (checks = c))
      .catch((e) => (error = e.status === 404 ? 'This server has no library health endpoint yet.' : e.message));
  $effect(() => void load());

  async function toggle(c: HealthCheck) {
    if (open[c.id]) return void (open = { ...open, [c.id]: null });
    try {
      open = { ...open, [c.id]: await A.healthItems(c.id) };
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  function pick(id: string, item: string, on: boolean) {
    const s = new Set(selected[id] ?? []);
    if (on) s.add(item);
    else s.delete(item);
    selected = { ...selected, [id]: s };
  }

  async function fix(c: HealthCheck, onlySelected: boolean) {
    if (!c.fix) return;
    const ids = onlySelected ? [...(selected[c.id] ?? [])] : undefined;
    const n = ids?.length ?? c.count;
    const what = `${n} item${n === 1 ? '' : 's'}${c.bytes && !ids ? ` (${fmtBytes(c.bytes)})` : ''}`;
    const ok = await ui.confirm(
      c.fix.label,
      c.fix.destructive
        ? `${c.fix.label}: ${what} from "${c.title}". This can’t be undone. The music files themselves are never touched.`
        : `${c.fix.label}: ${what}?`,
      c.fix.label,
    );
    if (!ok) return;
    busy = c.id;
    try {
      const r = await A.healthFix(c.id, ids);
      ui.toast(`${r.fixed} done${r.freedBytes ? ` · ${fmtBytes(r.freedBytes)} freed` : ''}`);
      selected = { ...selected, [c.id]: new Set() };
      open = { ...open, [c.id]: null };
      await load();
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      busy = null;
    }
  }
</script>

<h2>Library health &amp; pruning</h2>
<p class="muted small">Counts first. Nothing is removed without asking, and the read-only music library is never modified.</p>
{#if error}<p class="bad">{error}</p>{/if}
{#if checks}
  <div class="grid">
    {#each checks as c (c.id)}
      <div class="tile" class:clean={c.count === 0}>
        <div class="k">{c.title}</div>
        <div class="v" class:ok={c.count === 0} class:warnc={c.count > 0}>{c.count}</div>
        <div class="s">{c.description ?? ''}{c.bytes ? ` · ${fmtBytes(c.bytes)}` : ''}</div>
      </div>
    {/each}
  </div>

  {#each checks.filter((c) => c.count > 0) as c (c.id)}
    {@const items = open[c.id]}
    {@const sel = selected[c.id]?.size ?? 0}
    <div class="check">
      <div class="row hd">
        <button class="row expand" onclick={() => toggle(c)} aria-expanded={!!items}>
          {#if items}<ChevronDown size={18} />{:else}<ChevronRight size={18} />{/if}<b>{c.title}</b><span class="muted">· {c.count}</span>
        </button>
        <span class="sp"></span>
        {#if c.fix}
          {#if sel}<button class="btn sm" class:danger={c.fix.destructive} disabled={busy === c.id} onclick={() => fix(c, true)}>{c.fix.label} ({sel} selected)</button>{/if}
          <button class="btn sm" class:danger={c.fix.destructive} disabled={busy === c.id} onclick={() => fix(c, false)}>{c.fix.label} — all {c.count}</button>
        {:else}<span class="muted small">Fix these in your music files</span>{/if}
      </div>
      {#if items}
        {#if items.length}
          <div class="items">
            {#each items as it (it.id)}
              <label class="it">
                {#if c.fix}<input type="checkbox" checked={selected[c.id]?.has(it.id) ?? false} onchange={(e) => pick(c.id, it.id, e.currentTarget.checked)} />{/if}
                <span class="grow">
                  <span>{it.title}</span>{#if it.detail}<span class="muted"> · {it.detail}</span>{/if}
                  {#if it.path}<div class="mono muted">{it.path}</div>{/if}
                  {#if it.group}<div class="mono muted">{it.group}</div>{/if}
                </span>
                {#if it.bytes}<span class="small muted">{fmtBytes(it.bytes)}</span>{/if}
              </label>
            {/each}
          </div>
        {:else}<p class="muted small">No per-item list for this check.</p>{/if}
      {/if}
    </div>
  {/each}
{:else if !error}<p class="muted">Loading…</p>{/if}

<style>
  .clean {
    opacity: 0.6;
  }
  .check {
    border: 1px solid var(--line);
    border-radius: 12px;
    margin-top: 12px;
    padding: 8px 12px;
  }
  .expand {
    gap: 6px;
  }
  .sp {
    flex: 1;
  }
  .items {
    max-height: 320px;
    overflow-y: auto;
    margin-top: 6px;
  }
  .it {
    display: flex;
    gap: 10px;
    align-items: flex-start;
    padding: 6px 4px;
    border-top: 1px solid var(--line);
    cursor: pointer;
  }
  .grow {
    flex: 1;
    min-width: 0;
    overflow-wrap: anywhere;
  }
</style>
