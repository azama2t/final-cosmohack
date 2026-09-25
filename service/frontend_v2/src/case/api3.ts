// API v3 client (docs/CONTRACTS_V3.md v3.1). Case mode reads ONLY from here.
// Rules: null is «unknown» (never 0); errors come as {error:{code,message,details}}; an unreachable service is
// ApiErr('UNAVAILABLE'); mocks only with ?mock=1 / VITE_MOCK=1 (see mock.ts), never as a silent fallback.
import { mockGet } from './mock';
import { ApiErr } from './err';

const qp = new URLSearchParams(location.search);
/** ?api=http://host:port — another backend; default: same origin */
export const API_BASE = (qp.get('api') ?? (import.meta as any).env?.VITE_API ?? '').replace(/\/$/, '');
export const MOCK = qp.get('mock') === '1' || (import.meta as any).env?.VITE_MOCK === '1';

export { ApiErr };

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

/** GET JSON. Throws ApiErr: UNAVAILABLE (no service / 5xx without body), or the API error code with its message. */
export async function get<T>(path: string, params: Params = {}, signal?: AbortSignal): Promise<T> {
  if (MOCK) return mockGet<T>(path, params);
  let r: Response;
  try {
    r = await fetch(apiUrl(path, params), { signal, cache: 'no-cache' });
  } catch (e: any) {
    if (e?.name === 'AbortError') throw e;
    throw new ApiErr('UNAVAILABLE', 'Сервис недоступен', 0);
  }
  let j: any = null;
  try {
    j = await r.json();
  } catch {
    /* not JSON */
  }
  if (!r.ok || j === null) {
    if (j?.error) throw new ApiErr(j.error.code ?? 'ERROR', j.error.message ?? `HTTP ${r.status}`, r.status, j.error.details);
    throw new ApiErr(r.status >= 500 || r.status === 0 ? 'UNAVAILABLE' : 'HTTP', r.status >= 500 ? 'Сервис недоступен' : `HTTP ${r.status}`, r.status);
  }
  return j as T;
}

