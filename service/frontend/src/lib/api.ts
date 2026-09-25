// L20: optional backend endpoints (zones «why», crops, place card, calendar, human review).
// Every call is guarded by the list of paths from /openapi.json: an endpoint that the running backend does not
// have is never requested (no 404 in the console), and the UI hides the element or falls back to a client-side
// calculation from the /data files.
import { apiAvailable } from './data';

let pathsP: Promise<Set<string>> | null = null;

/** Paths of the running FastAPI (from /openapi.json); empty set without a backend. */
export function apiPaths(): Promise<Set<string>> {
  if (!pathsP) {
    pathsP = apiAvailable()
      .then(async (ok) => {
        if (!ok) return new Set<string>();
        const r = await fetch('/openapi.json', { cache: 'no-cache' });
        if (r.status !== 200) return new Set<string>();
        const j = await r.json();
        return new Set<string>(Object.keys(j?.paths ?? {}));
      })
      .catch(() => new Set<string>());
  }
  return pathsP;
}

export const hasApi = async (path: string) => (await apiPaths()).has(path);

const qs = (params: Record<string, string | number | null | undefined>) => {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== '') q.set(k, String(v));
  return q.toString();
};

/** GET JSON from an optional endpoint; null if the backend has no such path or answers non-200. */
export async function apiGet<T>(
  path: string,
  params: Record<string, string | number | null | undefined> = {},
  /** openapi path template when `path` has a filled-in parameter (e.g. /api/review/retrain/{job}) */
  template?: string,
): Promise<T | null> {
  if (!(await hasApi(template ?? path))) return null;
  try {
    const q = qs(params);
    const r = await fetch(q ? `${path}?${q}` : path);
    if (r.status !== 200) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

export interface PostResult<T> {
  ok: boolean;
  status: number;
  data: T | null;
  error?: string;
}

/** POST JSON; returns status + parsed body (FastAPI errors: {detail}). */
export async function apiPost<T>(path: string, body: unknown = {}): Promise<PostResult<T>> {
  if (!(await hasApi(path))) return { ok: false, status: 0, data: null, error: 'нет эндпоинта ' + path };
  try {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const j = await r.json().catch(() => null);
    if (!r.ok) return { ok: false, status: r.status, data: null, error: (j && (j.detail || j.error)) || `HTTP ${r.status}` };
    return { ok: true, status: r.status, data: j as T };
  } catch (e) {
    return { ok: false, status: 0, data: null, error: String(e) };
  }
}

export const apiUrl = (path: string, params: Record<string, string | number | null | undefined>) => `${path}?${qs(params)}`;

// ---------------------------------------------------------------- response types (service/place.py, review.py)
export interface WhyTerm {
  name: string;
  label: string;
  value: number;
  weight: number;
  contribution: number;
}
export interface Why {
  formula: string;
  formula_text: string;
  score: number;
  terms: WhyTerm[];
  text: string;
}
export interface ZoneApi {
  region: string;
  date: string;
  model: string;
  threshold: number;
  rank: number | null;
  h3: string;
  lon: number;
  lat: number;
  index: number | null;
  area_m2: number | null;
  flagged_water_px: number | null;
  n_detections: number;
  mean_prob: number | null;
  max_prob: number | null;
  repeat_dates: number | null;
  n_dates: number;
  observed_frac: number | null;
  cloud_frac: number | null;
  quality?: { haze?: boolean; glint_or_haze?: boolean; note?: string } | null;
  reason?: string;
  n_confirmed?: number;
  why: Why;
  crop: string;
  crop_false_color: string | null;
  place: string;
  pdf: string;
}

export type PlaceStatus = 'found' | 'clean' | 'no_observation' | 'no_image';
export interface PlaceRow {
  date: string;
  scene_id?: string;
  cloud_frac: number | null;
  quality?: { haze?: boolean; glint_or_haze?: boolean; note?: string } | null;
  model: string;
  status: PlaceStatus;
  index: number | null;
  observed_frac?: number | null;
  flagged_water_px?: number | null;
  n_detections: number;
  max_prob?: number | null;
  zone_rank?: number | null;
  n_confirmed?: number;
  other_models?: Record<string, { n_detections: number }>;
  crop?: string;
}
export interface PlaceApi {
  region: string;
  region_name?: string;
  h3: string;
  lon: number;
  lat: number;
  model: string;
  model_name?: string;
  threshold: number;
  summary: {
    n_dates: number;
    n_observed: number;
    n_found: number;
    first_found: string | null;
    last_found: string | null;
    n_detections_total: number;
    max_index: number | null;
  };
  history: PlaceRow[];
  formula?: string;
  formula_text?: string;
  sources: { name: string; url?: string; license?: string }[];
  limitations: string[];
  pdf?: string;
}

export type CalStatus = 'detected' | 'clean' | 'unreliable' | 'no_image';
export interface CalRow {
  date: string;
  model: string;
  scene_id?: string;
  cloud_frac: number | null;
  quality_flags?: string[];
  n_detections: number;
  n_confirmed?: number;
  observed_frac_water?: number | null;
  status: CalStatus;
  reason: string;
}

export interface ReviewItem {
  id: string;
  region: string;
  date: string;
  model: string;
  lon: number;
  lat: number;
  max_prob: number | null;
  mean_prob: number | null;
  area_m2: number | null;
  threshold: number;
  confirmed?: boolean | null;
  confirmed_by?: string | null;
  reasons: string[];
  reason: string;
  priority: number;
  scene_id?: string;
  crop_rgb: string;
  crop_false_color: string | null;
  label?: string;
}
export interface ReviewQueue {
  region: string;
  region_name?: string;
  model: string | null;
  n_total: number;
  n_labeled: number;
  labels: string[];
  labels_ru: Record<string, string>;
  rules?: Record<string, string>;
  items: ReviewItem[];
}
export interface LabelRec {
  kind: string;
  id: string;
  region: string;
  date: string;
  model: string;
  lon: number;
  lat: number;
  label: string | null;
  note?: string | null;
  provenance?: { user?: string; ts?: string; app_version?: string; source_scene_id?: string; max_prob?: number | null };
}
export interface RetrainJob {
  id: string;
  status: 'running' | 'done' | 'error' | 'unknown';
  started?: string;
  n_labels?: number;
  returncode?: number;
  log_tail?: string;
  result?: {
    error?: string;
    val_f1_before?: number;
    val_f1_after?: number;
    gain?: number;
    rule?: string;
    accepted?: boolean;
    decision?: string;
    replace_command?: string | null;
    [k: string]: unknown;
  } | null;
}
