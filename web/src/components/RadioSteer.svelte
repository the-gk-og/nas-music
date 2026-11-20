<script lang="ts">
  // Steerable radio (AI radio): why these picks, and controls that change the next tracks right away.
  // Works for any radio queue, played here or on the active device (Connect remote).
  import { Ban, Moon, RotateCcw, Shuffle, Star, ThumbsDown, ThumbsUp, X, Zap } from '@lucide/svelte';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { RadioStation, SteerAction } from '../lib/types';
  import { ui } from '../lib/ui.svelte';

  const qid = $derived(player.isRemote ? (player.remoteState?.queueId ?? null) : (player.queue?.id ?? null));
  const isRadio = $derived(!!qid && (player.isRemote ? player.queues.some((q) => q.id === qid && q.radio) : !!player.queue?.radio));
  let station = $state<RadioStation | null>(null);
  let artist = $state(false); // more/less/never apply to the artist instead of the song
  let busy = $state(false);

  $effect(() => {
    const id = isRadio ? qid : null;
    station = null;
    if (!id) return;
    let live = true;
    session.api
      .radioStation(id)
      .then((s) => live && (station = s))
      .catch(() => {});
    return () => (live = false);
  });

  const LABEL: Record<SteerAction, string> = {
    more_like: 'More like this',
    less_like: 'Less like this',
    more_energy: 'More energy',
    calmer: 'Calmer',
    more_variety: 'More variety',
    more_familiar: 'More familiar',
    never_here: 'Never on this station',
    remove: 'Undone',
    reset: 'Steering reset',
  };

  async function steer(a: SteerAction, refId?: string, refType?: 'track' | 'artist') {
    if (busy) return;
    busy = true;
    const s = await player.steerRadio(a, refType ?? (artist ? 'artist' : 'track'), refId);
    busy = false;
    if (s) {
      station = s;
      ui.toast(a === 'remove' || a === 'reset' ? LABEL[a] : `${LABEL[a]}${artist && ['more_like', 'less_like', 'never_here'].includes(a) ? ' (artist)' : ''} — next tracks updated`);
    }
  }
  const refs = $derived(
    station ? [...station.steering.moreLike.map((r) => ({ ...r, k: '+' })), ...station.steering.lessLike.map((r) => ({ ...r, k: '−' })), ...station.steering.never.map((r) => ({ ...r, k: '⊘' }))] : [],
  );
</script>

{#if isRadio}
  <section class="steer" aria-label="Steer this radio">
    {#if station?.explanation}<p class="why">{station.explanation}</p>{/if}
    <div class="row">
      <button disabled={busy} onclick={() => steer('more_like')} title="More like this {artist ? 'artist' : 'song'}"><ThumbsUp size={18} /><span>More like</span></button>
      <button disabled={busy} onclick={() => steer('less_like')} title="Less like this {artist ? 'artist' : 'song'}"><ThumbsDown size={18} /><span>Less like</span></button>
      <button disabled={busy} onclick={() => steer('more_energy')}><Zap size={18} /><span>Energy</span></button>
      <button disabled={busy} onclick={() => steer('calmer')}><Moon size={18} /><span>Calmer</span></button>
      <button disabled={busy} onclick={() => steer('more_variety')}><Shuffle size={18} /><span>Variety</span></button>
      <button disabled={busy} onclick={() => steer('more_familiar')}><Star size={18} /><span>Familiar</span></button>
      <button disabled={busy} onclick={() => steer('never_here')} title="Never play this {artist ? 'artist' : 'song'} on this station"><Ban size={18} /><span>Never here</span></button>
    </div>
    <div class="row small">
      <label class="toggle"><input type="checkbox" bind:checked={artist} /> Apply to the artist</label>
      {#if station?.steeringSummary}
        <span class="muted ellipsis">Steered: {station.steeringSummary}</span>
        <button class="link" disabled={busy} onclick={() => steer('reset')}><RotateCcw size={14} /> Reset</button>
      {/if}
    </div>
    {#if refs.length}
      <div class="refs">
        {#each refs as r (r.k + r.type + r.id)}
          <span class="ref">{r.k} {r.name ?? r.type}<button aria-label="Undo {r.name ?? r.type}" onclick={() => steer('remove', r.id, r.type)}><X size={12} /></button></span>
        {/each}
      </div>
    {/if}
  </section>
{/if}

<style>
  .steer {
    margin: 10px 0 4px;
    display: grid;
    gap: 8px;
  }
  .why {
    margin: 0;
    font-size: 0.9rem;
    opacity: 0.85;
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
  }
  .row button {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 6px 10px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--fg, #fff) 10%, transparent);
    font-size: 0.85rem;
  }
  .row button:hover:not(:disabled) {
    background: color-mix(in srgb, var(--accent) 30%, transparent);
  }
  .row.small {
    font-size: 0.82rem;
    gap: 10px;
  }
  .row.small .link {
    background: none;
    padding: 0;
  }
  .toggle {
    display: inline-flex;
    gap: 6px;
    align-items: center;
  }
  .refs {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    font-size: 0.8rem;
  }
  .ref {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 3px 4px 3px 9px;
    border-radius: 999px;
    background: color-mix(in srgb, var(--fg, #fff) 7%, transparent);
  }
  .ref button {
    display: inline-grid;
    place-items: center;
  }
</style>
