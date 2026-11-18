<script lang="ts">
  import { ArrowLeft, AudioLines, Headphones, Waves } from '@lucide/svelte';
  import { EQ_LABELS, EQ_MAX_DB, PRESET_NAMES, PRESETS, balanceLabel, presetFor, type PreampMode } from '../lib/eq';
  import { player } from '../lib/player.svelte';
  import { ui } from '../lib/ui.svelte';

  let page = $state<'bands' | 'effects'>('bands');
  const eq = $derived(player.eq);

  function setBand(i: number, v: number) {
    const bands = eq.bands.slice();
    bands[i] = Math.round(v * 10) / 10;
    player.setEq({ bands, preset: presetFor(bands), enabled: true });
  }
  function preset(name: string) {
    if (name === 'Custom') return;
    player.setEq({ bands: [...PRESETS[name]], preset: name, enabled: true });
  }

  // Rotary knob: drag vertically or use the wheel / arrow keys.
  function knob(node: HTMLElement, opts: { get: () => number; set: (v: number) => void; min: number; max: number }) {
    let o = opts;
    let startY = 0;
    let startV = 0;
    const down = (e: PointerEvent) => {
      node.setPointerCapture(e.pointerId);
      startY = e.clientY;
      startV = o.get();
    };
    const move = (e: PointerEvent) => {
      if (!node.hasPointerCapture(e.pointerId)) return;
      const range = o.max - o.min;
      o.set(Math.max(o.min, Math.min(o.max, startV + ((startY - e.clientY) / 150) * range)));
    };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const step = (o.max - o.min) / 50;
      o.set(Math.max(o.min, Math.min(o.max, o.get() - Math.sign(e.deltaY) * step)));
    };
    const key = (e: KeyboardEvent) => {
      const step = (o.max - o.min) / 50;
      if (e.key === 'ArrowUp' || e.key === 'ArrowRight') o.set(Math.min(o.max, o.get() + step));
      else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') o.set(Math.max(o.min, o.get() - step));
      else return;
      e.preventDefault();
      e.stopPropagation();
    };
    node.addEventListener('pointerdown', down);
    node.addEventListener('pointermove', move);
    node.addEventListener('wheel', wheel, { passive: false });
    node.addEventListener('keydown', key);
    return {
      update(n: typeof opts) {
        o = n;
      },
      destroy() {
        node.removeEventListener('pointerdown', down);
        node.removeEventListener('pointermove', move);
        node.removeEventListener('wheel', wheel);
        node.removeEventListener('keydown', key);
      },
    };
  }
  const angle = (v: number, min: number, max: number) => -135 + ((v - min) / (max - min)) * 270;
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>Equalizer</h1>
  </div>

  <div class="device">
    <Headphones size={22} color="var(--accent)" />
    {#if player.outputs.length}
      <select
        aria-label="Output device"
        value={player.outputs.find((o) => o.label.replace(/^Default - /, '') === player.outputKey)?.deviceId ?? ''}
        onchange={(e) => {
          const o = player.outputs.find((x) => x.deviceId === e.currentTarget.value);
          if (o) void player.setSink(o.deviceId, o.label);
        }}
      >
        {#each player.outputs as o (o.deviceId)}<option value={o.deviceId}>{o.label}</option>{/each}
      </select>
    {:else}
      <span class="name">{player.outputKey}</span>
      <button class="btn text small" onclick={() => player.revealOutputs()}>Pick output</button>
    {/if}
    <span class="sep"></span>
    <input type="checkbox" class="switch" aria-label="Equalizer on" checked={eq.enabled} onchange={(e) => player.setEq({ enabled: e.currentTarget.checked })} />
  </div>
  <p class="muted note">Settings are saved per output device. {player.sinkSupported ? '' : 'This browser cannot switch outputs; the profile follows the system default.'}</p>

  {#if page === 'bands'}
    <div class="chips">
      <span class="chip"><Waves size={20} /> 10</span>
      <select class="chip sel" aria-label="Preset" value={presetFor(eq.bands)} onchange={(e) => preset(e.currentTarget.value)}>
        {#each PRESET_NAMES as p}<option value={p} disabled={p === 'Custom'}>{p}</option>{/each}
      </select>
    </div>
    <div class="bands" class:off={!eq.enabled}>
      {#each EQ_LABELS as label, i}
        <div class="band">
          <div class="lbl">{label}<br /><b>{eq.bands[i].toFixed(1)}</b></div>
          <input
            type="range"
            class="vert"
            min={-EQ_MAX_DB}
            max={EQ_MAX_DB}
            step="0.5"
            value={eq.bands[i]}
            aria-label="{label} Hz"
            oninput={(e) => setBand(i, +e.currentTarget.value)}
            ondblclick={() => setBand(i, 0)}
          />
        </div>
      {/each}
    </div>
  {:else}
    <div class="knobs">
      <div class="k">
        <div class="knob" tabindex="0" role="slider" aria-label="Bass boost" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(eq.bassBoost)}
          use:knob={{ get: () => eq.bassBoost, set: (v) => player.setEq({ bassBoost: Math.round(v), enabled: true }), min: 0, max: 100 }}>
          <span class="tick" style:transform="rotate({angle(eq.bassBoost, 0, 100)}deg)"></span>
        </div>
        <b>Bass boost</b><span>{Math.round(eq.bassBoost)}%</span>
      </div>
      <div class="k">
        <div class="knob" tabindex="0" role="slider" aria-label="Surround sound" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(eq.surround)}
          use:knob={{ get: () => eq.surround, set: (v) => player.setEq({ surround: Math.round(v), enabled: true }), min: 0, max: 100 }}>
          <span class="tick" style:transform="rotate({angle(eq.surround, 0, 100)}deg)"></span>
        </div>
        <b>Surround Sound</b><span>{Math.round(eq.surround)}%</span>
      </div>
    </div>
    <hr />
    <div class="center">
      <b>Preamp</b>
      <div class="seg">
        {#each [['headroom', 'Headroom', '-6 dB'], ['off', 'Off', '0 dB'], ['boost', 'Boost', '+1 dB']] as [k, a, b]}
          <button class:on={eq.preampMode === k} onclick={() => player.setEq({ preampMode: k as PreampMode, enabled: true })}>{a}<br />{b}</button>
        {/each}
      </div>
      <input type="range" min="-12" max="12" step="0.5" value={eq.preampDb} style:--p="{((eq.preampDb + 12) / 24) * 100}%" aria-label="Preamp adjust" oninput={(e) => player.setEq({ preampDb: +e.currentTarget.value, enabled: true })} />
      <span class="muted">{eq.preampDb > 0 ? '+' : ''}{eq.preampDb} dB</span>
    </div>
    <hr />
    <div class="center">
      <b>Volume</b>
      <input type="range" min="0" max="1" step="0.01" value={player.volume} style:--p="{player.volume * 100}%" style:--fill="var(--accent)" style:--thumb="var(--accent)" aria-label="Volume" oninput={(e) => player.setVolume(+e.currentTarget.value)} />
    </div>
    <hr />
    <div class="knobs">
      <div class="seg vertical">
        <button class:on={!eq.mono} onclick={() => player.setEq({ mono: false })}>Stereo</button>
        <button class:on={eq.mono} onclick={() => player.setEq({ mono: true })}>Mono</button>
      </div>
      <div class="k">
        <div class="knob" tabindex="0" role="slider" aria-label="Channel balance" aria-valuemin={-100} aria-valuemax={100} aria-valuenow={Math.round(eq.balance * 100)}
          use:knob={{ get: () => eq.balance, set: (v) => player.setEq({ balance: Math.abs(v) < 0.03 ? 0 : Math.round(v * 100) / 100 }), min: -1, max: 1 }}
          ondblclick={() => player.setEq({ balance: 0 })}>
          <span class="tick" style:transform="rotate({angle(eq.balance, -1, 1)}deg)"></span>
        </div>
        <b>Audio channel balance</b><span>{balanceLabel(eq.balance)}</span>
      </div>
    </div>
  {/if}

  <div class="pages">
    <button class:on={page === 'bands'} aria-label="10-band equalizer" onclick={() => (page = 'bands')}><AudioLines size={26} /></button>
    <button class:on={page === 'effects'} aria-label="Effects" onclick={() => (page = 'effects')}><span class="dotknob"></span></button>
  </div>
</div>

<style>
  .device {
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 12px;
    padding: 10px 14px;
    border: 1.5px solid #444;
    border-radius: 12px;
  }
  .device select {
    flex: 1;
    background: none;
    border: 0;
    color: var(--accent);
    font-size: 1.1rem;
  }
  .name {
    flex: 1;
    color: var(--accent);
    font-size: 1.1rem;
  }
  .sep {
    width: 1px;
    height: 28px;
    background: #444;
  }
  .note {
    margin: 0 16px 8px;
    font-size: 0.8rem;
  }
  .small {
    padding: 4px 8px;
    font-size: 0.8rem;
  }
  .chips {
    display: flex;
    gap: 12px;
    padding: 4px 16px;
  }
  .chip.sel {
    background: none;
    color: #fff;
    padding: 8px 18px;
  }
  .bands {
    display: grid;
    grid-template-columns: repeat(10, 1fr);
    gap: 2px;
    padding: 24px 8px;
    max-width: 640px;
    margin: 0 auto;
  }
  .bands.off {
    opacity: 0.45;
  }
  .band {
    display: grid;
    justify-items: center;
    gap: 16px;
  }
  .lbl {
    text-align: center;
    font-size: 0.8rem;
    color: var(--fg-2);
  }
  .lbl b {
    font-weight: 500;
  }
  .vert {
    writing-mode: vertical-lr;
    direction: rtl;
    width: 32px;
    height: 300px;
  }
  .vert::-webkit-slider-runnable-track {
    width: 4px;
    height: 100%;
    background: linear-gradient(to top, var(--accent) 50%, #666 50%);
  }
  .vert::-webkit-slider-thumb {
    width: 34px;
    height: 54px;
    border-radius: 12px;
    background: #000;
    border: 2px solid #ccc;
    margin-left: -15px;
    margin-top: 0;
  }
  .vert::-moz-range-thumb {
    width: 30px;
    height: 50px;
    border-radius: 12px;
    background: #000;
    border: 2px solid #ccc;
  }
  .vert::-moz-range-progress {
    background: var(--accent);
    width: 4px;
  }
  .knobs {
    display: flex;
    justify-content: space-around;
    align-items: center;
    padding: 24px 12px;
    flex-wrap: wrap;
    gap: 16px;
  }
  .k {
    display: grid;
    justify-items: center;
    gap: 6px;
    text-align: center;
  }
  .knob {
    width: 150px;
    height: 150px;
    border-radius: 50%;
    background: #1c1c1c;
    border: 3px solid #555;
    position: relative;
    cursor: ns-resize;
    touch-action: none;
  }
  .tick {
    position: absolute;
    left: 50%;
    top: 50%;
    width: 4px;
    height: 70px;
    margin-left: -2px;
    transform-origin: 50% 0;
    translate: 0 -100%;
  }
  .tick::before {
    content: '';
    position: absolute;
    top: 8px;
    left: 0;
    width: 4px;
    height: 22px;
    border-radius: 2px;
    background: var(--accent);
  }
  .tick {
    transform-origin: 50% 100%;
  }
  hr {
    border: 0;
    border-top: 1px solid var(--line);
    margin: 4px 15%;
  }
  .center {
    display: grid;
    justify-items: center;
    gap: 12px;
    padding: 14px 15%;
  }
  .seg {
    display: flex;
    border: 1.5px solid #555;
    border-radius: 10px;
    overflow: hidden;
  }
  .seg button {
    padding: 8px 18px;
    color: var(--fg-3);
    letter-spacing: 0.06em;
  }
  .seg button.on {
    color: var(--accent);
    outline: 1.5px solid var(--accent);
    outline-offset: -1.5px;
  }
  .seg.vertical {
    flex-direction: column;
  }
  .seg.vertical button {
    padding: 14px 34px;
  }
  .seg.vertical button.on {
    background: #0f2a1a;
  }
  .pages {
    display: flex;
    justify-content: center;
    gap: 80px;
    border-top: 1px solid var(--line);
    margin: 16px 20%;
    padding-top: 10px;
  }
  .pages button {
    color: #777;
  }
  .pages button.on {
    color: #fff;
  }
  .dotknob {
    display: inline-block;
    width: 26px;
    height: 26px;
    border-radius: 50%;
    background: currentColor;
  }
</style>
