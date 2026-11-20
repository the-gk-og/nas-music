import { describe, expect, it } from 'vitest';
import { ApiError } from './api/client';
import { OffsetMismatch, allowedName, formatBytes, sendFile, type UploadSession } from './api/uploads';
import { previewPath, readTags, sniff } from './tagpreview';

const enc = new TextEncoder();
const cat = (...parts: (Uint8Array | number[])[]) => {
  const out: number[] = [];
  for (const p of parts) out.push(...p);
  return new Uint8Array(out);
};
const le32 = (n: number) => [n & 255, (n >> 8) & 255, (n >> 16) & 255, (n >>> 24) & 255];

function flacWith(comments: string[]): Uint8Array {
  const vendor = enc.encode('test');
  const body = cat(le32(vendor.length), vendor, le32(comments.length), ...comments.flatMap((c) => [le32(enc.encode(c).length), enc.encode(c)]));
  const streaminfo = new Uint8Array(34);
  return cat(enc.encode('fLaC'), [0x00, 0, 0, 34], streaminfo, [0x84, (body.length >> 16) & 255, (body.length >> 8) & 255, body.length & 255], body);
}

function id3With(frames: [string, string][]): Uint8Array {
  const fb = frames.flatMap(([id, v]) => {
    const payload = cat([3], enc.encode(v));
    const n = payload.length;
    return [cat(enc.encode(id), [(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127], [0, 0], payload)];
  });
  const body = cat(...fb);
  const n = body.length;
  return cat(enc.encode('ID3'), [4, 0, 0], [(n >> 21) & 127, (n >> 14) & 127, (n >> 7) & 127, n & 127], body, [0xff, 0xfb, 0x90, 0x64]);
}

describe('sniff', () => {
  it('knows the allowed formats and rejects the rest', () => {
    expect(sniff(flacWith([]))).toBe('flac');
    expect(sniff(id3With([['TIT2', 'x']]))).toBe('mp3');
    expect(sniff(cat([0, 0, 0, 0x20], enc.encode('ftypM4A '), [0, 0, 0, 0]))).toBe('m4a');
    expect(sniff(cat(enc.encode('RIFF'), [0, 0, 0, 0], enc.encode('WAVEfmt ')))).toBe('wav');
    expect(sniff(cat(enc.encode('OggS'), new Array(24).fill(0), enc.encode('OpusHead')))).toBe('opus');
    expect(sniff(new Uint8Array([0xff, 0xf1, 0x50, 0x80]))).toBe('aac');
    expect(sniff(enc.encode('<!doctype html>'))).toBeNull();
    expect(sniff(enc.encode('%PDF-1.7'))).toBeNull();
  });
});

describe('tag preview', () => {
  it('reads FLAC Vorbis comments', () => {
    const t = readTags(flacWith(['TITLE=Night Drive', 'ARTIST=Neon Owl', 'ALBUM=City Lights', 'tracknumber=3/12', 'DISCNUMBER=2']));
    expect(t).toMatchObject({ title: 'Night Drive', artist: 'Neon Owl', album: 'City Lights', track: 3, disc: 2 });
    expect(previewPath(t, 'x.flac')).toBe('Neon Owl / City Lights / 2-03 Night Drive');
  });
  it('reads ID3v2.4 text frames', () => {
    const t = readTags(id3With([['TIT2', 'Café'], ['TPE1', 'Ü'], ['TALB', 'Al'], ['TRCK', '7']]));
    expect(t).toMatchObject({ title: 'Café', artist: 'Ü', album: 'Al', track: 7 });
  });
  it('falls back to the file name', () => {
    expect(previewPath({}, 'C:\\music\\My Demo.wav')).toBe('Unsorted / My Demo');
    expect(readTags(enc.encode('garbage'))).toEqual({});
  });
});

describe('sendFile', () => {
  const file = Object.assign(new Blob([new Uint8Array(10)]), { name: 'a.flac' });
  function fakeApi(script: ((off: number, size: number) => UploadSession | Error)[]) {
    let serverOffset = 0;
    const calls: number[] = [];
    const session = (o: number, status: UploadSession['status'] = 'uploading'): UploadSession => ({
      id: 's1', filename: 'a.flac', bytes: 10, offset: o, visibility: 'everyone', status, error: null, upload: null,
    });
    return {
      calls,
      api: {
        create: async () => session(0),
        status: async () => session(serverOffset),
        append: async (_id: string, off: number, chunk: Blob) => {
          calls.push(off);
          const step = script.shift();
          const r = step ? step(off, chunk.size) : null;
          if (r instanceof Error) throw r;
          if (r) {
            serverOffset = r.offset;
            return r;
          }
          serverOffset = off + chunk.size;
          return session(serverOffset, serverOffset === 10 ? 'done' : 'uploading');
        },
      },
      session,
      setServer: (n: number) => (serverOffset = n),
    };
  }

  it('sends chunks and reports progress', async () => {
    const f = fakeApi([]);
    const progress: number[] = [];
    const s = await sendFile(f.api, file, { visibility: 'everyone', chunkBytes: 4, onProgress: (n) => progress.push(n) });
    expect(s.status).toBe('done');
    expect(f.calls).toEqual([0, 4, 8]);
    expect(progress).toEqual([0, 4, 8, 10]);
  });

  it('resumes from the server offset after a network error and after a 409', async () => {
    const f = fakeApi([
      () => new TypeError('network down'), // the chunk at 0 got through but the reply was lost
      () => new OffsetMismatch(4),
    ]);
    f.setServer(4);
    const s = await sendFile(f.api, file, { visibility: 'everyone', chunkBytes: 4, sleep: async () => {} });
    expect(s.status).toBe('done');
    expect(f.calls).toEqual([0, 4, 4, 8]);
  });

  it('gives up on server refusals and after too many failures', async () => {
    const refusing = fakeApi([() => new ApiError(415, 'not an audio file')]);
    await expect(sendFile(refusing.api, file, { visibility: 'everyone', chunkBytes: 4 })).rejects.toThrow('not an audio file');
    const offline = fakeApi(new Array(10).fill(() => new TypeError('offline')));
    await expect(sendFile(offline.api, file, { visibility: 'everyone', chunkBytes: 4, retries: 2, sleep: async () => {} })).rejects.toThrow('offline');
    expect(offline.calls.length).toBe(3);
  });
});

describe('helpers', () => {
  it('checks names and formats sizes', () => {
    expect(allowedName('x.FLAC')).toBe(true);
    expect(allowedName('x.exe')).toBe(false);
    expect(formatBytes(1536)).toBe('1.5 KB');
    expect(formatBytes(50 * 1024 ** 3)).toBe('50 GB');
  });
});
