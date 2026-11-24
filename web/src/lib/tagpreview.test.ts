// Upload previews: format sniffing and tag reading from hand-built file heads (no fixtures on disk).
import { describe, expect, it } from 'vitest';
import { inspect, previewPath, readTags, sniff } from './tagpreview';

const enc = new TextEncoder();
const bytes = (...parts: (number[] | Uint8Array | string)[]) => {
  const arrs = parts.map((p) => (typeof p === 'string' ? enc.encode(p) : Uint8Array.from(p)));
  const out = new Uint8Array(arrs.reduce((n, a) => n + a.length, 0));
  let i = 0;
  for (const a of arrs) {
    out.set(a, i);
    i += a.length;
  }
  return out;
};
const le32 = (n: number) => [n & 0xff, (n >> 8) & 0xff, (n >> 16) & 0xff, (n >>> 24) & 0xff];
const be32 = (n: number) => [(n >>> 24) & 0xff, (n >> 16) & 0xff, (n >> 8) & 0xff, n & 0xff];
const syncsafe = (n: number) => [(n >> 21) & 0x7f, (n >> 14) & 0x7f, (n >> 7) & 0x7f, n & 0x7f];

function vorbis(comments: string[]): Uint8Array {
  const parts: (number[] | Uint8Array)[] = [le32(6), enc.encode('vendor'), le32(comments.length)];
  for (const c of comments) {
    const b = enc.encode(c);
    parts.push(le32(b.length), b);
  }
  return bytes(...parts);
}

function flac(comments: string[]): Uint8Array {
  const streaminfo = new Uint8Array(34);
  const vc = vorbis(comments);
  return bytes('fLaC', [0x00, 0, 0, 34], streaminfo, [0x84, (vc.length >> 16) & 0xff, (vc.length >> 8) & 0xff, vc.length & 0xff], vc);
}

function id3(version: 3 | 4, frames: [string, string][]): Uint8Array {
  const body = frames.map(([id, text]) => {
    const payload = bytes([3], text); // UTF-8
    const size = version === 4 ? syncsafe(payload.length) : be32(payload.length);
    return bytes(id, size, [0, 0], payload);
  });
  const all = bytes(...body);
  return bytes('ID3', [version, 0, 0], syncsafe(all.length), all);
}

/** Atom types are 4 Latin-1 bytes: '©nam' is A9 6E 61 6D, not UTF-8. */
const latin1 = (s: string) => Array.from(s, (ch) => ch.charCodeAt(0));
function atom(type: string, ...children: Uint8Array[]): Uint8Array {
  const body = bytes(...children);
  return bytes(be32(8 + body.length), latin1(type), body);
}
const dataAtom = (payload: Uint8Array) => atom('data', bytes([0, 0, 0, 1], [0, 0, 0, 0], payload));

function m4a(): Uint8Array {
  const ilst = atom(
    'ilst',
    atom('©nam', dataAtom(enc.encode('Song'))),
    atom('©ART', dataAtom(enc.encode('Singer'))),
    atom('©alb', dataAtom(enc.encode('Record'))),
    atom('trkn', dataAtom(Uint8Array.from([0, 0, 0, 7, 0, 12, 0, 0]))),
    atom('disk', dataAtom(Uint8Array.from([0, 0, 0, 2, 0, 2]))),
  );
  const meta = atom('meta', bytes([0, 0, 0, 0]), ilst);
  return bytes(atom('ftyp', enc.encode('M4A '), new Uint8Array(4)), atom('moov', atom('udta', meta)));
}

