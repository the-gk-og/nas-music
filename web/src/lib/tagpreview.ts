// Upload page helpers: what kind of audio a file is (from its first bytes, like the server's sniffing) and a
// tag preview (FLAC Vorbis comments, ID3v2 text frames, MP4 ilst atoms, Ogg Vorbis/Opus comments), read
// from the start of the file in the browser before anything is sent. The server reads the tags again.

export type Sniffed = 'flac' | 'mp3' | 'm4a' | 'aac' | 'ogg' | 'opus' | 'wav';

export interface TagPreview {
  title?: string;
  artist?: string;
  albumArtist?: string;
  album?: string;
  track?: number;
  disc?: number;
}

const syncsafe = (b: Uint8Array, i: number) => (b[i] << 21) | (b[i + 1] << 14) | (b[i + 2] << 7) | b[i + 3];
const ascii = (b: Uint8Array, i: number, n: number) => String.fromCharCode(...b.subarray(i, i + n));
const u32be = (b: Uint8Array, i: number) => ((b[i] << 24) >>> 0) + (b[i + 1] << 16) + (b[i + 2] << 8) + b[i + 3];
const u32le = (b: Uint8Array, i: number) => b[i] + (b[i + 1] << 8) + (b[i + 2] << 16) + ((b[i + 3] << 24) >>> 0);

function id3End(b: Uint8Array): number {
  return 10 + syncsafe(b, 6) + (b[5] & 0x10 ? 10 : 0);
}

/** Same rules as the server (homebeat/library/uploads.py sniff). */
export function sniff(b: Uint8Array): Sniffed | null {
  if (b.length < 4) return null;
  if (ascii(b, 0, 3) === 'ID3' && b.length >= 10) {
    const end = id3End(b);
    if (b.length >= end + 4) {
      const inner = sniff(b.subarray(end));
      return inner === 'flac' || inner === 'mp3' || inner === 'aac' ? inner : null;
    }
    return 'mp3';
  }
  if (ascii(b, 0, 4) === 'fLaC') return 'flac';
  if (b.length >= 12 && ascii(b, 4, 4) === 'ftyp') return 'm4a';
  if (ascii(b, 0, 4) === 'OggS') return ascii(b, 0, Math.min(128, b.length)).includes('OpusHead') ? 'opus' : 'ogg';
  if (b.length >= 12 && ascii(b, 0, 4) === 'RIFF' && ascii(b, 8, 4) === 'WAVE') return 'wav';
  if (b[0] === 0xff && b.length >= 3) {
    if ((b[1] & 0xf6) === 0xf0) return 'aac';
    if ((b[1] & 0xe0) === 0xe0 && (b[1] & 0x06) !== 0 && (b[2] & 0xf0) !== 0xf0) return 'mp3';
  }
  return null;
}

const utf8 = new TextDecoder('utf-8');

function num(s: string | undefined): number | undefined {
  const m = s?.match(/\d+/);
  return m ? Number(m[0]) : undefined;
}

function fromComments(c: Map<string, string>): TagPreview {
  return {
    title: c.get('TITLE'),
    artist: c.get('ARTIST'),
    albumArtist: c.get('ALBUMARTIST') ?? c.get('ALBUM ARTIST'),
    album: c.get('ALBUM'),
    track: num(c.get('TRACKNUMBER')),
    disc: num(c.get('DISCNUMBER')),
  };
}

function vorbisComments(b: Uint8Array, i: number): Map<string, string> {
  const out = new Map<string, string>();
  if (i + 4 > b.length) return out;
  i += 4 + u32le(b, i); // vendor
  if (i + 4 > b.length) return out;
  const n = u32le(b, i);
  i += 4;
  for (let k = 0; k < n && i + 4 <= b.length; k++) {
    const len = u32le(b, i);
    i += 4;
    if (i + len > b.length) break;
    const s = utf8.decode(b.subarray(i, i + len));
    i += len;
    const eq = s.indexOf('=');
    if (eq > 0) {
      const key = s.slice(0, eq).toUpperCase();
      if (!out.has(key)) out.set(key, s.slice(eq + 1));
    }
  }
  return out;
}

function flacTags(b: Uint8Array, start: number): TagPreview {
  let i = start + 4;
  while (i + 4 <= b.length) {
    const type = b[i] & 0x7f;
    const last = (b[i] & 0x80) !== 0;
    const len = (b[i + 1] << 16) | (b[i + 2] << 8) | b[i + 3];
    if (type === 4) return fromComments(vorbisComments(b, i + 4));
    if (last) break;
    i += 4 + len;
  }
  return {};
}

