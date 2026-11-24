/// <reference types="vitest/config" />
import { defineConfig, type Plugin } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { PUBLIC_PRECACHE, precacheList } from './src/lib/swroutes';

/** Fills the service worker (src/sw.ts → /sw.js) with this build's precache list and a version hash. */
function serviceWorker(): Plugin {
  return {
    name: 'nm-service-worker',
    apply: 'build',
    // 'post': after Vite's HTML plugin has added index.html / jam.html to the bundle.
    generateBundle: { order: 'post', handler(_opts, bundle) {
      const sw = bundle['sw.js'];
      if (!sw || sw.type !== 'chunk') return;
      const list = precacheList(Object.keys(bundle));
      const h = createHash('sha256').update(list.join('\n'));
      // Public files keep their names, so their contents go into the version too.
      for (const f of PUBLIC_PRECACHE) h.update(readFileSync(resolve(__dirname, 'public' + f)));
      sw.code = sw.code
        .replaceAll('__NM_PRECACHE__', JSON.stringify(list))
        .replaceAll('__NM_VERSION__', JSON.stringify(h.digest('hex').slice(0, 12)));
      if (!list.includes('/index.html')) this.error('service worker: index.html missing from the precache list');
    } },
  };
}

// `npm run dev:mock` (mode "mock") runs the whole UI against the in-browser mock API.
// `npm run dev` proxies /api to a real server: NM_SERVER=https://192.168.68.2:8443 npm run dev
export default defineConfig(({ mode }) => ({
  plugins: [svelte(), serviceWorker()],
  base: '/',
  define: {
    __MOCK_DEFAULT__: JSON.stringify(mode === 'mock'),
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
    sourcemap: true,
    // jam.html = the Jam guest page (/jam/<code>): its own small bundle, no player or account code.
    // sw = the service worker, emitted at /sw.js (its scope must be the site root).
    rollupOptions: {
      input: { main: resolve(__dirname, 'index.html'), jam: resolve(__dirname, 'jam.html'), sw: resolve(__dirname, 'src/sw.ts') },
      output: { entryFileNames: (c) => (c.name === 'sw' ? 'sw.js' : 'assets/[name]-[hash].js') },
    },
  },
  server: {
    proxy: process.env.NM_SERVER
      ? { '/api': { target: process.env.NM_SERVER, changeOrigin: true, secure: false, ws: true } }
      : undefined,
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
}));
