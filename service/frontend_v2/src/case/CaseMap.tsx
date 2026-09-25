// Case-mode map: MapLibre native (WebGL) layers only — observations, transects, zones, scene previews and quality
// masks. Basemap: the user's choice (default Esri satellite on the globe); offline outline only while tiles fail.
import { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import type { Basemap, Projection } from '../types';
import { ctl, darkStyle, ESRI_TILES, CARTO_DARK, offlineStyle, satelliteStyle, withProjection } from '../map/controller';
import { attachStars } from '../map/stars';
import type { FC, Feat, Geometry, Meta, ObsProps, Scene, ZoneProps } from './api3';
import { API_BASE } from './api3';
import { SEQ } from '../lib/style';

/** concentration colour breaks, items/km² (log-like, fixed so the colours are comparable between filters) */
export const CONC_BREAKS = [1, 10, 30, 100, 300, 1000];
export const CONC_COLORS = SEQ.slice(1).map((c) => `rgb(${c[0]},${c[1]},${c[2]})`);
export const ACCENT = '#ffc53d';
/** world view: the Atlantic side of the globe (Sargasso, North Sea, Black Sea; the Pacific source via «Акватория») */
export const WORLD_CENTER: [number, number] = [-42, 30];
/** globe radius ≈ 45 % of the map height */
export function worldZoom(h: number) {
  const r = Math.max(160, (h - 120) * 0.5);
  return Math.log2((r * 2 * Math.PI) / 512);
}

export interface Pick {
  kind: 'obs' | 'zone';
  id: string;
}
export interface HoverInfo {
  x: number;
  y: number;
  kind: 'obs' | 'zone';
  id: string;
}

export interface CaseMapProps {
  meta: Meta;
  basemap: Basemap;
  offline: boolean;
  onOffline: (on: boolean) => void;
  projection: Projection;
  obs: FC<ObsProps> | null;
  zones: FC<ZoneProps> | null;
  scenes: Scene[];
  layers: { obs: boolean; zones: boolean; scenes: boolean; quality: boolean };
  selected: Pick | null;
  /** highlighted pair: observation geometry + scene footprint */
  pairHl: { geom: Geometry | null; scene: Scene | null } | null;
  initialCamera: { lon: number; lat: number; zoom: number } | null;
  onPick: (p: Pick | null) => void;
  onHover: (h: HoverInfo | null) => void;
  onCamera: (c: { lon: number; lat: number; zoom: number }) => void;
  /** the map can take data (style loaded; tiles may still be loading) */
  onReady: () => void;
}

function syncStyle(b: Basemap, proj: Projection): any | null {
  if (b === 'satellite') return satelliteStyle(proj);
  if (b === 'none') return offlineStyle(proj);
  return null;
}

function probeOnline(b: Basemap): Promise<boolean> {
  if (b === 'dark') return fetch(CARTO_DARK, { cache: 'no-store' }).then((r) => r.ok, () => false);
  return new Promise((res) => {
    const img = new Image();
    img.onload = () => res(true);
    img.onerror = () => res(false);
    img.src = ESRI_TILES.replace('{z}', '0').replace('{y}', '0').replace('{x}', '0') + `?probe=${Date.now()}`;
  });
}

// ---- data → map features ----
function obsPoints(fc: FC<ObsProps> | null) {
  const feats: any[] = [];
  for (const f of fc?.features ?? []) {
    const p = f.properties;
    let c: number[] | null = null;
    if (f.geometry?.type === 'Point') c = f.geometry.coordinates;
    else if (f.geometry?.type === 'LineString') {
      const cs = f.geometry.coordinates;
      c = [(cs[0][0] + cs[cs.length - 1][0]) / 2, (cs[0][1] + cs[cs.length - 1][1]) / 2];
    }
    if (!c) continue;
    const v = p.concentration_items_km2;
    const k = v === null || v === undefined ? 'i' : v === 0 ? 'z' : 'd';
    feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: c }, properties: { id: f.id, k, c: v ?? -1 } });
  }
  // denser values on top
  feats.sort((a, b) => a.properties.c - b.properties.c);
  return { type: 'FeatureCollection', features: feats };
}

