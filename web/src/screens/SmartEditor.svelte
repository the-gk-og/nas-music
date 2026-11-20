<script lang="ts">
  // v7 smart playlist rule editor: simple rows (field · operator · value), AND/OR groups, sort, limit,
  // presets to start from, and a live preview count from the server (debounced).
  // param: '' = new, 'preset:<id>' = new from a preset, else the smart playlist id to edit.
  import { ArrowLeft, Plus, Trash2, X } from '@lucide/svelte';
  import { library } from '../lib/library.svelte';
  import { session } from '../lib/session.svelte';
  import { emptyDefinition, fmtDuration, incomplete, isGroup, newRule, OP_LABELS, retarget } from '../lib/smart';
  import type { SmartDefinition, SmartFields, SmartFieldSpec, SmartGroup, SmartPreview, SmartRule, SmartUnit } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let { param }: { param: string } = $props();
  const editingId = $derived(param && !param.startsWith('preset:') ? param : null);

  let cat = $state<SmartFields | null>(null);
  let name = $state('');
  let def = $state<SmartDefinition>(emptyDefinition());
  let preset = $state<string | null>(null);
  let preview = $state<SmartPreview | null>(null);
  let previewErr = $state<string | null>(null);
  let saving = $state(false);
  let genres = $state<string[]>([]);
  let loadErr = $state<string | null>(null);

  const fields = $derived(cat?.fields ?? []);
  const spec = (id: string): SmartFieldSpec | undefined => fields.find((f) => f.id === id);
  const playlistOptions = $derived([
    ...library.playlists.map((p) => ({ id: p.id, name: p.name })),
    ...library.smartPlaylists.filter((p) => p.id !== editingId).map((p) => ({ id: p.id, name: `${p.name} (smart)` })),
  ]);

  $effect(() => {
    const p = param;
    void (async () => {
      try {
        const c = await session.api.smartFields();
        cat = c;
        if (p.startsWith('preset:')) usePreset(p.slice(7));
        else if (p) {
          const sp = await session.api.smartPlaylist(p);
          name = sp.name;
          def = sp.definition;
          preset = sp.preset ?? null;
        }
      } catch (e) {
        loadErr = (e as Error).message;
      }
    })();
    void library.refreshPlaylists();
    void library.refreshSmart();
    session.api
      .genres()
      .then((g) => (genres = g.map((x) => (typeof x === 'string' ? x : x.name))))
      .catch(() => {});
  });

  function usePreset(pid: string) {
    const pr = cat?.presets.find((x) => x.id === pid);
    if (!pr) return;
    def = structuredClone(pr.definition);
    preset = pr.id;
    if (!name.trim() || cat?.presets.some((x) => x.name === name)) name = pr.name;
  }

  // ---- live preview (debounced) ----
  $effect(() => {
    const snapshot = JSON.stringify(def);
    if (!cat) return;
    if (incomplete(def, fields)) {
      previewErr = 'Finish the highlighted rules to see matching songs';
      return;
    }
    const t = setTimeout(() => {
      session.api
        .previewSmartPlaylist(JSON.parse(snapshot), editingId, 8)
        .then((p) => {
          preview = p;
          previewErr = null;
        })
        .catch((e) => (previewErr = e.message));
    }, 350);
    return () => clearTimeout(t);
  });

  // ---- tree edits (immutable updates so $state notices) ----
  function update(g: SmartGroup, i: number, r: SmartRule | SmartGroup) {
    g.rules[i] = r;
  }
  function addRule(g: SmartGroup) {
    const f = spec('playCount') ?? fields[0];
    if (f) g.rules.push(newRule(f));
  }
  function addGroup(g: SmartGroup) {
    const f = spec('favourite') ?? fields[0];
    g.rules.push({ match: 'any', rules: f ? [newRule(f)] : [] });
  }
  function remove(g: SmartGroup, i: number) {
    g.rules.splice(i, 1);
  }

  function setNum(r: SmartRule, v: string, idx?: number, pct = false) {
    const n = v === '' ? NaN : Number(v) / (pct ? 100 : 1);
    if (idx === undefined) r.value = n;
    else {
      const arr = (Array.isArray(r.value) ? [...r.value] : [0, 0]) as [number, number];
      arr[idx] = n;
      r.value = arr;
    }
  }
  const shown = (v: unknown, pct: boolean) => (typeof v === 'number' && Number.isFinite(v) ? (pct ? Math.round(v * 100) : v) : '');

  function bad(r: SmartRule) {
    return incomplete({ match: 'all', rules: [r] }, fields);
  }

  async function save() {
    if (!name.trim()) return ui.toast('Give it a name');
    if (incomplete(def, fields)) return ui.toast('Some rules are not finished');
    saving = true;
    try {
      const sp = editingId
        ? await session.api.updateSmartPlaylist(editingId, { name: name.trim(), definition: def })
        : await session.api.createSmartPlaylist(name.trim(), def, preset);
      void library.refreshSmart();
      ui.go('smart', sp.id);
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      saving = false;
    }
  }