function id3Text(frame: Uint8Array): string {
  const enc = frame[0];
  const body = frame.subarray(1);
  let s: string;
  if (enc === 1 || enc === 2) {
    const le = enc === 1 ? !(body[0] === 0xfe && body[1] === 0xff) : false;
    const start = enc === 1 && ((body[0] === 0xff && body[1] === 0xfe) || (body[0] === 0xfe && body[1] === 0xff)) ? 2 : 0;
    s = new TextDecoder(le ? 'utf-16le' : 'utf-16be').decode(body.subarray(start));
  } else if (enc === 3) s = utf8.decode(body);
  else s = String.fromCharCode(...body);
  return s.split('\u0000')[0].trim();
}

function id3Tags(b: Uint8Array): TagPreview {
  const ver = b[3];
  const end = Math.min(b.length, id3End(b));
  const f = new Map<string, string>();
  let i = 10 + (b[5] & 0x40 ? (ver === 4 ? syncsafe(b, 10) : u32be(b, 10) + 4) : 0);
  while (i + 10 <= end) {
    const id = ascii(b, i, 4);
    if (!/^[A-Z0-9]{4}$/.test(id)) break;
    const size = ver === 4 ? syncsafe(b, i + 4) : u32be(b, i + 4);
    const body = b.subarray(i + 10, Math.min(end, i + 10 + size));
    if (id.startsWith('T') && !f.has(id)) f.set(id, id3Text(body));
    i += 10 + size;
  }
  return { title: f.get('TIT2'), artist: f.get('TPE1'), albumArtist: f.get('TPE2'), album: f.get('TALB'), track: num(f.get('TRCK')), disc: num(f.get('TPOS')) };
}

function mp4Tags(b: Uint8Array): TagPreview {
  const out: TagPreview = {};
  const walk = (s: number, e: number, depth: number) => {
    let i = s;
    while (i + 8 <= e && depth < 8) {
      const size = u32be(b, i);
      const type = ascii(b, i + 4, 4);
      if (size < 8 || i + size > e) return;
      if (['moov', 'udta', 'ilst'].includes(type)) walk(i + 8, i + size, depth + 1);
      else if (type === 'meta') walk(i + 12, i + size, depth + 1);
      else if (['©nam', '©ART', 'aART', '©alb', 'trkn', 'disk'].includes(type)) {
        // child "data" atom: size, 'data', type(4), locale(4), payload
        const d = i + 8;
        if (ascii(b, d + 4, 4) === 'data') {
          const payload = b.subarray(d + 16, d + u32be(b, d));
          if (type === 'trkn') out.track = (payload[2] << 8) | payload[3];
          else if (type === 'disk') out.disc = (payload[2] << 8) | payload[3];
          else {
            const v = utf8.decode(payload);
            if (type === '©nam') out.title = v;
            else if (type === '©ART') out.artist = v;
            else if (type === 'aART') out.albumArtist = v;
            else out.album = v;
          }
        }
      }
      i += size;
    }
  };
  walk(0, b.length, 0);
  return out;
}

function oggTags(b: Uint8Array): TagPreview {
  // The comment header is the second packet; find its signature in the first pages.
  const s = ascii(b, 0, Math.min(b.length, 65536));
  for (const [sig, skip] of [['OpusTags', 8], ['\u0003vorbis', 7]] as const) {
    const at = s.indexOf(sig);
    if (at >= 0) return fromComments(vorbisComments(b, at + skip));
  }
  return {};
}

/** Tags from the first bytes of a file (`head`, e.g. 256 KiB). Unknown/absent fields are left out. */
export function readTags(head: Uint8Array): TagPreview {
  try {
    if (ascii(head, 0, 3) === 'ID3') return id3Tags(head);
    if (ascii(head, 0, 4) === 'fLaC') return flacTags(head, 0);
    if (ascii(head, 4, 4) === 'ftyp') return mp4Tags(head);
    if (ascii(head, 0, 4) === 'OggS') return oggTags(head);
  } catch {
    /* a preview only */
  }
  return {};
}

/** Where the server will put it (mirrors target_relpath; the server's sanitising is authoritative). */
export function previewPath(t: TagPreview, filename: string): string {
  const artist = t.albumArtist || t.artist;
  if (artist && t.title) {
    const nn = t.track ? `${t.disc && t.disc > 1 ? `${t.disc}-` : ''}${String(t.track).padStart(2, '0')} ` : '';
    return `${artist} / ${t.album || 'Unknown Album'} / ${nn}${t.title}`;
  }
  return `Unsorted / ${filename.replace(/^.*[\\/]/, '').replace(/\.[^.]+$/, '')}`;
}

export async function inspect(file: Blob): Promise<{ format: Sniffed | null; tags: TagPreview }> {
  const head = new Uint8Array(await file.slice(0, 256 * 1024).arrayBuffer());
  return { format: sniff(head), tags: readTags(head) };
}
