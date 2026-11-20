<script lang="ts">
  // Desktop player bar (§6): cover/title, transport + seek, and lyrics/queue/devices/EQ/volume/full view.
  import {
    Captions,
    Heart,
    ListMusic,
    Maximize2,
    Pause,
    Play,
    Repeat,
    Repeat1,
    Shuffle,
    SkipBack,
    SkipForward,
    SlidersVertical,
    Speaker,
    Volume1,
    Volume2,
    VolumeX,
  } from '@lucide/svelte';
  import { withTransition } from '../lib/appearance.svelte';
  import { fmtTime } from '../lib/format';
  import { player } from '../lib/player.svelte';
  import { ui } from '../lib/ui.svelte';
  import Cover from './Cover.svelte';
  import ExplicitBadge from './ExplicitBadge.svelte';

  const s = $derived(player.shown);
  const t = $derived(s.track);
  let drag = $state<number | null>(null);
  const pos = $derived(drag ?? s.position);
  const pct = $derived(s.duration ? (pos / s.duration) * 100 : 0);
  const vol = $derived(player.muted ? 0 : s.volume);
  const openNow = () => withTransition(() => ui.go('now'));
</script>

<footer class="bar">
  <div class="left">
    {#if t}
      <button class="thumb" aria-label="Open Now Playing" onclick={openNow} style:view-transition-name={ui.route.name === 'now' ? 'none' : 'np-cover'}>
        <Cover albumId={t.albumId} size={128} />
      </button>
      <div class="meta">
        <button class="title ellipsis" onclick={() => ui.go('album', t.albumId)}><ExplicitBadge status={t.explicit} />{t.title}</button>
        <button class="artist ellipsis" onclick={() => ui.go('artist', t.artistId)}>{t.artist}</button>
        {#if player.remoteDevice}<div class="remote ellipsis"><Speaker size={12} /> {player.remoteLabel}{#if player.castSpeaker} · <button class="stopcast" onclick={() => player.stopCasting()}>{player.castConnecting ? 'Cancel' : 'Stop casting'}</button>{/if}</div>{/if}
      </div>
      <button class="icon-btn sm" class:on={t.starred} aria-label="Favourite" onclick={() => player.toggleStar(t)}><Heart size={18} fill={t.starred ? 'currentColor' : 'none'} /></button>
    {/if}
  </div>

  <div class="center">
    <div class="ctrls">
      <button class="icon-btn sm" class:on={s.shuffle} aria-label="Shuffle" title="Shuffle (S)" onclick={() => player.toggleShuffle()}><Shuffle size={18} /></button>
      <button class="icon-btn sm" aria-label="Previous" title="Previous (Shift+←)" onclick={() => player.prev()}><SkipBack size={20} fill="currentColor" /></button>
      <button class="pp" aria-label={s.playing ? 'Pause' : 'Play'} title="Play/pause (Space)" disabled={!t} onclick={() => player.toggle()}>
        {#if s.playing}<Pause size={20} fill="currentColor" />{:else}<Play size={20} fill="currentColor" />{/if}
      </button>
      <button class="icon-btn sm" aria-label="Next" title="Next (Shift+→)" onclick={() => player.next()}><SkipForward size={20} fill="currentColor" /></button>
      <button class="icon-btn sm" class:on={s.repeat !== 'off'} aria-label="Repeat: {s.repeat}" title="Repeat (R)" onclick={() => player.cycleRepeat()}>
        {#if s.repeat === 'one'}<Repeat1 size={18} />{:else}<Repeat size={18} />{/if}
      </button>
    </div>
    <div class="seek">
      <span>{fmtTime(pos)}</span>
      <input
        type="range"
        min="0"
        max={s.duration || 1}
        step="0.1"
        value={pos}
        style:--p="{pct}%"
        style:--fill="var(--accent)"
        aria-label="Seek"
        oninput={(e) => (drag = +e.currentTarget.value)}
        onchange={(e) => {
          player.seek(+e.currentTarget.value);
          drag = null;
        }}
      />
      <span>{fmtTime(s.duration)}</span>
    </div>
  </div>

  <div class="right">
    <button class="icon-btn sm" class:on={ui.panel === 'lyrics'} aria-label="Lyrics" title="Lyrics (L)" onclick={() => ui.togglePanel('lyrics')}><Captions size={19} /></button>
    <button class="icon-btn sm" class:on={ui.panel === 'queue'} aria-label="Queue" title="Queue" onclick={() => ui.togglePanel('queue')}><ListMusic size={19} /></button>
    <button class="icon-btn sm" class:on={ui.panel === 'devices' || !!player.remoteDevice} aria-label="Devices" title="Devices" onclick={() => ui.togglePanel('devices')}><Speaker size={19} /></button>
    <button class="icon-btn sm" aria-label="Equalizer" title="Equalizer" onclick={() => ui.go('equalizer')}><SlidersVertical size={18} /></button>
    <button class="icon-btn sm" aria-label="Mute" title="Mute (M)" onclick={() => player.toggleMute()}>
      {#if vol === 0}<VolumeX size={19} />{:else if vol < 0.5}<Volume1 size={19} />{:else}<Volume2 size={19} />{/if}
    </button>
    <input class="vol" type="range" min="0" max="1" step="0.01" value={vol} style:--p="{vol * 100}%" style:--fill="var(--accent)" aria-label="Volume" oninput={(e) => player.setVolume(+e.currentTarget.value)} />
    <button class="icon-btn sm" aria-label="Full Now Playing view" title="Now Playing" onclick={openNow}><Maximize2 size={18} /></button>
  </div>
</footer>

<style>
  .bar {
    display: grid;
    grid-template-columns: minmax(180px, 1fr) minmax(320px, 2fr) minmax(180px, 1fr);
    align-items: center;
    gap: 16px;
    height: 84px;
    padding: 0 14px;
    background: var(--bg);
    border-top: 1px solid var(--line);
  }
  .left {
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
  }
  .thumb {
    width: 58px;
    flex: none;
  }
  .meta {
    min-width: 0;
    display: grid;
  }
  .title {
    text-align: left;
    font-weight: 600;
  }
  .artist {
    text-align: left;
    color: var(--fg-3);
    font-size: 0.85rem;
  }
  .title:hover,
  .artist:hover {
    text-decoration: underline;
  }
  .remote {
    color: var(--accent);
    font-size: 0.78rem;
    display: flex;
    gap: 4px;
    align-items: center;
  }
  .stopcast {
    color: inherit;
    font-size: inherit;
    text-decoration: underline;
    flex: none;
  }
  .center {
    display: grid;
    gap: 2px;
    justify-items: center;
  }
  .ctrls {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .pp {
    width: 38px;
    height: 38px;
    border-radius: 50%;
    background: #fff;
    color: #000;
    display: grid;
    place-items: center;
  }
  .pp:hover {
    transform: scale(1.05);
  }
  .seek {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    font-size: 0.75rem;
    color: var(--fg-3);
    font-variant-numeric: tabular-nums;
  }
  .right {
    display: flex;
    justify-content: flex-end;
    align-items: center;
    gap: 2px;
  }
  .sm {
    width: 36px;
    height: 36px;
  }
  .vol {
    width: 100px;
  }
</style>
