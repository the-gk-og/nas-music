// Desktop (Tauri) bridge. The Rust side owns the tray, global media keys, notifications and the optional
// Discord status; it sends `nm-control` events here, and we tell it about track changes. No-op in a browser.
import { DISCORD_DEFAULTS, changed, presenceFor, validAppId, type DiscordPrefs, type NowPlaying } from './discord';
import { isTauri, loadJson, saveJson } from './platform';
import { player } from './player.svelte';

type Invoke = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;

class Desktop {
  /** Discord "Listening to" status; kept on this computer only. */
  discord = $state<DiscordPrefs>(loadJson('nm.discord', DISCORD_DEFAULTS));
  private invoke: Invoke | null = null;

  setDiscord(patch: Partial<DiscordPrefs>) {
    this.discord = { ...this.discord, ...patch, appId: (patch.appId ?? this.discord.appId).trim() };
    saveJson('nm.discord', this.discord);
  }

  get discordReady(): boolean {
    return this.discord.on && validAppId(this.discord.appId);
  }

  async init() {
    if (!isTauri()) return;
    const [{ listen }, { invoke }] = await Promise.all([import('@tauri-apps/api/event'), import('@tauri-apps/api/core')]);
    this.invoke = invoke;
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
    this.watchDiscord();
  }

  private watchDiscord() {
    let lastId: string | null | undefined;
    let last: NowPlaying | null | undefined;
    $effect.root(() => {
      $effect(() => {
        const id = this.discordReady ? this.discord.appId : null;
        if (id === lastId) return;
        lastId = id;
        last = undefined; // resend what's playing to the new (or no) connection
        void this.invoke?.('discord_configure', { appId: id }).catch(() => {});
      });
      $effect(() => {
        if (!this.discordReady) return;
        void this.discord.appId; // re-run after a reconfigure
        const s = player.shown;
        const next = presenceFor(
          { track: s.track, playing: s.playing, position: s.position, duration: s.duration, remote: player.isRemote, speed: player.speed },
          Date.now(),
        );
        if (!changed(last, next)) return;
        last = next;
        void this.invoke?.('discord_update', { now: next }).catch(() => {});
      });
    });
  }
}

export const desktop = new Desktop();

export function desktopInit() {
  return desktop.init();
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
