<script lang="ts">
  import {
    Ellipsis,
    Heart,
    Info,
    ListPlus,
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
  import { dominantColor, tint } from '../lib/color';
  import { fmtTime } from '../lib/format';
  import { player } from '../lib/player.svelte';
  import { ui } from '../lib/ui.svelte';
  import Lyrics from './Lyrics.svelte';

  const s = $derived(player.shown);
  const t = $derived(s.track);
  let bg = $state('rgb(30,30,30)');
  let dragging = $state<number | null>(null);
  let volOpen = $state(false);

  async function onCover(src: string) {
    const c = await dominantColor(src);
    if (c) bg = tint(c);
  }

  const pos = $derived(dragging ?? s.position);
  const pct = $derived(s.duration ? (pos / s.duration) * 100 : 0);
  const abA = $derived(player.ab.a != null && s.duration ? (player.ab.a / s.duration) * 100 : null);
  const abB = $derived(player.ab.b != null && s.duration ? (player.ab.b / s.duration) * 100 : null);
</script>

<div class="np" style:--tint={bg}>
  <div class="top">
    {#if player.remoteDevice}
      <button class="remote" onclick={() => ui.open({ kind: 'devices' })}><Speaker size={16} /> Playing on {player.remoteDevice.name}</button>
    {:else}<span></span>{/if}
    <button class="icon-btn" class:on={!!player.remoteDevice} aria-label="Devices" title="Devices" onclick={() => ui.open({ kind: 'devices' })}><Speaker size={24} /></button>
  </div>

  <div class="stage">
    {#if ui.lyricsOpen && t}
      <Lyrics track={t} />
    {:else}
      <button class="art" aria-label="Show lyrics" title="Tap for lyrics" onclick={() => (ui.lyricsOpen = true)} disabled={!t}>
        {#if t}<Cover albumId={t.albumId} size={768} rounded={2} onsrc={onCover} />{:else}<div class="noart"></div>{/if}
      </button>
    {/if}
  </div>

  <div class="meta">
    {#if t}
      <h2 class="ellipsis">{t.title}</h2>
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
</div>

<style>
  .np {
    height: 100%;
    display: flex;
    flex-direction: column;
    padding: 4px 16px 12px;
    background: radial-gradient(120% 80% at 50% 25%, var(--tint) 0%, color-mix(in srgb, var(--tint) 45%, #000) 60%, #0b0b0b 100%);
    transition: background 0.6s;
    overflow-y: auto;
  }
  .top {
    display: flex;
    justify-content: space-between;
    align-items: center;
    min-height: 48px;
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
