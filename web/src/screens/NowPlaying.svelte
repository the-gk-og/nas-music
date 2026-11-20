<script lang="ts">
  import {
    ChevronDown,
    Ellipsis,
    Heart,
    Info,
    ListPlus,
    Moon,
    Pause,
    Play,
    Repeat,
    Repeat1,
    Shuffle,
    SkipBack,
    SkipForward,
    SlidersVertical,
    Speaker,
    Volume2,
    VolumeX,
  } from '@lucide/svelte';
  import Cover from '../components/Cover.svelte';
  import RadioSteer from '../components/RadioSteer.svelte';
  import { appearance, withTransition } from '../lib/appearance.svelte';
  import { gesture } from '../lib/gesture';
  import { session } from '../lib/session.svelte';
  import { fmtTime } from '../lib/format';
  import { sleepLabel } from '../lib/listening';
  import { player } from '../lib/player.svelte';
  import { ui } from '../lib/ui.svelte';
  import Lyrics from './Lyrics.svelte';
  import ExplicitBadge from '../components/ExplicitBadge.svelte';

  const s = $derived(player.shown);
  const t = $derived(s.track);
  let bg = $state<string | null>(null);
  let dragging = $state<number | null>(null);
  let volOpen = $state(false);

  // Blurred, darkened cover as the background (§1: the one exception to true black).
  $effect(() => {
    const id = t?.albumId;
    if (!id) return void (bg = null);
    let live = true;
    session.api.artSrc(id, 256).then((u) => live && (bg = u)).catch(() => {});
    return () => (live = false);
  });

  const wide = $derived(appearance.form === 'desktop');
  const minimise = () => withTransition(() => (history.length > 1 ? history.back() : ui.go('home')));
  const upNext = $derived.by(() => {
    if (player.isRemote) return null;
    const q = player.queue;
    if (!q || q.index + 1 >= q.tracks.length) return null;
    return q.tracks[q.order?.length ? (q.order[q.order.indexOf(q.index) + 1] ?? q.index + 1) : q.index + 1] ?? null;
  });

  const pos = $derived(dragging ?? s.position);
  const pct = $derived(s.duration ? (pos / s.duration) * 100 : 0);
  const abA = $derived(player.ab.a != null && s.duration ? (player.ab.a / s.duration) * 100 : null);
  const abB = $derived(player.ab.b != null && s.duration ? (player.ab.b / s.duration) * 100 : null);
</script>

