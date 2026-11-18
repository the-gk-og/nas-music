<script lang="ts">
  import { ArrowLeft } from '@lucide/svelte';
  import { WEB_SIGNALS, type SignalKey } from '../lib/events';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import { ui } from '../lib/ui.svelte';

  let deviceName = $state(session.saved.deviceName);
  let summary = $state<Record<string, unknown> | null>(null);
  $effect(() => {
    session.api.dataSummary().then((s) => (summary = s)).catch(() => (summary = null));
  });

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
      await session.api.deleteData(signal);
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
    {#if summary}<pre class="sum">{JSON.stringify(summary, null, 2)}</pre>{/if}
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
  .btns {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .sum {
    background: var(--bg-1);
    padding: 10px;
    border-radius: 8px;
    font-size: 0.8rem;
    overflow-x: auto;
  }
</style>
