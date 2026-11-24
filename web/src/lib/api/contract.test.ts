// The web client against api/openapi.yaml (THE contract, CLAUDE.md): every method of ApiClient and the
// admin/tags/family/autotag/uploads APIs is called with placeholder arguments against a fetch that records the
// request, and each recorded METHOD + path must be an operation in the spec. A renamed or removed endpoint,
// or a client call to one that was never documented, fails here instead of in production.
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { adminApi, autotagApi, cleanupApi, familyApi, tagsApi } from './admin';
import { ApiClient } from './client';
import { uploadsApi } from './uploads';

const SPEC = readFileSync(resolve(__dirname, '../../../../api/openapi.yaml'), 'utf8');
const METHODS = ['get', 'post', 'put', 'patch', 'delete'];

/** "/tracks/{id}/stream" -> regex; operations as "GET /tracks/{id}/stream". Only the `paths:` section. */
function operations(spec: string): { op: string; re: RegExp }[] {
  const out: { op: string; re: RegExp }[] = [];
  const paths = spec.slice(spec.indexOf('\npaths:\n'), spec.indexOf('\ncomponents:\n'));
  let path: string | null = null;
  for (const line of paths.split('\n')) {
    const p = /^ {2}(\/\S*):\s*$/.exec(line);
    if (p) {
      path = p[1];
      continue;
    }
    const m = /^ {4}([a-z]+):/.exec(line);
    if (path && m && METHODS.includes(m[1])) {
      const re = new RegExp('^' + path.replace(/[.*+?^$()|[\]\\]/g, '\\$&').replace(/\{[^}]+\}/g, '[^/]+') + '$');
      out.push({ op: `${m[1].toUpperCase()} ${path}`, re });
    }
  }
  return out;
}

const OPS = operations(SPEC);

function documented(method: string, path: string): boolean {
  return OPS.some((o) => o.op.startsWith(method + ' ') && o.re.test(path));
}

interface Call {
  name: string;
  method: string;
  path: string;
}

function recorder() {
  const calls: { method: string; url: string }[] = [];
  const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ method: (init?.method ?? 'GET').toUpperCase(), url: String(url) });
    return new Response('{}', { status: 200, headers: { 'Content-Type': 'application/json' } });
  }) as typeof fetch;
  const client = new ApiClient({ origin: 'https://nas.test', mode: 'bearer', token: 'tok', fetchImpl });
  return { client, calls };
}

// The transport itself, and relArtSrc (fetches whatever art path the server handed out).
const SKIP = new Set(['constructor', 'request', 'fetchRaw', 'putRaw', 'get', 'post', 'put', 'patch', 'del', 'headers', 'relArtSrc']);

const ARGS = ['id1', 'id2', 'id3', 'id4'];

async function exercise(target: object, prefix: string, calls: { method: string; url: string }[]): Promise<Call[]> {
  const found: Call[] = [];
  const names = new Set<string>();
  for (let o: object | null = target; o && o !== Object.prototype; o = Object.getPrototypeOf(o))
    for (const k of Object.getOwnPropertyNames(o)) names.add(k);
  for (const name of [...names].sort()) {
    if (SKIP.has(name)) continue;
    const fn = (target as Record<string, unknown>)[name];
    if (typeof fn !== 'function') continue;
    const before = calls.length;
    try {
      const r = (fn as (...a: unknown[]) => unknown).apply(target, ARGS);
      if (r instanceof Promise) await r.catch(() => undefined);
    } catch {
      /* placeholder arguments can trip client-side validation; whatever was sent is still checked */
    }
    for (const c of calls.slice(before)) {
      const u = new URL(c.url, 'https://nas.test');
      if (!u.pathname.startsWith('/api/v1/')) continue;
      found.push({ name: `${prefix}${name}`, method: c.method, path: u.pathname.slice('/api/v1'.length) });
    }
  }
  return found;
}

describe('web client matches api/openapi.yaml', () => {
  it('parses the spec', () => {
    expect(OPS.length).toBeGreaterThan(150);
    expect(documented('GET', '/health')).toBe(true);
    expect(documented('GET', '/tracks/abc/lyrics')).toBe(true);
    expect(documented('DELETE', '/health')).toBe(false);
  });

  it('every request the client sends is a documented operation', async () => {
    const { client, calls } = recorder();
    const all: Call[] = [
      ...(await exercise(client, '', calls)),
      ...(await exercise(adminApi(client), 'admin.', calls)),
      ...(await exercise(tagsApi(client), 'tags.', calls)),
      ...(await exercise(familyApi(client), 'family.', calls)),
      ...(await exercise(autotagApi(client), 'autotag.', calls)),
      ...(await exercise(uploadsApi(client), 'uploads.', calls)),
      ...(await exercise(cleanupApi(client), 'cleanup.', calls)),
    ];
    // Sanity: the harness really drove the client (it has well over a hundred endpoint methods).
    expect(all.length).toBeGreaterThan(150);
    const bad = all.filter((c) => !documented(c.method, c.path)).map((c) => `${c.name}: ${c.method} ${c.path}`);
    expect(bad, 'client calls missing from api/openapi.yaml').toEqual([]);
  });
});
