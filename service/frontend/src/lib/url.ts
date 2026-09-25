import type { Basemap, Camera, LayerKey, Layers } from '../types';

export const DEFAULT_LAYERS: Layers = {
  rgb: true,
  prob: false,
  detections: true,
  h3: false,
  h3_3d: false,
  zones: false,
  drift: false,
};

export interface UrlState {
  region?: string;
  date?: string;
  model?: string;
  layers?: Layers;
  camera?: Camera;
  basemap?: Basemap;
  tour?: boolean;
  compare?: string; // "regionA:date,regionB:date"
}

const LAYER_KEYS = Object.keys(DEFAULT_LAYERS) as LayerKey[];

export function readUrl(): UrlState {
  const q = new URLSearchParams(location.search);
  const s: UrlState = {};
  if (q.get('r')) s.region = q.get('r')!;
  if (q.get('d')) s.date = q.get('d')!;
  if (q.get('m')) s.model = q.get('m')!;
  const l = q.get('l');
  if (l !== null) {
    const on = new Set(l.split(',').filter(Boolean));
    s.layers = Object.fromEntries(LAYER_KEYS.map((k) => [k, on.has(k)])) as Layers;
  }
  const c = q.get('c');
  if (c) {
    const [lon, lat, zoom, pitch, bearing] = c.split(',').map(Number);
    if ([lon, lat, zoom].every((v) => Number.isFinite(v)))
      s.camera = { lon, lat, zoom, pitch: pitch || 0, bearing: bearing || 0 };
  }
  const b = q.get('b');
  if (b === 'dark' || b === 'satellite' || b === 'none') s.basemap = b;
  if (q.get('tour') === '1') s.tour = true;
  if (q.get('cmp')) s.compare = q.get('cmp')!;
  return s;
}

export function writeUrl(s: UrlState) {
  const q = new URLSearchParams();
  if (s.region) q.set('r', s.region);
  if (s.date) q.set('d', s.date);
  if (s.model) q.set('m', s.model);
  if (s.layers) q.set('l', LAYER_KEYS.filter((k) => s.layers![k]).join(','));
  if (s.camera) {
    const c = s.camera;
    q.set('c', [c.lon.toFixed(5), c.lat.toFixed(5), c.zoom.toFixed(2), c.pitch.toFixed(0), c.bearing.toFixed(0)].join(','));
  }
  if (s.basemap) q.set('b', s.basemap);
  if (s.compare) q.set('cmp', s.compare);
  const qs = q.toString().replace(/%2C/g, ',').replace(/%3A/g, ':');
  history.replaceState(null, '', `${location.pathname}${qs ? '?' + qs : ''}`);
}