</script>

{#snippet valueInput(r: SmartRule)}
  {@const f = spec(r.field)}
  {#if f}
    {#if r.op === 'never' || r.op === 'ever'}
      <span class="none"></span>
    {:else if f.type === 'number' || f.type === 'percent'}
      {@const pct = f.type === 'percent'}
      {#if r.op === 'between'}
        <span class="pair">
          <input type="number" aria-label="From" value={shown(Array.isArray(r.value) ? r.value[0] : null, pct)} oninput={(e) => setNum(r, e.currentTarget.value, 0, pct)} />
          <span>and</span>
          <input type="number" aria-label="To" value={shown(Array.isArray(r.value) ? r.value[1] : null, pct)} oninput={(e) => setNum(r, e.currentTarget.value, 1, pct)} />
          {#if pct}<span>%</span>{/if}
        </span>
      {:else}
        <span class="pair">
          <input type="number" aria-label="Value" min={pct ? 0 : undefined} max={pct ? 100 : undefined} value={shown(r.value, pct)} oninput={(e) => setNum(r, e.currentTarget.value, undefined, pct)} />
          {#if pct}<span>%</span>{:else if f.unit}<span>{f.unit}</span>{/if}
        </span>
      {/if}
    {:else if f.type === 'text'}
      <input type="text" aria-label="Value" list={f.suggest === 'genres' ? 'sp-genres' : undefined} value={String(r.value ?? '')} oninput={(e) => (r.value = e.currentTarget.value)} placeholder={f.label} />
    {:else if f.type === 'date'}
      {#if r.op === 'inLast' || r.op === 'notInLast'}
        <span class="pair">
          <input type="number" min="1" aria-label="How many" value={shown(r.value, false)} oninput={(e) => setNum(r, e.currentTarget.value)} />
          <select aria-label="Unit" value={r.unit ?? 'days'} onchange={(e) => (r.unit = e.currentTarget.value as SmartUnit)}>
            {#each cat?.units ?? [] as u}<option value={u}>{u}</option>{/each}
          </select>
        </span>
      {:else}
        <input type="date" aria-label="Date" value={String(r.value ?? '')} onchange={(e) => (r.value = e.currentTarget.value)} />
      {/if}
    {:else if f.type === 'bool'}
      <select aria-label="Value" value={r.value ? 'yes' : 'no'} onchange={(e) => (r.value = e.currentTarget.value === 'yes')}>
        <option value="yes">yes</option><option value="no">no</option>
      </select>
    {:else if f.type === 'enum'}
      <select aria-label="Value" value={String(r.value)} onchange={(e) => (r.value = e.currentTarget.value)}>
        {#each f.options ?? [] as o}<option value={o}>{f.id === 'decade' ? `${o}s` : o}</option>{/each}
      </select>
    {:else if f.type === 'playlist'}
      <select aria-label="Playlist" value={String(r.value ?? '')} onchange={(e) => (r.value = e.currentTarget.value)}>
        <option value="" disabled>Choose a playlist</option>
        {#each playlistOptions as p (p.id)}<option value={p.id}>{p.name}</option>{/each}
      </select>
    {/if}
  {/if}
{/snippet}

{#snippet group(g: SmartGroup, depth: number)}
  <div class="group" class:nested={depth > 0}>
    <div class="match">
      Match
      <select aria-label="Match" value={g.match} onchange={(e) => (g.match = e.currentTarget.value as 'all' | 'any')}>
        <option value="all">all</option><option value="any">any</option>
      </select>
      of these rules
    </div>
    {#each g.rules as r, i (i)}
      {#if isGroup(r)}
        <div class="sub">
          {@render group(r, depth + 1)}
          <button class="icon-btn rm" aria-label="Remove group" onclick={() => remove(g, i)}><X size={18} /></button>
        </div>
      {:else}
        <div class="row" class:bad={bad(r)}>
          <select aria-label="Field" value={r.field} onchange={(e) => { const f = spec(e.currentTarget.value); if (f) update(g, i, retarget(r, f)); }}>
            {#each fields as f (f.id)}<option value={f.id}>{f.label}</option>{/each}
          </select>
          <select aria-label="Operator" value={r.op} onchange={(e) => { const f = spec(r.field); if (f) update(g, i, retarget(r, f, e.currentTarget.value)); }}>
            {#each spec(r.field)?.ops ?? [] as o}<option value={o}>{OP_LABELS[o] ?? o}</option>{/each}
          </select>
          {@render valueInput(r)}
          <button class="icon-btn rm" aria-label="Remove rule" onclick={() => remove(g, i)}><Trash2 size={18} /></button>
        </div>
      {/if}
    {/each}
    <div class="adds">
      <button class="btn text" onclick={() => addRule(g)}><Plus size={16} /> Add rule</button>
      {#if depth === 0}<button class="btn text" onclick={() => addGroup(g)}><Plus size={16} /> Add group (any/all)</button>{/if}
    </div>
  </div>
{/snippet}

<div class="page editor">
  <div class="top">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={22} /></button>
    <h1>{editingId ? 'Edit smart playlist' : 'New smart playlist'}</h1>
  </div>
  {#if loadErr}<div class="empty">{loadErr}</div>
  {:else if !cat}<div class="empty">Loading…</div>
  {:else}
    <datalist id="sp-genres">{#each genres as g}<option value={g}></option>{/each}</datalist>
    <label class="name">Name <input type="text" bind:value={name} maxlength="200" placeholder="My smart playlist" /></label>
    {#if !editingId}
      <div class="presets">
        <span>Start from:</span>
        {#each cat.presets as p (p.id)}
          <button class="chip" class:on={preset === p.id} title={p.description} onclick={() => usePreset(p.id)}>{p.name}</button>
        {/each}
      </div>
    {/if}

    {@render group(def, 0)}

    <div class="opts">
      <label>Sort by
        <select bind:value={def.sort}>{#each cat.sorts as s (s.id)}<option value={s.id}>{s.label}</option>{/each}</select>
      </label>
      <label>Limit
        <select value={def.limit.type} onchange={(e) => { const t = e.currentTarget.value as 'songs' | 'minutes' | 'none'; def.limit = t === 'none' ? { type: 'none' } : { type: t, value: def.limit.value ?? (t === 'songs' ? 50 : 60) }; }}>
          <option value="none">No limit</option><option value="songs">Songs</option><option value="minutes">Minutes</option>
        </select>
        {#if def.limit.type !== 'none'}
          <input type="number" min="1" aria-label="Limit" value={def.limit.value} oninput={(e) => (def.limit = { ...def.limit, value: Number(e.currentTarget.value) || 1 })} />
        {/if}
      </label>
    </div>

    <div class="preview" aria-live="polite">
      {#if previewErr}<span class="warn">{previewErr}</span>
      {:else if preview}
        <strong>{preview.trackCount} song{preview.trackCount === 1 ? '' : 's'}</strong> · {fmtDuration(preview.durationS)}
        {#if preview.tracks.length}
          <ul>{#each preview.tracks as t (t.id)}<li>{t.title} <span>— {t.artist}</span></li>{/each}</ul>
        {/if}
      {:else}Counting…{/if}
    </div>

    <div class="actions">
      <button class="btn" onclick={() => ui.back()}>Cancel</button>
      <button class="btn primary" disabled={saving} onclick={save}>{editingId ? 'Save' : 'Create'}</button>
    </div>
  {/if}
</div>

<style>
  .editor {
    --gutter: clamp(16px, 3vw, 32px);
    padding: 12px var(--gutter) 40px;
    max-width: 920px;
  }
  .top {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  h1 {
    font-size: 1.5rem;
    margin: 0;
  }
  .name {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin: 16px 0 10px;
    font-weight: 600;
  }
  .presets {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    margin-bottom: 14px;
    color: var(--fg-2);
  }
  .group {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .group.nested {
    flex: 1;
    border-left: 3px solid var(--line);
    padding-left: 12px;
  }
  .sub {
    display: flex;
    gap: 6px;
    align-items: flex-start;
  }
  .match {
    display: flex;
    gap: 8px;
    align-items: center;
    color: var(--fg-2);
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .row.bad :is(input, select) {
    border-color: #c55;
  }
  .row select,
  .row input[type='text'],
  .row input[type='date'] {
    min-width: 0;
  }
  .pair {
    display: inline-flex;
    gap: 6px;
    align-items: center;
  }
  .pair input[type='number'] {
    width: 90px;
  }
  .adds {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .adds button {
    display: inline-flex;
    gap: 4px;
    align-items: center;
  }
  .opts {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    margin: 18px 0 10px;
  }
  .opts label {
    display: flex;
    gap: 8px;
    align-items: center;
  }
  .opts input {
    width: 90px;
  }
  .preview {
    margin: 12px 0;
    padding: 12px 14px;
    border-radius: 10px;
    background: var(--bg-2);
  }
  .preview ul {
    margin: 8px 0 0;
    padding-left: 18px;
    color: var(--fg-2);
    font-size: 0.9rem;
  }
  .preview li span {
    color: var(--fg-3, var(--fg-2));
  }
  .warn {
    color: #e0a040;
  }
  .actions {
    display: flex;
    gap: 10px;
    justify-content: flex-end;
  }
  @media (max-width: 600px) {
    .row > select:first-child {
      flex: 1 1 100%;
    }
  }
</style>
