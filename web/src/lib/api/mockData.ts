// Deterministic fake library for the mock API. All names and lyrics are invented.
import type { Album, Artist, Mix, Suggestion, Track, TrackInfo } from '../types';

const ARTISTS = [
  ['Velvet Static', 'Synthpop'],
  ['The Marrow Lanes', 'Indie Rock'],
  ['Juniper & Coal', 'Folk'],
  ['Neon Harbor', 'Electronic'],
  ['Ada Quill', 'Singer-Songwriter'],
  ['Granite Choir', 'Metal'],
  ['Lumen Drift', 'Ambient'],
  ['Copperline', 'Country'],
] as const;

const ALBUM_WORDS = ['Afterglow', 'Paper Moons', 'Low Tide', 'Glass Houses', 'Northbound', 'Static Hearts', 'Wildfire Season', 'Quiet Machines', 'Salt & Signal', 'Halfway Home', 'Midnight Atlas', 'Small Hours', 'Iron Garden', 'Satellite Bloom', 'Blue Hour', 'Ember Road'];
const TITLE_A = ['Conversations', 'Echoes', 'Letters', 'Shadows', 'Rivers', 'Signals', 'Streetlights', 'Ghosts', 'Satellites', 'Fireflies', 'Postcards', 'Mirrors'];
const TITLE_B = ['with Strangers', 'in the Dark', 'from Home', 'of July', 'Over Water', 'at Dawn', 'and Static', 'Underground', 'for Nobody', 'in Reverse'];

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

export interface MockLibrary {
  artists: Artist[];
  albums: Album[];
  tracks: TrackInfo[];
  lyrics: Map<string, string>;
}

export function buildLibrary(): MockLibrary {
  const artists: Artist[] = [];
  const albums: Album[] = [];
  const tracks: TrackInfo[] = [];
  const lyrics = new Map<string, string>();
  let albumNo = 0;
  ARTISTS.forEach(([name, genre], ai) => {
    const artistId = 'ar' + ai;
    const nAlbums = 2;
    artists.push({ id: artistId, name, albumCount: nAlbums, mbid: null });
    for (let k = 0; k < nAlbums; k++) {
      const title = ALBUM_WORDS[albumNo % ALBUM_WORDS.length];
      const albumId = 'al' + albumNo;
      const year = 2008 + ((albumNo * 7) % 17);
      const nTracks = 4 + (hash(albumId) % 5);
      let dur = 0;
      for (let n = 1; n <= nTracks; n++) {
        const h = hash(albumId + n);
        const id = `t${albumNo}_${n}`;
        const d = 60 + (h % 150);
        dur += d;
        const ttl = `${TITLE_A[h % TITLE_A.length]} ${TITLE_B[(h >>> 5) % TITLE_B.length]}`;
        const folder = `${name}/${year} - ${title}`;
        const fileName = `${String(n).padStart(2, '0')} ${ttl}.flac`;
        tracks.push({
          id,
          title: ttl,
          artistId,
          artist: name,
          albumId,
          album: title,
          trackNo: n,
          discNo: 1,
          year,
          genre,
          durationS: d,
          flacBytes: d * 115000,
          starred: h % 7 === 0,
          rating: null,
          replayGain: h % 5 === 0 ? null : { trackGain: -((h % 90) / 10) - 2, trackPeak: 0.9 + (h % 10) / 100, albumGain: -6.5, albumPeak: 0.99 },
          fileName,
          folder,
          albumArtist: name,
          composer: h % 3 === 0 ? name : null,
          lyricist: null,
          comment: h % 4 === 0 ? 'Mock track' : null,
          codec: 'FLAC',
          bitrateKbps: 900 + (h % 200),
          sampleRate: 44100,
          bitsPerSample: 16,
          channels: 2,
          fileBytes: d * 115000,
        });
        if (h % 3 === 0) lyrics.set(id, syncedLyrics(ttl, d));
        else if (h % 3 === 1) lyrics.set(id, plainLyrics(ttl));
      }
      albums.push({ id: albumId, title, artistId, artist: name, year, trackCount: nTracks, durationS: dur, hasArt: true, mbid: null });
      albumNo++;
    }
  });
  return { artists, albums, tracks, lyrics };
}

const LINES = [
  'Placeholder line for the mock server',
  'Every word here is made up for testing',
  'The chorus comes around again',
  'Highlight follows the playing line',
  'Click a line to jump right to it',
  'Instrumental break below',
  '',
  'Back to the verse, slower now',
  'Nearly at the end of the song',
  'Last line of the mock lyrics',
];

