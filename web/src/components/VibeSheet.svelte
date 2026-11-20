<script lang="ts">
  // Describe-a-vibe (AI radio): type or dictate (the platform keyboard's voice input works here), see how it
  // was understood as chips, drop any you don't want, start an endless station.
  import { Sparkles, X } from '@lucide/svelte';
  import { ApiError } from '../lib/api/client';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { VibeConstraint } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import { VIBE_EXAMPLES, withoutChip } from '../lib/v3';
  import Sheet from './Sheet.svelte';

  let text = $state('');
  let parsed = $state(''); // the text the chips belong to
  let chips = $state<VibeConstraint[]>([]);
  let busy = $state(false);
  let error = $state<string | null>(null);

  async function interpret(t = text) {
    text = t;
    const q = t.trim();
    if (!q) return;
    busy = true;
    error = null;
    try {
      const r = await session.api.parseVibe(q);
      chips = r.constraints;
      parsed = q;
      if (!chips.length) error = 'Nothing recognised yet; try words like chill, upbeat, 90s, jazz, no vocals.';
    } catch (e) {
      error = e instanceof ApiError && (e.status === 404 || e.status === 503) ? 'Vibe radio isn’t available on this server yet' : (e as Error).message;
    } finally {
      busy = false;
    }
  }

  async function start() {
    const q = text.trim();
    if (q && q !== parsed) await interpret(q);
    if (!chips.length && !q) return;
    ui.close();
    await player.startRadio({ type: 'vibe', text: q, constraints: chips }, `“${q || 'vibe'}” radio`);
    ui.go('now');
  }
</script>

<Sheet label="Describe a vibe">
  <form class="pad" onsubmit={(e) => { e.preventDefault(); void interpret(); }}>
    <h2><Sparkles size={22} /> Describe a vibe</h2>
    <!-- svelte-ignore a11y_autofocus -->
    <input
      class="vibe"
      bind:value={text}
      autofocus
      maxlength="200"
      placeholder="chill rainy evening…"
      enterkeyhint="search"
      aria-label="Describe the music you want"
      onblur={() => text.trim() && text.trim() !== parsed && interpret()}
    />
    {#if !chips.length && !parsed}
      <div class="chips">
        {#each VIBE_EXAMPLES as ex (ex)}<button type="button" class="chip ex" onclick={() => interpret(ex)}>{ex}</button>{/each}
      </div>
    {/if}
    {#if chips.length}
      <p class="muted small">Understood as (tap × to drop):</p>
      <div class="chips">
        {#each chips as c (c.id)}
          <span class="chip"
            >{c.label}<button type="button" aria-label="Remove {c.label}" onclick={() => (chips = withoutChip(chips, c.id))}><X size={14} /></button></span
          >
        {/each}
      </div>
    {/if}
    {#if error}<p class="muted small">{error}</p>{/if}
  </form>
  <div class="foot">
    <button class="btn text" onclick={() => ui.close()}>Cancel</button>
    <button class="btn text" disabled={busy || (!chips.length && !text.trim())} onclick={start}>Start radio</button>
  </div>
</Sheet>

<style>
  .pad {
    padding: 20px 22px 8px;
  }
  h2 {
    margin: 0 0 12px;
    font-weight: 500;
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .vibe {
    width: 100%;
    font-size: 1.1rem;
    padding: 12px 14px;
    border-radius: 12px;
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 12px 0 4px;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 6px 8px 6px 12px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--accent) 22%, transparent);
    font-size: 0.92rem;
  }
  .chip button {
    display: inline-grid;
    place-items: center;
    padding: 2px;
    border-radius: 50%;
  }
  .chip.ex {
    padding: 6px 12px;
    background: color-mix(in srgb, var(--fg, #fff) 8%, transparent);
  }
  .small {
    font-size: 0.85rem;
    margin: 10px 0 0;
  }
  .foot {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 8px 14px 14px;
  }
</style>
