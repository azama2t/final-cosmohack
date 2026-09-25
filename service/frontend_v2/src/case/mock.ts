import { ApiErr } from './err';

// EXPLICIT demo mode only (?mock=1 or VITE_MOCK=1): a few invented records to check the layout without a backend.
// The UI shows a «Демо-данные» banner whenever this is used. Never used as a fallback when the API is down.
const meta = {
  version: 'mock',
  units: { concentration: 'items/km2', area: 'km2' },
  date_range: { min: '2018-08-01', max: '2018-08-31' },
  scene_date_range: { min: '2018-08-01', max: '2018-08-31' },
  quality_classes: [
    { id: 'valid', label: 'Пригодная вода', color: '#00000000', present: true },
    { id: 'cloud', label: 'Облако', color: '#ffffff99', present: true },
    { id: 'land', label: 'Суша', color: '#495057cc', present: true },
    { id: 'nodata', label: 'Нет данных', color: '#00000066', present: true },
  ],
  detection_statuses: [
    { id: 'detected', label: 'Обнаружено', color: '#d9480f' },
    { id: 'not_detected', label: 'Не обнаружено', color: '#2b8a3e' },
    { id: 'insufficient_data', label: 'Недостаточно данных', color: '#868e96' },
  ],
  concentration_statuses: [
    { id: 'measured_nearby', label: 'Есть полевое измерение рядом', color: '#1c7ed6' },
    { id: 'research_estimate', label: 'Исследовательская оценка', color: '#7048e8' },
    { id: 'unavailable', label: 'Концентрация недоступна', color: '#adb5bd' },
  ],
  sources: [{ id: 'DEMO', label: 'Демо-акватория', n: 3 }],
  measurement_profiles: [{ id: 'S2_visual_GT2', label: 'Визуально с судна, >2 см', size_class: '>2 cm' }],
  target_scopes: [
    { id: 'total_plastic', label: 'Весь пластик' },
    { id: 'all_litter', label: 'Весь мусор (не только пластик)' },
  ],
  record_types: [{ id: 'transect_density', label: 'Плотность на трансекте' }],
  reject_reasons: [{ id: 'CLOUD', label: 'Облачность над точкой' }],
};

const obs = (id: string, lon: number, lat: number, c: number | null, scope = 'total_plastic') => ({
  type: 'Feature',
  id,
  geometry: { type: 'Point', coordinates: [lon, lat] },
  properties: {
    kind: 'measurement',
    sample_id: id,
    event_id: 'DEMO:' + id,
    source_id: 'DEMO',
    region: 'Демо',
    record_type: 'transect_density',
    date_utc: '2018-08-12',
    target_scope: scope,
    target_scope_label: scope === 'all_litter' ? 'Весь мусор (не только пластик)' : 'Весь пластик',
    measurement_profile: 'S2_visual_GT2',
    size_class: '>2 cm',
    concentration_items_km2: c,
    concentration_g_km2: null,
    sampled_area_km2: 0.2,
    transect: null,
    quality_flags: [],
    zero_scope: c === 0 ? 'demo' : null,
    source_doi: null,
    linked_scenes: [],
  },
});

const zone = (id: string, lon: number, lat: number, det: string, conc: any) => ({
  type: 'Feature',
  id,
  geometry: { type: 'Polygon', coordinates: [[[lon - 0.02, lat - 0.02], [lon + 0.02, lat - 0.02], [lon + 0.02, lat + 0.02], [lon - 0.02, lat + 0.02], [lon - 0.02, lat - 0.02]]] },
  properties: {
    kind: 'model_estimate',
    zone_id: id,
    scene_id: 'DEMO_SCENE',
    mission: 'Sentinel-2',
    datetime: '2018-08-12T19:03:21Z',
    status: det,
    detection_status: det,
    concentration_status: conc ? 'research_estimate' : 'unavailable',
    status_reason: 'демо',
    concentration_reason: conc ? null : 'демо: концентрация не оценивалась',
    area_km2: 0.84,
    detected_area_m2: det === 'detected' ? 12400 : null,
    detector: { prob_mean: 0.7, prob_max: 0.9, n_pixels: 124 },
    field_estimate: null,
    concentration: conc,
    quality: { valid_fraction: 0.9, cloud_fraction: 0.05, glint_fraction: null, flags: [] },
    support: { n_linked_samples: 1, linked_sample_ids: ['DEMO-1'], nearest_measurement_km: 0.5 },
  },
});

const DATA: Record<string, any> = {
  '/api/v3/meta': meta,
  '/api/v3/observations': {
    type: 'FeatureCollection',
    count: 3,
    empty_reason: null,
    features: [obs('DEMO-1', -140, 32, 60), obs('DEMO-2', -139.5, 32.3, 0), obs('DEMO-3', -139.8, 31.7, 15, 'all_litter')],
  },
  '/api/v3/zones': {
    type: 'FeatureCollection',
    count: 2,
    empty_reason: null,
    features: [
      zone('Z-DEMO-1', -140.1, 32.05, 'detected', { value: 60, lo: 22, hi: 140, interval: 'p10-p90', unit: 'items/km2', measurement_profile: 'S2_visual_GT2', size_class: '>2 cm', target_scope: 'total_plastic' }),
      zone('Z-DEMO-2', -139.6, 31.8, 'not_detected', null),
    ],
  },
  '/api/v3/scenes': { count: 0, empty_reason: 'демо: снимков нет', scenes: [] },
  '/api/v3/pairs': { count: 0, empty_reason: 'демо: пар нет', pairs: [] },
  '/api/v3/metrics': { detector: { main: { name: 'демо', f1: 0.5 }, baseline: { name: 'демо', f1: 0.1 } }, concentration: {} },
  '/api/v3/queries': { queries: [] },
};

export async function mockGet<T>(path: string, _params: unknown): Promise<T> {
  await new Promise((r) => setTimeout(r, 60));
  if (DATA[path]) return JSON.parse(JSON.stringify(DATA[path])) as T;
  const m = path.match(/^\/api\/v3\/(zones|observations)\/(.+)$/);
  if (m) {
    const coll = DATA[`/api/v3/${m[1]}`];
    const f = coll.features.find((x: any) => x.id === m[2]);
    if (f) return { ...f, pairs: [], explain: ['демо-данные'], linked_observations: null, crop_url: null, prob_crop_url: null } as T;
  }
  throw new ApiErr('NOT_FOUND', 'Нет в демо-данных', 404);
}
