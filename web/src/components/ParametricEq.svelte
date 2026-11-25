<script lang="ts">
  import { Plus, Trash2 } from '@lucide/svelte';
  import {
    MAX_PARAM_BANDS,
    PARAM_LABELS,
    PARAM_MAX_GAIN,
    PARAM_TYPES,
    autoHeadroomDb,
    bandsFromAutoEq,
    bandsFromGraphic,
    clampBandParams,
    curveDb,
    isPass,
    type ParamBand,
    type ParamType,
  } from '../lib/eq';
  import { player } from '../lib/player.svelte';

  // Response graph: log frequency 20 Hz – 20 kHz across, ±18 dB up.
  // Width follows the element so handles stay round.
  let gw = $state(616);
  const W = $derived(Math.max(200, gw - 16));
  const H = 220;
  const F_MIN = 20;
  const F_MAX = 20000;
  const DB = 18;
  const fx = (f: number) => (Math.log(f / F_MIN) / Math.log(F_MAX / F_MIN)) * W;
  const xf = (x: number) => F_MIN * Math.pow(F_MAX / F_MIN, Math.max(0, Math.min(1, x / W)));
  const dy = (db: number) => ((DB - Math.max(-DB, Math.min(DB, db))) / (2 * DB)) * H;
  const yd = (y: number) => DB - (Math.max(0, Math.min(H, y)) / H) * 2 * DB;
  const FREQS = Array.from({ length: 200 }, (_, i) => F_MIN * Math.pow(F_MAX / F_MIN, i / 199));
  const GRID_F = [50, 100, 200, 500, 1000, 2000, 5000, 10000];
  const GRID_DB = [-12, -6, 0, 6, 12];

  const eq = $derived(player.eq);
  const bands = $derived(eq.parametric ?? []);
  const path = $derived.by(() => {
    const c = curveDb(eq, FREQS, 48000);
    return FREQS.map((f, i) => `${i ? 'L' : 'M'}${fx(f).toFixed(1)},${dy(c[i]).toFixed(1)}`).join('');
  });
  const headroom = $derived(autoHeadroomDb(eq));
  let sel = $state(0);
  const cur = $derived(bands[Math.min(sel, bands.length - 1)] as ParamBand | undefined);

  const fmtF = (f: number) => (f >= 1000 ? `${+(f / 1000).toFixed(1)} k` : `${Math.round(f)}`);

  function setBands(next: ParamBand[]) {
    player.setEq({ parametric: next, enabled: true });
  }
  function setBand(i: number, patch: Partial<ParamBand>) {
    const next = bands.slice();
    next[i] = clampBandParams({ ...next[i], ...patch });
    setBands(next);
  }
  function addBand() {
    // A new band starts flat, at a spot nothing else is using, so adding it never changes the sound.
    const f = [1000, 250, 4000, 60, 10000, 500, 2000].find((c) => bands.every((b) => Math.abs(Math.log(b.fc / c)) > 0.3)) ?? 1000;
    setBands([...bands, clampBandParams({ type: 'PK', fc: f, gainDb: 0, q: 1 })]);
    sel = bands.length;
  }
  function removeBand(i: number) {
    setBands(bands.filter((_, j) => j !== i));
    sel = Math.max(0, Math.min(sel, bands.length - 2));
  }
  function setType(i: number, type: ParamType) {
    setBand(i, isPass({ type }) ? { type, q: Math.SQRT1_2 } : { type });
  }

  // Drag a handle: across = frequency, up/down = gain (pass filters only move across).
  let svg: SVGSVGElement | undefined = $state();
  let dragging = -1;
  function point(e: PointerEvent) {
    const r = svg!.getBoundingClientRect();
    return { x: ((e.clientX - r.left) / r.width) * W, y: ((e.clientY - r.top) / r.height) * H };
  }
  function down(e: PointerEvent, i: number) {
    e.stopPropagation();
    sel = i;
    dragging = i;
    svg?.setPointerCapture(e.pointerId);
  }
  function move(e: PointerEvent) {
    if (dragging < 0) return;
    const p = point(e);
    const b = bands[dragging];
    setBand(dragging, { fc: Math.round(xf(p.x) * 10) / 10, gainDb: isPass(b) ? b.gainDb : Math.round(yd(p.y) * 2) / 2 });
  }
  function up() {
    dragging = -1;
  }
  function wheel(e: WheelEvent, i: number) {
    // Wheel over a handle changes its Q (width).
    e.preventDefault();
    const b = bands[i];
    if (isPass(b) && b.slope !== 12) return;
    setBand(i, { q: Math.round(b.q * Math.pow(1.1, -Math.sign(e.deltaY)) * 100) / 100 });
  }

  // Log-scale sliders for frequency and Q.
  const fToS = (f: number) => Math.log(f / F_MIN) / Math.log(F_MAX / F_MIN);
  const sToF = (s: number) => Math.round(F_MIN * Math.pow(F_MAX / F_MIN, s) * 10) / 10;
  const qToS = (q: number) => Math.log(q / 0.1) / Math.log(200);
  const sToQ = (s: number) => Math.round(0.1 * Math.pow(200, s) * 100) / 100;
