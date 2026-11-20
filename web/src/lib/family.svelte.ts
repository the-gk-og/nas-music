// Family mode (schema v6). The server does the filtering; this store mirrors the user's policy so the UI can
// show the toggle, the lock and reload views when it changes.
import { ApiError } from './api/client';
import { library } from './library.svelte';
import { session } from './session.svelte';
import type { FamilyMode } from './types';
import { ui } from './ui.svelte';

const OFF: FamilyMode = { enabled: false, hideProbable: true, hideUnknown: false, locked: false, hasPin: false };

class FamilyStore {
  mode = $state<FamilyMode>({ ...OFF });
  loaded = $state(false);

  async load() {
    try {
      this.mode = await session.api.family();
      this.loaded = true;
    } catch {
      /* older server or offline: family mode unavailable */
    }
  }

  /** Pushed over Connect (another device or an admin changed it). */
  changed(f: FamilyMode) {
    const before = JSON.stringify(this.mode);
    this.mode = f;
    this.loaded = true;
    if (before !== JSON.stringify(f)) library.tagsRev++; // open views reload with the new filter
  }

  /** Loosening a locked policy asks for the PIN. Returns true when saved. */
  async set(changes: Partial<Pick<FamilyMode, 'enabled' | 'hideProbable' | 'hideUnknown'>>): Promise<boolean> {
    const m = this.mode;
    const loosens =
      (m.enabled && changes.enabled === false) ||
      (m.hideProbable && changes.hideProbable === false) ||
      (m.hideUnknown && changes.hideUnknown === false);
    let pin: string | undefined;
    if (m.locked && loosens) {
      if (!m.hasPin) {
        ui.toast('Family mode is locked by an admin');
        return false;
      }
      const v = await ui.prompt('Family mode is locked', 'Unlock PIN', '', 'Unlock');
      if (!v) return false;
      pin = v;
    }
    try {
      this.changed(await session.api.putFamily({ ...changes, ...(pin ? { pin } : {}) }));
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) ui.toast('Too many wrong PINs — try again in a minute');
      else if (e instanceof ApiError && e.status === 403) ui.toast(pin ? 'Wrong PIN' : 'Family mode is locked by an admin');
      else ui.toast((e as Error).message);
      return false;
    }
  }

  toggle() {
    return this.set({ enabled: !this.mode.enabled });
  }
}

export const family = new FamilyStore();
