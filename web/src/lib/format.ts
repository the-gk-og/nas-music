export function fmtTime(sec: number | null | undefined): string {
  if (sec == null || !isFinite(sec) || sec < 0) sec = 0;
  const s = Math.floor(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}

export function fmtBytes(n: number | null | undefined): string {
  if (!n) return '[unknown]';
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
}

export const unknown = (v: unknown): string => (v === null || v === undefined || v === '' ? '[unknown]' : String(v));

/** "FLAC · 16-bit · 44.1 kHz · 912 kbps" */
export function formatLine(i: { codec?: string | null; bitsPerSample?: number | null; sampleRate?: number | null; bitrateKbps?: number | null; channels?: number | null }): string {
  const parts: string[] = [];
  if (i.codec) parts.push(i.codec.toUpperCase());
  if (i.bitsPerSample) parts.push(`${i.bitsPerSample}-bit`);
  if (i.sampleRate) parts.push(`${+(i.sampleRate / 1000).toFixed(1)} kHz`);
  if (i.bitrateKbps) parts.push(`${Math.round(i.bitrateKbps)} kbps`);
  if (i.channels && i.channels !== 2) parts.push(i.channels === 1 ? 'mono' : `${i.channels} ch`);
  return parts.join(' · ') || '[unknown]';
}

/** Browser tab title: "▶ Song · Artist" while playing, "Song · Artist" when paused, else the app name. */
export function tabTitle(track: { title: string; artist?: string | null } | null | undefined, playing: boolean, app = 'Homebeat'): string {
  if (!track) return app;
  const t = track.artist ? `${track.title} · ${track.artist}` : track.title;
  return `${playing ? '▶ ' : ''}${t} — ${app}`;
}
