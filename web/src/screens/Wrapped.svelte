<script lang="ts">
  // Wrapped: story-style cards drawn on a canvas (the same drawing is saved as PNG), year picker, and a
  // server-wide view for admins.
  import { untrack } from 'svelte';
  import { ArrowLeft, ChevronLeft, ChevronRight, Download, Share2 } from '@lucide/svelte';
  import { appearance } from '../lib/appearance.svelte';
  import { session } from '../lib/session.svelte';
  import type { ServerWrapped, Wrapped } from '../lib/types';
  import { ui } from '../lib/ui.svelte';
  import { accentColor } from '../lib/v3';
  import { CARD_H, CARD_W, cardAlbumIds, cardColors, drawCard, wrappedCards } from '../lib/wrapped';

  let { year: yearParam = '' }: { year?: string } = $props();
  let year = $state<number>(untrack(() => Number(yearParam)) || new Date().getFullYear());
  let years = $state<number[]>([]);
  let scope = $state<'me' | 'server'>('me');
  let data = $state<Wrapped | ServerWrapped | null>(null);
  let error = $state<string | null>(null);
  let index = $state(0);
  let canvas: HTMLCanvasElement | undefined = $state();
  let images = $state<Record<string, HTMLImageElement>>({});

  $effect(() => {
    session.api.wrappedYears().then((y) => (years = y)).catch(() => (years = [year]));
  });
  $effect(() => {
    const y = year;
    const s = scope;
    data = null;
    error = null;
    index = 0;
    (s === 'server' ? session.api.adminWrapped(y) : session.api.wrapped(y))
      .then((d) => (data = d))
      .catch((e) => (error = e.status === 404 ? 'This server has no Wrapped yet.' : e.message));
  });

  const cards = $derived(data ? wrappedCards(data, scope === 'server' ? 'The server’s' : 'Your') : []);
  const card = $derived(cards[Math.min(index, cards.length - 1)]);

  $effect(() => {
    for (const id of cardAlbumIds(cards)) {
      if (images[id]) continue;
      session.api
        .artSrc(id, 256)
        .then((src) => {
          const im = new Image();
          im.onload = () => (images = { ...images, [id]: im });
          im.src = src;
        })
        .catch(() => {});
    }
  });

  $effect(() => {
    if (!canvas || !card || !data) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    drawCard(ctx, card, cardColors(data, index, accentColor(appearance.prefs.accent)), images, `Homebeat · ${data.year}`);
  });

  const go = (d: number) => (index = Math.max(0, Math.min(cards.length - 1, index + d)));

  function blob(): Promise<Blob | null> {
    return new Promise((res) => (canvas ? canvas.toBlob(res, 'image/png') : res(null)));
  }
  const fileName = () => `wrapped-${data?.year}-${index + 1}.png`;
  async function save() {
    const b = await blob();
    if (!b) return;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(b);
    a.download = fileName();
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }
  async function share() {
    const b = await blob();
    if (!b) return;
    const file = new File([b], fileName(), { type: 'image/png' });
    if (navigator.canShare?.({ files: [file] })) await navigator.share({ files: [file], title: 'My Wrapped' }).catch(() => {});
    else await save();
  }
  function onKey(e: KeyboardEvent) {
    if (e.key === 'ArrowRight') go(1);
    if (e.key === 'ArrowLeft') go(-1);
  }
  const canShare = typeof navigator !== 'undefined' && 'canShare' in navigator;
</script>

<svelte:window onkeydown={onKey} />

<div class="page wrapped">
  <div class="topbar">
    <button class="icon-btn" aria-label="Back" onclick={() => ui.back()}><ArrowLeft size={24} /></button>
    <h1>Wrapped</h1>
  </div>
  <div class="chips">
    {#each years as y (y)}<button class="chip" class:on={y === year} onclick={() => (year = y)}>{y}</button>{/each}
    {#if session.isAdmin}
      <span class="sep"></span>
      <button class="chip" class:on={scope === 'me'} onclick={() => (scope = 'me')}>Me</button>
      <button class="chip" class:on={scope === 'server'} onclick={() => (scope = 'server')}>Whole server</button>
    {/if}
  </div>

  {#if error}<div class="empty">{error}</div>
  {:else if !data}<div class="empty">Loading…</div>
  {:else}
    <div class="stage">
      <div class="bars">{#each cards as _, i}<span class:on={i <= index}></span>{/each}</div>
      <div class="frame">
        <canvas bind:this={canvas} width={CARD_W} height={CARD_H} aria-label={card?.title}></canvas>
        <button class="zone l" aria-label="Previous card" onclick={() => go(-1)}></button>
        <button class="zone r" aria-label="Next card" onclick={() => go(1)}></button>
      </div>
      <div class="controls">
        <button class="icon-btn" aria-label="Previous" disabled={index === 0} onclick={() => go(-1)}><ChevronLeft size={24} /></button>
        <span class="muted">{index + 1} / {cards.length}</span>
        <button class="icon-btn" aria-label="Next" disabled={index >= cards.length - 1} onclick={() => go(1)}><ChevronRight size={24} /></button>
        <button class="btn" onclick={save}><Download size={18} /> Save image</button>
        {#if canShare}<button class="btn" onclick={share}><Share2 size={18} /> Share</button>{/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 0 16px 12px;
  }
  .sep {
    width: 12px;
  }
  .stage {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    padding: 0 16px 24px;
  }
  .bars {
    display: flex;
    gap: 4px;
    width: min(420px, 100%);
  }
  .bars span {
    flex: 1;
    height: 3px;
    border-radius: 2px;
    background: rgba(255, 255, 255, 0.25);
  }
  .bars span.on {
    background: #fff;
  }
  .frame {
    position: relative;
    width: min(420px, 100%, calc((100vh - 260px) * 9 / 16));
    aspect-ratio: 9 / 16;
  }
  canvas {
    width: 100%;
    height: 100%;
    border-radius: 16px;
    display: block;
  }
  .zone {
    position: absolute;
    top: 0;
    bottom: 0;
    width: 35%;
  }
  .zone.l {
    left: 0;
  }
  .zone.r {
    right: 0;
  }
  .controls {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    justify-content: center;
  }
  .btn {
    display: inline-flex;
    gap: 6px;
    align-items: center;
  }
</style>
