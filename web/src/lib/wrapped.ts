// Wrapped story cards: a pure card model (tested) and a canvas renderer used both for showing the cards
// and for exporting them as PNG, so the saved image is exactly what you saw.
import type { AudioAura, Wrapped, WrappedBase } from './types';

export const WEEKDAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

export function hourLabel(h: number): string {
  if (h === 0) return 'midnight';
  if (h === 12) return 'noon';
  return h < 12 ? `${h} am` : `${h - 12} pm`;
}

export function fmtMinutes(m: number): string {
  return Math.round(m).toLocaleString('en-GB');
}

export interface Row {
  title: string;
  sub?: string;
  value?: string;
  /** album id for a cover, if any */
  albumId?: string | null;
}

export type Card =
  | { kind: 'intro'; title: string; big: string; caption: string }
  | { kind: 'list'; title: string; rows: Row[]; heroAlbumId?: string | null }
  | { kind: 'genres'; title: string; genres: { name: string; share: number }[] }
  | { kind: 'clock'; title: string; byHour: number[]; caption: string }
  | { kind: 'stat'; title: string; big: string; caption: string; sub?: string }
  | { kind: 'aura'; title: string; aura: AudioAura }
  | { kind: 'summary'; title: string; cells: { label: string; value: string }[] };

/** The story, in order. Cards without data are left out. */
export function wrappedCards(w: Wrapped | WrappedBase, who = 'Your'): Card[] {
  const name = (w as Wrapped).final === false ? `${who} ${w.year} so far` : `${who} ${w.year} Wrapped`;
  const cards: Card[] = [];
  if (!w.totals.plays) return [{ kind: 'intro', title: name, big: '0', caption: 'No plays yet — press play and come back.' }];
  cards.push({ kind: 'intro', title: name, big: fmtMinutes(w.totals.minutes), caption: 'minutes of music' });
  if (w.topTracks.length)
    cards.push({
      kind: 'list',
      title: 'Top songs',
      heroAlbumId: w.topTracks[0].track.albumId,
      rows: w.topTracks.slice(0, 5).map((t) => ({ title: t.track.title, sub: t.track.artist, value: `${t.plays} plays`, albumId: t.track.albumId })),
    });
  if (w.topArtists.length)
    cards.push({
      kind: 'list',
      title: 'Top artists',
      heroAlbumId: artAlbum(w.topArtists[0].artUrl),
      rows: w.topArtists.slice(0, 5).map((a) => ({ title: a.artist.name, value: `${fmtMinutes(a.minutes)} min`, albumId: artAlbum(a.artUrl) })),
    });
  if (w.topAlbums.length)
    cards.push({
      kind: 'list',
      title: 'Top albums',
      heroAlbumId: w.topAlbums[0].album.id,
      rows: w.topAlbums.slice(0, 5).map((a) => ({ title: a.album.title, sub: a.album.artist, value: `${fmtMinutes(a.minutes)} min`, albumId: a.album.id })),
    });
  if (w.topGenres.length) cards.push({ kind: 'genres', title: 'Your genres', genres: w.topGenres.slice(0, 5).map((g) => ({ name: g.name, share: g.share })) });
  if (w.peakHour != null)
    cards.push({
      kind: 'clock',
      title: 'When you listen',
      byHour: w.byHour,
      caption: `Most of all around ${hourLabel(w.peakHour)}${w.peakWeekday != null ? `, on ${WEEKDAYS[w.peakWeekday]}s` : ''}`,
    });
  const ww = w as Wrapped;
  if (ww.streaks && ww.streaks.longest > 1)
    cards.push({
      kind: 'stat',
      title: 'Longest streak',
      big: `${ww.streaks.longest} days`,
      caption: 'in a row with music',
      sub: ww.streaks.current > 1 ? `Current streak: ${ww.streaks.current} days` : undefined,
    });
  if (ww.discoveries && ww.discoveries.artists > 0)
    cards.push({
      kind: 'stat',
      title: 'New discoveries',
      big: `${ww.discoveries.artists}`,
      caption: ww.discoveries.artists === 1 ? 'artist you played for the first time' : 'artists you played for the first time',
      sub: ww.discoveries.topArtists[0] ? `Favourite find: ${ww.discoveries.topArtists[0].artist.name}` : undefined,
    });
  if (w.aura) cards.push({ kind: 'aura', title: 'Your audio aura', aura: w.aura });
  cards.push({
    kind: 'summary',
    title: name,
    cells: [
      { label: 'Minutes', value: fmtMinutes(w.totals.minutes) },
      { label: 'Plays', value: fmtMinutes(w.totals.plays) },
      { label: 'Songs', value: fmtMinutes(w.totals.tracks) },
      { label: 'Artists', value: fmtMinutes(w.totals.artists) },
      { label: 'Top artist', value: w.topArtists[0]?.artist.name ?? '—' },
      { label: 'Aura', value: w.aura?.label ?? '—' },
    ],
  });
  return cards;
}

