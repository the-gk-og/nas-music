<script lang="ts">
  import { onMount } from 'svelte';
  import Dialogs from './components/Dialogs.svelte';
  import MiniPlayer from './components/MiniPlayer.svelte';
  import Nav from './components/Nav.svelte';
  import { player } from './lib/player.svelte';
  import { session } from './lib/session.svelte';
  import { ui } from './lib/ui.svelte';
  import { desktopInit } from './lib/desktop';
  import ConnectScreen from './screens/ConnectScreen.svelte';
  import Queues from './screens/Queues.svelte';
  import NowPlaying from './screens/NowPlaying.svelte';
  import Folders from './screens/Folders.svelte';
  import Albums from './screens/Albums.svelte';
  import AlbumDetail from './screens/AlbumDetail.svelte';
  import Artists from './screens/Artists.svelte';
  import ArtistDetail from './screens/ArtistDetail.svelte';
  import Genres from './screens/Genres.svelte';
  import TrackListPage from './screens/TrackListPage.svelte';
  import Playlists from './screens/Playlists.svelte';
  import PlaylistDetail from './screens/PlaylistDetail.svelte';
  import Search from './screens/Search.svelte';
  import More from './screens/More.svelte';
  import Settings from './screens/Settings.svelte';
  import Equalizer from './screens/Equalizer.svelte';
  import Mixes from './screens/Mixes.svelte';
  import Discover from './screens/Discover.svelte';
  import Account from './screens/Account.svelte';
  import Admin from './screens/Admin.svelte';

  /** `/join/<code>` (served by the server) or `#/join/<code>` registers with an invite. */
  function inviteFromUrl(): string {
    const p = /^\/join\/([^/?#]+)/.exec(location.pathname);
    if (p) return decodeURIComponent(p[1]);
    const h = /^#\/join\/([^/?#]+)/.exec(location.hash);
    return h ? decodeURIComponent(h[1]) : '';
  }

  const invite = inviteFromUrl();
  let ready = $state(false);
  let signedIn = $state(false);

  async function start() {
    signedIn = invite ? false : await session.refresh();
    ready = true;
    if (signedIn) {
      if (ui.route.name === 'join') ui.go('now');
      void player.init();
    }
  }

  function onSignedIn() {
    signedIn = true;
    if (ui.route.name === 'join' || !location.hash) ui.go('now');
    void player.init();
  }

  // Session expired elsewhere (401) → back to the Connect screen.
  $effect(() => {
    if (ready && signedIn && !session.signedIn) {
      player.reset();
      signedIn = false;
      location.reload();
    }
  });

  function onKey(e: KeyboardEvent) {
    if (!signedIn) return;
    const el = e.target as HTMLElement;
    if (el.closest('input, textarea, select, [contenteditable="true"]')) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === ' ' || e.code === 'Space' || e.key === 'MediaPlayPause') {
      e.preventDefault();
      player.toggle();
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      if (e.shiftKey) void player.next();
      else player.seekBy(5);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      if (e.shiftKey) void player.prev();
      else player.seekBy(-5);
    } else if (e.key === 'l' || e.key === 'L') {
      ui.go('now');
      ui.lyricsOpen = !ui.lyricsOpen;
    } else if (e.key === 'm' || e.key === 'M') player.toggleMute();
    else if (e.key === 's' || e.key === 'S') player.toggleShuffle();
    else if (e.key === 'r' || e.key === 'R') player.cycleRepeat();
    else if (e.key === '/') {
      e.preventDefault();
      ui.go('search');
    } else if (e.key === 'Escape' && ui.dialog) ui.close();
  }

  onMount(() => {
    void start();
    void desktopInit();
  });
</script>

<svelte:window onkeydown={onKey} />

{#if !ready}
  <div class="splash"><span class="ring"></span></div>
{:else if !signedIn}
  <ConnectScreen inviteCode={invite} ondone={onSignedIn} />
{:else}
  <div class="shell">
    <Nav />
    <main>
      <div class="content">
        {#key ui.route.name + '|' + ui.route.param}
          {#if ui.route.name === 'queues'}<Queues />
          {:else if ui.route.name === 'now'}<NowPlaying />
          {:else if ui.route.name === 'folders'}<Folders path={ui.route.param} />
          {:else if ui.route.name === 'albums'}<Albums />
          {:else if ui.route.name === 'album'}<AlbumDetail id={ui.route.param} />
          {:else if ui.route.name === 'artists'}<Artists />
          {:else if ui.route.name === 'artist'}<ArtistDetail id={ui.route.param} />
          {:else if ui.route.name === 'genres'}<Genres />
          {:else if ui.route.name === 'genre'}<TrackListPage kind="genre" param={ui.route.param} />
          {:else if ui.route.name === 'list'}<TrackListPage kind="list" param={ui.route.param} />
          {:else if ui.route.name === 'mix'}<TrackListPage kind="mix" param={ui.route.param} />
          {:else if ui.route.name === 'playlists'}<Playlists />
          {:else if ui.route.name === 'playlist'}<PlaylistDetail id={ui.route.param} />
          {:else if ui.route.name === 'search'}<Search />
          {:else if ui.route.name === 'settings'}<Settings />
          {:else if ui.route.name === 'equalizer'}<Equalizer />
          {:else if ui.route.name === 'mixes'}<Mixes />
          {:else if ui.route.name === 'discover'}<Discover />
          {:else if ui.route.name === 'account'}<Account />
          {:else if ui.route.name === 'admin'}<Admin />
          {:else}<More />
          {/if}
        {/key}
      </div>
      {#if ui.route.name !== 'now'}<MiniPlayer />{/if}
    </main>
  </div>
  <Dialogs />
{/if}

<div class="toasts" aria-live="polite">
  {#each ui.toasts as t (t.id)}<div class="toast">{t.text}</div>{/each}
</div>

<style>
  .splash {
    height: 100%;
    display: grid;
    place-items: center;
  }
  .ring {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    border: 5px solid var(--accent);
    border-right-color: transparent;
    animation: spin 0.9s linear infinite;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  .shell {
    height: 100%;
    display: flex;
    flex-direction: column-reverse;
  }
  main {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }
  .content {
    flex: 1;
    min-height: 0;
    position: relative;
  }
  @media (min-width: 900px) {
    .shell {
      flex-direction: row;
    }
  }
  .toasts {
    position: fixed;
    left: 50%;
    bottom: calc(var(--tabbar-h) + var(--mini-h) + 16px);
    transform: translateX(-50%);
    display: grid;
    gap: 8px;
    z-index: 100;
    pointer-events: none;
  }
  .toast {
    background: #f2f2f2;
    color: #111;
    padding: 12px 22px;
    border-radius: 999px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.5);
    text-align: center;
    animation: up 0.15s ease-out;
  }
  @keyframes up {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
  }
</style>
