import type { Map as MLMap } from 'maplibre-gl';
import type { MapboxOverlay } from '@deck.gl/mapbox';
import type { Basemap, Bounds, Camera, Projection } from '../types';

/** Module-level handle to the main map, used by panels, tour and screenshots. */
export const ctl = {
  map: null as MLMap | null,
  overlay: null as MapboxOverlay | null,
  render: () => {},
  moving: false,
  /** L27: current projection; embedded into every style (setStyle with diff:false resets it otherwise) */
  projection: 'mercator' as Projection,
  /** L27: globe overview is centred on this point (best region) */
  overviewFocus: undefined as [number, number] | undefined,
};

/**
 * L27: default projection on the overview — globe when WebGL is hardware-accelerated
 * (globe measured ≥ 50 fps with --gl gpu, see reports/ui_perf.md), flat map on software renderers
 * (SwiftShader / llvmpipe / Microsoft Basic Render) where the globe would stutter.
 */
export function defaultProjection(): Projection {
  try {
    const gl = document.createElement('canvas').getContext('webgl2') as WebGL2RenderingContext | null;
    if (!gl) return 'mercator';
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    const r = String(ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER));
    gl.getExtension('WEBGL_lose_context')?.loseContext();
    return /swiftshader|llvmpipe|software|basic render/i.test(r) ? 'mercator' : 'globe';
  } catch {
    return 'mercator';
  }
}

/** Adds a projection (and the globe atmosphere) to a style object. Compare maps keep the default (mercator). */
export function withProjection(s: any, proj: Projection = 'mercator'): any {
  const globe = proj === 'globe';
  return {
    ...s,
    projection: { type: globe ? 'globe' : 'mercator' },
    ...(globe ? { sky: { 'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 0.9, 5, 0.6, 8, 0] } } : {}),
  };
}

// ---- drift animation clock (outside React: updated every frame) ----
export const anim = {
  hour: 0,
  playing: false,
  speed: 1, // hours of forecast per 1/6 second of wall time (see DriftPlayer)
  maxHour: 72,
  /** wind-factor ensemble cloud on/off (DriftPlayer toggle) */
  spread: true,
  listeners: new Set<(h: number) => void>(),
};

export function mapPadding() {
  const w = window.innerWidth;
  const left = document.querySelector('.panel-left:not(.collapsed)')?.getBoundingClientRect();
  const right = document.querySelector('.panel-right:not(.collapsed)')?.getBoundingClientRect();
  const l = left ? left.right + 24 : 48;
  const r = right ? w - right.left + 24 : 48;
  return { top: 72, bottom: 96, left: l, right: r };
}

export function flyToBounds(b: Bounds, opts: { duration?: number; pitch?: number; bearing?: number; extra?: number; minBottom?: number } = {}) {
  const map = ctl.map;
  if (!map) return;
  const pad = mapPadding();
  const e = opts.extra ?? 0;
  const cam = map.cameraForBounds(
    [
      [b[0], b[1]],
      [b[2], b[3]],
    ],
    { padding: { top: pad.top + e, bottom: Math.max(pad.bottom + e, opts.minBottom ?? 0), left: pad.left + e, right: pad.right + e } },
  );
  if (!cam) return;
  map.flyTo({
    center: cam.center,
    zoom: Math.min(cam.zoom ?? 11, 15),
    pitch: opts.pitch ?? 0,
    bearing: opts.bearing ?? 0,
    duration: opts.duration ?? 2600,
    essential: true,
    curve: 1.5,
  });
}

export function flyToPoint(lon: number, lat: number, zoom = 14, duration = 1800) {
  ctl.map?.flyTo({ center: [lon, lat], zoom, duration, essential: true, pitch: ctl.map.getPitch() });
}

export function getCamera(): Camera | undefined {
  const m = ctl.map;
  if (!m) return undefined;
  const c = m.getCenter();
  return { lon: c.lng, lat: c.lat, zoom: m.getZoom(), pitch: m.getPitch(), bearing: m.getBearing() };
}

export function unionBounds(list: Bounds[]): Bounds {
  return list.reduce<Bounds>(
    (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])],
    [180, 90, -180, -90],
  );
}

export function waitIdle(timeoutMs = 8000): Promise<void> {
  const map = ctl.map;
  if (!map) return Promise.resolve();
  return new Promise((res) => {
    const t = setTimeout(res, timeoutMs);
    const check = () => {
      if (!map.isMoving()) {
        clearTimeout(t);
        res();
      } else map.once('moveend', check);
    };
    setTimeout(check, 50);
  });
}

// ---- basemaps ----
export const ESRI_TILES = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
export const CARTO_DARK = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';

export const ATTRIBUTION: Record<Basemap, string> = {
  satellite: 'Подложка: Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
  dark: 'Подложка: © CARTO, © OpenStreetMap contributors',
  none: 'Подложка отключена (офлайн-режим): показаны только вырезки Sentinel-2',
};

const OCEAN_BG = '#07111f';

/**
 * Offline basemap «Без»: ocean colour + a coarse local land outline (Natural Earth 1:110m, public domain,
 * served from service/static/land-110m.geojson, 75 KB) so that the coastline is visible without internet.
 * The outline fades out at region zooms, where the Sentinel-2 crop gives the context and 1:110m is too coarse.
 */
export function offlineStyle(proj: Projection = 'mercator'): any {
  return withProjection({
    version: 8,
    name: 'offline',
    sources: { land: { type: 'geojson', data: '/land-110m.geojson', attribution: 'Контур суши: Natural Earth (public domain)' } },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': OCEAN_BG } },
      {
        id: 'land',
        type: 'fill',
        source: 'land',
        paint: { 'fill-color': '#13233a', 'fill-opacity': ['interpolate', ['linear'], ['zoom'], 7, 1, 10, 0.35] },
      },
      {
        id: 'coast',
        type: 'line',
        source: 'land',
        paint: {
          'line-color': '#2e4a70',
          'line-width': ['interpolate', ['linear'], ['zoom'], 1, 0.6, 6, 1.2],
          'line-opacity': ['interpolate', ['linear'], ['zoom'], 7, 1, 10, 0.3],
        },
      },
    ],
  }, proj);
}

