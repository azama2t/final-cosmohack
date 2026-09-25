import type { Map as MLMap } from 'maplibre-gl';
import type { MapboxOverlay } from '@deck.gl/mapbox';
import type { Basemap, Bounds, Camera } from '../types';

/** Module-level handle to the main map, used by panels, tour and screenshots. */
export const ctl = {
  map: null as MLMap | null,
  overlay: null as MapboxOverlay | null,
  render: () => {},
  moving: false,
};

// ---- drift animation clock (outside React: updated every frame) ----
export const anim = {
  hour: 0,
  playing: false,
  speed: 1, // hours of forecast per 1/6 second of wall time (see DriftPlayer)
  maxHour: 72,
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

export function flyToBounds(b: Bounds, opts: { duration?: number; pitch?: number; bearing?: number } = {}) {
  const map = ctl.map;
  if (!map) return;
  const cam = map.cameraForBounds(
    [
      [b[0], b[1]],
      [b[2], b[3]],
    ],
    { padding: mapPadding() },
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

export function offlineStyle(): any {
  return {
    version: 8,
    name: 'offline',
    sources: {},
    layers: [{ id: 'bg', type: 'background', paint: { 'background-color': OCEAN_BG } }],
  };
}

export function satelliteStyle(): any {
  return {
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
  };
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
export function fitOverview(bounds: Bounds[], duration = 2000) {
  const map = ctl.map;
  if (!map || !bounds.length) return;
  const b = unionBounds(bounds);
  const pad = mapPadding();
  const cam = map.cameraForBounds(
    [
      [b[0], b[1]],
      [b[2], b[3]],
    ],
    { padding: { ...pad, right: pad.right + 220, top: pad.top + 40, bottom: pad.bottom + 40 }, maxZoom: 5 },
  );
  if (!cam) return;
  const opts = { center: cam.center, zoom: Math.max(1.2, Math.min(cam.zoom ?? 2, 5)), pitch: 0, bearing: 0 };
  if (duration) map.flyTo({ ...opts, duration, essential: true });
  else map.jumpTo(opts);
}
