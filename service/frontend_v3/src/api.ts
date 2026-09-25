// API v3 client (docs/CONTRACTS_V3.md). v3 UI reads ONLY real data from here: no mocks, no silent fallbacks.
// null = unknown (never drawn as 0). Errors: {error:{code,message,details}}; unreachable service = ApiErr('UNAVAILABLE').

const qp = new URLSearchParams(location.search);
/** ?api=http://host:port — another backend; default: same origin */
export const API_BASE = (qp.get('api') ?? '').replace(/\/$/, '');

export class ApiErr extends Error {
  code: string;
  status: number;
  constructor(code: string, message: string, status = 0) {
    super(message);
    this.code = code;
    this.status = status;
  }
}

export type Params = Record<string, string | number | null | undefined | string[]>;

export function qs(params: Params): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === '') continue;
    if (Array.isArray(v)) {
      if (v.length) q.set(k, v.join(','));
    } else q.set(k, String(v));
  }
  return q.toString().replace(/%2C/g, ',');
}

export const apiUrl = (path: string, params: Params = {}) => {
  const s = qs(params);
  return `${API_BASE}${path}${s ? '?' + s : ''}`;
};

export async function get<T>(path: string, params: Params = {}, signal?: AbortSignal): Promise<T> {
  let r: Response;
  try {
    r = await fetch(apiUrl(path, params), { signal });
  } catch (e: any) {
    if (e?.name === 'AbortError') throw e;
    throw new ApiErr('UNAVAILABLE', 'Сервис недоступен');
  }
  let j: any = null;
  try {
    j = await r.json();
  } catch {
    /* not JSON */
  }
  if (!r.ok || j === null) {
    if (j?.error) throw new ApiErr(j.error.code ?? 'ERROR', j.error.message ?? `HTTP ${r.status}`, r.status);
    throw new ApiErr(r.status >= 500 ? 'UNAVAILABLE' : 'HTTP', r.status >= 500 ? 'Сервис недоступен' : `HTTP ${r.status}`, r.status);
  }
  return j as T;
}

// ------------------------------------------------------------------ types (only the fields the UI reads)
export interface Labeled {
  id: string;
  label: string;
  color?: string;
}
export interface Meta {
  version: string;
  date_range: { min: string; max: string } | null;
  scene_date_range: { min: string; max: string } | null;
  quality_classes: (Labeled & { present?: boolean })[];
  detection_statuses: Labeled[];
  sources: (Labeled & { n?: number })[];
  measurement_profiles: (Labeled & { size_class?: string })[];
  reject_reasons?: Labeled[];
  summary?: { n_strips?: number; n_confirmed_pairs?: number; text?: string } | null;
}
export interface Geometry {
  type: string;
  coordinates: any;
}
export interface Feat<P> {
  type: 'Feature';
  id: string;
  geometry: Geometry | null;
  properties: P;
}
export interface FC<P> {
  type: 'FeatureCollection';
  count: number;
  total?: number;
  empty_reason: string | null;
  features: Feat<P>[];
}
export interface Interval {
  value: number | null;
  lo: number | null;
  hi: number | null;
  unit?: string;
  model?: string | null;
  note?: string | null;
}
export interface ObsProps {
  sample_id: string;
  event_id: string;
  source_id: string;
  region: string;
  record_type: string;
  date_utc: string | null;
  time_start_utc?: string | null;
  target_scope: string;
  target_scope_label?: string;
  is_plastic_scope?: boolean;
  measurement_profile: string | null;
  size_class: string | null;
  concentration_items_km2: number | null;
  sampled_area_km2: number | null;
  zero_scope: string | null;
  quality_flags: string[];
  source_short?: string | null;
  source_doi?: string | null;
  ci95_lo?: number | null;
  ci95_hi?: number | null;
  field_estimate?: Interval | null;
  linked_scenes?: string[];
  linked_scenes_unsynced?: string[];
  track_center?: [number, number] | null;
  evidence_level?: string | null;
}
export interface ZoneProps {
  zone_id: string;
  scene_id: string | null;
  mission: string | null;
  datetime: string | null;
  detection_status: string;
  status_reason?: string | null;
  area_km2: number | null;
  pair_status?: string | null;
  pair_reject_reasons?: string[] | null;
  pair_sync?: string | null;
  pair_drift_shift_km?: number | null;
  pair_tolerance_km?: number | null;
  quality_decision?: string | null;
  quality_reject_label?: string | null;
  quality?: { valid_fraction: number | null; cloud_fraction: number | null; glint_fraction: number | null } | null;
  suspicious_pixels?: { n_objects?: number | null; area_m2?: number | null; prob_max?: number | null; quality_rejected?: boolean } | null;
  support?: { n_linked_samples: number; linked_sample_ids: string[] } | null;
  event_id?: string;
  evidence_level?: string | null;
}
export interface ZoneDetail extends Feat<ZoneProps> {
  detections?: FC<{ det_id: string; n_pixels: number | null; area_m2: number | null; prob_max: number | null; in_strip: boolean | null; quality_rejected?: boolean }> | null;
}
export interface Pair {
  pair_id: string;
  sample_id: string;
  scene_id: string | null;
  mission: string | null;
  scene_datetime: string | null;
  obs_datetime: string | null;
  dt_hours: number | null;
  drift_shift_km: number | null;
  tolerance_km?: number | null;
  drift_scenarios_km?: { low?: number; typical?: number; high?: number } | null;
  dt_drift_hours?: number | null;
  status: 'accepted' | 'rejected';
  reject_reasons: string[];
  scene_cloud_pct?: number | null;
  catalog?: string | null;
  quality_decision?: string | null;
  evidence_level?: string | null;
}
export interface Scene {
  scene_id: string;
  mission: string;
  source?: string;
  collection?: string;
  datetime: string;
  bounds: [number, number, number, number] | null;
  cloud_pct: number | null;
  preview_url: string | null;
  quality_url: string | null;
  mask_url?: string | null;
  prob_url?: string | null;
  status: string;
  reject_reasons: string[];
  evidence_level?: string | null;
}