export function satelliteStyle(proj: Projection = 'mercator'): any {
  return withProjection({
    version: 8,
    name: 'satellite',
    sources: {
      esri: { type: 'raster', tiles: [ESRI_TILES], tileSize: 256, maxzoom: 18, attribution: ATTRIBUTION.satellite },
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': OCEAN_BG } },
      {
        id: 'esri',
        type: 'raster',
        source: 'esri',
        paint: { 'raster-brightness-max': 0.82, 'raster-saturation': -0.15, 'raster-contrast': 0.05 },
      },
    ],
  }, proj);
}

let darkStyleCache: Promise<any> | null = null;
/** CARTO dark-matter, recoloured to the «ночной океан» palette. Rejects when offline. */
export function darkStyle(): Promise<any> {
  if (!darkStyleCache) {
    darkStyleCache = fetch(CARTO_DARK)
      .then((r) => {
        if (!r.ok) throw new Error('carto ' + r.status);
        return r.json();
      })
      .then((s) => {
        for (const l of s.layers as any[]) {
          if (l.type === 'background') l.paint = { ...l.paint, 'background-color': '#0c1522' };
          if (l.id === 'water' || l['source-layer'] === 'water') {
            if (l.type === 'fill') l.paint = { ...l.paint, 'fill-color': OCEAN_BG };
          }
          // tone down road/labels clutter a bit
          if (l.type === 'symbol' && l.paint) {
            l.paint['text-color'] = '#6f819c';
            l.paint['text-halo-color'] = '#07111f';
          }
        }
        return s;
      });
    darkStyleCache.catch(() => (darkStyleCache = null));
  }
  return darkStyleCache;
}

(window as any).__ctl = ctl;

/** Frames all regions; extra right padding leaves room for the region label pills. */
export function fitOverview(bounds: Bounds[], duration = 2000, focus: [number, number] | undefined = ctl.overviewFocus) {
  const map = ctl.map;
  if (!map || !bounds.length) return;
  const b = unionBounds(bounds);
  const pad = mapPadding();
  if (ctl.projection === 'globe') {
    // a globe shows one hemisphere: turn it to the longitude where most regions are on the visible side
    // (≤ 70° from the centre; ties → closer to the focus/best region) and size the sphere to ~92 % of the free
    // map height; offset keeps it between the side panels
    const H = map.getContainer().clientHeight;
    const r = Math.max(160, (H - pad.top - pad.bottom) * 0.46);
    const pts = bounds.map((q) => [(q[0] + q[2]) / 2, (q[1] + q[3]) / 2]);
    const ang = (a: number[], c: number[]) => {
      const t = Math.PI / 180;
      const cosd = Math.sin(a[1] * t) * Math.sin(c[1] * t) + Math.cos(a[1] * t) * Math.cos(c[1] * t) * Math.cos((a[0] - c[0]) * t);
      return Math.acos(Math.max(-1, Math.min(1, cosd))) / t;
    };
    let lon = focus ? focus[0] : (b[0] + b[2]) / 2;
    let best = -1;
    for (let L = -180; L < 180; L += 5) {
      const n = pts.filter((q) => ang(q, [L, 12]) <= 70).length;
      const tie = focus ? -ang(focus, [L, 12]) / 1000 : 0;
      if (n + tie > best) {
        best = n + tie;
        lon = L;
      }
    }
    const vis = pts.filter((q) => ang(q, [lon, 12]) <= 70);
    const lat = Math.max(-25, Math.min(25, vis.reduce((a, q) => a + q[1], 0) / Math.max(1, vis.length)));
    const zoom = Math.log2((r * 2 * Math.PI) / 512);
    const opts = { center: [lon, lat] as [number, number], zoom, pitch: 0, bearing: 0, offset: [(pad.left - pad.right) / 2, 0] as [number, number] };
    if (duration) map.flyTo({ ...opts, duration, essential: true });
    else {
      map.jumpTo({ center: opts.center, zoom, pitch: 0, bearing: 0 });
      map.panBy([-(pad.left - pad.right) / 2, 0], { duration: 0 });
    }
    return;
  }
  const cam = map.cameraForBounds(
    [
      [b[0], b[1]],
      [b[2], b[3]],
    ],
    { padding: { ...pad, right: pad.right + 170, top: pad.top + 40, bottom: pad.bottom + 40 }, maxZoom: 5 },
  );
  if (!cam) return;
  // no hard minimum around 1.2: on 1366 px the 12 regions need zoom ≈ 0.8, otherwise labels end up under the panels
  const opts = { center: cam.center, zoom: Math.max(0.4, Math.min(cam.zoom ?? 2, 5)), pitch: 0, bearing: 0 };
  if (duration) map.flyTo({ ...opts, duration, essential: true });
  else map.jumpTo(opts);
}
