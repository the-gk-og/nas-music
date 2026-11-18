<script lang="ts">
  import {
    ArrowRightLeft,
    Captions,
    Disc3,
    Gauge,
    Info,
    Laptop,
    ListEnd,
    ListPlus,
    ListStart,
    Monitor,
    Plus,
    Share2,
    Smartphone,
    Sparkles,
    Speaker,
    User,
    X,
  } from '@lucide/svelte';
  import { fmtTime, formatLine, unknown } from '../lib/format';
  import { parseLrc, stampLine } from '../lib/lyrics';
  import { player } from '../lib/player.svelte';
  import { session } from '../lib/session.svelte';
  import type { Playlist, TrackInfo } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import { queueLabel } from '../lib/queue';
  import Cover from './Cover.svelte';
  import Sheet from './Sheet.svelte';

  const d = $derived(ui.dialog);

  // ---- song info ----
  let info = $state<TrackInfo | null>(null);
  let infoErr = $state<string | null>(null);
  $effect(() => {
    if (d?.kind !== 'songInfo') return;
    info = null;
    infoErr = null;
    session.api.trackInfo(d.trackId).then((i) => (info = i)).catch((e) => (infoErr = e.message));
  });

  // ---- playlists picker ----
  let playlists = $state<Playlist[] | null>(null);
  $effect(() => {
    if (d?.kind !== 'addToPlaylist') return;
    playlists = null;
    session.api.playlists().then((p) => (playlists = p)).catch(() => (playlists = []));
  });

  // ---- prompt value / lyrics editor ----
  let promptValue = $state('');
  let raw = $state('');
  let editorLine = $state(0);
  let saving = $state(false);
  $effect(() => {
    if (d?.kind === 'prompt') promptValue = d.value;
    if (d?.kind === 'lyricsEditor') {
      raw = d.raw;
      editorLine = 0;
    }
  });

  function goto(route: string, param: string) {
    ui.close();
    ui.go(route, param);
  }

  async function moreLike(id: string) {
    ui.close();
    ui.toast('Building a mix…');
    try {
      const m = await session.api.moreLike(id, 40);
      ui.go('mix', m.id);
    } catch (e) {
      ui.toast((e as { status?: number }).status === 404 ? "This song hasn't been analysed yet" : (e as Error).message);
    }
  }

  async function share(id: string) {
    const base = session.saved.origin && session.saved.origin !== 'mock' ? session.saved.origin : location.origin;
    const url = `${base}/#/album/${encodeURIComponent((d as { ctx: { track: { albumId: string } } }).ctx.track.albumId)}?track=${encodeURIComponent(id)}`;
    try {
      await navigator.clipboard.writeText(url);
      ui.toast('Link copied');
    } catch {
      ui.toast(url);
    }
    ui.close();
  }

  async function addTo(p: Playlist) {
    if (d?.kind !== 'addToPlaylist') return;
    try {
      await session.api.addToPlaylist(p.id, d.tracks.map((t) => t.id));
      ui.toast(`Added to "${p.name}"`);
      ui.close();
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }
  async function newPlaylist() {
    if (d?.kind !== 'addToPlaylist') return;
    const tracks = d.tracks;
    const name = await ui.prompt('New playlist', 'Name', '', 'Create');
    if (!name) return;
    try {
      const p = await session.api.createPlaylist(name);
      await session.api.addToPlaylist(p.id, tracks.map((t) => t.id));
      ui.toast(`Added to "${name}"`);
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  async function saveLyrics() {
    if (d?.kind !== 'lyricsEditor') return;
    saving = true;
    try {
      await session.api.saveLyrics(d.track.id, raw);
      ui.toast('Lyrics saved');
      ui.close();
    } catch (e) {
      ui.toast((e as Error).message);
    } finally {
      saving = false;
    }
  }

  function stamp() {
    raw = stampLine(raw, editorLine, Math.round(player.position * 1000));
    editorLine = Math.min(editorLine + 1, raw.split('\n').length - 1);
  }

  const kindIcon = (k: string) => (k === 'android' || k === 'watch' ? Smartphone : k === 'desktop' ? Laptop : k === 'web' ? Monitor : Speaker);
  const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2];
</script>

{#if d?.kind === 'songMenu'}
  {@const t = d.ctx.track}
  <Sheet label="Song menu">
    <div class="menuhead">
      <div class="thumb"><Cover albumId={t.albumId} size={128} /></div>
      <div class="ellipsis"><b class="ellipsis">{t.title}</b><div class="muted ellipsis">{t.artist}</div></div>
    </div>
    <div class="menu">
      <button onclick={() => { ui.close(); ui.go('now'); ui.lyricsOpen = true; if (player.current?.id !== t.id && d.ctx.list) void player.playList(d.ctx.list, d.ctx.list.indexOf(t), 'Queue'); }}><Captions />Show lyrics</button>
      <button onclick={() => { ui.close(); player.abStep(); ui.toast(player.ab.b == null ? 'A set — tap A-B repeat again to set B' : 'A-B repeat on'); }} disabled={player.current?.id !== t.id}><ArrowRightLeft />A-B repeat{player.ab.a != null ? (player.ab.b != null ? ' (clear)' : ' (set B)') : ''}</button>
      <button onclick={() => ui.open({ kind: 'speed' })}><Gauge />Play speed and pitch</button>
      <hr />
      <button onclick={() => { ui.close(); void player.addToQueue([t], 'next'); }}><ListStart />Play next</button>
      <button onclick={() => ui.open({ kind: 'addToQueue', tracks: [t] })}><ListEnd />Add to a queue</button>
      <button onclick={() => ui.open({ kind: 'addToPlaylist', tracks: [t] })}><ListPlus />Add to playlists</button>
      {#if d.ctx.queueId != null && d.ctx.position != null}
        <button onclick={() => { ui.close(); void player.removeFromQueue(d.ctx.queueId!, d.ctx.position!); }}><X />Remove from this queue</button>
      {/if}
      <hr />
      <button onclick={() => goto('album', t.albumId)}><Disc3 />Go to album</button>
      <button onclick={() => goto('artist', t.artistId)}><User />Go to artist</button>
      <button onclick={() => moreLike(t.id)}><Sparkles />More like this</button>
      <button onclick={() => ui.open({ kind: 'songInfo', trackId: t.id })}><Info />Song info</button>
      <button onclick={() => share(t.id)}><Share2 />Share</button>
    </div>
  </Sheet>
{:else if d?.kind === 'songInfo'}
  <Sheet label="Song info">
    <div class="pad">
      <h2 class="title"><Info size={30} /> Song info</h2>
      {#if infoErr}<p class="error">{infoErr}</p>
      {:else if !info}<p class="muted">Loading…</p>
      {:else}
        <div class="filehead">
          <div class="bigthumb"><Cover albumId={info.albumId} size={256} /></div>
          <div><b>File name</b><div class="v">{unknown(info.fileName)}</div></div>
        </div>
        <div class="f"><b>Location</b><div class="v">{info.folder ? info.folder.split('/').join(' › ') : '[unknown]'}</div></div>
        <hr />
        {#each [['Title', info.title], ['Album', info.album], ['Artist', info.artist], ['Album-Artist', info.albumArtist], ['Composer', info.composer], ['Genre', info.genre], ['Lyricist', info.lyricist], ['Track number', info.trackNo], ['Disc number', info.discNo], ['Year', info.year], ['Comment', info.comment]] as [k, v]}
          <div class="f"><b>{k}</b><div class="v">{unknown(v)}</div></div>
        {/each}
        <hr />
        <div class="f"><b>Duration</b><div class="v">{fmtTime(info.durationS)}</div></div>
        <div class="f"><b>Format</b><div class="v">{formatLine(info)}</div></div>
      {/if}
    </div>
    <div class="foot"><span></span><button class="btn text" onclick={() => ui.close()}>Done</button></div>
  </Sheet>
{:else if d?.kind === 'addToPlaylist'}
  <Sheet label="Add to playlist">
    <div class="pad">
      <h2>Add to playlist</h2>
      {#if !playlists}<p class="muted">Loading…</p>{/if}
      {#each playlists ?? [] as p (p.id)}
        <button class="row-item" onclick={() => addTo(p)}><ListPlus size={22} />{p.name}</button>
      {/each}
      <button class="row-item" onclick={newPlaylist}><Plus size={22} color="var(--accent)" />New playlist…</button>
    </div>
    <div class="foot"><span></span><button class="btn text" onclick={() => ui.close()}>Cancel</button></div>
  </Sheet>
{:else if d?.kind === 'addToQueue'}
  <Sheet label="Add to a queue">
    <div class="pad">
      <h2>Add to a queue</h2>
      {#each player.queues as q (q.id)}
        <button class="row-item" onclick={() => { ui.close(); void player.addToQueue(d.tracks, 'end', q.id); }}>
          <ListEnd size={22} /><span class="ellipsis">{queueLabel(q)}</span>{#if q.id === player.queue?.id}<span class="muted"> · playing</span>{/if}
        </button>
      {/each}
      <button class="row-item" onclick={async () => {
        const tracks = d.tracks;
        const name = await ui.prompt('New queue', 'Name', tracks[0]?.title ?? 'Queue', 'Create');
        if (!name) return;
        try { await session.api.createQueue(name, tracks.map((t) => t.id), 0); await player.refreshQueues(); ui.toast(`Queue "${name}" created`); } catch (e) { ui.toast((e as Error).message); }
      }}><Plus size={22} color="var(--accent)" />New queue…</button>
    </div>
    <div class="foot"><span></span><button class="btn text" onclick={() => ui.close()}>Cancel</button></div>
  </Sheet>
{:else if d?.kind === 'speed'}
  <Sheet label="Play speed and pitch">
    <div class="pad">
      <h2>Play speed and pitch</h2>
      <div class="f"><b>Speed: {player.speed.toFixed(2)}×</b></div>
      <input type="range" min="0.5" max="2" step="0.05" value={player.speed} style:--p="{((player.speed - 0.5) / 1.5) * 100}%" aria-label="Speed" oninput={(e) => player.setSpeed(+e.currentTarget.value)} />
      <div class="chips">{#each SPEEDS as s}<button class="chip" class:on={player.speed === s} onclick={() => player.setSpeed(s)}>{s}×</button>{/each}</div>
      <div class="f"><b>Pitch: {player.pitch > 0 ? '+' : ''}{player.pitch} semitones</b></div>
      {#if player.pitchAvailable}
        <input type="range" min="-12" max="12" step="0.5" value={player.pitch} style:--p="{((player.pitch + 12) / 24) * 100}%" aria-label="Pitch" oninput={(e) => player.setPitch(+e.currentTarget.value)} />
      {:else}
        <p class="muted small">Pitch shifting starts working once playback has started in this tab (needs Web Audio worklets).</p>
      {/if}
      <p class="muted small">Speed keeps the pitch; pitch changes don't change the speed. Both are local to this device.</p>
    </div>
    <div class="foot">
      <button class="btn text" onclick={() => { player.setSpeed(1); player.setPitch(0); }}>Reset</button>
      <button class="btn text" onclick={() => ui.close()}>Done</button>
    </div>
  </Sheet>
{:else if d?.kind === 'lyricsEditor'}
  <Sheet wide label="Edit lyrics">
    <div class="pad">
      <h2>Lyrics — {d.track.title}</h2>
      <p class="muted small">Plain text or LRC (<code>[mm:ss.xx]line</code>). Saved on the server as your own version; the music file is never changed.</p>
      <textarea bind:value={raw} rows="14" spellcheck="false" aria-label="Lyrics"></textarea>
      <div class="sync">
        <span class="muted small">Sync while playing: line {editorLine + 1}</span>
        <button class="btn" onclick={() => (editorLine = Math.max(0, editorLine - 1))}>◀</button>
        <button class="btn" onclick={() => (editorLine = Math.min(raw.split('\n').length - 1, editorLine + 1))}>▶</button>
        <button class="btn" onclick={stamp} title="Set this line's time to the current position">Stamp {fmtTime(player.position)}</button>
        <span class="muted small">{parseLrc(raw).synced ? 'synced' : 'plain'}</span>
      </div>
    </div>
    <div class="foot">
      <button class="btn text" onclick={() => ui.close()}>Cancel</button>
      <button class="btn text" disabled={saving} onclick={saveLyrics}>Save</button>
    </div>
  </Sheet>
{:else if d?.kind === 'devices'}
  <Sheet label="Devices">
    <div class="pad">
      <h2><Speaker size={24} /> Devices</h2>
      {#if player.connectStatus !== 'open'}
        <p class="muted small">{player.connectStatus === 'reconnecting' ? 'Connecting to the server… (live device control needs a v2 server)' : 'Not connected'}</p>
      {/if}
      {#each player.devices.filter((x) => x.online || x.isActive) as dev (dev.id)}
        {@const Icon = kindIcon(dev.kind)}
        {@const active = player.remoteState?.activeDeviceId === dev.id}
        <button class="row-item dev" class:active disabled={!dev.canPlay} onclick={() => { player.transfer(dev.id); ui.close(); }}>
          <Icon size={24} />
          <div class="grow">
            <div>{dev.name}{dev.id === player.myDeviceId ? ' (this device)' : ''}</div>
            <div class="sub">{active ? (player.remoteState?.playing ? 'Playing' : 'Active') : dev.canPlay ? 'Tap to play here' : 'Remote only'}</div>
          </div>
        </button>
      {:else}
        {#if player.connectStatus === 'open'}<p class="muted">No other devices online.</p>{/if}
      {/each}
    </div>
    <div class="foot"><span></span><button class="btn text" onclick={() => ui.close()}>Close</button></div>
  </Sheet>
{:else if d?.kind === 'prompt'}
  <Sheet label={d.title}>
    <form class="pad form" onsubmit={(e) => { e.preventDefault(); const v = promptValue.trim(); const r = d.resolve; ui.dialog = null; r(v || null); }}>
      <h2>{d.title}</h2>
      <!-- svelte-ignore a11y_autofocus -->
      <label>{d.label}<input bind:value={promptValue} autofocus /></label>
      <div class="foot inline">
        <button type="button" class="btn text" onclick={() => ui.close()}>Cancel</button>
        <button type="submit" class="btn text">{d.ok}</button>
      </div>
    </form>
  </Sheet>
{:else if d?.kind === 'confirm'}
  <Sheet label={d.title}>
    <div class="pad">
      <h2>{d.title}</h2>
      <p>{d.message}</p>
    </div>
    <div class="foot">
      <button class="btn text" onclick={() => ui.close()}>Cancel</button>
      <button class="btn text" onclick={() => { const r = d.resolve; ui.dialog = null; r(true); }}>{d.ok}</button>
    </div>
  </Sheet>
{/if}

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
  .title {
    font-size: 1.7rem;
    gap: 18px;
  }
  .menuhead {
    display: flex;
    gap: 12px;
    align-items: center;
    padding: 16px 22px 6px;
  }
  .thumb {
    width: 44px;
    flex: none;
  }
  .menu {
    padding: 6px 0 12px;
  }
  .menu button {
    display: flex;
    align-items: center;
    gap: 22px;
    width: 100%;
    padding: 12px 30px;
    font-size: 1.08rem;
    text-align: left;
  }
  .menu button:hover:not(:disabled) {
    background: rgba(255, 255, 255, 0.05);
  }
  .menu :global(svg) {
    width: 24px;
    height: 24px;
    flex: none;
  }
  hr {
    border: 0;
    border-top: 1.5px solid #3a3a3a;
    margin: 6px 30px;
  }
  .filehead {
    display: flex;
    gap: 16px;
    align-items: flex-start;
  }
  .bigthumb {
    width: 96px;
    flex: none;
  }
  .f {
    margin: 14px 0;
  }
  .f b {
    font-size: 1.05rem;
  }
  .v {
    color: var(--fg-2);
    font-size: 1.05rem;
    margin-top: 2px;
    word-break: break-word;
  }
  .foot {
    display: flex;
    justify-content: space-between;
    padding: 6px 12px 12px;
    position: sticky;
    bottom: 0;
    background: var(--bg-2);
  }
  .foot.inline {
    position: static;
    padding: 0;
    justify-content: flex-end;
  }
  .chips {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin: 8px 0;
  }
  .chips .chip {
    padding: 4px 12px;
    border-color: #666;
  }
  textarea {
    width: 100%;
    font-family: ui-monospace, monospace;
    font-size: 0.9rem;
  }
  .sync {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
    margin-top: 8px;
  }
  .sync .btn {
    padding: 6px 10px;
  }
  .small {
    font-size: 0.85rem;
  }
  .dev.active {
    color: var(--accent);
  }
  .grow {
    flex: 1;
  }
</style>
