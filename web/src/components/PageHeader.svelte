<script lang="ts">
  // Album / artist / playlist header (§3.4): "Big header" (large art, colour bleeding into black, Play,
  // Shuffle, Radio, ⋯) or "Simple list" (small, Musicolet-like). Style toggle lives in the ⋯ menu.
  import { ArrowLeft, Ellipsis, Pin, PinOff, Play, Radio, Rows3, Shuffle, SquareStack } from '@lucide/svelte';
  import type { Snippet } from 'svelte';
  import { appearance, type PageStyle } from '../lib/appearance.svelte';
  import { dominantColor, tint } from '../lib/color';
  import { library } from '../lib/library.svelte';
  import { session } from '../lib/session.svelte';
  import type { ShelfItem } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import Art from './Art.svelte';

  let {
    item,
    kind,
    label,
    meta = '',
    onplay,
    onshuffle,
    onradio,
    menu,
  }: {
    item: ShelfItem;
    kind: 'album' | 'artist' | 'playlist' | 'mix' | 'list';
    label: string;
    meta?: string;
    onplay?: () => void;
    onshuffle?: () => void;
    onradio?: () => void;
    menu?: Snippet;
  } = $props();

  const styleKind = $derived(kind === 'album' || kind === 'artist' ? kind : 'playlist');
  const style = $derived<PageStyle>(appearance.pageStyle(styleKind));
  const pinKind = $derived(kind === 'list' ? null : kind);
  let color = $state('rgb(40,40,40)');
  let menuOpen = $state(false);

  $effect(() => {
    const id = item.albumId;
    if (!id) return;
    session.api.artSrc(id, 256).then(dominantColor).then((c) => c && (color = tint(c, 0.75))).catch(() => {});
  });

  const pinned = $derived(pinKind ? library.isPinned(pinKind, item.id) : false);
</script>

<header class="ph {style}" style:--c={color}>
  <div class="nav">
    <button class="round" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={20} /></button>
  </div>
  <div class="main">
    <div class="art" class:round={kind === 'artist'}><Art {item} size={style === 'big' ? 512 : 256} round={kind === 'artist'} /></div>
    <div class="txt">
      <div class="label">{label}</div>
      <h1>{item.title}</h1>
      {#if item.subtitle || meta}<div class="meta">{item.subtitle}{item.subtitle && meta ? ' · ' : ''}{meta}</div>{/if}
    </div>
  </div>
  <div class="actions">
    {#if onplay}<button class="playbig" aria-label="Play" onclick={onplay}><Play size={26} fill="currentColor" /></button>{/if}
    {#if onshuffle}<button class="icon-btn" aria-label="Shuffle" title="Shuffle" onclick={onshuffle}><Shuffle size={24} /></button>{/if}
    {#if onradio}<button class="icon-btn" aria-label="Start radio" title="Radio" onclick={onradio}><Radio size={24} /></button>{/if}
    {#if pinKind}
      <button class="icon-btn" class:on={pinned} aria-label={pinned ? 'Unpin from Home' : 'Pin to Home'} title={pinned ? 'Unpin from Home' : 'Pin to Home'} onclick={() => library.togglePin(pinKind, item.id, item.title)}>
        {#if pinned}<PinOff size={22} />{:else}<Pin size={22} />{/if}
      </button>
    {/if}
    <div class="rel">
      <button class="icon-btn" aria-label="More options" onclick={() => (menuOpen = !menuOpen)}><Ellipsis size={24} /></button>
      {#if menuOpen}
        <div class="menu" role="menu">
          {@render menu?.()}
          <button role="menuitem" onclick={() => { appearance.setPageStyle(styleKind, style === 'big' ? 'simple' : 'big'); menuOpen = false; }}>
            {#if style === 'big'}<Rows3 size={18} /> Simple list view{:else}<SquareStack size={18} /> Big header view{/if}
          </button>
        </div>
      {/if}
    </div>
  </div>
</header>

<style>
  .ph {
    --gutter: clamp(16px, 3vw, 32px);
    padding: 10px var(--gutter) 8px;
  }
  .big {
    background: linear-gradient(180deg, var(--c) 0%, color-mix(in srgb, var(--c) 40%, #000) 70%, #000 100%);
    transition: background 0.6s;
  }
  .round {
    width: 34px;
    height: 34px;
    border-radius: 50%;
    background: rgba(0, 0, 0, 0.55);
    display: grid;
    place-items: center;
  }
  .main {
    display: flex;
    gap: 22px;
    align-items: flex-end;
    margin-top: 8px;
  }
  .big .art {
    width: clamp(150px, 22vw, 232px);
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.55);
    flex: none;
  }
  .simple .art {
    width: 72px;
    flex: none;
  }
  .simple .main {
    align-items: center;
  }
  .txt {
    min-width: 0;
  }
  .label {
    font-size: 0.85rem;
    font-weight: 600;
  }
  h1 {
    margin: 4px 0 8px;
    font-size: clamp(1.6rem, 5vw, 4.2rem);
    font-weight: 900;
    line-height: 1.02;
    letter-spacing: -0.02em;
    overflow-wrap: anywhere;
  }
  .simple h1 {
    font-size: 1.5rem;
    margin: 2px 0;
  }
  .meta {
    color: var(--fg-2);
    font-size: 0.92rem;
  }
  @media (max-width: 599px) {
    .big .main {
      flex-direction: column;
      align-items: center;
      text-align: center;
    }
    .big .art {
      width: min(62vw, 260px);
    }
  }
  .actions {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 18px;
  }
  .playbig {
    width: 56px;
    height: 56px;
    border-radius: 50%;
    background: var(--accent);
    color: #000;
    display: grid;
    place-items: center;
  }
  .playbig:hover {
    transform: scale(1.04);
  }
  .rel {
    position: relative;
  }
  .menu {
    position: absolute;
    left: 0;
    top: 46px;
    z-index: 6;
    background: var(--bg-2);
    border-radius: 10px;
    padding: 6px 0;
    min-width: 220px;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.6);
  }
  .menu :global(button) {
    display: flex;
    gap: 12px;
    align-items: center;
    width: 100%;
    padding: 10px 16px;
    text-align: left;
  }
  .menu :global(button:hover) {
    background: rgba(255, 255, 255, 0.06);
  }
  /* Expressive motion: parallax header art (scroll-driven, where supported). */
  @supports (animation-timeline: scroll()) {
    :global([data-motion='expressive']) .big .art {
      animation: parallax linear both;
      animation-timeline: scroll(nearest);
      animation-range: 0 400px;
    }
    @keyframes parallax {
      to {
        transform: translateY(80px) scale(0.85);
        opacity: 0.4;
      }
    }
  }
</style>
