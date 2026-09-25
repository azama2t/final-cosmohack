// Map controller (imperative, outside React): MapLibre native WebGL layers only — no deck.gl, no DOM markers,
// no particles. Basemap: Esri World Imagery (attributed); the offline outline only after real tile failures.
import maplibregl from 'maplibre-gl';
import type { FC, ObsProps, ZoneProps, Geometry } from './api';
import { API_BASE } from './api';
import type { BBox } from './geo';

export const ESRI_TILES = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';
/** the same tiles through a retrying loader: on this network ~1/3 of Esri requests are reset (curl 26.09: 19/30 ok) —
 * without a retry the globe gets holes and the offline switch fires falsely */
const ESRI_RETRY = 'retry://' + ESRI_TILES.slice('https://'.length);
maplibregl.addProtocol('retry', async (params, ac) => {
  const url = 'https://' + params.url.slice('retry://'.length);
  let last: unknown = null;
  for (let i = 0; i < 3; i++) {
    if (ac.signal.aborted) break;
    try {
      const r = await fetch(url, { signal: ac.signal, mode: 'cors', credentials: 'omit' });
      if (r.ok) return { data: await r.arrayBuffer(), cacheControl: r.headers.get('Cache-Control'), expires: r.headers.get('Expires') };
      if (r.status === 404) break; // no tile at this zoom: not a network problem
      last = new Error('HTTP ' + r.status);
    } catch (e) {
      if ((e as any)?.name === 'AbortError') throw e;
      last = e;
    }
    await new Promise((res) => setTimeout(res, 120 * (i + 1)));
  }
  throw last ?? new Error('tile failed');
});
const ESRI_ATTR = '© Esri, Maxar, Earthstar Geographics';
const NE_ATTR = 'Natural Earth';
const SPACE = '#0a0c10';
const SEA = '#0b1a2b';

/** concentration colour steps, items/km² (fixed breaks: colours comparable between filters) */
export const CONC_BREAKS = [1, 10, 30, 100, 300, 1000];
export const CONC_COLORS = ['#fde8a8', '#fdc46a', '#fb9b45', '#f26b3a', '#d8433c', '#a8263f', '#6d1a44'];
export const ACCENT = '#ffb547';
export const STRIP = '#e8eaed';

export type PickKind = 'obs' | 'zone' | 'scene' | 'oil';
export type Pick = { kind: PickKind; id: string; lngLat: [number, number] };
export interface Filters {
  from: string | null; // YYYY-MM-DD
  to: string | null;
  sources: string[] | null; // null = all
  obs: boolean;
  zones: boolean;
  /** footprints of scenes that have real views (studio API) */
  scenes?: boolean;
  /** experimental oil-spill layer */
  oil?: boolean;
}
export interface Cam {
  center: [number, number];
  zoom: number;
  bearing: number;
  pitch: number;
}
export interface Overlay {
  url: string;
  /** tl, tr, br, bl */
  coords: number[][];
}
export interface SiteShape {
  id: string;
  ring: number[][];
  active: boolean;
}

const dateInt = (s: string | null | undefined) => (s ? Number(s.slice(0, 10).replace(/-/g, '')) || 0 : 0);
const EMPTY: any = { type: 'FeatureCollection', features: [] };

/** globe on the overview only; mercator from zoom 4.5 (L96: globe matrices cost fps when zoomed in).
 * ?proj=mercator | ?proj=globe | ?proj=late (5→7) — for A/B perf runs */
const PROJ_Q = new URLSearchParams(location.search).get('proj');
const PROJ: any =
  PROJ_Q === 'mercator'
    ? 'mercator'
    : PROJ_Q === 'globe'
      ? 'globe'
      : PROJ_Q === 'late'
        ? ['interpolate', ['linear'], ['zoom'], 5, 'vertical-perspective', 7, 'mercator']
        : // A/B CPU×4, 1920, median of 3 (pan/flyIn/flyOut fps): 5→7: 45/41/45; 3→4.5: 50/46/49; mercator 57/55/59
          ['interpolate', ['linear'], ['zoom'], 3, 'vertical-perspective', 4.5, 'mercator'];

