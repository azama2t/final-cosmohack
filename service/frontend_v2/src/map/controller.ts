import type { Map as MLMap } from 'maplibre-gl';
import type { MapboxOverlay } from '@deck.gl/mapbox';
import type { Basemap, Bounds, Camera, Projection } from '../types';

/** Module-level handle to the main map, used by panels, tour and screenshots. */
export const ctl = {
  map: null as MLMap | null,
  overlay: null as MapboxOverlay | null,
  render: () => {},
  moving: false,
  projection: 'globe' as Projection,
  overviewFocus: undefined as [number, number] | undefined,
};


/** Space background colour (also behind the globe, see Stars) */
export const SPACE = '#07080a';
export const OCEAN = '#101113';
/** background of the satellite style: Esri deep-ocean tone */
export const SAT_OCEAN = '#0b1a2b';

/** Projection + a thin, calm atmosphere at the limb of the globe (fades out when zooming into a region). */
export function withProjection(s: any, proj: Projection = 'mercator'): any {
  const globe = proj === 'globe';
  return {
    ...s,
    projection: { type: globe ? 'globe' : 'mercator' },
    sky: {
      'sky-color': SPACE,
      'horizon-color': '#15191f',
      'fog-color': '#0d1014',
      'sky-horizon-blend': 0.6,
      'horizon-fog-blend': 0.6,
      'fog-ground-blend': 0.9,
      'atmosphere-blend': globe ? ['interpolate', ['linear'], ['zoom'], 0, 0.55, 4, 0.35, 7, 0] : 0,
    },
  };
}

// ---- drift animation clock (outside React: updated every frame) ----
export const anim = {
  hour: 0,
  playing: false,
  speed: 1,
  maxHour: 72,
  spread: true,
  listeners: new Set<(h: number) => void>(),
};

/** The map column already excludes the side panels (they shift the map): only the overlays inside it. */
export function mapPadding() {
  const top = (document.querySelector('[data-testid="actions"]') as HTMLElement | null)?.offsetHeight ? 72 : 48;
  return { top, bottom: 72, left: 40, right: 40 };
}

export function flyToBounds(
  b: Bounds,
  opts: { duration?: number; pitch?: number; bearing?: number; extra?: number; rightPanel?: boolean } = {},
) {
  const map = ctl.map;
  if (!map) return;
  const pad = mapPadding();
  // the context panel opens together with the fly-in (it narrows the map column): frame for the final width
  const shrink = opts.rightPanel && !document.querySelector('.app.right-open') ? panelWidth() : 0;
  const e = opts.extra ?? 0;
  pad.right += shrink;
  const cam = map.cameraForBounds(
    [
      [b[0], b[1]],
      [b[2], b[3]],
    ],
    { padding: { top: pad.top + e, bottom: pad.bottom + e, left: pad.left + e, right: pad.right + e } },
  );
  if (!cam) return;
  map.stop();
  map.flyTo({
    center: cam.center,
    zoom: Math.min(cam.zoom ?? 11, 15),
    pitch: opts.pitch ?? 0,
    bearing: opts.bearing ?? 0,
    duration: opts.duration ?? 2600,
    essential: true,
    curve: 1.3,
    easing: easeInOut,
  });
}

/** camera easing: gentle start and a long soft landing (no hard cubic snap) */
export const easeInOut = (t: number) => 0.5 - Math.cos(Math.PI * t) / 2;

export function panelWidth() {
  const v = getComputedStyle(document.documentElement).getPropertyValue('--rw');
  return parseFloat(v) || 400;
}

/** Fly to a point keeping it in the centre of the free map area. */
export function flyToPoint(lon: number, lat: number, zoom = 14, duration = 1800) {
  const map = ctl.map;
  if (!map) return;
  map.stop();
  map.flyTo({ center: [lon, lat], zoom, duration, essential: true, pitch: map.getPitch(), easing: easeInOut });
}

/**
 * Centre a place in the visible map column with ~widthM metres of context around it (spot + 1–2 km).
 * The map column already excludes both side panels, so plain symmetric padding centres it on screen.
 */