function obsLines(fc: FC<ObsProps> | null) {
  const feats: any[] = [];
  for (const f of fc?.features ?? []) {
    const t = f.properties.transect;
    if (!t || t.lon_start === null || t.lat_start === null || t.lon_end === null || t.lat_end === null) continue;
    if (t.lon_start === t.lon_end && t.lat_start === t.lat_end) continue;
    feats.push({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: [[t.lon_start, t.lat_start], [t.lon_end, t.lat_end]] },
      properties: { id: f.id },
    });
  }
  return { type: 'FeatureCollection', features: feats };
}

export function geomCenter(g: Geometry | null): [number, number] | null {
  const b = geomBounds(g);
  return b ? [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] : null;
}
export function geomBounds(g: Geometry | null): [number, number, number, number] | null {
  if (!g) return null;
  let b: [number, number, number, number] = [180, 90, -180, -90];
  const walk = (c: any) => {
    if (typeof c[0] === 'number') {
      b = [Math.min(b[0], c[0]), Math.min(b[1], c[1]), Math.max(b[2], c[0]), Math.max(b[3], c[1])];
    } else c.forEach(walk);
  };
  walk(g.coordinates);
  return b[0] <= b[2] ? b : null;
}

function zoneFeatures(fc: FC<ZoneProps> | null) {
  const polys: any[] = [];
  const pts: any[] = [];
  for (const f of fc?.features ?? []) {
    if (!f.geometry) continue;
    const props = { id: f.id, ds: f.properties.detection_status };
    polys.push({ type: 'Feature', geometry: f.geometry, properties: props });
    const c = geomCenter(f.geometry);
    if (c) pts.push({ type: 'Feature', geometry: { type: 'Point', coordinates: c }, properties: props });
  }
  return { polys: { type: 'FeatureCollection', features: polys }, pts: { type: 'FeatureCollection', features: pts } };
}

const EMPTY = { type: 'FeatureCollection', features: [] };

/** SDF icons (drawn once; recoloured by icon-color): diamond = single object without density; square = zone at small zoom */
function makeIcon(kind: 'diamond' | 'square'): { width: number; height: number; data: Uint8Array } {
  const S = 32;
  const cv = document.createElement('canvas');
  cv.width = cv.height = S;
  const g = cv.getContext('2d')!;
  g.fillStyle = '#fff';
  g.strokeStyle = '#fff';
  if (kind === 'diamond') {
    g.beginPath();
    g.moveTo(S / 2, 4);
    g.lineTo(S - 4, S / 2);
    g.lineTo(S / 2, S - 4);
    g.lineTo(4, S / 2);
    g.closePath();
    g.fill();
  } else {
    g.lineWidth = 6;
    g.setLineDash([7, 4]);
    g.strokeRect(5, 5, S - 10, S - 10);
  }
  const d = g.getImageData(0, 0, S, S);
  return { width: S, height: S, data: new Uint8Array(d.data.buffer) };
}

function statusMatch(meta: Meta): any {
  const m: any[] = ['match', ['get', 'ds']];
  for (const s of meta.detection_statuses) m.push(s.id, (s.color ?? '#868e96').slice(0, 7));
  m.push('#868e96');
  return m;
}

const stepExpr = (outs: (string | number)[]) => {
  const e: any[] = ['step', ['get', 'c'], outs[0]];
  CONC_BREAKS.forEach((b, i) => e.push(b, outs[i + 1]));
  return e;
};