describe('sniff', () => {
  it('recognises each upload format by its first bytes', () => {
    expect(sniff(flac([]))).toBe('flac');
    expect(sniff(m4a())).toBe('m4a');
    expect(sniff(bytes('OggS', new Uint8Array(24), 'OpusHead'))).toBe('opus');
    expect(sniff(bytes('OggS', new Uint8Array(24), '\u0001vorbis'))).toBe('ogg');
    expect(sniff(bytes('RIFF', le32(100), 'WAVE'))).toBe('wav');
    expect(sniff(Uint8Array.from([0xff, 0xfb, 0x90, 0x00]))).toBe('mp3');
    expect(sniff(Uint8Array.from([0xff, 0xf1, 0x50, 0x80]))).toBe('aac');
  });
  it('looks past an ID3 header at what follows', () => {
    const tag = id3(4, [['TIT2', 'x']]);
    expect(sniff(bytes(tag, [0xff, 0xfb, 0x90, 0x00]))).toBe('mp3');
    expect(sniff(bytes(tag, 'fLaC'))).toBe('flac');
    expect(sniff(bytes(tag, 'RIFF'))).toBeNull();
    expect(sniff(tag)).toBe('mp3'); // head cut short: assume mp3
  });
  it('rejects too-short and unknown data', () => {
    expect(sniff(bytes('fLa'))).toBeNull();
    expect(sniff(bytes('%PDF-1.7'))).toBeNull();
  });
});

describe('readTags', () => {
  it('reads FLAC Vorbis comments (keys case-insensitive, first value wins)', () => {
    const t = readTags(flac(['title=Song', 'ARTIST=Singer', 'ALBUMARTIST=Band', 'ALBUM=Record', 'TRACKNUMBER=3/12', 'DISCNUMBER=1', 'TITLE=Ignored']));
    expect(t).toEqual({ title: 'Song', artist: 'Singer', albumArtist: 'Band', album: 'Record', track: 3, disc: 1 });
  });
  it('reads ID3v2.3 and v2.4 text frames', () => {
    for (const v of [3, 4] as const) {
      const t = readTags(id3(v, [['TIT2', 'Song'], ['TPE1', 'Singer'], ['TALB', 'Record'], ['TRCK', '05/10'], ['TPOS', '2/2']]));
      expect(t).toMatchObject({ title: 'Song', artist: 'Singer', album: 'Record', track: 5, disc: 2 });
    }
  });
  it('reads MP4 ilst atoms', () => {
    expect(readTags(m4a())).toEqual({ title: 'Song', artist: 'Singer', album: 'Record', track: 7, disc: 2 });
  });
  it('reads Ogg Opus comments', () => {
    const t = readTags(bytes('OggS', new Uint8Array(24), 'OpusHead', new Uint8Array(11), 'OpusTags', vorbis(['TITLE=Live', 'ARTIST=Band'])));
    expect(t).toMatchObject({ title: 'Live', artist: 'Band' });
  });
  it('never throws on truncated or garbage input', () => {
    const full = flac(['TITLE=Song']);
    for (let n = 0; n < full.length; n += 7) expect(() => readTags(full.subarray(0, n))).not.toThrow();
    expect(readTags(bytes('garbage'))).toEqual({});
  });
});

describe('previewPath', () => {
  it('mirrors the server layout: artist / album / NN title', () => {
    expect(previewPath({ albumArtist: 'Band', artist: 'Singer', album: 'Record', title: 'Song', track: 3 }, 'x.flac')).toBe('Band / Record / 03 Song');
    expect(previewPath({ artist: 'Singer', title: 'Song', track: 4, disc: 2 }, 'x.flac')).toBe('Singer / Unknown Album / 2-04 Song');
    expect(previewPath({ artist: 'Singer', title: 'Song' }, 'x.flac')).toBe('Singer / Unknown Album / Song');
  });
  it('puts untagged files in Unsorted under their bare name', () => {
    expect(previewPath({}, 'C:\\Music\\track one.mp3')).toBe('Unsorted / track one');
    expect(previewPath({ title: 'No artist' }, 'dir/a.b.flac')).toBe('Unsorted / a.b');
  });
});

describe('inspect', () => {
  it('sniffs and reads tags from a Blob', async () => {
    const head = flac(['TITLE=Song', 'ARTIST=Singer']);
    const r = await inspect(new Blob([head.slice().buffer as ArrayBuffer]));
    expect(r.format).toBe('flac');
    expect(r.tags).toMatchObject({ title: 'Song', artist: 'Singer' });
  });
});
