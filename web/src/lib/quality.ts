// Streaming quality (web + desktop): one choice for "at home" (the server's LAN address) and one for "remote"
// (the public/tunnel address). Lossless = the original file; Opus at one of the server's bitrate tiers.
import { loadJson, saveJson } from './platform';

export type Quality = 'auto' | 'lossless' | 'opus64' | 'opus96' | 'opus128' | 'opus160' | 'opus192' | 'opus256';
export type NetKind = 'home' | 'remote';

export const QUALITIES: { key: Quality; label: string }[] = [
  { key: 'auto', label: 'Auto' },
  { key: 'lossless', label: 'Lossless' },
  { key: 'opus64', label: 'Opus 64' },
  { key: 'opus96', label: 'Opus 96' },
  { key: 'opus128', label: 'Opus 128' },
  { key: 'opus160', label: 'Opus 160' },
  { key: 'opus192', label: 'Opus 192' },
  { key: 'opus256', label: 'Opus 256' },
];

/** What "Auto" means away from home: fits a ~2 Mbit/s relay comfortably. */
export const AUTO_AWAY_KBPS = 128;

export interface StreamSpec {
  format: 'opus' | 'flac';
  kbps: number; // opus only; 0 for flac
}

export interface QualitySettings {
  home: Quality;
  remote: Quality;
}

const KEY = 'nm.quality';
const DEFAULTS: QualitySettings = { home: 'auto', remote: 'auto' };

function valid(q: unknown): q is Quality {
  return QUALITIES.some((x) => x.key === q);
}

export function loadQuality(): QualitySettings {
  const s = loadJson<QualitySettings>(KEY, DEFAULTS);
  return { home: valid(s.home) ? s.home : 'auto', remote: valid(s.remote) ? s.remote : 'auto' };
}

export function saveQuality(s: QualitySettings) {
  saveJson(KEY, s);
}

const PRIVATE_V4 = /^(10\.|127\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/;

/** Private/LAN host names: RFC 1918 and link-local IPv4, localhost, *.local / *.lan / *.home.arpa, IPv6 ULA/link-local. */
export function isLanHost(host: string): boolean {
  const h = host.replace(/^\[|\]$/g, '').toLowerCase();
  if (h === 'localhost' || h.endsWith('.localhost')) return true;
  if (PRIVATE_V4.test(h)) return true;
  if (/\.(local|lan|home\.arpa|internal)$/.test(h)) return true;
  if (h.includes(':') && (/^f[cd]/.test(h) || h.startsWith('fe80') || h === '::1')) return true;
  return false;
}

function hostOf(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    return new URL(url).host.toLowerCase();
  } catch {
    return null;
  }
}

/**
 * Home when the address we talk to is the server's LAN address (or any private host); remote when it is the
 * tunnel address or anything else public. `origin` '' means "same origin as this page".
 */
export function netKind(origin: string, here: string, lanUrl: string | null, tunnelUrl: string | null): NetKind {
  const effective = hostOf(origin || here);
  if (!effective) return 'home';
  if (effective === hostOf(tunnelUrl)) return 'remote';
  if (effective === hostOf(lanUrl)) return 'home';
  return isLanHost(effective.replace(/:\d+$/, '')) ? 'home' : 'remote';
}

/** Resolve a choice for a network. Without Ogg Opus support (Safari) everything is lossless. */
export function resolveQuality(q: Quality, net: NetKind, canOpus = true): StreamSpec {
  if (!canOpus) return { format: 'flac', kbps: 0 };
  if (q === 'auto') return net === 'home' ? { format: 'flac', kbps: 0 } : { format: 'opus', kbps: AUTO_AWAY_KBPS };
  if (q === 'lossless') return { format: 'flac', kbps: 0 };
  return { format: 'opus', kbps: Number(q.slice(4)) };
}

export function streamQuery(spec: StreamSpec): string {
  return spec.format === 'flac' ? 'format=flac' : `format=opus&bitrate=${spec.kbps}`;
}

const LOSSLESS = new Set(['FLAC', 'ALAC', 'WAV', 'PCM', 'AIFF', 'APE', 'WAVPACK']);

function khz(sr: number): string {
  return sr % 1000 === 0 ? String(sr / 1000) : (sr / 1000).toFixed(1);
}

/** The original file, short (matches the server's X-Stream-Format): "FLAC 16/44.1", "MP3 320". */
export function originalLabel(codec?: string | null, bits?: number | null, sampleRate?: number | null, kbps?: number | null): string {
  let name = (codec ?? '').trim().toUpperCase() || 'Original';
  name = ({ OPUS: 'Opus', VORBIS: 'Vorbis' } as Record<string, string>)[name] ?? name;
  const lossless = LOSSLESS.has(name);
  if (lossless && bits && sampleRate) return `${name} ${bits}/${khz(sampleRate)}`;
  if (lossless && sampleRate) return `${name} ${khz(sampleRate)} kHz`;
  if (!lossless && kbps) return `${name} ${kbps}`;
  return name;
}

export function specLabel(spec: StreamSpec): string {
  return spec.format === 'flac' ? 'Lossless' : `Opus ${spec.kbps}`;
}