function withProj(style: any): any {
  return {
    ...style,
    projection: { type: PROJ },
    sky: {
      'sky-color': SPACE,
      'horizon-color': '#1a2130',
      'fog-color': SPACE,
      'sky-horizon-blend': 0.5,
      'horizon-fog-blend': 0.5,
      'fog-ground-blend': 0.9,
      'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 0.6, 4, 0.3, 7, 0],
    },
  };
}
function satStyle(): any {
  return withProj({
    version: 8,
    name: 'esri',
    sources: {
      esri: { type: 'raster', tiles: [ESRI_RETRY], tileSize: 256, maxzoom: 18, attribution: ESRI_ATTR },
      // local Natural Earth land (77 KB, same origin): the Earth is visible at once, before the first Esri tile;
      // removed after the first full load (one source less while panning)
      land0: { type: 'geojson', data: '/land-110m.geojson' },
    },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': SEA } },
      { id: 'land0', type: 'fill', source: 'land0', paint: { 'fill-color': '#2b3326' } },
      { id: 'esri', type: 'raster', source: 'esri', paint: { 'raster-fade-duration': 150 } },
    ],
  });
}
function offlineStyle(): any {
  return withProj({
    version: 8,
    name: 'offline',
    sources: { land: { type: 'geojson', data: '/land-110m.geojson', attribution: NE_ATTR } },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': '#101318' } },
      { id: 'land', type: 'fill', source: 'land', paint: { 'fill-color': '#22262d' } },
      { id: 'coast', type: 'line', source: 'land', paint: { 'line-color': '#3a3f48', 'line-width': 0.7 } },
    ],
  });
}

/** globe radius ≈ 42 % of the map height */
export function worldZoom(h: number) {
  const r = Math.max(150, h * 0.42);
  return Math.log2((r * 2 * Math.PI) / 512);
}

function obsPoints(fc: FC<ObsProps> | null) {
  const out: any[] = [];
  for (const f of fc?.features ?? []) {
    const p = f.properties;
    let c: number[] | null = Array.isArray(p.track_center) ? p.track_center : null;
    if (!c && f.geometry?.type === 'Point') c = f.geometry.coordinates;
    if (!c) continue;
    const v = p.concentration_items_km2;
    const k = v === null || v === undefined ? 'i' : v === 0 || p.zero_scope ? 'z' : 'd';
    out.push({ type: 'Feature', geometry: { type: 'Point', coordinates: c }, properties: { id: f.id, k, c: v ?? -1, t: dateInt(p.date_utc), s: p.source_id, ...sphere(c) } });
  }
  out.sort((a, b) => a.properties.c - b.properties.c); // denser on top
  return { type: 'FeatureCollection', features: out };
}
function obsLines(fc: FC<ObsProps> | null) {
  const out: any[] = [];
  for (const f of fc?.features ?? []) {
    const g = f.geometry;
    if (!g || (g.type !== 'LineString' && g.type !== 'MultiLineString')) continue;
    out.push({ type: 'Feature', geometry: g, properties: { id: f.id, t: dateInt(f.properties.date_utc), s: f.properties.source_id } });
  }
  return { type: 'FeatureCollection', features: out };
}
function zoneFeats(fc: FC<ZoneProps> | null) {
  const polys: any[] = [];
  const pts: any[] = [];
  for (const f of fc?.features ?? []) {
    if (!f.geometry) continue;
    const p = f.properties;
    const props = { id: f.id, t: dateInt(p.datetime), ok: p.quality_decision === 'accept' ? 1 : 0 };
    polys.push({ type: 'Feature', geometry: f.geometry, properties: props });
    const ring = firstRing(f.geometry);
    if (ring) {
      let x = 0,
        y = 0;
      for (const q of ring) {
        x += q[0];
        y += q[1];
      }
      const c = [x / ring.length, y / ring.length];
      pts.push({ type: 'Feature', geometry: { type: 'Point', coordinates: c }, properties: { ...props, ...sphere(c) } });
    }
  }
  return { polys: { type: 'FeatureCollection', features: polys }, pts: { type: 'FeatureCollection', features: pts } };
}
/** unit-sphere terms for the horizon filter (globe: points behind the Earth are hidden) */
const RAD = Math.PI / 180;
function sphere(c: number[]) {
  return { sa: Math.sin(c[1] * RAD), ca: Math.cos(c[1] * RAD), lr: c[0] * RAD };
}
/** cos(angular distance to the view centre) > 0.42 (≈ 65°: points closer to the limb are drawn squashed into a white smudge) — MapLibre does not cull circles behind the globe */
function horizonExpr(lon: number, lat: number): any {
  return ['>', ['+', ['*', ['get', 'sa'], Math.sin(lat * RAD)], ['*', ['*', ['get', 'ca'], Math.cos(lat * RAD)], ['cos', ['-', ['get', 'lr'], lon * RAD]]]], 0.42];
}
const GLOBE_MAX_Z = 4.5;

