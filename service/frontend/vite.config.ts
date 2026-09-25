import { defineConfig, type Plugin, type Connect } from 'vite';
import react from '@vitejs/plugin-react';
import fs from 'node:fs';
import path from 'node:path';
import http from 'node:http';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
// Data root for dev/preview: env DATA_ROOT, else ../demo_fixtures (see docs/CONTRACTS.md)
const DATA_ROOT = path.resolve(process.env.DATA_ROOT || path.join(here, '..', 'demo_fixtures'));
const API = process.env.API_URL || 'http://127.0.0.1:8000';

const MIME: Record<string, string> = {
  '.json': 'application/json; charset=utf-8',
  '.geojson': 'application/geo+json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.webp': 'image/webp',
  '.tif': 'image/tiff',
  '.csv': 'text/csv; charset=utf-8',
};

/** Serves /data/* from DATA_ROOT (static files, as the backend does in production). */
const serveData: Connect.NextHandleFunction = (req, res, next) => {
  const rel = decodeURIComponent((req.url || '/').split('?')[0]);
  const fp = path.normalize(path.join(DATA_ROOT, rel));
  if (!fp.startsWith(DATA_ROOT)) {
    res.statusCode = 403;
    return res.end();
  }
  fs.stat(fp, (err, st) => {
    if (err || !st.isFile()) {
      res.statusCode = 404;
      res.setHeader('Content-Type', 'application/json');
      return res.end(JSON.stringify({ error: 'not found', path: rel }));
    }
    res.setHeader('Content-Type', MIME[path.extname(fp).toLowerCase()] || 'application/octet-stream');
    res.setHeader('Content-Length', String(st.size));
    res.setHeader('Cache-Control', 'no-cache');
    fs.createReadStream(fp).pipe(res);
  });
  void next;
};

/**
 * Proxies /api/* and /health to the backend. If the backend is not running,
 * answers 204 + X-No-Backend (instead of 500) so the UI silently falls back to
 * client-side computations and the console stays clean during dev screenshots.
 */
const proxyApi: Connect.NextHandleFunction = (req, res) => {
  const target = new URL(req.originalUrl || req.url || '/', API);
  const preq = http.request(
    target,
    { method: req.method, headers: { ...req.headers, host: target.host } },
    (pres) => {
      res.writeHead(pres.statusCode || 502, pres.headers);
      pres.pipe(res);
    },
  );
  preq.on('error', () => {
    if (res.headersSent) return res.end();
    res.statusCode = 204;
    res.setHeader('X-No-Backend', '1');
    res.end();
  });
  req.pipe(preq);
};

function dataPlugin(): Plugin {
  const install = (mw: Connect.Server) => {
    mw.use('/data', serveData);
    mw.use('/api', proxyApi);
    mw.use('/health', proxyApi);
  };
  return {
    name: 'macroplastic-data-root',
    configureServer(server) {
      server.config.logger.info(`[data] /data -> ${DATA_ROOT}; /api -> ${API}`);
      install(server.middlewares);
    },
    configurePreviewServer(server) {
      install(server.middlewares);
    },
  };
}

export default defineConfig({
  plugins: [react(), dataPlugin()],
  base: '/',
  server: { host: '127.0.0.1', port: 5173, strictPort: true },
  preview: { host: '127.0.0.1', port: 4173 },
  build: {
    outDir: '../static',
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
