<script lang="ts">
  // Spectrum bars behind full-screen Now Playing. Reads the player's analyser tap; draws only while the
  // page is visible and something is playing here (not while controlling another device or casting).
  import { onMount } from 'svelte';
  import { player } from '../lib/player.svelte';
  import { barLevels, bandEdges } from '../lib/visualiser';

  let canvas: HTMLCanvasElement;
  const playing = $derived(player.shown.playing && !player.isRemote);
  let kick = () => {};
  $effect(() => {
    void playing;
    kick();
  });

  onMount(() => {
    const g = canvas.getContext('2d');
    if (!g) return;
    let raf = 0;
    let levels: number[] = [];
    let edges: number[] = [];
    let data: Uint8Array<ArrayBuffer> | null = null;
    let color = '#4ade80';
    let colorAt = 0;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.round(canvas.clientWidth * dpr);
      canvas.height = Math.round(canvas.clientHeight * dpr);
      edges = [];
    };
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);
    resize();

    const frame = (now: number) => {
      raf = 0;
      const a = player.analyser();
      const w = canvas.width;
      const h = canvas.height;
      g.clearRect(0, 0, w, h);
      if (!a || document.hidden) return;
      const bars = Math.max(16, Math.min(96, Math.round(w / (14 * Math.min(window.devicePixelRatio || 1, 2)))));
      if (edges.length !== bars + 1 || !data || data.length !== a.frequencyBinCount) {
        data = new Uint8Array(a.frequencyBinCount);
        edges = bandEdges(bars, a.frequencyBinCount, a.context.sampleRate);
        levels = [];
      }
      if (now - colorAt > 1000) {
        color = getComputedStyle(canvas).getPropertyValue('--accent').trim() || color;
        colorAt = now;
      }
      if (playing) a.getByteFrequencyData(data);
      else data.fill(0);
      levels = barLevels(data, edges, levels);
      const slot = w / bars;
      const bw = Math.max(1, slot * 0.62);
      const grad = g.createLinearGradient(0, h, 0, 0);
      grad.addColorStop(0, color);
      grad.addColorStop(1, 'transparent');
      g.fillStyle = grad;
      for (let i = 0; i < bars; i++) {
        const bh = Math.max(levels[i] * levels[i] * h, levels[i] > 0.01 ? 2 : 0); // squared: quiet bands stay low
        if (bh <= 0) continue;
        g.beginPath();
        g.roundRect(i * slot + (slot - bw) / 2, h - bh, bw, bh, [bw / 2, bw / 2, 0, 0]);
        g.fill();
      }
      // Keep going while playing, or until the bars have fallen after a pause.
      if (playing || levels.some((v) => v > 0)) raf = requestAnimationFrame(frame);
    };
    kick = () => {
      if (!raf && !document.hidden) raf = requestAnimationFrame(frame);
    };
    document.addEventListener('visibilitychange', kick);
    kick();
    return () => {
      if (raf) cancelAnimationFrame(raf);
      kick = () => {};
      ro.disconnect();
      document.removeEventListener('visibilitychange', kick);
    };
  });
</script>

<canvas bind:this={canvas} class="viz" aria-hidden="true"></canvas>

<style>
  .viz {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 0;
    width: 100%;
    height: 40%;
    z-index: -1;
    opacity: 0.5;
    pointer-events: none;
  }
</style>
