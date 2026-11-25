<script lang="ts">
  // v7 Settings: scrobbling to ListenBrainz / Last.fm (server-side, from your listening events), the
  // ListenBrainz history import, and "new music added" notifications.
  import { onDestroy } from 'svelte';
  import { ApiError } from '../lib/api/client';
  import { notifications } from '../lib/notifications.svelte';
  import { SERVICE_NAMES, scrobbleLine } from '../lib/notify';
  import { session } from '../lib/session.svelte';
  import type { ScrobbleService, ScrobbleStatus } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  let st = $state<ScrobbleStatus | null>(null);
  let unsupported = $state(false);
  let lbToken = $state('');
  let busy = $state(false);
  let lfmWaiting = $state(false);
  let lfmUrl = $state<string | null>(null);
  let settings = $state<Record<string, unknown>>({});
  let poll: ReturnType<typeof setTimeout> | null = null;

  const devKey = $derived(session.saved.deviceId ? `device.${session.saved.deviceId}.scrobble` : null);
  const thisDevice = $derived(devKey ? settings[devKey] !== false : true);
  const notifyOn = $derived(settings['notify.newMusic'] === true);

  async function refresh() {
    try {
      st = await session.api.scrobble();
      if (st.import?.state === 'running') schedule(refresh, 3000);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) unsupported = true;
    }
  }
  function schedule(fn: () => void, ms: number) {
    if (poll) clearTimeout(poll);
    poll = setTimeout(fn, ms);
  }
  onDestroy(() => poll && clearTimeout(poll));

  $effect(() => {
    void refresh();
    session.api.getSettings().then((s) => (settings = s.settings)).catch(() => {});
  });

  async function act(fn: () => Promise<ScrobbleStatus>, ok?: string) {
    busy = true;
    try {
      st = await fn();
      if (ok) ui.toast(ok);
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      busy = false;
    }
  }

  async function putSetting(key: string, value: unknown) {
    try {
      settings = (await session.api.putSettings({ settings: { [key]: value } })).settings;
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async function connectLb() {
    const t = lbToken.trim();
    if (!t) return;
    await act(() => session.api.connectListenBrainz(t), 'ListenBrainz connected');
    lbToken = '';
  }

  async function connectLfm() {
    busy = true;
    try {
      const { authUrl } = await session.api.lastfmConnect();
      lfmUrl = authUrl;
      window.open(authUrl, '_blank', 'noopener');
      lfmWaiting = true;
      const deadline = Date.now() + 5 * 60_000;
      const tick = async () => {
        if (!lfmWaiting) return;
        try {
          st = await session.api.lastfmComplete();
          lfmWaiting = false;
          ui.toast('Last.fm connected');
        } catch (e) {
          if (e instanceof ApiError && e.status === 409 && Date.now() < deadline) schedule(tick, 3000);
          else {
            lfmWaiting = false;
            ui.toast(e instanceof ApiError && e.status === 409 ? 'Last.fm was not approved in time — try again' : (e as Error).message);
            void refresh();
          }
        }
      };
      schedule(tick, 3000);
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      busy = false;
    }
  }

  async function disconnect(s: ScrobbleService) {
    if (!(await ui.confirm(`Disconnect ${SERVICE_NAMES[s.service]}`, 'Plays will no longer be scrobbled there. Unsent scrobbles are dropped.', 'Disconnect'))) return;
    await act(() => session.api.disconnectScrobble(s.service));
  }

  async function removeImport() {
    if (!(await ui.confirm('Remove imported history', 'Removes every listen imported from ListenBrainz (your own plays here stay).', 'Remove'))) return;
    await act(async () => {
      const r = await session.api.removeImported();
      ui.toast(`Removed ${r.removed} imported listens`);
      return r;
    });
  }

  const imp = $derived(st?.import);
  const lb = $derived(st?.services.find((s) => s.service === 'listenbrainz'));
</script>

<div class="section-title">Scrobbling</div>
{#if unsupported}
  <p class="pad muted small">This server does not support scrobbling yet.</p>
{:else if !st}
  <p class="pad muted small">Loading…</p>
{:else}
  <p class="pad muted small">
    Your server sends what you play — on any device, including speakers — to ListenBrainz or Last.fm. Off until you connect
    an account. A song counts after half of it (or 4 minutes); songs under 30 seconds never count.
  </p>
  {#each st.services as s (s.service)}
    <div class="pad svc">
      <div class="row">
        <div class="grow">
          <div>{SERVICE_NAMES[s.service]}</div>
          <div class="muted small" class:err={s.needsReauth}>{scrobbleLine(s)}</div>
          {#if s.lastError && s.connected}<div class="small err">Last error: {s.lastError}</div>{/if}
        </div>
        {#if s.connected}
          <input type="checkbox" class="switch" aria-label="Scrobble to {SERVICE_NAMES[s.service]}" checked={s.enabled} onchange={(e) => act(() => session.api.putScrobble(s.service, { enabled: e.currentTarget.checked }))} />
        {/if}
      </div>
      {#if s.connected}
        <label class="sig inline">
          <div><div>Show "now playing"</div></div>
          <input type="checkbox" class="switch" checked={s.nowPlaying} onchange={(e) => act(() => session.api.putScrobble(s.service, { nowPlaying: e.currentTarget.checked }))} />
        </label>
        <div class="btns">
          {#if s.failed}<button class="btn" disabled={busy} onclick={() => act(() => session.api.retryScrobbles(), 'Retrying')}>Retry failed</button>{/if}
          {#if s.needsReauth && s.service === 'lastfm'}<button class="btn" disabled={busy} onclick={connectLfm}>Reconnect</button>{/if}
          <button class="btn danger" disabled={busy} onclick={() => disconnect(s)}>Disconnect</button>
        </div>
      {/if}
      {#if s.service === 'listenbrainz' && (!s.connected || s.needsReauth)}
        <form class="lb" onsubmit={(e) => { e.preventDefault(); void connectLb(); }}>
          <input type="password" autocomplete="off" placeholder="ListenBrainz user token" aria-label="ListenBrainz user token" bind:value={lbToken} />
          <button class="btn" disabled={busy || !lbToken.trim()}>Connect</button>
        </form>
        <div class="muted small">Copy your user token from <a href="https://listenbrainz.org/settings/" target="_blank" rel="noopener noreferrer">listenbrainz.org/settings</a>.</div>
      {/if}
      {#if s.service === 'lastfm' && !s.connected}
        {#if s.available}
          <div class="btns">
            <button class="btn" disabled={busy || lfmWaiting} onclick={connectLfm}>{lfmWaiting ? 'Waiting for approval on last.fm…' : 'Connect Last.fm'}</button>
            {#if lfmWaiting}<button class="btn" onclick={() => (lfmWaiting = false)}>Cancel</button>{/if}
          </div>
          {#if lfmWaiting && lfmUrl}<div class="muted small">Approve Homebeat on last.fm. Nothing opened? <a href={lfmUrl} target="_blank" rel="noopener noreferrer">Open the Last.fm page</a> (or copy it to a browser where you're signed in).</div>{/if}
        {:else}
          <div class="muted small">Your server admin needs to add a Last.fm API key and secret to config.toml first.</div>
        {/if}
      {/if}
    </div>
  {/each}
  {#if devKey && st.services.some((s) => s.connected)}
    <label class="sig">
      <div><div>Scrobble plays from this device</div><div class="muted small">Turn off for a shared or kids' device.</div></div>
      <input type="checkbox" class="switch" checked={thisDevice} onchange={(e) => putSetting(devKey, e.currentTarget.checked ? null : false)} />
    </label>
  {/if}
  {#if lb?.connected}
    <div class="pad svc">
      <div>Import ListenBrainz history</div>
      <div class="muted small">
        Copies your past listens of songs in this library into your history here, so your mixes learn from them. Imported
        listens are marked and can be removed again; they are never scrobbled back.
      </div>
      {#if imp}
        <div class="small {imp.state === 'error' ? 'err' : 'muted'}">
          {imp.state === 'running' ? 'Importing…' : imp.state === 'error' ? `Import failed: ${imp.error ?? ''}` : 'Last import:'}
          {#if imp.scanned != null}{imp.scanned} listens read, {imp.imported ?? 0} imported, {imp.alreadyKnown ?? 0} already here, {imp.unmatched ?? 0} not in the library{/if}
        </div>
      {/if}
      <div class="btns">
        <button class="btn" disabled={busy || imp?.state === 'running'} onclick={() => act(() => session.api.importListenBrainz()).then(() => schedule(refresh, 2000))}>Import</button>
        {#if st.importedEvents}<button class="btn danger" disabled={busy || imp?.state === 'running'} onclick={removeImport}>Remove {st.importedEvents} imported</button>{/if}
      </div>
    </div>
  {/if}
{/if}

<div class="section-title">Notifications</div>
<label class="sig">
  <div><div>New music added</div><div class="muted small">One notification when a library scan adds songs or albums. Synced to all your devices.</div></div>
  <input type="checkbox" class="switch" checked={notifyOn} onchange={(e) => putSetting('notify.newMusic', e.currentTarget.checked)} />
</label>
{#if notifyOn}
  <label class="sig">
    <div><div>Desktop notifications on this device</div><div class="muted small">Also show a system notification while this app is in the background.</div></div>
    <input type="checkbox" class="switch" checked={notifications.browser} onchange={async (e) => { const el = e.currentTarget; await notifications.setBrowser(el.checked); el.checked = notifications.browser; }} />
  </label>
{/if}

<style>
  /* same row styles as Settings.svelte (its styles are scoped) */
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
  .sig.inline {
    padding: 8px 0;
    border: 0;
  }
  .svc {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 14px;
  }
  .row {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .btns {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .lb {
    display: flex;
    gap: 8px;
  }
  .lb input {
    flex: 1;
    min-width: 0;
  }
  .err {
    color: #f87171;
  }
</style>
