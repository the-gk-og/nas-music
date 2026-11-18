// Signed-in session: which server, how we authenticate, who we are.
import { ApiClient, ApiError, isSameOrigin, normaliseServerAddress, type AuthMode } from './api/client';
import { mockBackend } from './api/mock';
import { defaultDeviceName, deviceKind, isTauri, loadJson, saveJson, storage } from './platform';
import type { ServerInfo, Session } from './types';

const KEY = 'nm.session';

interface Saved {
  origin: string; // '' = same origin, 'mock' = in-browser mock
  mode: AuthMode;
  token: string | null;
  userId: string | null;
  username: string | null;
  isAdmin: boolean;
  deviceId: string | null;
  deviceName: string;
  lanUrl: string | null;
  tunnelUrl: string | null;
}

const empty = (): Saved => ({
  origin: '',
  mode: 'cookie',
  token: null,
  userId: null,
  username: null,
  isAdmin: false,
  deviceId: null,
  deviceName: defaultDeviceName(),
  lanUrl: null,
  tunnelUrl: null,
});

/** What the Connect screen pre-fills: the current origin when served by the server, nothing in the desktop app. */
export function defaultServerAddress(): string {
  if (isTauri()) return '';
  if (__MOCK_DEFAULT__) return 'mock';
  return typeof location !== 'undefined' ? location.origin : '';
}

export function makeClient(origin: string, mode: AuthMode, token: string | null): ApiClient {
  if (origin === 'mock') {
    const b = mockBackend();
    return new ApiClient({ origin: 'http://mock', mode: 'bearer', token, fetchImpl: b.fetch, media: b.media });
  }
  return new ApiClient({ origin, mode, token });
}

class SessionStore {
  saved = $state<Saved>(loadJson(KEY, empty()));
  api = $state<ApiClient>(null!);
  info = $state<ServerInfo | null>(null);

  constructor() {
    this.api = this.client();
  }

  private client() {
    const c = makeClient(this.saved.origin, this.saved.mode, this.saved.token);
    c.onUnauthorized = () => {
      if (this.signedIn) this.expire();
    };
    return c;
  }

  get signedIn(): boolean {
    return !!this.saved.userId;
  }
  get isMock(): boolean {
    return this.saved.origin === 'mock';
  }
  get isAdmin(): boolean {
    return this.saved.isAdmin;
  }

  /** Same origin → cookie auth; anything else (desktop, other server) → bearer token in storage. */
  modeFor(origin: string): AuthMode {
    if (origin === 'mock') return 'bearer';
    if (isTauri()) return 'bearer';
    return isSameOrigin(origin) ? 'cookie' : 'bearer';
  }

  /** Check the address answers like a nas-music server; returns server info (v1 servers: health only). */
  async probe(address: string): Promise<{ origin: string; info: ServerInfo | null }> {
    const origin = normaliseServerAddress(address);
    const here = typeof location !== 'undefined' ? location.origin : '';
    const effective = origin === here && !isTauri() ? '' : origin;
    const c = makeClient(effective, this.modeFor(effective), null);
    try {
      await c.health();
    } catch (e) {
      if (e instanceof ApiError) throw new Error(`That address answered, but not like a nas-music server (HTTP ${e.status}).`, { cause: e });
      throw new Error(
        "Can't reach that server. Check the address; for a LAN address with a self-signed certificate, open it once in this browser and accept the certificate.",
        { cause: e },
      );
    }
    let info: ServerInfo | null = null;
    try {
      info = await c.serverInfo();
    } catch {
      /* v1 server */
    }
    return { origin: effective, info };
  }

  private adopt(origin: string, mode: AuthMode, s: Session, info: ServerInfo | null, deviceName: string) {
    this.saved = {
      origin,
      mode,
      token: mode === 'bearer' ? s.deviceToken : null,
      userId: s.userId,
      username: s.username,
      isAdmin: s.isAdmin,
      deviceId: s.deviceId,
      deviceName,
      lanUrl: info?.lanUrl ?? null,
      tunnelUrl: info?.tunnelUrl ?? null,
    };
    this.info = info;
    saveJson(KEY, this.saved);
    this.api = this.client();
  }

  async login(address: string, username: string, password: string, deviceName = this.saved.deviceName) {
    const { origin, info } = await this.probe(address);
    const mode = this.modeFor(origin);
    const c = makeClient(origin, mode, null);
    const s = await c.login({ username, password, deviceName, deviceKind: deviceKind(), cookie: mode === 'cookie' });
    this.adopt(origin, mode, s, info, deviceName);
  }

  async register(address: string, inviteCode: string, username: string, password: string, deviceName = this.saved.deviceName) {
    const { origin, info } = await this.probe(address);
    const mode = this.modeFor(origin);
    const c = makeClient(origin, mode, null);
    const s = await c.register({ inviteCode, username, password, deviceName, deviceKind: deviceKind(), cookie: mode === 'cookie' });
    this.adopt(origin, mode, s, info, deviceName);
  }

  /** Validate the stored session on startup (cookie may have expired, token may be revoked). */
  async refresh(): Promise<boolean> {
    if (!this.signedIn) return false;
    if (this.isMock && !mockBackend().userFor(this.saved.token)) {
      // The mock forgets everything on reload: sign in again silently.
      const s = await this.api.login({ username: 'admin', password: 'admin123', deviceName: this.saved.deviceName, deviceKind: deviceKind() });
      this.adopt('mock', 'bearer', s, null, this.saved.deviceName);
    }
    try {
      const me = await this.api.me();
      this.saved = { ...this.saved, username: me.username, isAdmin: me.isAdmin };
      saveJson(KEY, this.saved);
      this.api.serverInfo().then((i) => (this.info = i)).catch(() => {});
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        this.expire();
        return false;
      }
      return true; // offline: keep the session
    }
  }

  async logout() {
    try {
      await this.api.logout();
    } catch {
      /* already gone */
    }
    this.expire();
  }

  expire() {
    const keep = { origin: this.saved.origin, deviceName: this.saved.deviceName };
    this.saved = { ...empty(), ...keep };
    saveJson(KEY, this.saved);
    this.api = this.client();
  }

  setDeviceName(name: string) {
    this.saved = { ...this.saved, deviceName: name };
    saveJson(KEY, this.saved);
  }

  /** Address shown in the Connect form next time. */
  get lastAddress(): string {
    if (this.saved.origin === 'mock') return 'mock';
    return this.saved.origin || (storage ? defaultServerAddress() : '');
  }
}

export const session = new SessionStore();