function syncedLyrics(title: string, dur: number): string {
  const step = Math.max(4, Math.floor((dur - 10) / LINES.length));
  const out = [`[ti:${title}]`];
  const ts = (sec: number) => {
    const cs = Math.round(sec * 100);
    const mm = Math.floor(cs / 6000);
    const ss = Math.floor((cs % 6000) / 100);
    return `${String(mm).padStart(2, '0')}:${String(ss).padStart(2, '0')}.${String(cs % 100).padStart(2, '0')}`;
  };
  LINES.forEach((l, i) => {
    const t = 5 + i * step;
    // Enhanced LRC (word stamps) so the karaoke highlight shows in mock mode.
    const words = l.split(' ').filter(Boolean);
    const per = Math.min(0.6, (step * 0.8) / Math.max(1, words.length));
    const body = words.map((w, j) => `<${ts(t + j * per)}>${w}${j < words.length - 1 ? ' ' : ''}`).join('');
    out.push(`[${ts(t)}]${body}`);
  });
  return out.join('\n');
}

function plainLyrics(title: string): string {
  return [`(${title})`, '', ...LINES.filter(Boolean)].join('\n');
}

export function mockMixes(tracks: Track[]): Mix[] {
  const pick = (seed: number, n: number) =>
    Array.from({ length: n }, (_, i) => tracks[(seed * 31 + i * 17) % tracks.length]).map((t, i) => ({
      track: t,
      reason: i % 2 ? `Because you played ${t.artist} a lot this week` : 'Sounds like your recent favourites',
    }));
  const now = new Date().toISOString();
  return [
    { id: 'mx1', kind: 'daily', title: 'Daily Mix 1', createdAt: now, tracks: pick(1, 14) },
    { id: 'mx2', kind: 'morning', title: 'Morning', createdAt: now, tracks: pick(2, 10) },
    { id: 'mx3', kind: 'deep_cuts', title: 'Deep cuts', createdAt: now, tracks: pick(3, 12) },
  ].map((m) => ({ ...m, durationS: m.tracks.reduce((s, x) => s + x.track.durationS, 0) }));
}

export const mockSuggestions: Suggestion[] = [
  { id: 's1', artist: 'Harbor Lights', album: 'Undertow', year: 2021, hasCover: true, hasPreview: false, reason: 'Similar to Neon Harbor and Velvet Static', status: 'new', buyLinks: [{ name: 'Bandcamp', url: 'https://bandcamp.com' }] },
  { id: 's2', artist: 'Moss & Mortar', album: 'Field Notes', year: 2019, hasCover: true, hasPreview: false, reason: 'Fans of Juniper & Coal like this', status: 'new', buyLinks: [] },
];

// ---- generated media ----

export function artSvg(albumId: string, title: string): string {
  const h = hash(albumId);
  const c1 = `hsl(${h % 360} 65% 45%)`;
  const c2 = `hsl(${(h >>> 9) % 360} 70% 22%)`;
  const initials = title
    .split(/\s+/)
    .map((w) => w[0])
    .join('')
    .slice(0, 3)
    .replace(/[<&>"]/g, '');
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${c1}"/><stop offset="1" stop-color="${c2}"/></linearGradient></defs><rect width="256" height="256" fill="url(#g)"/><circle cx="${60 + (h % 140)}" cy="${60 + ((h >>> 4) % 140)}" r="${30 + (h % 50)}" fill="rgba(255,255,255,.18)"/><text x="20" y="232" font-family="sans-serif" font-size="44" font-weight="700" fill="rgba(255,255,255,.9)">${initials}</text></svg>`;
  return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

/** A quiet two-note WAV (8 kHz, 8-bit mono) as long as the track, so seeking and "ended" behave. */
export function toneWav(trackId: string, seconds: number): Blob {
  const rate = 8000;
  const n = Math.floor(rate * seconds);
  const buf = new ArrayBuffer(44 + n);
  const v = new DataView(buf);
  const w = (o: number, s: string) => [...s].forEach((ch, i) => v.setUint8(o + i, ch.charCodeAt(0)));
  w(0, 'RIFF');
  v.setUint32(4, 36 + n, true);
  w(8, 'WAVE');
  w(12, 'fmt ');
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, 1, true);
  v.setUint32(24, rate, true);
  v.setUint32(28, rate, true);
  v.setUint16(32, 1, true);
  v.setUint16(34, 8, true);
  w(36, 'data');
  v.setUint32(40, n, true);
  const h = hash(trackId);
  const f1 = 196 + (h % 200);
  const f2 = f1 * 1.5;
  for (let i = 0; i < n; i++) {
    const t = i / rate;
    const beat = (t % 1) < 0.5 ? f1 : f2;
    const env = 0.5 + 0.5 * Math.sin(2 * Math.PI * 0.25 * t);
    v.setUint8(44 + i, 128 + Math.round(18 * env * Math.sin(2 * Math.PI * beat * t)));
  }
  return new Blob([buf], { type: 'audio/wav' });
}
