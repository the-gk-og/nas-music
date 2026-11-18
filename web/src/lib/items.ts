// What tapping (open) and the play button (play) do for every kind of card.
import { library } from './library.svelte';
import { player } from './player.svelte';
import { session } from './session.svelte';
import type { ShelfItem, Track } from './types';
import { ui } from './ui.svelte';
import { parseRadioId } from './v3';

export function openItem(it: ShelfItem, siblings?: ShelfItem[]) {
  switch (it.type) {
    case 'album':
    case 'artist':
    case 'playlist':
    case 'mix':
      return ui.go(it.type, it.id);
    case 'list':
      return ui.go('list', it.id);
    case 'radio':
      return void player.startRadio(parseRadioId(it.id), it.title);
    case 'queue':
      ui.go('queue');
      return void player.playQueue(it.id);
    case 'track': {
      const tracks = (siblings ?? [it]).map((x) => x.track).filter((t): t is Track => !!t);
      const i = Math.max(0, tracks.findIndex((t) => t.id === it.id));
      return void player.playList(tracks.length ? tracks : [it.track!], i, it.subtitle ?? 'Queue', { source: 'mix', addedFrom: 'shelf' });
    }
    case 'catalog':
      if (it.catalog?.owned && it.catalog.ownedId) return ui.go(it.catalog.type === 'track' ? 'album' : it.catalog.type, it.catalog.ownedId);
      if (it.catalog) ui.open({ kind: 'catalog', item: it.catalog });
  }
}

/** The ▶ button on a card: play without opening. */
export async function playItem(it: ShelfItem) {
  try {
    switch (it.type) {
      case 'album': {
        const a = await session.api.album(it.id);
        return player.playList(a.tracks, 0, `Album: ${a.title}`, { source: 'library', addedFrom: 'album' });
      }
      case 'playlist': {
        const p = await session.api.playlist(it.id);
        return player.playList(p.tracks ?? [], 0, `Playlist: ${p.name}`, { source: 'library', addedFrom: 'playlist' });
      }
      case 'mix': {
        const m = await session.api.mix(it.id);
        return player.playList(m.tracks.map((x) => x.track), 0, m.title, { source: m.kind === 'more_like' ? 'more_like' : 'mix', mixId: m.id, addedFrom: 'mix' });
      }
      case 'artist':
        return player.startRadio({ type: 'artist', id: it.id }, `${it.title} radio`);
      default:
        return openItem(it);
    }
  } catch (e) {
    ui.toast((e as Error).message);
  }
}

export function canPin(it: ShelfItem): it is ShelfItem & { type: 'album' | 'artist' | 'playlist' | 'mix' } {
  return it.type === 'album' || it.type === 'artist' || it.type === 'playlist' || it.type === 'mix';
}

export function togglePin(it: ShelfItem) {
  if (canPin(it)) void library.togglePin(it.type, it.id, it.title);
}

export function radioFor(it: ShelfItem) {
  if (it.type === 'album' || it.type === 'artist' || it.type === 'playlist' || it.type === 'track')
    void player.startRadio({ type: it.type, id: it.id }, `${it.title} radio`);
}
