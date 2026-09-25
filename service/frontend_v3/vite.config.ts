import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// v3 UI (L94): one left column + map. Dev proxies the backend (env API_URL, default :8072).
// Build: ../static_v3 (served by the service only when MACROPLASTIC_UI=v3).
const API = process.env.API_URL || 'http://127.0.0.1:8072';
const proxied = ['/api', '/data', '/tiles', '/health', '/openapi.json'];

export default defineConfig({
  plugins: [react()],
  base: '/',
  server: {
    host: '127.0.0.1',
    port: 5175,
    strictPort: true,
    proxy: Object.fromEntries(proxied.map((p) => [p, { target: API, changeOrigin: true }])),
  },
  build: {
    outDir: '../static_v3',
    emptyOutDir: true,
    target: 'es2020',
    chunkSizeWarningLimit: 1500,
    rollupOptions: { output: { manualChunks: { maplibre: ['maplibre-gl'] } } },
  },
});
