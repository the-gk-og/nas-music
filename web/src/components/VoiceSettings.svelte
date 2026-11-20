<script lang="ts">
  // v7 voice control: voice tokens for Home Assistant Assist and what each speaker/device is called by voice.
  import { session } from '../lib/session.svelte';
  import { ui } from '../lib/ui.svelte';
  import type { VoiceDevice, VoiceToken } from '../lib/types';

  let tokens = $state<VoiceToken[]>([]);
  let devices = $state<VoiceDevice[]>([]);
  let newName = $state('Home Assistant');
  let created = $state<string | null>(null);
  let busy = $state(false);
  const day = (s?: string | null) => (s ? new Date(s).toLocaleString() : 'never');
  const serverUrl = $derived(session.isMock ? 'https://your-server:8443' : session.saved.origin || location.origin);

  async function load() {
    try {
      tokens = (await session.api.voiceTokens()).tokens;
      devices = (await session.api.voiceDevices()).devices;
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  $effect(() => {
    void load();
  });

  async function create() {
    busy = true;
    try {
      const t = await session.api.createVoiceToken(newName.trim() || 'Home Assistant');
      created = t.token;
      tokens = (await session.api.voiceTokens()).tokens;
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      busy = false;
    }
  }

  async function revoke(t: VoiceToken) {
    if (!(await ui.confirm('Revoke voice token', `"${t.name}" stops working at once. Home Assistant will need a new token.`, 'Revoke'))) return;
    try {
      await session.api.revokeVoiceToken(t.id);
      tokens = tokens.filter((x) => x.id !== t.id);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      ui.toast('Copied');
    } catch {
      ui.toast('Copy failed: select the text and copy it');
    }
  }

  async function saveAliases(d: VoiceDevice, raw: string) {
    const aliases = raw.split(',').map((s) => s.trim()).filter(Boolean);
    try {
      const r = await session.api.putVoiceDevice(d.id, { aliases });
      d.aliases = r.aliases;
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async function makeDefault(d: VoiceDevice, on: boolean) {
    try {
      await session.api.putVoiceDevice(d.id, { isDefault: on });
      for (const x of devices) x.isDefault = on && x.id === d.id;
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
</script>

<div class="section-title">Voice control</div>
<p class="pad muted small">
  Control your music by voice through Home Assistant Assist ("play my Pop radio in the bedroom", "skip", "what's playing?").
  Create a voice token here and paste it into Home Assistant (setup steps: <code>deploy/homeassistant/README.md</code>).
  A voice token can only send voice commands: it can't sign in, browse or stream.
</p>
<div class="pad form">
  <div class="row">
    <input aria-label="Token name" bind:value={newName} maxlength="100" />
    <button class="btn" disabled={busy} onclick={create}>Create voice token</button>
  </div>
  {#if created}
    <div class="created">
      <div class="small">Copy it now — it won't be shown again. Server address for Home Assistant: <code>{serverUrl}</code></div>
      <div class="row">
        <code class="tok">{created}</code>
        <button class="btn" onclick={() => copy(created!)}>Copy</button>
        <button class="btn" onclick={() => (created = null)}>Done</button>
      </div>
    </div>
  {/if}
  {#each tokens as t (t.id)}
    <div class="row item">
      <div class="grow"><div>{t.name}</div><div class="muted small">created {day(t.createdAt)} · last used {day(t.lastUsed)}</div></div>
      <button class="btn danger" onclick={() => revoke(t)}>Revoke</button>
    </div>
  {/each}
</div>

<div class="pad muted small">
  Names for voice: add the rooms or nicknames you'll say ("bedroom", "Stacey's room"), separated by commas. The default plays
  when you don't name a room and nothing is playing.
</div>
{#each devices as d (d.id)}
  <div class="dev">
    <div class="grow">
      <div>{d.name} <span class="muted small">{d.castType ?? d.kind}{d.online ? '' : ' · offline'}</span></div>
      <input aria-label="Voice names for {d.name}" placeholder="e.g. bedroom, upstairs" value={d.aliases.join(', ')} onchange={(e) => saveAliases(d, e.currentTarget.value)} />
    </div>
    <label class="def"><input type="radio" name="voice-default" checked={d.isDefault} onchange={(e) => makeDefault(d, e.currentTarget.checked)} /> Default</label>
  </div>
{:else}
  <p class="pad muted small">No devices or speakers found yet.</p>
{/each}

<style>
  .pad {
    padding: 4px 18px 10px;
  }
  .small {
    font-size: 0.85rem;
  }
  .row {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }
  .item {
    padding: 6px 0;
    border-bottom: 1px solid var(--line);
  }
  .grow {
    flex: 1;
    min-width: 0;
  }
  .created {
    padding: 10px;
    border: 1px solid var(--line);
    border-radius: 8px;
    display: grid;
    gap: 6px;
  }
  .tok {
    word-break: break-all;
    user-select: all;
  }
  .dev {
    display: flex;
    gap: 12px;
    align-items: center;
    padding: 8px 18px;
    border-bottom: 1px solid var(--line);
  }
  .dev input:not([type='radio']) {
    width: 100%;
    margin-top: 4px;
  }
  .def {
    display: flex;
    gap: 4px;
    align-items: center;
    flex: none;
  }
</style>
