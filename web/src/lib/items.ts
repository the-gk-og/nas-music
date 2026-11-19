// What tapping (open) and the play button (play) do for every kind of card (ShelfItem.type).
import { isPinType, library } from './library.svelte';
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
    case 'genre':
      return ui.go(it.type, it.id);
    case 'smart_list':
      return ui.go('list', it.id);
    case 'folder':
      return ui.go('folders', it.id);
    case 'radio':
      return void player.startRadio(parseRadioId(it.id, it.seed), it.title);
    case 'queue':
      ui.go('queue');
      return void player.playQueue(it.progress?.queueId ?? it.id);
    case 'device':
      // "Your devices": one tap = take over here.
      if (player.myDeviceId) player.transfer(player.myDeviceId);
      return;
    case 'track': {
      const tracks = (siblings ?? [it]).map((x) => x.track).filter((t): t is Track => !!t);
      const i = Math.max(0, tracks.findIndex((t) => t.id === it.id));
      return void player.playList(tracks.length ? tracks : [it.track!], i, it.subtitle ?? 'Queue', { source: 'mix', addedFrom: 'shelf' });
    }
    case 'catalog':
      if (it.catalog?.owned && it.catalog.ownedId) return ui.go(it.catalog.type === 'track' ? 'album' : it.catalog.type, it.catalog.ownedId);
      if (it.catalog) ui.open({ kind: 'catalog', item: it.catalog });
      return;
    case 'toget':
      return ui.go('toget');
    case 'suggestion':
      return ui.go('foryou');
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
      case 'genre': {
        const t = await session.api.genreTracks(it.id);
        return player.playList(t, 0, it.title, { source: 'library', addedFrom: 'genre' });
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

export const canPin = (it: ShelfItem) => isPinType(it.type);

export function togglePin(it: ShelfItem) {
  if (isPinType(it.type)) void library.togglePin(it.type, it.id, it.title);
}

export function radioFor(it: ShelfItem) {
  if (it.type === 'album' || it.type === 'artist' || it.type === 'playlist' || it.type === 'track')
    void player.startRadio({ type: it.type, id: it.id }, `${it.title} radio`);
}

export const isPlayable = (it: ShelfItem) => ['album', 'playlist', 'mix', 'genre', 'artist', 'radio', 'queue', 'track'].includes(it.type);