// ------------------------------------------------------------------ studio views (L95: /api/v3/studio/..., optional)
export type ViewKind = 'rgb' | 'spectral' | 'detection' | 'quality';
export const VIEW_LABEL: Record<ViewKind, string> = { rgb: 'Снимок', spectral: 'Спектральный', detection: 'Детекция', quality: 'Качество' };
export const VIEW_ORDER: ViewKind[] = ['rgb', 'spectral', 'detection', 'quality'];

/** one scene as the studio sees it: date + source always known, only the views that really exist */
export interface StudioScene {
  id: string;
  /** product id (same as /api/v3/scenes scene_id when known) — used to merge the two lists */
  key: string;
  datetime: string;
  mission: string;
  source: string | null;
  bounds: [number, number, number, number] | null;
  /** 4 corners tl,tr,br,bl (MapLibre image source order) */
  coords: number[][] | null;
  cloud_pct: number | null;
  views: Partial<Record<ViewKind, string>>;
  /** views drawn over «Снимок» (transparent PNG) */
  overlay: Partial<Record<ViewKind, boolean>>;
  viewSource: Partial<Record<ViewKind, string>>;
  /** default variant of a view as served (spectral: fdi | swir; detection: lgbm | mdd) */
  viewVariant: Partial<Record<ViewKind, string>>;
  level: string | null;
  status: string | null;
  from: 'studio' | 'scenes';
  sourceType?: string;
  sourceLabel?: string;
  note?: string | null;
  quality?: { decision?: string | null; reason?: string | null; valid_water_frac?: number | null; cloud_frac?: number | null } | null;
  detector?: {
    threshold?: number | null;
    n_det?: number | null;
    n_above_threshold?: number | null;
    prob_max?: number | null;
    weights?: string | null;
    harmonize?: string | null;
    note?: string | null;
    /** L95 (01:56): current detector mode only */
    run?: boolean | null;
    pixels?: number | null;
    objects?: number | null;
    /** pairs / search: inside the observation strip */
    strip?: { objects?: number | null; pixels?: number | null } | null;
    label?: string | null;
  } | null;
  fieldIds?: string[];
  region?: string | null;
  eventId?: string | null;
}

const LEVELS = new Set(['A', 'B', 'C', 'D']);
export function levelOf(x: any): string | null {
  const v = x?.evidence_level ?? x?.level ?? null;
  return typeof v === 'string' && LEVELS.has(v.trim().toUpperCase()) ? v.trim().toUpperCase() : null;
}
const cornersOf = (b: [number, number, number, number] | null) =>
  b
    ? [
        [b[0], b[3]],
        [b[2], b[3]],
        [b[2], b[1]],
        [b[0], b[1]],
      ]
    : null;

/** /api/v3/scenes record → studio scene (views from the real PNGs of the case API) */
export function fromScene(s: Scene): StudioScene {
  const views: Partial<Record<ViewKind, string>> = {};
  if (s.preview_url) views.rgb = s.preview_url;
  if (s.mask_url || s.prob_url) views.detection = (s.prob_url ?? s.mask_url)!;
  if (s.quality_url) views.quality = s.quality_url;
  return {
    id: s.scene_id,
    key: s.scene_id,
    datetime: s.datetime,
    mission: s.mission,
    source: [s.source, s.collection].filter(Boolean).join(' / ') || null,
    bounds: s.bounds,
    coords: cornersOf(s.bounds),
    cloud_pct: s.cloud_pct,
    views,
    overlay: { detection: true, quality: true },
    viewSource: {},
    viewVariant: {},
    level: levelOf(s),
    status: s.status ?? null,
    from: 'scenes',
  };
}

