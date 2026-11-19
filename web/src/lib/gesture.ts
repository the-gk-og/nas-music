// Svelte action: swipe / tap / long-press on an element (cover, mini player). Mouse and touch.
import { classifySwipe, type Swipe } from './v3';

export interface GestureOpts {
  onswipe?: (dir: Exclude<Swipe, 'tap' | null>) => void;
  ontap?: () => void;
  onlongpress?: () => void;
  /** Follow the finger horizontally while dragging (visual feedback). */
  follow?: boolean;
}

export function gesture(node: HTMLElement, opts: GestureOpts) {
  let o = opts;
  let x0 = 0,
    y0 = 0,
    t0 = 0,
    id = -1,
    long: ReturnType<typeof setTimeout> | null = null,
    longFired = false;

  const down = (e: PointerEvent) => {
    if (e.button !== 0) return;
    id = e.pointerId;
    x0 = e.clientX;
    y0 = e.clientY;
    t0 = performance.now();
    longFired = false;
    if (o.onlongpress)
      long = setTimeout(() => {
        longFired = true;
        o.onlongpress?.();
      }, 550);
  };
  const move = (e: PointerEvent) => {
    if (e.pointerId !== id) return;
    const dx = e.clientX - x0;
    const dy = e.clientY - y0;
    if (Math.abs(dx) > 8 || Math.abs(dy) > 8) {
      if (long) clearTimeout(long);
      long = null;
    }
    if (o.follow && Math.abs(dx) > Math.abs(dy)) node.style.transform = `translateX(${dx * 0.6}px)`;
  };
  const up = (e: PointerEvent) => {
    if (e.pointerId !== id) return;
    id = -1;
    if (long) clearTimeout(long);
    long = null;
    node.style.transform = '';
    if (longFired) return;
    const s = classifySwipe(e.clientX - x0, e.clientY - y0, performance.now() - t0);
    if (s === 'tap') o.ontap?.();
    else if (s) o.onswipe?.(s);
  };
  const cancel = () => {
    id = -1;
    if (long) clearTimeout(long);
    node.style.transform = '';
  };
  const ctx = (e: MouseEvent) => {
    if (o.onlongpress) {
      e.preventDefault();
      o.onlongpress();
    }
  };
  node.addEventListener('pointerdown', down);
  node.addEventListener('pointermove', move);
  node.addEventListener('pointerup', up);
  node.addEventListener('pointercancel', cancel);
  node.addEventListener('contextmenu', ctx);
  node.style.touchAction = 'pan-y';
  return {
    update(n: GestureOpts) {
      o = n;
    },
    destroy() {
      node.removeEventListener('pointerdown', down);
      node.removeEventListener('pointermove', move);
      node.removeEventListener('pointerup', up);
      node.removeEventListener('pointercancel', cancel);
      node.removeEventListener('contextmenu', ctx);
    },
  };
}
