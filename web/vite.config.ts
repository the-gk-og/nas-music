/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// `npm run dev:mock` (mode "mock") runs the whole UI against the in-browser mock API.
// `npm run dev` proxies /api to a real server: NM_SERVER=https://192.168.68.2:8443 npm run dev
export default defineConfig(({ mode }) => ({
  plugins: [svelte()],
  base: '/',
  define: {
    __MOCK_DEFAULT__: JSON.stringify(mode === 'mock'),
  },
  build: { outDir: 'dist', emptyOutDir: true, target: 'es2022', sourcemap: true },
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