/** GET /api/v3/studio/scenes record (contract 3.8, L95) → studio scene; only available_views */
export function fromStudio(r: any): StudioScene | null {
  const id = r?.id ?? r?.scene_id;
  if (!id) return null;
  const views: Partial<Record<ViewKind, string>> = {};
  const overlay: Partial<Record<ViewKind, boolean>> = {};
  const viewSource: Partial<Record<ViewKind, string>> = {};
  const viewVariant: Partial<Record<ViewKind, string>> = {};
  const avail: string[] = Array.isArray(r.available_views) ? r.available_views : [];
  for (const k of VIEW_ORDER) {
    const v = r.views?.[k];
    if (!avail.includes(k) || !v?.available || !v.url) continue;
    views[k] = v.url;
    overlay[k] = !!v.overlay;
    if (v.source) viewSource[k] = v.source;
    if (Array.isArray(v.variants) && v.variants.length) viewVariant[k] = v.variants[0];
  }
  const b = Array.isArray(r.bounds) && r.bounds.length === 4 ? (r.bounds as [number, number, number, number]) : null;
  const c = Array.isArray(r.coordinates) && r.coordinates.length === 4 ? r.coordinates : cornersOf(b);
  return {
    id,
    key: r.product_id ?? id,
    datetime: r.datetime ?? r.date ?? '',
    mission: r.platform ?? r.mission ?? '',
    source: [r.catalog, r.collection].filter(Boolean).join(' / ') || null,
    bounds: b,
    coords: c,
    cloud_pct: typeof r.quality?.cloud_frac === 'number' ? r.quality.cloud_frac : null,
    views,
    overlay,
    viewSource,
    viewVariant,
    level: levelOf(r),
    status: null,
    from: 'studio',
    sourceType: r.source_type,
    sourceLabel: r.source_label,
    note: r.footprint_note ?? null,
    quality: r.quality ?? null,
    detector: r.detector ?? null,
    fieldIds: r.field?.sample_ids ?? [],
    region: r.region ?? null,
    eventId: r.event_id ?? null,
  };
}

let studioProbe: Promise<boolean> | null = null;
/** is the studio router (L95) mounted? Asked once via /openapi.json — a blind 404 would be a console error */
export function hasStudioApi(): Promise<boolean> {
  if (!studioProbe)
    studioProbe = get<any>('/openapi.json')
      .then((j) => !!j?.paths?.['/api/v3/studio/scenes'])
      .catch(() => false);
  return studioProbe;
}

/** studio scenes (optionally in a bbox); null when the studio API is not there (then /api/v3/scenes only).
 * Duplicates of one product (pair + search copies) are folded into the one with more views. */
export async function studioScenes(bbox: number[] | null, signal?: AbortSignal): Promise<StudioScene[] | null> {
  if (!(await hasStudioApi())) return null;
  try {
    const j: any = await get('/api/v3/studio/scenes', { bbox: bbox ? bbox.map((x) => x.toFixed(4)).join(',') : null, limit: 2000 }, signal);
    const list = ((j.scenes ?? []) as any[]).map(fromStudio).filter((x): x is StudioScene => !!x && Object.keys(x.views).length > 0);
    const best = new Map<string, StudioScene>();
    const rank = (x: StudioScene) => Object.keys(x.views).length * 10 + (x.sourceType === 'pair' ? 1 : 0);
    for (const x of list) {
      const o = best.get(x.key);
      if (!o || rank(x) > rank(o)) best.set(x.key, { ...x, level: x.level ?? o?.level ?? null });
      else if (!o.level && x.level) o.level = x.level;
    }
    return [...best.values()];
  } catch (e: any) {
    if (e?.name === 'AbortError') throw e;
    return null;
  }
}

// ------------------------------------------------------------------ oil (contract 3.9, L101) — EXPERIMENTAL layer
/** set to true when docs/LOG.md says «oil API готов»; ?oil=1 turns it on for testing, ?oil=0 off */
const OIL_READY = true; // L101 26.09 02:30 «oil API готов»; the layer itself is OFF by default (experimental)
const OIL_Q = new URLSearchParams(location.search).get('oil');
export const OIL_ON = OIL_Q === '1' ? true : OIL_Q === '0' ? false : OIL_READY;

export interface OilMeta {
  class_label: string;
  experimental: boolean;
  unit_note: string;
  color?: { fill?: string; fill_opacity?: number; line?: string; line_width?: number } | null;
  metrics?: { test?: { f1?: number; precision?: number; recall?: number } | null } | null;
  limitations?: string[];
}
export interface OilProps {
  id: string;
  scene_id: string | null;
  scene_key: string | null;
  date: string | null;
  region: string | null;
  source: string | null;
  area_km2: number | null;
  scene_frac: number | null;
  experimental: boolean;
  unit_note?: string;
}
let oilProbe: Promise<boolean> | null = null;
export function hasOilApi(): Promise<boolean> {
  if (!OIL_ON) return Promise.resolve(false);
  if (!oilProbe)
    oilProbe = get<any>('/openapi.json')
      .then((j) => !!j?.paths?.['/api/v3/oil/spills'])
      .catch(() => false);
  return oilProbe;
}
