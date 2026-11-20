// v7 "new music added" notifications: the bell list, Connect pushes, catch-up after reconnect, optional
// browser Notifications (per device, opt-in; shown only while the tab is in the background).
import { mergeNotifications, newestAt } from './notify';
import { session } from './session.svelte';
import type { AppNotification } from './types';
import { ui } from './ui.svelte';

const BROWSER_KEY = 'nm.notify.browser';

function storage(): Storage | null {
  try {
    return typeof localStorage !== 'undefined' ? localStorage : null;
  } catch {
    return null;
  }
}

class NotificationStore {
  items = $state<AppNotification[]>([]);
  unread = $state(0);
  loaded = $state(false);
  browser = $state(storage()?.getItem(BROWSER_KEY) === '1');

  async load() {
    try {
      const r = await session.api.notifications();
      this.items = mergeNotifications([], r.items);
      this.unread = r.unread;
      this.loaded = true;
    } catch {
      /* older server or offline */
    }
  }

  /** After a (re)connect: fetch what arrived while we were away, and pop up the newest one. */
  async catchUp() {
    if (!this.loaded) return this.load();
    try {
      const r = await session.api.notifications(newestAt(this.items));
      if (r.items.length) {
        this.items = mergeNotifications(this.items, r.items);
        this.announce(r.items[0]);
      }
      this.unread = r.unread;
    } catch {
      /* ignore */
    }
  }

  /** Connect handler: null = reconnected. */
  onPush(n: AppNotification | null) {
    if (!n) {
      void this.catchUp();
      return;
    }
    if (this.items.some((x) => x.id === n.id)) return;
    this.items = mergeNotifications(this.items, [n]);
    if (!n.read) this.unread++;
    this.announce(n);
  }

  private announce(n: AppNotification) {
    ui.toast(n.body ? `${n.title}: ${n.body}` : n.title);
    if (this.browser && typeof Notification !== 'undefined' && Notification.permission === 'granted' && document.visibilityState === 'hidden') {
      try {
        const sys = new Notification(n.title, { body: n.body, tag: n.id });
        sys.onclick = () => {
          window.focus();
          ui.go('new');
        };
      } catch {
        /* some platforms only allow service-worker notifications */
      }
    }
  }

  async markAllRead() {
    if (!this.unread) return;
    try {
      const r = await session.api.readNotifications();
      this.unread = r.unread;
      this.items = this.items.map((n) => ({ ...n, read: true }));
    } catch (e) {
      ui.toast((e as Error).message);
    }
  }

  /** Per device: ask the browser for permission, remember the choice. */
  async setBrowser(on: boolean) {
    if (on && typeof Notification !== 'undefined' && Notification.permission !== 'granted') {
      const p = await Notification.requestPermission();
      if (p !== 'granted') {
        ui.toast('The browser did not allow notifications');
        on = false;
      }
    }
    if (on && typeof Notification === 'undefined') {
      ui.toast('This browser has no notifications');
      on = false;
    }
    this.browser = on;
    storage()?.setItem(BROWSER_KEY, on ? '1' : '0');
  }
}

export const notifications = new NotificationStore();
