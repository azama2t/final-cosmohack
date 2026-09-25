import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// v2 UI (L48). Dev: proxies every backend path to the running FastAPI (default :8000, env API_URL).
// Build: ../static_v2 (served by the service when MACROPLASTIC_UI=v2).
const API = process.env.API_URL || 'http://127.0.0.1:8000';
const proxied = ['/api', '/data', '/tiles', '/health', '/openapi.json'];

export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {
    host: '127.0.0.1',
    port: 5174,
    strictPort: true,
    proxy: Object.fromEntries(proxied.map((p) => [p, { target: API, changeOrigin: true }])),
  },
  preview: { host: '127.0.0.1', port: 4174 },
  build: {
    outDir: '../static_v2',
    emptyOutDir: true,
    target: 'es2020',
    chunkSizeWarningLimit: 2500,
    rollupOptions: {
      output: {
        manualChunks: {
          maplibre: ['maplibre-gl'],
          deck: ['@deck.gl/core', '@deck.gl/layers', '@deck.gl/mapbox'],
        },
      },
    },
  },
});
