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
  import Home from './screens/Home.svelte';
  import ForYou from './screens/ForYou.svelte';
  import BlendPage from './screens/BlendPage.svelte';
  import ReleaseRadar from './screens/ReleaseRadar.svelte';
  import Wrapped from './screens/Wrapped.svelte';
  import Search from './screens/Search.svelte';
  import Library from './screens/Library.svelte';
  import Queues from './screens/Queues.svelte';
  import NowPlaying from './screens/NowPlaying.svelte';
  import Folders from './screens/Folders.svelte';
  import AlbumDetail from './screens/AlbumDetail.svelte';
  import ArtistDetail from './screens/ArtistDetail.svelte';
  import TrackListPage from './screens/TrackListPage.svelte';
  import PlaylistDetail from './screens/PlaylistDetail.svelte';
  import ToGet from './screens/ToGet.svelte';
  import Uploads from './screens/Uploads.svelte';
  import Settings from './screens/Settings.svelte';
  import Equalizer from './screens/Equalizer.svelte';
  import Account from './screens/Account.svelte';
  import AdminConsole from './screens/admin/AdminConsole.svelte';
  import PlayerBar from './components/PlayerBar.svelte';
  import RightPanel from './components/RightPanel.svelte';
  import { appearance } from './lib/appearance.svelte';
  import { library } from './lib/library.svelte';
  import { tabletSidePane } from './lib/v3';
  import { family } from './lib/family.svelte';

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
      if (ui.route.name === 'join') ui.go('home');
      afterSignIn();
    }
  }

  function afterSignIn() {
    void player.init();
    void appearance.load();
    void library.refreshPlaylists();
    void library.refreshPins();
    void family.load();
  }

  function onSignedIn() {
    signedIn = true;
    if (ui.route.name === 'join' || !location.hash) ui.go('home');
    afterSignIn();
  }

  const form = $derived(appearance.form);
  const name = $derived(ui.route.name === 'queues' ? 'queue' : ui.route.name);
  /** Tablet landscape: Now Playing (or the Queue, when Now Playing is the page) as a side pane. */
  const sidePane = $derived(form === 'tablet' && tabletSidePane(appearance.width, appearance.height));
  const fullNow = $derived(name === 'now');

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
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) {
      e.preventDefault();
      ui.focusSearch();
      return;
    }
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
      if (form === 'desktop') ui.togglePanel('lyrics');
      else {
        ui.go('now');
        ui.lyricsOpen = !ui.lyricsOpen;
      }
    } else if (e.key === 'q' || e.key === 'Q') {
      if (form === 'desktop') ui.togglePanel('queue');
      else ui.go('queue');
    } else if (e.key === 'm' || e.key === 'M') player.toggleMute();
    else if (e.key === 's' || e.key === 'S') player.toggleShuffle();
    else if (e.key === 'r' || e.key === 'R') player.cycleRepeat();
    else if (e.key === '/') {
      e.preventDefault();
      ui.focusSearch();
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
  {#snippet page(n: string, p: string)}
    {#if n === 'home'}<Home />
    {:else if n === 'foryou'}<ForYou />
    {:else if n === 'search'}<Search />
    {:else if n === 'library'}<Library />
    {:else if n === 'queue'}<Queues />
    {:else if n === 'now'}<NowPlaying />
    {:else if n === 'folders'}<Folders path={p} />
    {:else if n === 'album'}<AlbumDetail id={p} />
    {:else if n === 'artist'}<ArtistDetail id={p} />
    {:else if n === 'genre'}<TrackListPage kind="genre" param={p} />
    {:else if n === 'list'}<TrackListPage kind="list" param={p} />
    {:else if n === 'mix'}<TrackListPage kind="mix" param={p} />
    {:else if n === 'playlist'}<PlaylistDetail id={p} />
    {:else if n === 'toget'}<ToGet />
    {:else if n === 'uploads'}<Uploads />
    {:else if n === 'release-radar'}<ReleaseRadar />
    {:else if n === 'wrapped'}<Wrapped year={p} />
    {:else if n === 'blend'}<BlendPage id={p || 'new'} />
    {:else if n === 'settings'}<Settings />
    {:else if n === 'equalizer'}<Equalizer />
    {:else if n === 'account'}<Account />
    {:else if n === 'admin'}<AdminConsole />
    {:else}<Home />
    {/if}
  {/snippet}

  <div class="shell {form}" class:fullnow={fullNow}>
    {#if !(fullNow && form === 'phone')}<Nav />{/if}
    <main>
      <div class="content">
        {#key name + '|' + ui.route.param}
          <div class="view">{@render page(name, ui.route.param)}</div>
        {/key}
      </div>
      {#if form !== 'desktop' && !fullNow}<MiniPlayer />{/if}
    </main>
    {#if form === 'desktop' && ui.panel && !fullNow && name !== 'admin'}<RightPanel />{/if}
    {#if sidePane}
      <aside class="side">{#if fullNow}<Queues compact />{:else}<NowPlaying />{/if}</aside>
    {/if}
    {#if form === 'desktop'}<div class="playerbar"><PlayerBar /></div>{/if}
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
    display: grid;
    background: var(--bg);
  }
  /* phone: content, mini player, bottom tabs */
  .shell.phone {
    grid-template-rows: 1fr auto;
    grid-template-areas: 'main' 'nav';
  }
  .shell.phone :global(.nav) {
    grid-area: nav;
  }
  .shell.phone main {
    grid-area: main;
  }
  /* tablet: rail | content | optional side pane */
  .shell.tablet {
    grid-template-columns: auto 1fr auto;
  }
  .side {
    width: min(420px, 40vw);
    border-left: 1px solid var(--line);
    min-height: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }
  .side > :global(*) {
    flex: 1;
  }
  .side :global(.art) {
    width: min(100%, 38vh);
  }
  /* desktop: sidebar | main | right panel, player bar along the bottom */
  .shell.desktop {
    grid-template-columns: auto 1fr auto;
    grid-template-rows: 1fr auto;
  }
  .playerbar {
    grid-column: 1 / -1;
  }
  .shell.desktop.fullnow main {
    grid-column: 1 / -1;
    grid-row: 1;
  }
  .shell.desktop.fullnow :global(.nav) {
    display: none;
  }
  main {
    min-height: 0;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }
  .content {
    flex: 1;
    min-height: 0;
    position: relative;
  }
  .view {
    height: 100%;
  }
  .view > :global(*) {
    height: 100%;
  }
  :global([data-motion='quick']) .view {
    animation: fadein 0.15s ease-out;
  }
  :global([data-motion='expressive']) .view {
    animation: risein 0.3s cubic-bezier(0.2, 0.8, 0.2, 1);
  }
  @keyframes fadein {
    from {
      opacity: 0;
    }
  }
  @keyframes risein {
    from {
      opacity: 0;
      transform: translateY(14px);
    }
  }
  .toasts {
    position: fixed;
    left: 50%;
    bottom: calc(var(--tabbar-h) + var(--mini-h) + 24px);
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
