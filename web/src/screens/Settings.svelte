<script lang="ts">
  import { ArrowDown, ArrowLeft, ArrowUp } from '@lucide/svelte';
  import { appearance } from '../lib/appearance.svelte';
  import ScrobbleSettings from '../components/ScrobbleSettings.svelte';
  import { family } from '../lib/family.svelte';
  import { ACCENTS, HOME_SHELVES, HOME_SHELF_TITLES, moveInList, type Motion } from '../lib/v3';
  import { WEB_SIGNALS, type SignalKey } from '../lib/events';
  import { player } from '../lib/player.svelte';
  import { QUALITIES, resolveQuality, specLabel, type NetKind, type Quality } from '../lib/quality';
  import { preferredFormat } from '../lib/platform';
  import type { LevellingMode } from '../lib/listening';
  import type { TransitionMode } from '../lib/automix';
  import { session } from '../lib/session.svelte';
  import { ui } from '../lib/ui.svelte';
  import VoiceSettings from '../components/VoiceSettings.svelte';

  import type { DataSummary } from '../lib/types';

  let deviceName = $state(session.saved.deviceName);
  let summary = $state<DataSummary | null>(null);
  const day = (s?: string | null) => (s ? new Date(s).toLocaleDateString() : '—');
  $effect(() => {
    session.api.dataSummary().then((s) => (summary = s)).catch(() => (summary = null));
  });

  // Every context signal (also the Android-only ones), for "Erase collected context".
  const ALL_SIGNALS = ['listening', 'output', 'device', 'motion', 'location', 'weather', 'search', 'time', 'health'];

  const shelfOrder = $derived([
    ...appearance.prefs.homeShelves.order.filter((x) => (HOME_SHELVES as readonly string[]).includes(x)),
    ...HOME_SHELVES.filter((x) => !appearance.prefs.homeShelves.order.includes(x)),
  ]);
  function moveShelf(i: number, d: number) {
    void appearance.set('homeShelves', { ...appearance.prefs.homeShelves, order: moveInList(shelfOrder, i, i + d) });
  }
  function toggleShelf(id: string, on: boolean) {
    const hidden = appearance.prefs.homeShelves.hidden.filter((x) => x !== id);
    void appearance.set('homeShelves', { order: shelfOrder, hidden: on ? hidden : [...hidden, id] });
  }

  const allOn = $derived(WEB_SIGNALS.every((s) => player.signals[s.key]));

  async function exportData() {
    try {
      const data = await session.api.dataExport();
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `nas-music-data-${session.saved.username}.json`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async function erase(signal: string) {
    const all = signal === 'all';
    const ok = await ui.confirm(
      all ? 'Erase everything' : 'Erase collected context',
      all
        ? 'This erases all collected context AND your listening history on the server. Your mixes will start over. This cannot be undone.'
        : 'This erases the collected context signals on the server (your play history stays). This cannot be undone.',
      'Erase',
    );
    if (!ok) return;
    try {
      if (signal === 'context') for (const s of ALL_SIGNALS) await session.api.deleteData(s);
      else await session.api.deleteData(signal);
      ui.toast('Erased');
      summary = await session.api.dataSummary().catch(() => null);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
</script>

<div class="page">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>Settings</h1>
  </div>

  <div class="section-title">This device</div>
  <div class="pad form">
    <label>Device name (shown in Devices)
      <input bind:value={deviceName} onchange={() => session.setDeviceName(deviceName.trim() || session.saved.deviceName)} />
    </label>
    <div class="muted small">Server: {session.isMock ? 'built-in demo (mock)' : session.saved.origin || location.origin} · signed in as {session.saved.username}</div>
  </div>

  <div class="section-title">Appearance</div>
  <div class="pad form">
    <div class="opt">
      <div><div>Animations</div><div class="muted small">{appearance.reducedMotion ? 'Your system asks for reduced motion, so animations are off.' : 'Off is instant; Quick keeps transitions under 200 ms; Expressive adds parallax and animated lyrics.'}</div></div>
      <div class="seg" role="radiogroup" aria-label="Animations">
        {#each [['off', 'Off'], ['quick', 'Quick'], ['expressive', 'Expressive']] as [k, label]}
          <button role="radio" aria-checked={appearance.prefs.animations === k} class:on={appearance.prefs.animations === k} onclick={() => appearance.set('animations', k as Motion)}>{label}</button>
        {/each}
      </div>
    </div>
    <div class="opt">
      <div>Default view</div>
      <div class="seg">
        {#each [['grid', 'Grid'], ['list', 'List']] as [k, label]}
          <button class:on={appearance.prefs.defaultView === k} onclick={() => appearance.set('defaultView', k as 'grid' | 'list')}>{label}</button>
        {/each}
      </div>
    </div>
    <div class="opt">
      <div>Album page style</div>
      <div class="seg">
        {#each [['big', 'Big header'], ['simple', 'Simple list']] as [k, label]}
          <button class:on={appearance.prefs.albumStyle === k} onclick={() => appearance.set('albumStyle', k as 'big' | 'simple')}>{label}</button>
        {/each}
      </div>
    </div>
    <label class="sig inline">
      <div><div>Classical mode</div><div class="muted small">Group albums by work with a play-whole-work button, show movement names, put the composer before the performer, and add Composers to the library.</div></div>
      <input type="checkbox" class="switch" checked={appearance.prefs.classical} onchange={(e) => appearance.set('classical', e.currentTarget.checked)} />
    </label>
    <div class="opt">
      <div>Accent colour</div>
      <div class="swatches">
        {#each Object.entries(ACCENTS) as [name, c]}
          <button class="sw" class:on={appearance.prefs.accent.toLowerCase() === name} style:background={c} aria-label={name} title={name[0].toUpperCase() + name.slice(1)} onclick={() => appearance.set('accent', name)}></button>
        {/each}
      </div>
    </div>
    <div>
      <div>Home shelves</div>
      <div class="muted small">Order and visibility. Empty shelves hide themselves.</div>
      {#each shelfOrder as id, i (id)}
        <div class="shelfrow">
          <input type="checkbox" class="switch" aria-label="Show {HOME_SHELF_TITLES[id]}" checked={!appearance.prefs.homeShelves.hidden.includes(id)} onchange={(e) => toggleShelf(id, e.currentTarget.checked)} />
          <span class="grow">{HOME_SHELF_TITLES[id]}</span>
          <button class="icon-btn" aria-label="Move up" disabled={i === 0} onclick={() => moveShelf(i, -1)}><ArrowUp size={18} /></button>
          <button class="icon-btn" aria-label="Move down" disabled={i === shelfOrder.length - 1} onclick={() => moveShelf(i, 1)}><ArrowDown size={18} /></button>
        </div>
      {/each}
    </div>
    <label class="sig inline">
      <div><div>Only on this device</div><div class="muted small">Keep these appearance settings here instead of syncing them to your other devices.</div></div>
      <input type="checkbox" class="switch" checked={appearance.localOnly} onchange={(e) => appearance.setLocalOnly(e.currentTarget.checked)} />
    </label>
  </div>

  <div class="section-title">Streaming quality</div>
  <div class="pad form">
    {#each [['home', 'At home', 'Using the server\'s home (LAN) address'], ['remote', 'Remote', 'Using the public / tunnel address']] as [k, label, hint]}
      {@const net = k as NetKind}
      <div class="opt">
        <div>
          <div>{label}{#if player.netKind === net}<span class="muted small"> · now</span>{/if}</div>
          <div class="muted small">{hint}{#if player.quality[net] === 'auto'} · Auto = {specLabel(resolveQuality('auto', net))}{/if}</div>
        </div>
        <select aria-label="{label} quality" value={player.quality[net]} onchange={(e) => player.setQuality(net, e.currentTarget.value as Quality)}>
          {#each QUALITIES as q}<option value={q.key}>{q.label}</option>{/each}
        </select>
      </div>
    {/each}
    <div class="muted small">
      Lossless plays the original file; Opus is transcoded on the server and cached. Changes apply from the next song.
      {#if preferredFormat() !== 'opus'}This browser can't play Opus, so it always gets the original file.{/if}
    </div>
  </div>

  <div class="section-title">Playback</div>
  <div class="pad form">
    <div class="opt">
      <div>
        <div>Transitions</div>
        <div class="muted small">
          {#if player.listening.transition === 'off'}Gapless: each song follows straight on.
          {:else if player.listening.transition === 'crossfade'}Blend the end of a song into the next.
          {:else}DJ automix: beat-matched mixes from each song's outro into the next one's intro, with a bass swap. Songs the server hasn't analysed yet, or that don't match, get a smooth crossfade.{/if}
          Songs that follow each other on the same album always play gapless.
        </div>
      </div>
      <div class="seg" role="radiogroup" aria-label="Transitions">
        {#each [['off', 'Off'], ['crossfade', 'Crossfade'], ['dj', 'DJ automix']] as [k, label]}
          <button role="radio" aria-checked={player.listening.transition === k} class:on={player.listening.transition === k}
            onclick={() => player.setListening({ transition: k as TransitionMode, crossfadeS: k === 'crossfade' && !player.listening.crossfadeS ? 6 : player.listening.crossfadeS })}>{label}</button>
        {/each}
      </div>
    </div>
    {#if player.listening.transition !== 'off'}
      <div>
        <div>{player.listening.transition === 'dj' ? 'Crossfade when songs don’t match' : 'Crossfade'}: {player.listening.crossfadeS || 6} s</div>
        <input type="range" min="1" max="12" step="1" value={player.listening.crossfadeS || 6} style:--p="{((player.listening.crossfadeS || 6) / 12) * 100}%" aria-label="Crossfade seconds" oninput={(e) => player.setListening({ crossfadeS: +e.currentTarget.value })} />
      </div>
    {/if}
    {#if player.listening.transition === 'dj'}
      <div class="opt">
        <div><div>Mix length</div><div class="muted small">How many beats two songs play together.</div></div>
        <div class="seg" role="radiogroup" aria-label="Mix length">
          {#each [8, 16, 32] as b}
            <button role="radio" aria-checked={player.listening.djBeats === b} class:on={player.listening.djBeats === b} onclick={() => player.setListening({ djBeats: b })}>{b} beats</button>
          {/each}
        </div>
      </div>
      <label class="sig inline">
        <div><div>Allow tempo change</div><div class="muted small">Speed the next song up or down (up to 8 %, pitch kept) so the beats line up. Off: only songs at the same tempo are beat-matched.</div></div>
        <input type="checkbox" class="switch" checked={player.listening.allowTempo} onchange={(e) => player.setListening({ allowTempo: e.currentTarget.checked })} />
      </label>
      <label class="sig inline">
        <div><div>Reorder for flow</div><div class="muted small">When radio, a mix or a playlist starts, order it by tempo, key and energy. Songs only move a few places.</div></div>
        <input type="checkbox" class="switch" checked={player.listening.automixOrder} onchange={(e) => player.setListening({ automixOrder: e.currentTarget.checked })} />
      </label>
      <div class="muted small">When you cast to a speaker, the speaker plays normally (gapless, or its own crossfade); DJ mixing happens only when this device plays the music.</div>
    {/if}
    <div class="opt">
      <div><div>Volume levelling</div><div class="muted small">ReplayGain from your server. Auto uses album gain while an album plays in order, so quiet songs stay quiet.</div></div>
      <div class="seg" role="radiogroup" aria-label="Volume levelling">
        {#each [['off', 'Off'], ['track', 'Track'], ['album', 'Album'], ['auto', 'Auto']] as [k, label]}
          <button role="radio" aria-checked={player.listening.levelling === k} class:on={player.listening.levelling === k} onclick={() => player.setListening({ levelling: k as LevellingMode })}>{label}</button>
        {/each}
      </div>
    </div>
    {#if player.listening.levelling !== 'off'}
      <div>
        <div>Pre-amp: {player.listening.preampDb > 0 ? '+' : ''}{player.listening.preampDb} dB</div>
        <input type="range" min="-15" max="15" step="0.5" value={player.listening.preampDb} style:--p="{((player.listening.preampDb + 15) / 30) * 100}%" aria-label="Levelling pre-amp" oninput={(e) => player.setListening({ preampDb: +e.currentTarget.value })} />
      </div>
      <label class="sig inline">
        <div><div>Prevent clipping</div><div class="muted small">Never raise a song so far that its loudest peak would distort.</div></div>
        <input type="checkbox" class="switch" checked={player.listening.preventClipping} onchange={(e) => player.setListening({ preventClipping: e.currentTarget.checked })} />
      </label>
    {/if}
    <div class="muted small">These settings are kept on this device.</div>
  </div>

  <div class="section-title">Family mode</div>
  <p class="pad muted small">
    Hides explicit songs everywhere — library, search, mixes, radio — and skips them in queues. Your server does the
    filtering, so it applies on all your devices.{#if family.mode.locked} Locked by an admin{family.mode.hasPin ? ': loosening it needs the unlock PIN' : ''}.{/if}
  </p>
  <label class="sig">
    <div><div>Family mode {#if family.mode.locked}🔒{/if}</div><div class="muted small">Hide songs marked explicit</div></div>
    <input type="checkbox" class="switch" checked={family.mode.enabled} onchange={async (e) => { const el = e.currentTarget; const want = el.checked; if (!(await family.set({ enabled: want }))) el.checked = family.mode.enabled; }} />
  </label>
  {#if family.mode.enabled}
    <label class="sig">
      <div><div>Also hide "probably explicit"</div><div class="muted small">Songs whose lyrics contain swearing (found automatically, not certain)</div></div>
      <input type="checkbox" class="switch" checked={family.mode.hideProbable} onchange={async (e) => { const el = e.currentTarget; if (!(await family.set({ hideProbable: el.checked }))) el.checked = family.mode.hideProbable; }} />
    </label>
    <label class="sig">
      <div><div>Also hide unchecked songs</div><div class="muted small">Songs nobody has classified yet. Strictest, hides a lot until your server has checked the library.</div></div>
      <input type="checkbox" class="switch" checked={family.mode.hideUnknown} onchange={async (e) => { const el = e.currentTarget; if (!(await family.set({ hideUnknown: el.checked }))) el.checked = family.mode.hideUnknown; }} />
    </label>
  {/if}

  <VoiceSettings />
  <ScrobbleSettings />

  <div class="section-title">Data &amp; recommendations</div>
  <p class="pad muted small">
    Plays, skips, favourites and ratings are always sent to your server. The signals below are off until you switch them on.
    They're only ever sent to this server — nowhere else — and help it build better mixes for you.
  </p>
  <div class="pad"><button class="btn" disabled={allOn} onclick={() => player.setSignals(Object.fromEntries(WEB_SIGNALS.map((s) => [s.key, true])) as Record<SignalKey, boolean>)}>Turn everything on</button></div>
  {#each WEB_SIGNALS as s (s.key)}
    <label class="sig">
      <div>
        <div>{s.label}</div>
        <div class="muted small">{s.explain}</div>
      </div>
      <input type="checkbox" class="switch" checked={!!player.signals[s.key]} onchange={(e) => player.setSignals({ [s.key]: e.currentTarget.checked })} />
    </label>
  {/each}
  <p class="pad muted small">Movement, location, weather and watch health are collected by the Android app only.</p>

  <div class="section-title">Your data</div>
  <div class="pad">
    {#if summary?.events}
      <p class="small">{summary.events.count} listening events ({summary.events.withContext} with context), {day(summary.events.first)} – {day(summary.events.last)}</p>
      {#each Object.entries(summary.signals ?? {}).filter(([, v]) => v.count) as [name, v]}
        <div class="small muted">{name}: {v.count} events · {day(v.first)} – {day(v.last)}</div>
      {/each}
    {/if}
    <div class="btns">
      <button class="btn" onclick={exportData}>Export my data (JSON)</button>
      <button class="btn danger" onclick={() => erase('context')}>Erase collected context</button>
      <button class="btn danger" onclick={() => erase('all')}>Erase everything</button>
    </div>
  </div>
</div>

<style>
  .pad {
    padding: 4px 18px 10px;
  }
  .small {
    font-size: 0.85rem;
  }
  .sig {
    display: flex;
    gap: 16px;
    align-items: center;
    justify-content: space-between;
    padding: 12px 18px;
    border-bottom: 1px solid var(--line);
    cursor: pointer;
  }
  .opt {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
  }
  .seg {
    display: flex;
    border: 1px solid #444;
    border-radius: 999px;
    overflow: hidden;
    flex: none;
  }
  .seg button {
    padding: 7px 14px;
    color: var(--fg-2);
  }
  .seg button.on {
    background: #fff;
    color: #000;
  }
  .swatches {
    display: flex;
    gap: 8px;
  }
  .sw {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    border: 2px solid transparent;
  }
  .sw.on {
    border-color: #fff;
    box-shadow: 0 0 0 2px #000 inset;
  }
  .shelfrow {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 4px 0;
  }
  .grow {
    flex: 1;
  }
  .sig.inline {
    padding: 8px 0;
    border: 0;
  }
  .btns {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
</style>