function firstRing(g: Geometry): number[][] | null {
  if (g.type === 'Polygon') return g.coordinates[0];
  if (g.type === 'MultiPolygon') return g.coordinates[0]?.[0] ?? null;
  return null;
}
const stepExpr = (outs: (string | number)[]) => {
  const e: any[] = ['step', ['get', 'c'], outs[0]];
  CONC_BREAKS.forEach((b, i) => e.push(b, outs[i + 1]));
  return e;
};

type AuxId = 'sites' | 'det' | 'drift' | 'lasso' | 'oil';
const AUX: Record<AuxId, { before?: string; layers: any[] }> = {
  sites: {
    before: 'zone-fill',
    layers: [{ id: 'sites', type: 'line', source: 'sites', paint: { 'line-color': ['case', ['==', ['get', 'a'], 1], ACCENT, '#ffffff'], 'line-width': ['case', ['==', ['get', 'a'], 1], 2, 1], 'line-opacity': ['case', ['==', ['get', 'a'], 1], 0.95, 0.5], 'line-dasharray': [3, 2] } }],
  },
  det: {
    before: 'obs-sel',
    layers: [
      { id: 'det-fill', type: 'fill', source: 'det', paint: { 'fill-color': ['case', ['==', ['get', 'quality_rejected'], true], '#9aa0a8', ACCENT], 'fill-opacity': 0.5 } },
      { id: 'det-line', type: 'line', source: 'det', paint: { 'line-color': ['case', ['==', ['get', 'quality_rejected'], true], '#9aa0a8', ACCENT], 'line-width': 1.2 } },
    ],
  },
  // drift SCENARIO: rings of possible displacement (low / typical / high) around the field measurement
  drift: {
    layers: [{ id: 'drift', type: 'line', source: 'drift', paint: { 'line-color': '#9ad0ff', 'line-width': ['case', ['==', ['get', 'k'], 'typical'], 1.6, 1], 'line-opacity': ['case', ['==', ['get', 'k'], 'typical'], 0.9, 0.55], 'line-dasharray': [2, 2] } }],
  },
  // EXPERIMENTAL oil spills: fuchsia (never the warm debris palette nor the blue drift); area, not volume/mass
  oil: {
    before: 'obs-sel',
    layers: [
      { id: 'oil-fill', type: 'fill', source: 'oil', paint: { 'fill-color': '#c026d3', 'fill-opacity': 0.45 } },
      { id: 'oil-line', type: 'line', source: 'oil', paint: { 'line-color': '#f0abfc', 'line-width': 1.5 } },
      { id: 'oil-pt', type: 'circle', source: 'oil', maxzoom: 9, filter: ['==', ['geometry-type'], 'Point'], paint: { 'circle-radius': 4, 'circle-color': '#c026d3', 'circle-stroke-color': '#f0abfc', 'circle-stroke-width': 1 } },
    ],
  },
  lasso: {
    layers: [
      { id: 'lasso-fill', type: 'fill', source: 'lasso', paint: { 'fill-color': ACCENT, 'fill-opacity': 0.08 } },
      { id: 'lasso-line', type: 'line', source: 'lasso', paint: { 'line-color': ACCENT, 'line-width': 2 } },
    ],
  },
};

const OBS_LAYERS = ['obs-lines', 'obs-i', 'obs-z', 'obs-d'];
const ZONE_LAYERS = ['zone-fill', 'zone-line', 'zone-pt'];
const SCENE_LAYERS = ['scene-fill', 'scene-line'];
const HIT = ['obs-d', 'obs-z', 'obs-i', 'zone-fill', 'zone-pt', 'oil-fill', 'scene-fill'];

export interface MapHooks {
  onPick: (p: Pick | null) => void;
  onLasso: (ring: number[][]) => void;
  onOffline: (on: boolean) => void;
  onReady: () => void;
  onMoveEnd: () => void;
}

export class MapCtl {
  map: maplibregl.Map;
  private hooks: MapHooks;
  private offline = false;
  private data = {
    obsPts: EMPTY as any,
    obsLines: EMPTY as any,
    zonePolys: EMPTY as any,
    zonePts: EMPTY as any,
    sites: EMPTY as any,
    det: EMPTY as any,
    sel: null as { kind: PickKind; id: string } | null,
    scenes: EMPTY as any,
    filters: { from: null, to: null, sources: null, obs: true, zones: true } as Filters,
    overlay: { base: null, top: null } as { base: Overlay | null; top: Overlay | null },
    lasso: EMPTY as any,
    drift: EMPTY as any,
    oil: EMPTY as any,
  };
  private popup: maplibregl.Popup;
  private lassoOn = false;
  private lastTileOk = 0;
  private styleAt = performance.now();
  private errStreak = 0;
  private overlayKey: Record<string, string> = {};
  /** width of the left column over the map (the map canvas stays full-screen: no resize on collapse) */
  private padLeft = 0;