export async function send<T>(method: 'POST' | 'DELETE', path: string, body?: unknown): Promise<T | null> {
  if (MOCK) throw new ApiErr('MOCK', 'В демо-режиме запросы не сохраняются на сервере');
  let r: Response;
  try {
    r = await fetch(`${API_BASE}${path}`, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiErr('UNAVAILABLE', 'Сервис недоступен');
  }
  if (r.status === 204) return null;
  const j = await r.json().catch(() => null);
  if (!r.ok) throw new ApiErr(j?.error?.code ?? 'HTTP', j?.error?.message ?? `HTTP ${r.status}`, r.status, j?.error?.details);
  return j as T;
}

// ------------------------------------------------------------------ types (only the fields the UI reads)
export interface Labeled {
  id: string;
  label: string;
  color?: string;
}
export interface QualityClass extends Labeled {
  present?: boolean;
  note?: string;
}
export interface Meta {
  version: string;
  units: Record<string, string>;
  date_range: { min: string; max: string } | null;
  scene_date_range: { min: string; max: string } | null;
  quality_classes: QualityClass[];
  detection_statuses: Labeled[];
  concentration_statuses: Labeled[];
  sources: (Labeled & { n?: number })[];
  measurement_profiles: (Labeled & { size_class?: string })[];
  target_scopes: Labeled[];
  record_types: Labeled[];
  reject_reasons?: Labeled[];
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
  model?: any;
}

export interface ObsProps {
  kind: 'measurement';
  sample_id: string;
  event_id: string;
  source_id: string;
  region: string;
  sea_area?: string;
  record_type: string;
  date_utc: string | null;
  time_start_utc?: string | null;
  time_end_utc?: string | null;
  target_scope: string;
  target_scope_label?: string;
  is_plastic_scope?: boolean;
  measurement_profile: string | null;
  size_class: string | null;
  concentration_items_km2: number | null;
  concentration_g_km2: number | null;
  sampled_area_km2: number | null;
  transect: { lat_start: number | null; lon_start: number | null; lat_end: number | null; lon_end: number | null; length_km: number | null; width_m: number | null } | null;
  position_role?: string;
  quality_flags: string[];
  zero_scope: string | null;
  litter_item_type?: string;
  material?: string;
  notes?: string;
  source_doi?: string | null;
  linked_scenes?: string[];
  // L62f (may be absent in older builds)
  source_short?: string | null;
  source_license?: string | null;
  ci95_lo?: number | null;
  ci95_hi?: number | null;
  density_numerator_items?: number | null;
  items_count?: number | null;
  model_estimate?: (Interval & { split?: string | null; note?: string | null }) | number | null;
}

export interface Interval {
  value: number | null;
  lo: number | null;
  hi: number | null;
  interval?: string | null;
  unit: string;
  measurement_profile?: string | null;
  size_class?: string | null;
  target_scope?: string | null;
  method?: string | null;
  model?: string | null;
}

export interface ZoneProps {
  kind: 'model_estimate';
  zone_id: string;
  scene_id: string | null;
  mission: string | null;
  datetime: string | null;
  status: string;
  detection_status: string;
  concentration_status: string;
  status_reason?: string | null;
  concentration_reason?: string | null;
  area_km2: number | null;
  area_basis?: string | null;
  detected_area_m2: number | null;
  detector: { prob_mean: number | null; prob_max: number | null; n_pixels: number | null; n_objects?: number | null; threshold?: number | null } | null;
  field_estimate: Interval | null;
  concentration: Interval | null;
  quality: { valid_fraction: number | null; cloud_fraction: number | null; glint_fraction: number | null; land_fraction?: number | null; coverage?: number | null; flags: string[] } | null;
  support: {
    n_linked_samples: number;
    linked_sample_ids: string[];
    nearest_measurement_km: number | null;
    field_items_km2?: number | null;
    field_sample_id?: string | null;
    field_target_scope?: string | null;
    field_scope_label?: string | null;
  } | null;
  event_id?: string;
  pair_status?: string | null;
  pair_reject_reasons?: string[] | null;
  pair_sync?: string | null;
  pair_drift_shift_km?: number | null;
  pair_tolerance_km?: number | null;
  strip_area_raster_km2?: number | null;
  // L62h
  layer_kind?: string | null;
  detection_reason?: string | null;
  suspicious_pixels?: { n_objects?: number | null; area_m2?: number | null; prob_max?: number | null; [k: string]: any } | null;
}

export interface ZoneDetail extends Feat<ZoneProps> {
  crop_url: string | null;
  prob_crop_url: string | null;
  linked_observations: FC<ObsProps> | null;
  explain: string[];
  detections?: FC<DetProps> | null;
}

export interface DetProps {
  kind: 'detection';
  det_id: string;
  zone_id: string;
  n_pixels: number | null;
  area_m2: number | null;
  prob_max: number | null;
  in_strip: boolean | null;
}

export interface Pair {
  pair_id: string;
  sample_id: string;
  event_id: string;
  source_id: string;
  scene_id: string | null;
  mission: string | null;
  scene_datetime: string | null;
  obs_datetime: string | null;
  dt_hours: number | null;
  distance_km: number | null;
  drift_shift_km: number | null;
  tolerance_km?: number | null;
  drift_scenarios_km?: Record<string, number> | null;
  dt_drift_hours?: number | null;
  status_without_drift?: string | null;
  geometry: Geometry | null;
  cloud_pct_local: number | null;
  valid_fraction_local: number | null;
  status: 'accepted' | 'rejected';
  reject_reasons: string[];
  scene_cloud_pct?: number | null;
  catalog?: string | null;
  time_known?: boolean | null;
  registry_note?: string | null;
  quality_decision?: string | null;
}

export interface Scene {
  scene_id: string;
  mission: string;
  source?: string;
  collection?: string;
  datetime: string;
  footprint: Geometry | null;
  bounds: [number, number, number, number] | null;
  footprint_note?: string;
  cloud_pct: number | null;
  valid_water_fraction: number | null;
  preview_url: string | null;
  quality_url: string | null;
  mask_url?: string | null;
  n_zones?: number;
  n_linked_samples?: number;
  status: string;
  reject_reasons: string[];
}

export interface SavedQuery {
  query_id: string;
  name: string;
  created_at: string;
  query: ApiQuery;
  local?: boolean;
}
/** contract query object (section 8) */
export interface ApiQuery {
  bbox: number[] | null;
  date_from: string | null;
  date_to: string | null;
  statuses: string[];
  sources: string[];
  profiles: string[];
  scopes?: string[];
  layers: string[];
  scene_id: string | null;
}