function artAlbum(rel?: string | null): string | null {
  const m = /^art\/([^/?]+)/.exec(rel ?? '');
  return m ? decodeURIComponent(m[1]) : null;
}

/** Card background colours: the aura's gradient, else the accent. */
export function cardColors(w: WrappedBase, i: number, accent = '#4ade80'): [string, string] {
  const base = w.aura?.colors?.length === 2 ? (w.aura.colors as [string, string]) : [accent, '#6d28d9'];
  return i % 2 ? [base[1], base[0]] : [base[0], base[1]];
}

// ---------------------------------------------------------------- canvas

export const CARD_W = 1080;
export const CARD_H = 1920;

type Images = Record<string, HTMLImageElement | undefined>;

function fit(ctx: CanvasRenderingContext2D, text: string, max: number): string {
  if (ctx.measureText(text).width <= max) return text;
  let t = text;
  while (t.length > 1 && ctx.measureText(t + '…').width > max) t = t.slice(0, -1);
  return t + '…';
}

export function drawCard(ctx: CanvasRenderingContext2D, card: Card, colors: [string, string], images: Images = {}, footer = 'nas-music') {
  const W = CARD_W;
  const H = CARD_H;
  const g = ctx.createLinearGradient(0, 0, W, H);
  g.addColorStop(0, colors[0]);
  g.addColorStop(1, colors[1]);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = 'rgba(0,0,0,0.35)';
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = '#fff';
  ctx.textBaseline = 'alphabetic';
  const font = (px: number, w = 700) => `${w} ${px}px system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif`;
  ctx.font = font(64);
  ctx.fillText(fit(ctx, card.title, W - 160), 80, 200);
  const img = (id?: string | null) => (id ? images[id] : undefined);

  switch (card.kind) {
    case 'intro':
    case 'stat': {
      ctx.font = font(card.big.length > 6 ? 180 : 260, 800);
      ctx.fillText(fit(ctx, card.big, W - 160), 80, 900);
      ctx.font = font(56, 500);
      ctx.fillText(fit(ctx, card.caption, W - 160), 80, 1000);
      if (card.kind === 'stat' && card.sub) {
        ctx.font = font(48, 500);
        ctx.globalAlpha = 0.8;
        ctx.fillText(fit(ctx, card.sub, W - 160), 80, 1100);
        ctx.globalAlpha = 1;
      }
      break;
    }
    case 'list': {
      const hero = img(card.heroAlbumId);
      if (hero) ctx.drawImage(hero, 80, 280, 560, 560);
      let y = hero ? 960 : 360;
      card.rows.forEach((r, i) => {
        ctx.font = font(56, 800);
        ctx.fillText(`${i + 1}`, 80, y + 50);
        const th = img(r.albumId);
        const x = th ? 300 : 170;
        if (th) ctx.drawImage(th, 160, y - 20, 110, 110);
        ctx.font = font(48, 700);
        ctx.fillText(fit(ctx, r.title, W - x - 260), x, y + 30);
        ctx.font = font(36, 500);
        ctx.globalAlpha = 0.75;
        if (r.sub) ctx.fillText(fit(ctx, r.sub, W - x - 260), x, y + 80);
        if (r.value) {
          ctx.textAlign = 'right';
          ctx.fillText(r.value, W - 80, y + 30);
          ctx.textAlign = 'left';
        }
        ctx.globalAlpha = 1;
        y += 170;
      });
      break;
    }
    case 'genres': {
      let y = 420;
      const max = Math.max(...card.genres.map((x) => x.share), 0.01);
      for (const gnr of card.genres) {
        ctx.font = font(52, 700);
        ctx.fillText(fit(ctx, gnr.name, W - 360), 80, y);
        ctx.textAlign = 'right';
        ctx.fillText(`${Math.round(gnr.share * 100)}%`, W - 80, y);
        ctx.textAlign = 'left';
        ctx.fillStyle = 'rgba(255,255,255,0.25)';
        ctx.fillRect(80, y + 30, W - 160, 28);
        ctx.fillStyle = '#fff';
        ctx.fillRect(80, y + 30, ((W - 160) * gnr.share) / max, 28);
        y += 220;
      }
      break;
    }
    case 'clock': {
      const max = Math.max(...card.byHour, 0.01);
      const bw = (W - 160) / 24;
      const base = 1300;
      card.byHour.forEach((v, h) => {
        const bh = (v / max) * 700;
        ctx.globalAlpha = v === max ? 1 : 0.6;
        ctx.fillRect(80 + h * bw + 4, base - bh, bw - 8, bh);
      });
      ctx.globalAlpha = 0.75;
      ctx.font = font(32, 500);
      for (const h of [0, 6, 12, 18]) ctx.fillText(hourLabel(h), 80 + h * bw, base + 50);
      ctx.globalAlpha = 1;
      ctx.font = font(52, 600);
      wrap(ctx, card.caption, 80, 1500, W - 160, 70);
      break;
    }
    case 'aura': {
      const a = card.aura;
      const rg = ctx.createRadialGradient(W / 2, 760, 40, W / 2, 760, 420);
      rg.addColorStop(0, a.colors[0] ?? '#fff');
      rg.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = rg;
      ctx.beginPath();
      ctx.arc(W / 2, 760, 420, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.textAlign = 'center';
      ctx.font = font(150, 800);
      ctx.fillText(a.label, W / 2, 1350);
      ctx.font = font(48, 500);
      wrap(ctx, a.description, W / 2, 1450, W - 200, 64);
      ctx.font = font(40, 600);
      const moods = a.moods.slice(0, 3).map((m) => m.name).join(' · ');
      if (moods) ctx.fillText(moods, W / 2, 1650);
      ctx.textAlign = 'left';
      break;
    }
    case 'summary': {
      card.cells.forEach((c, i) => {
        const x = 80 + (i % 2) * 470;
        const y = 420 + Math.floor(i / 2) * 330;
        ctx.globalAlpha = 0.75;
        ctx.font = font(40, 500);
        ctx.fillText(c.label, x, y);
        ctx.globalAlpha = 1;
        ctx.font = font(c.value.length > 9 ? 56 : 90, 800);
        ctx.fillText(fit(ctx, c.value, 420), x, y + 100);
      });
      break;
    }
  }
  ctx.globalAlpha = 0.7;
  ctx.font = font(36, 600);
  ctx.fillText(footer, 80, H - 80);
  ctx.globalAlpha = 1;
}

function wrap(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, max: number, lh: number) {
  let line = '';
  for (const word of text.split(' ')) {
    const t = line ? `${line} ${word}` : word;
    if (ctx.measureText(t).width > max && line) {
      ctx.fillText(line, x, y);
      y += lh;
      line = word;
    } else line = t;
  }
  if (line) ctx.fillText(line, x, y);
}

/** Album ids whose covers the cards need. */
export function cardAlbumIds(cards: Card[]): string[] {
  const ids = new Set<string>();
  for (const c of cards) {
    if (c.kind !== 'list') continue;
    if (c.heroAlbumId) ids.add(c.heroAlbumId);
    for (const r of c.rows) if (r.albumId) ids.add(r.albumId);
  }
  return [...ids];
}

// ---------------------------------------------------------------- blend helpers

/** "Because Alex plays…" line for a blend track. */
export function blendReason(reason: string, users: string[], names: Record<string, string>, me?: string): string {
  const n = users.map((u) => (u === me ? 'you' : (names[u] ?? 'someone')));
  if (!n.length) return '';
  if (reason === 'shared') return n.length === 2 ? `${cap(n[0])} and ${n[1]} both love this` : `${n.length} of you love this`;
  const who = n[0];
  if (who === 'you') return reason === 'top' ? 'Because you play it a lot' : 'From your taste';
  return reason === 'top' ? `Because ${who} plays it a lot` : `Because ${who} plays this`;
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/** "2026-11-13" -> "13 Nov"; "2026" stays. */
export function releaseDate(d?: string | null): string {
  if (!d) return '';
  if (d.length < 10) return d;
  const dt = new Date(d + 'T00:00:00Z');
  return dt.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' });
}