<div class="np" class:wide>
  <div class="bg" aria-hidden="true">{#if bg}<img src={bg} alt="" />{/if}</div>
  <div class="top">
    <button class="icon-btn" aria-label="Minimise" title="Minimise" onclick={minimise}><ChevronDown size={28} /></button>
    {#if player.remoteDevice}
      <button class="remote" onclick={() => ui.open({ kind: 'devices' })}><Speaker size={16} /> {player.remoteLabel}</button>
      {#if player.castSpeaker}<button class="stopcast" onclick={() => player.stopCasting()}>{player.castConnecting ? 'Cancel' : 'Stop casting'}</button>{/if}
    {:else}<span></span>{/if}
    <div class="topr">
      {#if !player.remoteDevice}
        <button class="icon-btn" class:on={!!player.sleep} aria-label="Sleep timer" title={player.sleep ? `Sleep timer: ${sleepLabel(player.sleep, player.sleepNow)}` : 'Sleep timer'} onclick={() => ui.open({ kind: 'sleep' })}><Moon size={22} /></button>
      {/if}
      <button class="icon-btn" class:on={!!player.remoteDevice} aria-label="Devices" title="Devices" onclick={() => ui.open({ kind: 'devices' })}><Speaker size={24} /></button>
    </div>
  </div>

  <div class="body">
  <div class="stage">
    {#if ui.lyricsOpen && t && !wide}
      <Lyrics track={t} />
    {:else}
      <div
        class="art"
        role="button"
        tabindex="0"
        aria-label="Cover: tap for lyrics, swipe for next or previous, long-press for the song menu"
        style:view-transition-name="np-cover"
        onkeydown={(e) => e.key === 'Enter' && !wide && (ui.lyricsOpen = true)}
        use:gesture={{
          follow: true,
          ontap: () => !wide && t && (ui.lyricsOpen = true),
          onswipe: (d) => (d === 'left' ? player.next() : d === 'right' ? player.prev() : d === 'down' ? minimise() : d === 'up' ? ui.go('queue') : undefined),
          onlongpress: () => t && ui.open({ kind: 'songMenu', ctx: { track: t } }),
        }}
      >
        {#if t}<Cover albumId={t.albumId} size={768} rounded={6} />{:else}<div class="noart"></div>{/if}
      </div>
    {/if}
  </div>
  <div class="controls">

  <div class="meta">
    {#if t}
      <h2 class="ellipsis"><ExplicitBadge status={t.explicit} />{t.title}</h2>
      <button class="link ellipsis" onclick={() => ui.go('artist', t.artistId)}>{t.artist}</button>
      <button class="link ellipsis" onclick={() => ui.go('album', t.albumId)}>{t.album}</button>
    {:else}
      <h2>Nothing playing</h2>
      <div class="muted">Pick something in Albums, Playlists or Search.</div>
    {/if}
  </div>

  <div class="actions">
    <button class="icon-btn" class:on={t?.starred} aria-label="Favourite" disabled={!t} onclick={() => t && player.toggleStar(t)}>
      <Heart size={28} fill={t?.starred ? 'currentColor' : 'none'} />
    </button>
    <button class="icon-btn" aria-label="Song info" disabled={!t} onclick={() => t && ui.open({ kind: 'songInfo', trackId: t.id })}><Info size={28} /></button>
    <button class="icon-btn" aria-label="Add to playlist" disabled={!t} onclick={() => t && ui.open({ kind: 'addToPlaylist', tracks: [t] })}><ListPlus size={28} /></button>
    <button class="icon-btn boxed" aria-label="More" disabled={!t} onclick={() => t && ui.open({ kind: 'songMenu', ctx: { track: t } })}><Ellipsis size={22} strokeWidth={3} /></button>
    <span class="spacer"></span>
    {#if player.ab.a != null}
      <button class="ab on" onclick={() => player.abStep()} title="A-B repeat (tap to set B / clear)">A{player.ab.b != null ? '-B' : ''}</button>
    {/if}
    <button class="icon-btn" class:on={s.repeat !== 'off'} aria-label="Repeat: {s.repeat}" title="Repeat: {s.repeat}" onclick={() => player.cycleRepeat()}>
      {#if s.repeat === 'one'}<Repeat1 size={26} />{:else}<Repeat size={26} />{/if}
    </button>
    <button class="icon-btn" class:on={s.shuffle} aria-label="Shuffle" title="Shuffle" onclick={() => player.toggleShuffle()}><Shuffle size={26} /></button>
  </div>

  <RadioSteer />

  <div class="seek">
    <span>{fmtTime(pos)}</span>
    <div class="bar">
      {#if abA != null}<span class="mark" style:left="{abA}%">A</span>{/if}
      {#if abB != null}<span class="mark" style:left="{abB}%">B</span>{/if}
      <input
        type="range"
        min="0"
        max={s.duration || 1}
        step="0.1"
        value={pos}
        style:--p="{pct}%"
        aria-label="Seek"
        oninput={(e) => (dragging = +e.currentTarget.value)}
        onchange={(e) => {
          player.seek(+e.currentTarget.value);
          dragging = null;
        }}
      />
    </div>
    <span>{fmtTime(s.duration)}</span>
  </div>

  <div class="transport">
    <div class="vol">
      <button class="icon-btn small" aria-label="Volume" onclick={() => (volOpen = !volOpen)}>
        {#if s.volume === 0 || player.muted}<VolumeX size={22} />{:else}<Volume2 size={22} />{/if}
      </button>
      {#if volOpen}
        <div class="volpop">
          <input type="range" min="0" max="1" step="0.01" value={s.volume} style:--p="{s.volume * 100}%" aria-label="Volume" oninput={(e) => player.setVolume(+e.currentTarget.value)} />
        </div>
      {/if}
    </div>
    <button class="icon-btn big" aria-label="Previous" onclick={() => player.prev()}><SkipBack size={40} fill="currentColor" /></button>
    <button class="icon-btn huge" aria-label={s.playing ? 'Pause' : 'Play'} onclick={() => player.toggle()} disabled={!t}>
      {#if s.playing}<Pause size={56} fill="currentColor" />{:else}<Play size={56} fill="currentColor" />{/if}
    </button>
    <button class="icon-btn big" aria-label="Next" onclick={() => player.next()}><SkipForward size={40} fill="currentColor" /></button>
    <button class="icon-btn small" aria-label="Equalizer" onclick={() => ui.go('equalizer')}><SlidersVertical size={22} /></button>
  </div>
  {#if upNext && !wide}
    <button class="upnext" onclick={() => ui.go('queue')} use:gesture={{ onswipe: (d) => d === 'up' && ui.go('queue'), ontap: () => ui.go('queue') }}>
      <span class="muted">Up next</span><span class="ellipsis">{upNext.title} · {upNext.artist}</span>
    </button>
  {/if}
  </div>
  {#if wide && t}
    <div class="side"><Lyrics track={t} embedded big /></div>
  {/if}
  </div>
</div>

<style>
  .topr {
    display: flex;
    align-items: center;
  }
  .np {
    position: relative;
    height: 100%;
    display: flex;
    flex-direction: column;
    padding: 4px 16px 12px;
    background: #000;
    overflow-y: auto;
    isolation: isolate;
  }
  .bg {
    position: absolute;
    inset: 0;
    z-index: -1;
    overflow: hidden;
  }
  .bg img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    filter: blur(60px) saturate(1.3) brightness(0.38);
    transform: scale(1.35);
  }
  .bg::after {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(180deg, rgba(0, 0, 0, 0.15), rgba(0, 0, 0, 0.55));
  }
  .body {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  .controls {
    display: flex;
    flex-direction: column;
  }
  .wide .body {
    flex-direction: row;
    gap: 48px;
    padding: 0 32px 16px;
    align-items: center;
  }
  .wide .controls {
    width: min(520px, 42vw);
  }
  .wide .stage {
    flex: none;
    min-height: 0;
  }
  .wide .body > .stage,
  .wide .body > .controls {
    order: 0;
  }
  .wide .body {
    display: grid;
    grid-template-columns: min(520px, 42vw) minmax(0, 1fr);
    grid-template-rows: auto auto;
  }
  .wide .stage {
    grid-column: 1;
  }
  .wide .controls {
    grid-column: 1;
  }
  .wide .side {
    grid-column: 2;
    grid-row: 1 / span 2;
    align-self: stretch;
    min-height: 0;
    min-width: 0;
    max-height: 78vh;
    display: flex;
    padding-top: 12px;
  }
  .wide .art {
    width: min(100%, 46vh);
  }
  .upnext {
    display: flex;
    gap: 10px;
    justify-content: center;
    align-items: center;
    margin: 12px auto 0;
    padding: 8px 18px;
    border-radius: 999px;
    background: rgba(255, 255, 255, 0.08);
    max-width: 100%;
    font-size: 0.9rem;
  }
  .top {
    display: flex;
    gap: 8px;
    align-items: center;
    min-height: 48px;
  }
  .top > span,
  .top > .remote {
    flex: 1;
  }
  .stopcast {
    flex: none;
    padding: 5px 12px;
    border-radius: 999px;
    border: 1px solid currentColor;
    font-size: 0.82rem;
    opacity: 0.85;
  }
  .remote {
    display: flex;
    align-items: center;
    gap: 6px;
    color: var(--accent);
    font-weight: 600;
  }
  .stage {
    flex: 1;
    min-height: 220px;
    display: grid;
    place-items: center;
    padding: 8px 0 16px;
  }
  .art {
    width: min(100%, 52vh, 560px);
    box-shadow: 0 16px 50px rgba(0, 0, 0, 0.55);
    cursor: pointer;
    transition: transform 0.15s;
    user-select: none;
  }
  .noart {
    aspect-ratio: 1;
    background: #1a1a1a;
  }
  .meta {
    text-align: center;
    display: grid;
    gap: 4px;
    justify-items: center;
    padding: 0 8px;
  }
  h2 {
    margin: 0;
    font-size: 1.45rem;
    max-width: 100%;
  }
  .link {
    color: var(--fg-2);
    font-size: 1.08rem;
    max-width: 100%;
  }
  .link:hover {
    text-decoration: underline;
  }
  .actions {
    display: flex;
    align-items: center;
    gap: 4px;
    margin-top: 14px;
  }
  .spacer {
    flex: 1;
  }
  .boxed :global(svg) {
    background: #ddd;
    stroke: #000;
    border-radius: 8px;
    padding: 3px;
    width: 32px;
    height: 32px;
  }
  .ab {
    font-weight: 700;
    color: var(--accent);
    border: 1.5px solid var(--accent);
    border-radius: 6px;
    padding: 2px 6px;
  }
  .seek {
    display: flex;
    align-items: center;
    gap: 14px;
    margin-top: 12px;
    font-variant-numeric: tabular-nums;
  }
  .bar {
    flex: 1;
    position: relative;
  }
  .mark {
    position: absolute;
    top: -14px;
    transform: translateX(-50%);
    font-size: 0.7rem;
    color: var(--accent);
    font-weight: 700;
  }
  .transport {
    display: flex;
    justify-content: space-around;
    align-items: center;
    margin-top: 10px;
  }
  .big {
    width: 64px;
    height: 64px;
  }
  .huge {
    width: 84px;
    height: 84px;
  }
  .vol {
    position: relative;
  }
  .volpop {
    position: absolute;
    bottom: 50px;
    left: 0;
    width: 180px;
    background: var(--bg-2);
    padding: 10px 14px;
    border-radius: 10px;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.6);
  }
</style>
