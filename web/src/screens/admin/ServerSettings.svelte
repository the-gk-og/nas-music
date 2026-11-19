<script lang="ts">
  import { TriangleAlert } from '@lucide/svelte';
  import { adminApi, validateBaseUrl, validateCacheGb, validateTimezone, type AdminSettings } from '../../lib/api/admin';
  import { session } from '../../lib/session.svelte';
  import { ui } from '../../lib/ui.svelte';

  const A = $derived(adminApi(session.api));
  let saved = $state<AdminSettings | null>(null);
  let lanUrl = $state('');
  let tunnelUrl = $state('');
  let timezone = $state('');
  let cacheGb = $state(50);
  let lrclib = $state(true);
  let busy = $state(false);
  let error = $state<string | null>(null);
  let restart = $state<string[]>([]);

  const zones: string[] = (() => {
    try {
      return (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf?.('timeZone') ?? [];
    } catch {
      return [];
    }
  })();

  function fill(s: AdminSettings) {
    saved = s;
    lanUrl = s.lanUrl ?? '';
    tunnelUrl = s.tunnelUrl ?? '';
    timezone = s.timezone;
    cacheGb = s.opusCacheMaxGb;
    lrclib = s.lrclib;
  }
  $effect(() => {
    A.settings()
      .then(fill)
      .catch((e) => (error = e.status === 404 ? 'This server has no settings endpoint yet.' : e.message));
  });

  const lanV = $derived(validateBaseUrl(lanUrl));
  const tunV = $derived(validateBaseUrl(tunnelUrl));
  const tzErr = $derived(validateTimezone(timezone, zones.length ? [...zones, 'UTC'] : undefined));
  const cacheErr = $derived(validateCacheGb(Number(cacheGb)));
  const valid = $derived(lanV.ok && tunV.ok && !tzErr && !cacheErr);

  const next = $derived<Partial<AdminSettings>>({
    lanUrl: lanV.ok ? lanV.value : null,
    tunnelUrl: tunV.ok ? tunV.value : null,
    timezone: timezone.trim(),
    opusCacheMaxGb: Number(cacheGb),
    lrclib,
  });
  const changed = $derived(
    saved ? (Object.keys(next) as (keyof AdminSettings)[]).filter((k) => JSON.stringify(next[k]) !== JSON.stringify(saved![k])) : [],
  );

  async function save(e: Event) {
    e.preventDefault();
    if (!valid || !changed.length) return;
    busy = true;
    try {
      const body = Object.fromEntries(changed.map((k) => [k, next[k]])) as Partial<AdminSettings>;
      const r = await A.saveSettings(body);
      fill(r.values);
      restart = [...new Set([...restart, ...(r.restartRequired ?? [])])];
      ui.toast('Settings saved');
    } catch (err) {
      ui.toast((err as Error).message);
    } finally {
      busy = false;
    }
  }

  const LABEL: Record<string, string> = { lanUrl: 'LAN address', tunnelUrl: 'Tunnel / relay address', timezone: 'Time zone', opusCacheMaxGb: 'Cache size', lrclib: 'LRCLIB' };
</script>

<h2>Server settings</h2>
{#if restart.length}
  <div class="notice" role="status">
    <TriangleAlert size={18} />
    <span>Restart the server to apply: {restart.map((k) => LABEL[k] ?? k).join(', ')}. Everything else is already live.</span>
  </div>
{/if}
{#if error}<p class="bad">{error}</p>{/if}
{#if saved}
  <form class="form settings" onsubmit={save} novalidate>
    <label>LAN address
      <input bind:value={lanUrl} placeholder="https://192.168.68.2:8443" aria-invalid={!lanV.ok} />
      {#if !lanV.ok}<span class="error">{lanV.error}</span>{:else}<span class="muted small">Used by devices at home. Include the port if it isn’t 443.</span>{/if}
    </label>
    <label>Tunnel / relay address
      <input bind:value={tunnelUrl} placeholder="https://music.example.com or https://relay.example.com:8443" aria-invalid={!tunV.ok} />
      {#if !tunV.ok}<span class="error">{tunV.error}</span>{:else}<span class="muted small">Public address (Cloudflare Tunnel or a relay). A custom port is fine, e.g. <span class="mono">https://music.example.com:8443</span>.</span>{/if}
    </label>
    <label>Time zone
      <input bind:value={timezone} list="tz-list" placeholder="Europe/London" aria-invalid={!!tzErr} />
      <datalist id="tz-list">{#each zones as z}<option value={z}></option>{/each}</datalist>
      {#if tzErr}<span class="error">{tzErr}</span>{:else}<span class="muted small">Nightly mixes run at 02:00 in this zone.</span>{/if}
    </label>
    <label>Opus cache size (GB)
      <input type="number" min="1" max="2000" step="1" bind:value={cacheGb} aria-invalid={!!cacheErr} />
      {#if cacheErr}<span class="error">{cacheErr}</span>{:else}<span class="muted small">Transcoded audio kept on the data volume; oldest files are removed first.</span>{/if}
    </label>
    <label class="toggle">
      <span><span>Fetch missing lyrics from LRCLIB</span><br /><span class="muted small">Looks up lyrics on lrclib.net (open data) when a file has none. Sends artist, title, album and duration.</span></span>
      <input type="checkbox" class="switch" bind:checked={lrclib} />
    </label>
    <div class="row">
      <button class="btn primary" type="submit" disabled={!valid || !changed.length || busy}>{busy ? 'Saving…' : 'Save'}</button>
      {#if changed.length}<button class="btn" type="button" onclick={() => saved && fill(saved)}>Discard</button><span class="muted small">{changed.length} unsaved change{changed.length === 1 ? '' : 's'}</span>{/if}
    </div>
  </form>
{:else if !error}<p class="muted">Loading…</p>{/if}

<style>
  .settings {
    max-width: 620px;
  }
  .toggle {
    display: flex !important;
    justify-content: space-between;
    align-items: center;
    gap: 16px;
  }
  .notice {
    display: flex;
    gap: 10px;
    align-items: center;
    padding: 10px 14px;
    border-radius: 10px;
    background: #3a2a05;
    border: 1px solid #a16207;
    color: #fde68a;
    margin-bottom: 14px;
  }
  input[aria-invalid='true'] {
    border-color: var(--danger);
  }
</style>
