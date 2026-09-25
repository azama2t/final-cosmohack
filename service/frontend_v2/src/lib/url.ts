import type { Basemap, Camera, LayerKey, Layers, Projection } from '../types';

export const DEFAULT_LAYERS: Layers = {
  rgb: true,
  prob: false,
  detections: true,
  h3: false,
  h3_3d: false,
  zones: false,
  drift: false,
  currents: false,
  wind: false,
  osm: false,
  sources: false,
  artifacts: false,
};

export interface UrlState {
  region?: string;
  date?: string;
  model?: string;
  layers?: Layers;
  camera?: Camera;
  basemap?: Basemap;
  projection?: Projection;
  route?: boolean;
  tour?: boolean;
  compare?: string;
  confirmed?: boolean;
  tab?: 'review';
  zone?: number;
  place?: string;
  /** selected detection id */
  det?: string;
  /** drift forecast check mode */
  check?: boolean;
  /** right panel view of the region: findings | zones | history | drift */
  view?: string;
  /** world overview (no region) was chosen explicitly */
  world?: boolean;
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
    if ([lon, lat, zoom].every((v) => Number.isFinite(v))) s.camera = { lon, lat, zoom, pitch: pitch || 0, bearing: bearing || 0 };
  }
  const b = q.get('b');
  if (b === 'dark' || b === 'satellite' || b === 'none') s.basemap = b;
  const pr = q.get('pr');
  if (pr === 'globe' || pr === 'map') s.projection = pr === 'globe' ? 'globe' : 'mercator';
  if (q.get('route') === '1') s.route = true;
  if (q.get('tour') === '1') s.tour = true;
  if (q.get('cmp')) s.compare = q.get('cmp')!;
  if (q.get('cf') === '1') s.confirmed = true;
  if (q.get('tab') === 'review') s.tab = 'review';
  const z = Number(q.get('zone'));
  if (Number.isInteger(z) && z > 0) s.zone = z;
  if (q.get('place')) s.place = q.get('place')!;
  if (q.get('det')) s.det = q.get('det')!;
  if (q.get('check') === '1') s.check = true;
  if (q.get('v')) s.view = q.get('v')!;
  if (q.get('world') === '1') s.world = true;
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
  if (s.projection) q.set('pr', s.projection === 'globe' ? 'globe' : 'map');
  if (s.route) q.set('route', '1');
  if (s.compare) q.set('cmp', s.compare);
  if (s.confirmed) q.set('cf', '1');
  if (s.tab) q.set('tab', s.tab);
  if (s.zone) q.set('zone', String(s.zone));
  if (s.place) q.set('place', s.place);
  if (s.det) q.set('det', s.det);
  if (s.check) q.set('check', '1');
  if (s.view) q.set('v', s.view);
  if (s.world) q.set('world', '1');
  // keep ?tour=1 out of the shared link (the link reproduces the view, not the tour)
  const qs = q.toString().replace(/%2C/g, ',').replace(/%3A/g, ':');
  history.replaceState(null, '', `${location.pathname}${qs ? '?' + qs : ''}`);
}