  constructor(el: HTMLElement, hooks: MapHooks, init: Cam | null, padLeft: number) {
    this.hooks = hooks;
    const h = el.clientHeight || 800;
    this.map = new maplibregl.Map({
      container: el,
      style: satStyle(),
      center: init?.center ?? [-25, 33],
      zoom: init?.zoom ?? worldZoom(h),
      bearing: init?.bearing ?? 0,
      pitch: init?.pitch ?? 0,
      maxPitch: 60,
      attributionControl: { compact: false },
      fadeDuration: 0,
      renderWorldCopies: false,
      canvasContextAttributes: { antialias: false, powerPreference: 'high-performance' },
    } as any);
    this.padLeft = padLeft;
    this.popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 12, maxWidth: '280px', className: 'pop' });
    (window as any).__map = this.map; // perf / test handle (L96)
    (window as any).__ctl = this;
    const map = this.map;
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric', maxWidth: 90 }), 'bottom-right');

    let ready = false;
    const fireReady = () => {
      if (ready) return;
      ready = true;
      performance.mark('mp3-map-ready');
      (window as any).__mapReady = true;
      hooks.onReady();
    };
    map.on('style.load', () => {
      this.overlayKey = {};
      this.build();
      setTimeout(fireReady, 4000); // hanging tiles never finish: do not wait for 'load' forever
    });
    map.on('load', fireReady);
    // the placeholder land goes once the imagery of the first view is complete
    const dropLand = () => {
      if (!map.getLayer('land0')) return;
      if (!this.lastTileOk) return void map.once('idle', dropLand);
      map.removeLayer('land0');
      map.removeSource('land0');
    };
    map.on('style.load', () => map.once('idle', dropLand));
    // horizon: debounced — a series of short moves (wheel, kinetic pan) does not re-filter after each step
    let hzT: ReturnType<typeof setTimeout> | undefined;
    map.on('movestart', () => clearTimeout(hzT));
    map.on('moveend', () => {
      clearTimeout(hzT);
      hzT = setTimeout(() => this.updateHorizon(), 300);
      hooks.onMoveEnd();
    });
    // the horizon is refreshed on moveend only: a setFilter during the move re-lays the points out in the worker
    // (A/B at CPU×4: pan 41 → 30 fps with a 250 ms refresh)
    // basemap health: several failed Esri tiles in a row, or no tile at all within 12 s → offline outline
    map.on('error', (e: any) => {
      if (this.offline || !(e?.sourceId === 'esri' || /arcgisonline/.test(String(e?.error?.url ?? e?.error?.message ?? '')))) return;
      // each error here is already 3 failed attempts; offline only if nothing arrived for 8 s as well
      if (++this.errStreak >= 6 && (!this.lastTileOk || performance.now() - this.lastTileOk > 8000)) hooks.onOffline(true);
    });
    map.on('sourcedata', (e: any) => {
      // sourcedata also fires for errored tiles: count only really loaded ones
      if (e.tile && e.sourceId === 'esri' && e.tile.state === 'loaded') {
        if (!this.lastTileOk) {
          performance.mark('mp3-first-tile');
          (window as any).__firstTile = true;
        }
        this.errStreak = 0;
        this.lastTileOk = performance.now();
      }
    });
    setInterval(() => {
      if (!this.offline && !this.lastTileOk && performance.now() - this.styleAt > 12000) hooks.onOffline(true);
    }, 3000);

    let raf = 0;
    map.on('mousemove', (e) => {
      if (this.lassoOn || raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        map.getCanvas().style.cursor = this.hit(e.point) ? 'pointer' : '';
      });
    });
    map.on('click', (e) => {
      if (this.lassoOn) return;
      const h = this.hit(e.point);
      hooks.onPick(h ? { ...h, lngLat: [e.lngLat.lng, e.lngLat.lat] } : null);
    });
    this.setupLasso();
  }

  private hit(pt: maplibregl.Point): { kind: PickKind; id: string } | null {
    const map = this.map;
    const layers = HIT.filter((l) => map.getLayer(l) && map.getLayoutProperty(l, 'visibility') !== 'none');
    if (!layers.length) return null;
    const fs = map.queryRenderedFeatures(
      [
        [pt.x - 5, pt.y - 5],
        [pt.x + 5, pt.y + 5],
      ],
      { layers },
    );
    const o = fs.find((f) => f.layer.id.startsWith('obs'));
    const z = fs.find((f) => f.layer.id.startsWith('zone'));
    const oi = fs.find((f) => f.layer.id.startsWith('oil'));
    const sc = fs.find((f) => f.layer.id.startsWith('scene'));
    const f = o ?? z ?? oi ?? sc;
    return f ? { kind: f === o ? 'obs' : f === z ? 'zone' : f === oi ? 'oil' : 'scene', id: String(f.properties?.id) } : null;
  }

  /** (re)create sources and layers after every style load */
  private build() {
    const map = this.map;
    const d = this.data;
    const src = (id: string, data: any) => {
      if (!map.getSource(id)) map.addSource(id, { type: 'geojson', data, promoteId: 'id' } as any);
    };
    src('scenes', d.scenes);
    src('zones', d.zonePolys);
    src('zone-pts', d.zonePts);
    src('obs-lines', d.obsLines);
    src('obs', d.obsPts);
    const add = (l: any) => !map.getLayer(l.id) && map.addLayer(l);
    // scene footprints (only scenes with real views): faint outline from zoom 4, the fill is only a click target
    add({ id: 'scene-fill', type: 'fill', source: 'scenes', minzoom: 4, paint: { 'fill-color': '#000', 'fill-opacity': 0 } });
    add({ id: 'scene-line', type: 'line', source: 'scenes', minzoom: 4, paint: { 'line-color': '#8fb8ff', 'line-width': 1, 'line-opacity': 0.45 } });
    add({ id: 'scene-sel', type: 'line', source: 'scenes', filter: ['==', ['get', 'id'], ''], paint: { 'line-color': ACCENT, 'line-width': 2 } });
    add({ id: 'zone-fill', type: 'fill', source: 'zones', minzoom: 7, paint: { 'fill-color': STRIP, 'fill-opacity': 0.12 } });
    add({ id: 'zone-line', type: 'line', source: 'zones', minzoom: 7, paint: { 'line-color': STRIP, 'line-width': 1.6, 'line-dasharray': [2, 1.4] } });
    add({ id: 'zone-pt', type: 'circle', source: 'zone-pts', maxzoom: 7, paint: { 'circle-radius': 5, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': STRIP, 'circle-stroke-width': 1.5, 'circle-pitch-alignment': 'map' } });
    add({ id: 'obs-lines', type: 'line', source: 'obs-lines', minzoom: 6, paint: { 'line-color': '#e8eaed', 'line-width': 1.2, 'line-opacity': 0.7 } });
    add({ id: 'obs-i', type: 'circle', source: 'obs', filter: ['==', ['get', 'k'], 'i'], paint: { 'circle-radius': 2.5, 'circle-color': '#aeb4bd', 'circle-stroke-color': '#0e1013', 'circle-stroke-width': 1 } });
    add({ id: 'obs-z', type: 'circle', source: 'obs', filter: ['==', ['get', 'k'], 'z'], paint: { 'circle-radius': 3.5, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#e8eaed', 'circle-stroke-width': 1.3 } });
    add({ id: 'obs-d', type: 'circle', source: 'obs', filter: ['==', ['get', 'k'], 'd'], paint: { 'circle-radius': stepExpr([3, 3.6, 4.3, 5, 5.8, 6.6, 7.4]), 'circle-color': stepExpr(CONC_COLORS), 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 0.8 } });
    add({ id: 'obs-sel', type: 'circle', source: 'obs', filter: ['==', ['get', 'id'], ''], paint: { 'circle-radius': 10, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': ACCENT, 'circle-stroke-width': 2 } });
    add({ id: 'zone-sel', type: 'line', source: 'zones', filter: ['==', ['get', 'id'], ''], paint: { 'line-color': ACCENT, 'line-width': 2.6 } });
    add({ id: 'zone-pt-sel', type: 'circle', source: 'zone-pts', maxzoom: 7, filter: ['==', ['get', 'id'], ''], paint: { 'circle-radius': 8, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': ACCENT, 'circle-stroke-width': 2 } });
    for (const id of Object.keys(AUX)) this.syncAux(id as AuxId);
    this.updateHorizon(true);
    this.applyFilters();
    this.applySel();
    this.applyOverlay();
  }

  private setData(id: string, data: any) {
    if (id in AUX) return this.syncAux(id as AuxId);
    (this.map.getSource(id) as maplibregl.GeoJSONSource | undefined)?.setData(data);
  }
  /** auxiliary layers exist only while they have data (A/B at CPU×4: 4 empty GeoJSON sources cost 12–19 % fps) */
  private syncAux(id: AuxId) {
    const map = this.map;
    if (!map.getLayer('obs-d')) return; // style not built yet: build() calls again
    const data = this.data[id];
    const spec = AUX[id];
    const src = map.getSource(id) as maplibregl.GeoJSONSource | undefined;
    if (data.features.length) {
      if (src) return src.setData(data);
      map.addSource(id, { type: 'geojson', data });
      const before = spec.before && map.getLayer(spec.before) ? spec.before : undefined;
      for (const l of spec.layers) map.addLayer(l, before);
    } else if (src) {
      for (const l of spec.layers) if (map.getLayer(l.id)) map.removeLayer(l.id);
      map.removeSource(id);
    }
  }
  private horizon: any = null;
  private horizonAt: number[] | null = null;
  /** globe only: hide points behind the Earth (recomputed when the centre moves > 3° or the zoom crosses the globe range) */
  private updateHorizon(force = false) {
    const map = this.map;
    const z = map.getZoom();
    if (z >= GLOBE_MAX_Z) {
      if (this.horizon !== null) {
        this.horizon = null;
        this.horizonAt = null;
        this.applyFilters();
      }
      return;
    }
    const c = map.getCenter();
    const a = this.horizonAt;
    if (!force && a && Math.abs(a[0] - c.lng) < 3 && Math.abs(a[1] - c.lat) < 3) return;
    this.horizonAt = [c.lng, c.lat];
    this.horizon = horizonExpr(c.lng, c.lat);
    if (!force) this.applyFilters();
  }

  setObs(fc: FC<ObsProps> | null) {
    this.data.obsPts = obsPoints(fc);
    this.data.obsLines = obsLines(fc);
    this.setData('obs', this.data.obsPts);
    this.setData('obs-lines', this.data.obsLines);
  }
  setZones(fc: FC<ZoneProps> | null) {
    const z = zoneFeats(fc);
    this.data.zonePolys = z.polys;
    this.data.zonePts = z.pts;
    this.setData('zones', z.polys);
    this.setData('zone-pts', z.pts);
  }
  setSites(list: SiteShape[]) {
    this.data.sites = {
      type: 'FeatureCollection',
      features: list.map((s) => ({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [s.ring] }, properties: { id: s.id, a: s.active ? 1 : 0 } })),
    };
    this.setData('sites', this.data.sites);
  }
  /** drift scenario rings (km) around a point; null clears */
  setDrift(center: number[] | null, radii: { k: string; km: number }[] = []) {
    const feats: any[] = [];
    if (center)
      for (const r of radii) {
        if (!(r.km > 0)) continue;
        const ring: number[][] = [];
        const dLat = r.km / 111.32;
        const dLon = r.km / (111.32 * Math.max(0.05, Math.cos((center[1] * Math.PI) / 180)));
        for (let i = 0; i <= 72; i++) {
          const a = (i / 72) * 2 * Math.PI;
          ring.push([center[0] + dLon * Math.cos(a), center[1] + dLat * Math.sin(a)]);
        }
        feats.push({ type: 'Feature', geometry: { type: 'LineString', coordinates: ring }, properties: { k: r.k } });
      }
    this.data.drift = { type: 'FeatureCollection', features: feats };
    this.setData('drift', this.data.drift);
  }
  /** oil spills (FeatureCollection from /api/v3/oil/spills); properties get t = date int for the date filter */
  setOil(fc: any | null) {
    this.data.oil = fc
      ? { type: 'FeatureCollection', features: (fc.features ?? []).map((f: any) => ({ ...f, properties: { ...f.properties, id: f.id ?? f.properties?.id, t: dateInt(f.properties?.date) } })) }
      : EMPTY;
    this.setData('oil', this.data.oil);
    this.applyFilters();
  }
  setDetections(fc: any | null) {
    this.data.det = fc ?? EMPTY;
    this.setData('det', this.data.det);
  }
  setScenes(list: { id: string; coords: number[][] | null; datetime: string }[]) {
    const feats: any[] = [];
    for (const s of list) {
      if (!s.coords) continue;
      feats.push({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[...s.coords, s.coords[0]]] }, properties: { id: s.id, t: dateInt(s.datetime) } });
    }
    this.data.scenes = { type: 'FeatureCollection', features: feats };
    this.setData('scenes', this.data.scenes);
  }
  setSelection(sel: { kind: PickKind; id: string } | null) {
    this.data.sel = sel;
    this.applySel();
  }
  private applySel() {
    const map = this.map;
    const s = this.data.sel;
    if (!map.getLayer('obs-sel')) return;
    map.setFilter('obs-sel', ['==', ['get', 'id'], s?.kind === 'obs' ? s.id : '']);
    map.setFilter('zone-sel', ['==', ['get', 'id'], s?.kind === 'zone' ? s.id : '']);
    map.setFilter('zone-pt-sel', ['==', ['get', 'id'], s?.kind === 'zone' ? s.id : '']);
    map.setFilter('scene-sel', ['==', ['get', 'id'], s?.kind === 'scene' ? s.id : '']);
  }
  setFilters(f: Filters) {
    this.data.filters = f;
    this.applyFilters();
  }
  private applyFilters() {
    const map = this.map;
    const f = this.data.filters;
    if (!map.getLayer('obs-d')) return;
    const cond: any[] = [];
    if (f.from) cond.push(['>=', ['get', 't'], dateInt(f.from)]);
    if (f.to) cond.push(['<=', ['get', 't'], dateInt(f.to)]);
    const src: any[] = f.sources ? [['in', ['get', 's'], ['literal', f.sources]]] : [];
    const kinds: Record<string, any> = { 'obs-i': ['==', ['get', 'k'], 'i'], 'obs-z': ['==', ['get', 'k'], 'z'], 'obs-d': ['==', ['get', 'k'], 'd'] };
    const hz = this.horizon ? [this.horizon] : [];
    for (const id of ['obs-i', 'obs-z', 'obs-d']) map.setFilter(id, ['all', kinds[id], ...cond, ...src, ...hz]);
    map.setFilter('obs-lines', cond.length + src.length ? ['all', ...cond, ...src] : null);
    for (const id of ['zone-fill', 'zone-line', ...SCENE_LAYERS]) map.setFilter(id, cond.length ? ['all', ...cond] : null);
    map.setFilter('zone-pt', cond.length + hz.length ? ['all', ...cond, ...hz] : null);
    const vis = (id: string, on: boolean) => map.getLayer(id) && map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
    for (const id of OBS_LAYERS) vis(id, f.obs);
    for (const id of ZONE_LAYERS) vis(id, f.zones);
    for (const id of SCENE_LAYERS) vis(id, f.scenes !== false);
    for (const id of ['oil-fill', 'oil-line', 'oil-pt']) {
      if (!map.getLayer(id)) continue;
      vis(id, f.oil !== false);
      const base = id === 'oil-pt' ? [['==', ['geometry-type'], 'Point']] : [];
      map.setFilter(id, base.length + cond.length ? ['all', ...base, ...cond] : null);
    }
  }

  /** scene view as image layers under the vector layers: base (Снимок/Спектральный) + optional transparent top
   * (Детекция/Качество). Only these ≤ 2 image sources ever exist — never one per scene (L96: image sources cost fps). */
  setOverlay(base: Overlay | null, top: Overlay | null = null) {
    this.data.overlay = { base, top };
    this.applyOverlay();
  }
  private applyOverlay() {
    const map = this.map;
    if (!map.getLayer('obs-d')) return;
    const { base, top } = this.data.overlay;
    const put = (id: 'ov' | 'ov2', o: Overlay | null, before: string, resampling: 'linear' | 'nearest') => {
      const key = o ? `${o.url}|${o.coords.flat().join(',')}` : '';
      if (key === this.overlayKey[id]) return;
      this.overlayKey[id] = key;
      if (!o) {
        if (map.getLayer(id)) map.removeLayer(id);
        if (map.getSource(id)) map.removeSource(id);
        return;
      }
      const full = o.url.startsWith('http') ? o.url : API_BASE + o.url;
      const studio = full.includes('/api/v3/studio/');
      const url = studio ? full + (full.includes('?') ? '&' : '?') + 'px=512' : full;
      const coords = o.coords as any;
      if (studio) {
        const img = new Image();
        img.onload = () => {
          if (this.overlayKey[id] !== key) return; // replaced meanwhile
          (map.getSource(id) as maplibregl.ImageSource | undefined)?.updateImage({ url: full, coordinates: coords });
        };
        img.src = full;
      }
      const s = map.getSource(id) as maplibregl.ImageSource | undefined;
      if (s) s.updateImage({ url, coordinates: coords });
      else {
        map.addSource(id, { type: 'image', url, coordinates: coords });
        map.addLayer({ id, type: 'raster', source: id, paint: { 'raster-fade-duration': 0, 'raster-resampling': resampling } }, before);
      }
    };
    put('ov', base, 'scene-fill', 'linear');
    put('ov2', top, 'scene-fill', 'nearest'); // mask pixels stay crisp
  }

  setOffline(on: boolean) {
    if (on === this.offline) return;
    this.offline = on;
    this.styleAt = performance.now();
    this.lastTileOk = 0;
    this.errStreak = 0;
    this.map.setStyle(on ? offlineStyle() : satStyle(), { diff: false });
  }

  showPopup(lngLat: [number, number], html: string) {
    this.popup.setLngLat(lngLat).setHTML(html).addTo(this.map);
  }
  hidePopup() {
    this.popup.remove();
  }

  setPadLeft(px: number) {
    this.padLeft = px;
  }
  camera(): Cam {
    const c = this.map.getCenter();
    return { center: [c.lng, c.lat], zoom: this.map.getZoom(), bearing: this.map.getBearing(), pitch: this.map.getPitch() };
  }
  flyCam(c: Cam, duration = 1200) {
    this.map.stop();
    this.map.flyTo({ center: c.center, zoom: c.zoom, bearing: c.bearing, pitch: c.pitch, duration, essential: true });
  }
  fitBBox(b: BBox, maxZoom = 12, duration = 1400) {
    const w = Math.max(b[2] - b[0], 0.01);
    const h = Math.max(b[3] - b[1], 0.01);
    const cx = (b[0] + b[2]) / 2,
      cy = (b[1] + b[3]) / 2;
    const cam = this.map.cameraForBounds(
      [
        [cx - w / 2, cy - h / 2],
        [cx + w / 2, cy + h / 2],
      ],
      { padding: { top: 60, bottom: 60, left: this.padLeft + 60, right: 60 }, maxZoom },
    );
    if (!cam) return;
    this.map.stop();
    this.map.flyTo({ center: cam.center, zoom: cam.zoom, bearing: 0, pitch: 0, duration, essential: true });
  }

  // ---------------------------------------------------------------- lasso: drag to outline an area
  startLasso() {
    this.lassoOn = true;
    this.map.dragPan.disable();
    this.map.getCanvas().style.cursor = 'crosshair';
    this.hidePopup();
  }
  stopLasso() {
    this.lassoOn = false;
    this.map.dragPan.enable();
    this.map.getCanvas().style.cursor = '';
    this.setArea(null);
  }
  /** the outlined area stays drawn while it is the current candidate */
  setArea(ring: number[][] | null) {
    this.data.lasso = ring ? { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: { type: 'Polygon', coordinates: [ring] }, properties: {} }] } : EMPTY;
    this.setData('lasso', this.data.lasso);
  }
  get lassoActive() {
    return this.lassoOn;
  }
  private setupLasso() {
    const map = this.map;
    let pts: number[][] = [];
    let drawing = false;
    let raf = 0;
    const draw = () => {
      raf = 0;
      const ring = pts.length > 2 ? [...pts, pts[0]] : pts;
      this.data.lasso = {
        type: 'FeatureCollection',
        features: pts.length > 1 ? [{ type: 'Feature', geometry: pts.length > 2 ? { type: 'Polygon', coordinates: [ring] } : { type: 'LineString', coordinates: pts }, properties: {} }] : [],
      };
      this.setData('lasso', this.data.lasso);
    };
    map.on('mousedown', (e) => {
      if (!this.lassoOn) return;
      e.preventDefault();
      drawing = true;
      pts = [[e.lngLat.lng, e.lngLat.lat]];
    });
    map.on('mousemove', (e) => {
      if (!this.lassoOn || !drawing) return;
      const last = map.project(pts[pts.length - 1] as [number, number]);
      if (Math.hypot(last.x - e.point.x, last.y - e.point.y) < 4) return;
      pts.push([e.lngLat.lng, e.lngLat.lat]);
      if (!raf) raf = requestAnimationFrame(draw);
    });
    const finish = () => {
      if (!this.lassoOn || !drawing) return;
      drawing = false;
      const ring = pts.length > 2 ? [...pts, pts[0]] : null;
      this.stopLasso();
      if (ring) this.hooks.onLasso(ring);
    };
    map.on('mouseup', finish);
    map.getCanvas().addEventListener('mouseleave', finish);
  }
}