export default function CaseMap(p: CaseMapProps) {
  const el = useRef<HTMLDivElement>(null);
  const stars = useRef<HTMLDivElement>(null);
  const halo = useRef<HTMLDivElement>(null);
  const props = useRef(p);
  props.current = p;
  const eff: Basemap = p.offline ? 'none' : p.basemap;
  const appliedStyle = useRef<Basemap | null>(null);
  const selMarker = useRef<maplibregl.Marker | null>(null);
  const sceneKeys = useRef<string[]>([]);

  /** (re)create sources + layers after every style load; then update data */
  const sync = () => {
    const map = ctl.map;
    if (!map || !(map as any).style?._loaded) return;
    const cur = props.current;
    const meta = cur.meta;
    if (!map.hasImage('mp-diamond')) map.addImage('mp-diamond', makeIcon('diamond'), { sdf: true, pixelRatio: 2 });
    if (!map.hasImage('mp-square')) map.addImage('mp-square', makeIcon('square'), { sdf: true, pixelRatio: 2 });
    const src = (id: string, data: any) => {
      const s = map.getSource(id) as maplibregl.GeoJSONSource | undefined;
      if (s) s.setData(data);
      else map.addSource(id, { type: 'geojson', data, promoteId: 'id' } as any);
    };
    const z = zoneFeatures(cur.zones);
    src('c-obs', obsPoints(cur.obs));
    src('c-obs-lines', obsLines(cur.obs));
    src('c-zones', z.polys);
    src('c-zone-pts', z.pts);
    const fp = cur.scenes.filter((s) => s.footprint).map((s) => ({ type: 'Feature', geometry: s.footprint, properties: { id: s.scene_id } }));
    src('c-scene-fp', { type: 'FeatureCollection', features: fp });
    const hl = cur.pairHl;
    src('c-pair', hl?.geom ? { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: hl.geom, properties: {} }] } : EMPTY);
    src(
      'c-pair-scene',
      hl?.scene?.footprint ? { type: 'FeatureCollection', features: [{ type: 'Feature', geometry: hl.scene.footprint, properties: {} }] } : EMPTY,
    );

    const add = (l: any) => {
      if (!map.getLayer(l.id)) map.addLayer(l);
    };
    const statusCol = statusMatch(meta);
    add({ id: 'c-scene-fp', type: 'line', source: 'c-scene-fp', paint: { 'line-color': '#e7e8ea', 'line-opacity': 0.45, 'line-width': 1 } });
    add({ id: 'c-obs-lines', type: 'line', source: 'c-obs-lines', paint: { 'line-color': '#e7e8ea', 'line-width': 1.4, 'line-opacity': 0.75 } });
    add({ id: 'c-zones-fill', type: 'fill', source: 'c-zones', paint: { 'fill-color': statusCol, 'fill-opacity': 0.3 } });
    add({
      id: 'c-zones-line',
      type: 'line',
      source: 'c-zones',
      paint: { 'line-color': statusCol, 'line-width': 2.6, 'line-dasharray': [2, 1.2] },
    });
    add({
      id: 'c-zones-sel',
      type: 'line',
      source: 'c-zones',
      filter: ['==', ['get', 'id'], ''],
      paint: { 'line-color': '#ffffff', 'line-width': 3, 'line-dasharray': [2, 1.5] },
    });
    add({
      id: 'c-zone-pts',
      type: 'symbol',
      source: 'c-zone-pts',
      maxzoom: 10.5,
      layout: { 'icon-image': 'mp-square', 'icon-size': 1.15, 'icon-allow-overlap': true, 'icon-ignore-placement': true },
      paint: { 'icon-color': statusCol, 'icon-halo-color': 'rgba(11,12,14,0.85)', 'icon-halo-width': 1.5 },
    });
    add({
      id: 'c-obs-items',
      type: 'symbol',
      source: 'c-obs',
      filter: ['==', ['get', 'k'], 'i'],
      layout: { 'icon-image': 'mp-diamond', 'icon-size': 0.55, 'icon-allow-overlap': true, 'icon-ignore-placement': true },
      paint: { 'icon-color': '#c8ccd2', 'icon-halo-color': '#0b0c0e', 'icon-halo-width': 1 },
    });
    add({
      id: 'c-obs-zero',
      type: 'circle',
      source: 'c-obs',
      filter: ['==', ['get', 'k'], 'z'],
      paint: { 'circle-radius': 4, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#e7e8ea', 'circle-stroke-width': 1.5 },
    });
    add({
      id: 'c-obs-dens',
      type: 'circle',
      source: 'c-obs',
      filter: ['==', ['get', 'k'], 'd'],
      paint: {
        'circle-radius': stepExpr([3.5, 4.2, 5, 5.8, 6.6, 7.4, 8.2]),
        'circle-color': stepExpr(CONC_COLORS),
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 1,
      },
    });
    add({
      id: 'c-obs-sel',
      type: 'circle',
      source: 'c-obs',
      filter: ['==', ['get', 'id'], ''],
      paint: { 'circle-radius': 11, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2 },
    });
    add({ id: 'c-pair-scene', type: 'line', source: 'c-pair-scene', paint: { 'line-color': ACCENT, 'line-width': 2 } });
    add({ id: 'c-pair', type: 'line', source: 'c-pair', filter: ['==', ['geometry-type'], 'LineString'], paint: { 'line-color': ACCENT, 'line-width': 3 } });
    add({
      id: 'c-pair-pt',
      type: 'circle',
      source: 'c-pair',
      filter: ['==', ['geometry-type'], 'Point'],
      paint: { 'circle-radius': 9, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': ACCENT, 'circle-stroke-width': 2.5 },
    });

    // scene previews + quality masks (image overlays, below the vector layers)
    const want: { key: string; url: string; bounds: number[]; kind: 'rgb' | 'q' }[] = [];
    for (const s of cur.scenes) {
      if (!s.bounds) continue;
      if (cur.layers.scenes && s.preview_url) want.push({ key: `c-img-rgb-${s.scene_id}`, url: API_BASE + s.preview_url, bounds: s.bounds, kind: 'rgb' });
      if (cur.layers.quality && s.quality_url) want.push({ key: `c-img-q-${s.scene_id}`, url: API_BASE + s.quality_url, bounds: s.bounds, kind: 'q' });
    }
    const wantKeys = new Set(want.map((w) => w.key));
    for (const k of sceneKeys.current)
      if (!wantKeys.has(k)) {
        if (map.getLayer(k)) map.removeLayer(k);
        if (map.getSource(k)) map.removeSource(k);
      }
    // rgb first, then quality on top of it; both under the zones
    for (const w of [...want.filter((x) => x.kind === 'rgb'), ...want.filter((x) => x.kind === 'q')]) {
      if (map.getSource(w.key)) continue;
      const [x0, y0, x1, y1] = w.bounds;
      map.addSource(w.key, { type: 'image', url: w.url, coordinates: [[x0, y1], [x1, y1], [x1, y0], [x0, y0]] });
      map.addLayer({ id: w.key, type: 'raster', source: w.key, paint: { 'raster-fade-duration': 0, 'raster-opacity': w.kind === 'q' ? 0.55 : 1 } }, 'c-scene-fp');
    }
    // keep quality above rgb when an rgb layer is added later
    for (const w of want) if (w.kind === 'q' && map.getLayer(w.key)) map.moveLayer(w.key, 'c-scene-fp');
    sceneKeys.current = [...wantKeys];

    // visibility + selection
    const vis = (id: string, on: boolean) => map.getLayer(id) && map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
    for (const id of ['c-obs-lines', 'c-obs-items', 'c-obs-zero', 'c-obs-dens', 'c-obs-sel']) vis(id, cur.layers.obs);
    for (const id of ['c-zones-fill', 'c-zones-line', 'c-zones-sel', 'c-zone-pts']) vis(id, cur.layers.zones);
    vis('c-scene-fp', cur.layers.scenes);
    const sel = cur.selected;
    map.setFilter('c-zones-sel', ['==', ['get', 'id'], sel?.kind === 'zone' ? sel.id : '']);
    map.setFilter('c-obs-sel', ['==', ['get', 'id'], sel?.kind === 'obs' ? sel.id : '']);
  };

  // ---- init ----
  useEffect(() => {
    ctl.projection = p.projection;
    const init = p.initialCamera;
    const map = new maplibregl.Map({
      container: el.current!,
      style: syncStyle(eff, p.projection) ?? offlineStyle(p.projection),
      center: init ? [init.lon, init.lat] : WORLD_CENTER,
      zoom: init ? init.zoom : worldZoom(el.current!.clientHeight),
      attributionControl: false,
      fadeDuration: 200,
      maxPitch: 0,
      dragRotate: false,
      canvasContextAttributes: { antialias: true, powerPreference: 'high-performance' } as any,
    } as any);
    ctl.map = map;
    (window as any).__caseMap = map; // test / debug handle
    ctl.overlay = null;
    ctl.render = () => {};
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric', maxWidth: 100 }), 'bottom-left');
    appliedStyle.current = syncStyle(eff, p.projection) ? eff : null;
    let streak = 0;
    let lastOk = 0;
    let styleAt = performance.now();
    const isOnlineTile = (e: any) => e?.sourceId === 'esri' || /arcgisonline|cartocdn|carto\.com/.test(String(e?.error?.url ?? e?.error?.message ?? ''));
    map.on('error', (e: any) => {
      if (!isOnlineTile(e) || props.current.offline || props.current.basemap === 'none') return;
      streak++;
      if (streak >= 4) props.current.onOffline(true);
    });
    map.on('sourcedata', (e: any) => {
      if (e.tile && e.sourceId === 'esri') {
        streak = 0;
        lastOk = performance.now();
      }
    });
    const watchdog = setInterval(() => {
      const cur = props.current;
      if (cur.offline || cur.basemap !== 'satellite' || appliedStyle.current !== 'satellite') return;
      // no basemap tile at all within 12 s (failed or hanging requests) → temporary offline outline
      if (!lastOk && performance.now() - styleAt > 12000) cur.onOffline(true);
    }, 3000);
    (map as any).__resetStyleClock = () => {
      styleAt = performance.now();
      lastOk = 0;
      streak = 0;
    };
    let readyT: ReturnType<typeof setTimeout> | undefined;
    let isReady = false;
    const ready = () => {
      if (isReady) return;
      isReady = true;
      window.__mapReady = true;
      props.current.onReady();
    };
    map.on('style.load', () => {
      sceneKeys.current = [];
      sync();
      // tiles of a hanging network never finish: do not wait for 'load' forever
      if (!readyT) readyT = setTimeout(ready, 6000);
    });
    map.on('load', () => {
      sync();
      ready();
    });
    map.on('moveend', () => {
      const c = map.getCenter();
      props.current.onCamera({ lon: c.lng, lat: c.lat, zoom: map.getZoom() });
    });
    const HIT = ['c-obs-dens', 'c-obs-zero', 'c-obs-items', 'c-zones-fill', 'c-zone-pts'];
    const hit = (pt: maplibregl.PointLike) => {
      const layers = HIT.filter((l) => map.getLayer(l));
      if (!layers.length) return null;
      const box: [maplibregl.PointLike, maplibregl.PointLike] = [
        [(pt as any).x - 4, (pt as any).y - 4],
        [(pt as any).x + 4, (pt as any).y + 4],
      ];
      const fs = map.queryRenderedFeatures(box, { layers });
      const o = fs.find((f) => f.layer.id.startsWith('c-obs'));
      const z = fs.find((f) => f.layer.id.startsWith('c-zone'));
      const f = o ?? z;
      if (!f) return null;
      return { kind: (f === o ? 'obs' : 'zone') as 'obs' | 'zone', id: String(f.properties?.id) };
    };
    let raf = 0;
    map.on('mousemove', (e) => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const h = hit(e.point);
        map.getCanvas().style.cursor = h ? 'pointer' : '';
        props.current.onHover(h ? { ...h, x: e.point.x, y: e.point.y } : null);
      });
    });
    map.on('mouseout', () => props.current.onHover(null));
    map.on('click', (e) => props.current.onPick(hit(e.point)));
    const detachStars = attachStars(stars.current!, halo.current!);
    return () => {
      clearInterval(watchdog);
      clearTimeout(readyT);
      detachStars();
      selMarker.current?.remove();
      map.remove();
      ctl.map = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- basemap ----
  useEffect(() => {
    const map = ctl.map!;
    let cancelled = false;
    if (appliedStyle.current === eff) return;
    const apply = (st: any) => {
      appliedStyle.current = eff;
      (map as any).__resetStyleClock?.();
      map.setStyle(st, { diff: false });
    };
    const st = syncStyle(eff, ctl.projection);
    if (st) apply(st);
    else
      darkStyle()
        .then((s) => !cancelled && apply(withProjection(s, ctl.projection)))
        .catch(() => !cancelled && props.current.onOffline(true));
    return () => {
      cancelled = true;
    };
  }, [eff]);

  useEffect(() => {
    if (!p.offline || p.basemap === 'none') return;
    let alive = true;
    const probe = () => probeOnline(props.current.basemap).then((ok) => alive && ok && props.current.onOffline(false));
    const t = setInterval(probe, 20000);
    window.addEventListener('online', probe);
    return () => {
      alive = false;
      clearInterval(t);
      window.removeEventListener('online', probe);
    };
  }, [p.offline, p.basemap]);

  // ---- projection ----
  const firstProj = useRef(true);
  useEffect(() => {
    const map = ctl.map!;
    ctl.projection = p.projection;
    if (firstProj.current) {
      firstProj.current = false;
      return;
    }
    const apply = () => {
      const s = withProjection({}, p.projection);
      map.setProjection(s.projection);
      try {
        map.setSky(s.sky);
      } catch {
        /* no sky */
      }
    };
    if ((map as any).style?._loaded) apply();
    else map.once('style.load', apply);
  }, [p.projection]);

  // ---- data ----
  useEffect(sync, [p.obs, p.zones, p.scenes, p.layers, p.selected, p.pairHl, p.meta]);

  // ---- scenes with a quality mask but no RGB preview: say so (the translucent mask alone is not a picture) ----
  const noPrevMarkers = useRef<maplibregl.Marker[]>([]);
  useEffect(() => {
    const map = ctl.map!;
    noPrevMarkers.current.forEach((m) => m.remove());
    noPrevMarkers.current = [];
    if (!p.layers.quality && !p.layers.scenes) return;
    for (const s of p.scenes) {
      if (!s.bounds || s.preview_url || !s.quality_url) continue;
      const d = document.createElement('div');
      d.className = 'c-noprev';
      d.textContent = `превью ${s.mission?.startsWith('Landsat') ? 'Landsat' : s.mission ?? ''} нет`;
      d.setAttribute('data-testid', 'no-preview-tag');
      noPrevMarkers.current.push(new maplibregl.Marker({ element: d, anchor: 'bottom-left', offset: [0, -2] }).setLngLat([s.bounds[0], s.bounds[3]]).addTo(map));
    }
    const vis = () => {
      const on = map.getZoom() >= 9;
      noPrevMarkers.current.forEach((m) => (m.getElement().style.visibility = on ? 'visible' : 'hidden'));
    };
    vis();
    map.on('zoom', vis);
    return () => {
      map.off('zoom', vis);
    };
  }, [p.scenes, p.layers.quality, p.layers.scenes]);

  // ---- label next to the selected feature: «измерение» / «оценка модели» ----
  useEffect(() => {
    const map = ctl.map!;
    selMarker.current?.remove();
    selMarker.current = null;
    const s = p.selected;
    if (!s) return;
    let at: [number, number] | null = null;
    if (s.kind === 'obs') {
      const f = p.obs?.features.find((x) => x.id === s.id);
      at = f ? geomCenter(f.geometry) : null;
    } else {
      const f = p.zones?.features.find((x) => x.id === s.id);
      at = f ? geomCenter(f.geometry) : null;
    }
    if (!at) return;
    const d = document.createElement('div');
    d.className = `c-sel-tag ${s.kind}`;
    d.textContent = s.kind === 'obs' ? 'измерение' : 'оценка модели';
    d.setAttribute('data-testid', 'sel-tag');
    selMarker.current = new maplibregl.Marker({ element: d, anchor: 'left', offset: [14, 0] }).setLngLat(at).addTo(map);
  }, [p.selected, p.obs, p.zones]);

  return (
    <>
      <div className="space" aria-hidden>
        <div ref={stars} className="stars" />
        <div ref={halo} className="halo" />
      </div>
      <div ref={el} className="map" data-testid="map" />
    </>
  );
}

export function flyToBox(b: [number, number, number, number], opts: { duration?: number; maxZoom?: number; pad?: number } = {}) {
  const map = ctl.map;
  if (!map) return;
  const pad = opts.pad ?? 60;
  const w = Math.max(b[2] - b[0], 0.002),
    h = Math.max(b[3] - b[1], 0.002);
  const bb: [[number, number], [number, number]] = [
    [b[0] - (w < 0.01 ? 0.01 : 0), b[1] - (h < 0.01 ? 0.01 : 0)],
    [b[2] + (w < 0.01 ? 0.01 : 0), b[3] + (h < 0.01 ? 0.01 : 0)],
  ];
  const cam = map.cameraForBounds(bb, { padding: { top: pad + 40, bottom: pad + 40, left: pad, right: pad } });
  if (!cam) return;
  map.stop();
  map.flyTo({ center: cam.center, zoom: Math.min(cam.zoom ?? 8, opts.maxZoom ?? 13), duration: opts.duration ?? 1600, essential: true, curve: 1.3 });
}

export function featBounds(f: Feat<any> | null | undefined) {
  return f ? geomBounds(f.geometry) : null;
}