export function flyToArea(lon: number, lat: number, widthM = 2000, duration = 1800) {
  const map = ctl.map;
  if (!map) return;
  const half = widthM / 2;
  const dLat = half / 111320;
  const dLon = half / (111320 * Math.max(0.2, Math.cos((lat * Math.PI) / 180)));
  flyToBounds([lon - dLon, lat - dLat, lon + dLon, lat + dLat], { duration, pitch: map.getPitch() > 5 ? 0 : 0 });
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

// ---- basemaps (L48 / INBOX §4): online CARTO dark (GL style) and Esri imagery; offline — our Sentinel-2 crops over a
// free Natural Earth land outline. No bulk tile download or server-side caching of CARTO/Esri tiles.
export const ESRI_TILES = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
export const CARTO_DARK = 'https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json';

export const ATTRIBUTION: Record<Basemap, string> = {
  satellite: 'Tiles © Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
  dark: '© CARTO, © OpenStreetMap contributors',
  none: 'Контур суши: Natural Earth (public domain)',
};

const landLayers = (fillOpacity: any) => [
  { id: 'land', type: 'fill', source: 'land', paint: { 'fill-color': '#1a1c20', 'fill-opacity': fillOpacity } },
  {
    id: 'coast',
    type: 'line',
    source: 'land',
    paint: {
      'line-color': '#34383f',
      'line-width': ['interpolate', ['linear'], ['zoom'], 1, 0.5, 6, 1],
      'line-opacity': ['interpolate', ['linear'], ['zoom'], 7, 1, 10, 0.4],
    },
  },
];

/** «Без подложки» / offline: neutral ocean + Natural Earth 1:110m land outline (public domain, served locally). */
export function offlineStyle(proj: Projection = 'mercator'): any {
  return withProjection(
    {
      version: 8,
      name: 'offline',
      sources: { land: { type: 'geojson', data: '/land-110m.geojson', attribution: ATTRIBUTION.none } },
      layers: [{ id: 'bg', type: 'background', paint: { 'background-color': OCEAN } }, ...landLayers(['interpolate', ['linear'], ['zoom'], 7, 1, 10, 0.4])],
    },
    proj,
  );
}

export function satelliteStyle(proj: Projection): any {
  return withProjection(
    {
      version: 8,
      name: 'satellite',
      sources: { esri: { type: 'raster', tiles: [ESRI_TILES], tileSize: 256, maxzoom: 18, attribution: ATTRIBUTION.satellite } },
      layers: [
        // deep-ocean navy while tiles load (never a black hole); imagery at natural brightness — deep water in Esri
        // imagery is dark by itself, dimming it further made the open sea look black
        { id: 'bg', type: 'background', paint: { 'background-color': SAT_OCEAN } },
        { id: 'esri', type: 'raster', source: 'esri', paint: { 'raster-brightness-max': 0.96, 'raster-saturation': -0.05, 'raster-contrast': 0.04, 'raster-fade-duration': 200 } },
      ],
    },
    proj,
  );
}

let darkCache: Promise<any> | null = null;
/** CARTO dark-matter (vector), recoloured to neutral graphite. Rejects when offline. */
export function darkStyle(): Promise<any> {
  if (!darkCache) {
    darkCache = fetch(CARTO_DARK)
      .then((r) => {
        if (!r.ok) throw new Error('carto ' + r.status);
        return r.json();
      })
      .then((s) => {
        for (const l of s.layers as any[]) {
          if (l.type === 'background') l.paint = { ...l.paint, 'background-color': '#17181b' };
          if (l.id === 'water' || l['source-layer'] === 'water') if (l.type === 'fill') l.paint = { ...l.paint, 'fill-color': OCEAN };
          if (l.type === 'symbol' && l.paint) {
            l.paint['text-color'] = '#8b9098';
            l.paint['text-halo-color'] = '#0d0e10';
          }
        }
        return s;
      });
    darkCache.catch(() => (darkCache = null));
  }
  return darkCache;
}

/** Frames all regions (globe: turned to where most regions are on the visible side). */
export function fitOverview(bounds: Bounds[], duration = 2000, focus: [number, number] | undefined = ctl.overviewFocus) {
  const map = ctl.map;
  if (!map || !bounds.length) return;
  const b = unionBounds(bounds);
  const pad = mapPadding();
  if (ctl.projection === 'globe') {
    const H = map.getContainer().clientHeight;
    const r = Math.max(160, (H - pad.top - pad.bottom) * 0.5);
    const pts = bounds.map((q) => [(q[0] + q[2]) / 2, (q[1] + q[3]) / 2]);
    let lon = focus ? focus[0] : (b[0] + b[2]) / 2;
    let best = -1;
    for (let L = -180; L < 180; L += 5) {
      const n = pts.filter((q) => angDist(q, [L, 12]) <= 70).length;
      const tie = focus ? -angDist(focus, [L, 12]) / 1000 : 0;
      if (n + tie > best) {
        best = n + tie;
        lon = L;
      }
    }
    const vis = pts.filter((q) => angDist(q, [lon, 12]) <= 70);
    const lat = Math.max(-20, Math.min(20, vis.reduce((a, q) => a + q[1], 0) / Math.max(1, vis.length)));
    const zoom = Math.log2((r * 2 * Math.PI) / 512);
    const offset: [number, number] = [(pad.left - pad.right) / 2, 0];
    if (duration) map.flyTo({ center: [lon, lat], zoom, pitch: 0, bearing: 0, offset, duration, essential: true, easing: easeInOut });
    else {
      map.jumpTo({ center: [lon, lat], zoom, pitch: 0, bearing: 0 });
      map.panBy([-offset[0], 0], { duration: 0 });
    }
    return;
  }
  const cam = map.cameraForBounds(
    [
      [b[0], b[1]],
      [b[2], b[3]],
    ],
    { padding: { ...pad, top: pad.top + 40, bottom: pad.bottom + 40 }, maxZoom: 5 },
  );
  if (!cam) return;
  const opts = { center: cam.center, zoom: Math.max(0.4, Math.min(cam.zoom ?? 2, 5)), pitch: 0, bearing: 0 };
  if (duration) map.flyTo({ ...opts, duration, essential: true, easing: easeInOut });
  else map.jumpTo(opts);
}

export function angDist(a: number[], c: number[]) {
  const t = Math.PI / 180;
  const cosd = Math.sin(a[1] * t) * Math.sin(c[1] * t) + Math.cos(a[1] * t) * Math.cos(c[1] * t) * Math.cos((a[0] - c[0]) * t);
  return Math.acos(Math.max(-1, Math.min(1, cosd))) / t;
}

/** Globe: turn to a point on the far side before flying in. Returns the turn duration (0 = none). */
export function turnGlobeTo(lon: number, lat: number, duration = 2400): number {
  const map = ctl.map;
  if (!map || ctl.projection !== 'globe') return 0;
  const c = map.getCenter();
  const d = angDist([lon, lat], [c.lng, c.lat]);
  const occluded = !!(map as any).transform?.isLocationOccluded?.({ lng: lon, lat });
  if (!occluded && d <= 55) return 0;
  const pad = mapPadding();
  map.easeTo({
    center: [lon, Math.max(-30, Math.min(30, lat))],
    zoom: map.getZoom(),
    offset: [(pad.left - pad.right) / 2, 0],
    duration,
    essential: true,
    easing: easeInOut,
  });
  return duration;
}