</script>

<div class="peq" class:off={!eq.enabled}>
  <div class="graph" bind:clientWidth={gw}>
    <svg
      bind:this={svg}
      viewBox="0 0 {W} {H}"
            role="application"
      aria-label="EQ curve: drag a handle to move a band, scroll on it to change Q"
      onpointermove={move}
      onpointerup={up}
      onpointercancel={up}
    >
      {#each GRID_F as f}<line class="grid" x1={fx(f)} x2={fx(f)} y1="0" y2={H} />{/each}
      {#each GRID_DB as d}<line class="grid" class:zero={d === 0} x1="0" x2={W} y1={dy(d)} y2={dy(d)} />{/each}
      <path d={path} class="curve" />
      {#each bands as b, i}
        <circle
          role="button"
          tabindex="-1"
          aria-label="Band {i + 1}"
          cx={fx(b.fc)}
          cy={dy(isPass(b) ? 0 : b.gainDb)}
          r={i === sel ? 9 : 6}
          class="handle"
          class:sel={i === sel}
          class:dim={!b.on}
          onpointerdown={(e) => down(e, i)}
          onwheel={(e) => wheel(e, i)}
        />
      {/each}
    </svg>
    <div class="axis muted small">
      {#each [[20, '20'], [100, '100'], [1000, '1 k'], [10000, '10 k']] as [f, l]}<span style:left="{(fx(+f) / W) * 100}%">{l}</span>{/each}
    </div>
    {#if headroom < -0.05}<span class="hr muted small">Auto headroom {headroom.toFixed(1)} dB</span>{/if}
  </div>

  <div class="list">
    {#each bands as b, i}
      <button class="chip" class:on={i === sel} class:dim={!b.on} onclick={() => (sel = i)}>{i + 1} · {PARAM_LABELS[b.type]} {fmtF(b.fc)}</button>
    {/each}
    {#if bands.length < MAX_PARAM_BANDS}<button class="chip" onclick={addBand}><Plus size={16} /> Band</button>{/if}
  </div>

  {#if cur}
    {@const i = Math.min(sel, bands.length - 1)}
    <div class="editor">
      <div class="row">
        <select aria-label="Filter type" value={cur.type} onchange={(e) => setType(i, e.currentTarget.value as ParamType)}>
          {#each PARAM_TYPES as t}<option value={t}>{PARAM_LABELS[t]}</option>{/each}
        </select>
        <label class="on"><input type="checkbox" class="switch" checked={cur.on} onchange={(e) => setBand(i, { on: e.currentTarget.checked })} /> On</label>
        <span class="grow"></span>
        <button class="icon-btn" aria-label="Remove band" onclick={() => removeBand(i)}><Trash2 size={18} /></button>
      </div>
      <label class="row"
        ><span class="k">Frequency</span>
        <input type="range" min="0" max="1" step="0.001" value={fToS(cur.fc)} oninput={(e) => setBand(i, { fc: sToF(+e.currentTarget.value) })} />
        <input class="num" type="number" min="10" max="22000" step="1" value={cur.fc} onchange={(e) => setBand(i, { fc: +e.currentTarget.value })} /> Hz</label
      >
      {#if !isPass(cur)}
        <label class="row"
          ><span class="k">Gain</span>
          <input type="range" min={-PARAM_MAX_GAIN} max={PARAM_MAX_GAIN} step="0.5" value={cur.gainDb} oninput={(e) => setBand(i, { gainDb: +e.currentTarget.value })} ondblclick={() => setBand(i, { gainDb: 0 })} />
          <input class="num" type="number" min={-PARAM_MAX_GAIN} max={PARAM_MAX_GAIN} step="0.1" value={cur.gainDb} onchange={(e) => setBand(i, { gainDb: +e.currentTarget.value })} /> dB</label
        >
      {:else}
        <div class="row">
          <span class="k">Slope</span>
          <div class="seg">
            {#each [12, 24, 48] as const as s}
              <button class:on={cur.slope === s} onclick={() => setBand(i, { slope: s })}>{s} dB/oct</button>
            {/each}
          </div>
        </div>
      {/if}
      {#if !isPass(cur) || cur.slope === 12}
        <label class="row"
          ><span class="k">Q</span>
          <input type="range" min="0" max="1" step="0.001" value={qToS(cur.q)} oninput={(e) => setBand(i, { q: sToQ(+e.currentTarget.value) })} />
          <input class="num" type="number" min="0.1" max="20" step="0.01" value={cur.q} onchange={(e) => setBand(i, { q: +e.currentTarget.value })} /></label
        >
      {/if}
    </div>
  {:else}
    <p class="muted small pad">No bands yet. Add one, or start from below.</p>
  {/if}

  <div class="row actions">
    <button class="btn text" onclick={() => setBands(bandsFromGraphic(eq.bands))}>Copy sliders</button>
    {#if eq.headphone}
      <!-- Moves the AutoEq correction into editable bands and turns the separate correction off, so it isn't doubled. -->
      <button class="btn text" onclick={() => player.setEq({ parametric: bandsFromAutoEq(eq.headphone!), headphone: null, enabled: true })}
        >Edit {eq.headphone.name} correction</button
      >
    {/if}
    {#if bands.length}<button class="btn text" onclick={() => setBands([])}>Clear</button>{/if}
  </div>
</div>

<style>
  .peq {
    max-width: 680px;
    margin: 0 auto;
    padding: 8px 16px 16px;
  }
  .peq.off {
    opacity: 0.6;
  }
  .graph {
    position: relative;
    background: var(--bg-1);
    border: 1px solid var(--line);
    border-radius: var(--radius);
    padding: 8px 8px 4px;
  }
  svg {
    display: block;
    width: 100%;
    height: 220px;
    touch-action: none;
    overflow: visible;
  }
  .grid {
    stroke: var(--line);
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
  }
  .grid.zero {
    stroke: var(--fg-3);
  }
  .curve {
    fill: none;
    stroke: var(--accent);
    stroke-width: 2.5;
    vector-effect: non-scaling-stroke;
  }
  .handle {
    fill: var(--fg);
    cursor: grab;
  }
  .handle:focus {
    outline: none;
  }
  .handle.sel {
    fill: var(--accent);
  }
  .handle.dim {
    fill: var(--fg-3);
  }
  .axis {
    position: relative;
    height: 1.2em;
  }
  .axis span {
    position: absolute;
    transform: translateX(-10%);
  }
  .hr {
    position: absolute;
    top: 6px;
    right: 10px;
  }
  .small {
    font-size: 0.8rem;
  }
  .list {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 12px 0;
  }
  .chip.dim {
    opacity: 0.55;
  }
  .editor {
    display: grid;
    gap: 8px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .row input[type='range'] {
    flex: 1;
    min-width: 120px;
  }
  .k {
    width: 80px;
  }
  .num {
    width: 80px;
  }
  .grow {
    flex: 1;
  }
  .on {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .seg {
    display: inline-flex;
    border: 1px solid var(--line);
    border-radius: 8px;
    overflow: hidden;
  }
  .seg button {
    background: none;
    border: 0;
    color: var(--fg-2);
    padding: 6px 10px;
  }
  .seg button.on {
    color: var(--accent);
    background: rgba(74, 222, 128, 0.12);
  }
  .actions {
    padding-top: 8px;
  }
  .pad {
    padding: 8px 0;
  }
</style>
