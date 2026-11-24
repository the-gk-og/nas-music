// Desktop (Tauri) bridge. The Rust side owns the tray, global media keys and notifications; it sends
// `nm-control` events here, and we tell it about track changes. No-op in a normal browser.
import { isTauri } from './platform';
import { player } from './player.svelte';

export async function desktopInit() {
  if (!isTauri()) return;
  const [{ listen }, { invoke }] = await Promise.all([import('@tauri-apps/api/event'), import('@tauri-apps/api/core')]);
  await listen<string>('nm-control', (e) => {
    switch (e.payload) {
      case 'toggle':
        return player.toggle();
      case 'play':
        return void player.play();
      case 'pause':
        return player.pause();
      case 'next':
        return void player.next();
      case 'prev':
        return void player.prev();
    }
  });
  player.onTrackChange = (t) => {
    void invoke('track_changed', { title: t.title, artist: t.artist, album: t.album }).catch(() => {});
  };
}

/** Desktop app only: is "start with the computer (minimised to the tray)" on? null = not the desktop app. */
export async function getAutostart(): Promise<boolean | null> {
  if (!isTauri()) return null;
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<boolean>('autostart_get').catch(() => null);
}

export async function setAutostart(on: boolean): Promise<boolean> {
  const { invoke } = await import('@tauri-apps/api/core');
  return invoke<boolean>('autostart_set', { on });
}
