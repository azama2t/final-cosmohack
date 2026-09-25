// Studio views (L95): types of GET /api/v3/studio/* + loader of scene views (docs/CONTRACTS_V3.md, section 3.8).
// Only views the scene really has are offered: scene.available_views; a missing view is never drawn as empty.
// No mocks: errors surface as ApiErr (code NO_VIEW / NOT_FOUND / BAD_PARAM / UNAVAILABLE).

import { ApiErr, API_BASE, apiUrl, get, type Params } from '../api';

export type ViewKind = 'rgb' | 'spectral' | 'detection' | 'quality';
export const VIEW_KINDS: ViewKind[] = ['rgb', 'spectral', 'detection', 'quality'];
/** One-line switcher labels (§11 п.6): «Снимок · Спектральный · Детекция · Качество» */
export const VIEW_LABELS: Record<ViewKind, string> = {
  rgb: 'Снимок',
  spectral: 'Спектральный',
  detection: 'Детекция',
  quality: 'Качество',
};
export type SourceType = 'pair' | 'live' | 'drift' | 'search';
export type EvidenceLevel = 'A' | 'B' | 'C' | 'D';
/** [lon, lat] corners: top-left, top-right, bottom-right, bottom-left (MapLibre image source order) */
export type Corners = [[number, number], [number, number], [number, number], [number, number]];

export interface LegendItem {
  id?: string;
  label: string;
  color: string; // #rrggbbaa
}
export interface ViewLegend {
  type: 'probability' | 'classes';
  threshold?: number | null;
  palette?: string;
  items: LegendItem[];
  note?: string;
}
export interface StudioView {
  available: boolean;
  label: string;
  overlay: boolean; // true: draw over the «Снимок» (transparent PNG)
  url: string | null; // relative to API_BASE; null when not available
  source: string | null; // which files/channels the view is built from
  variants: string[] | null; // spectral: fdi|swir; detection: lgbm|mdd (first = default)
  reason: string | null; // why the view is missing (only when available=false)
  legend?: ViewLegend; // only in GET /studio/scenes/{id}
}
export interface LevelRecord {
  level: EvidenceLevel;
  event_id: string | null;
  scene_id: string | null;
  reason: string | null; // Russian: the registry reason if Cyrillic, else the level definition
  reason_raw: string | null; // registry text as is (may be English) — for audit, not for the UI
  file: string;
}
export interface LegacyDetector {
  weights: 'lgbm_live';
  harmonization: 'water_median';
  threshold: number | null;
  note: string; // «прежний режим …, отвергнут» — never show as the detector result
}
/** Always the CURRENT detector mode (weights lgbm, no harmonization) or run=false + reason in label. */
export interface StudioDetector {
  run: boolean;
  weights: 'lgbm' | null;
  harmonization: false | null;
  threshold: number | null;
  pixels: number | null; // whole crop, after cloud/shadow filters
  objects: number | null;
  scope: string | null; // «вся вырезка»
  strip: { objects: number | null; pixels: number | null } | null; // pairs: inside the observation strip
  source: string | null;
  label: string; // ready Russian caption: «Детектор (текущий режим: …): 2 объекта, 3 пикселя на всей вырезке»
  legacy: LegacyDetector | null;
}
export interface StudioScene {
  id: string;
  source_type: SourceType;
  source_label: string;
  title: string;
  datetime: string | null; // ISO UTC
  date: string | null;
  mission: string | null; // Sentinel-2 | Landsat-8 | ...
  platform: string | null; // Sentinel-2A | ...
  catalog: string | null; // earth-search | planetary-computer
  collection: string | null; // sentinel-2-l2a | landsat-c2-l2 | ...
  product_id: string | null;
  tile: string | null;
  region: string | null;
  event_id: string | null;
  level: EvidenceLevel | null; // null = not in data/search/*/candidates.csv
  level_match: 'event_id+scene_id' | 'scene_id' | null;
  level_records?: LevelRecord[]; // only in GET /studio/scenes/{id}
  coordinates: Corners;
  bounds: [number, number, number, number]; // [w, s, e, n]
  crs: string;
  size_px: [number, number];
  pixel_m: number | null;
  views: Record<ViewKind, StudioView>;
  available_views: ViewKind[];
  quality: {
    decision?: string | null;
    decision_label?: string | null;
    reason?: string | null;
    reason_label?: string | null;
    valid_water_frac?: number | null;
    cloud_frac?: number | null;
    [k: string]: unknown;
  } | null;
  detector: StudioDetector;
  field: {
    sample_ids?: string[];
    field_items_km2?: number | null;
    obs_datetime?: string | null;
    dt_hours?: number | null;
    [k: string]: unknown;
  } | null;
  links: Record<string, string | null>;
  footprint_note: string;
}
export interface StudioSceneDetail extends StudioScene {
  timeline: {
    id: string;
    datetime: string | null;
    source_type: SourceType;
    platform: string | null;
    level: EvidenceLevel | null;
    available_views: ViewKind[];
  }[];
}
export interface StudioScenesResponse {
  version: string;
  count: number;
  total: number;
  offset: number;
  limit: number;
  empty_reason: string | null;
  kinds: { id: ViewKind; label: string; overlay: boolean }[];
  source_types: { id: SourceType; label: string }[];
  levels: { id: EvidenceLevel; label: string }[];
  by_source_type: Partial<Record<SourceType, number>>;
  scenes: StudioScene[];
}
export interface StudioScenesQuery {
  bbox?: [number, number, number, number] | null;
  date_from?: string | null;
  date_to?: string | null;
  source?: SourceType[];
  level?: (EvidenceLevel | 'none')[];
  view?: ViewKind[]; // scenes that have ALL of these views
  limit?: number;
  offset?: number;
}

const P = '/api/v3/studio';

export function listStudioScenes(q: StudioScenesQuery = {}, signal?: AbortSignal) {
  const params: Params = {
    bbox: q.bbox ? q.bbox.join(',') : null,
    date_from: q.date_from,
    date_to: q.date_to,
    source: q.source,
    level: q.level,
    view: q.view,
    limit: q.limit,
    offset: q.offset,
  };
  return get<StudioScenesResponse>(`${P}/scenes`, params, signal);
}

export function getStudioScene(id: string, signal?: AbortSignal) {
  return get<StudioSceneDetail>(`${P}/scenes/${encodeURIComponent(id)}`, {}, signal);
}

export interface ViewOptions {
  px?: number; // max side of the PNG, 64..4096 (default 1024; never upscaled)
  variant?: string; // one of view.variants
}

/** Absolute URL of a view PNG, or null if the scene has no such view (do not render a switcher item then). */
export function viewUrl(scene: StudioScene, kind: ViewKind, opt: ViewOptions = {}): string | null {
  const v = scene.views[kind];
  if (!v?.available || !v.url) return null;
  if (opt.variant && !(v.variants ?? []).includes(opt.variant)) return null;
  return apiUrl(v.url, { px: opt.px, variant: opt.variant });
}

/** Switcher items in fixed order; only the available ones. */
export function switcherItems(scene: StudioScene): { kind: ViewKind; label: string; overlay: boolean }[] {
  return VIEW_KINDS.filter((k) => scene.available_views.includes(k)).map((k) => ({
    kind: k,
    label: VIEW_LABELS[k],
    overlay: scene.views[k].overlay,
  }));
}

export interface LoadedView {
  kind: ViewKind;
  objectUrl: string; // URL.createObjectURL(blob) — call release() when the view is replaced
  width: number;
  height: number;
  coordinates: Corners;
  level: EvidenceLevel | null;
  datetime: string | null;
  variant: string | null;
  scale: string | null; // e.g. "FDI 0.0049..0.0096 (p2..p99.5 water)"
  detectionPixels: number | null;
  release: () => void;
}

const _cache = new Map<string, Promise<LoadedView>>();

/** Fetch a view PNG (memoized per URL). Throws ApiErr('NO_VIEW', reason) if the server has no such view. */
export function loadView(scene: StudioScene, kind: ViewKind, opt: ViewOptions = {}, signal?: AbortSignal): Promise<LoadedView> {
  const url = viewUrl(scene, kind, opt);
  if (!url) {
    const reason = scene.views[kind]?.reason ?? 'вид недоступен';
    return Promise.reject(new ApiErr('NO_VIEW', reason, 404));
  }
  const hit = _cache.get(url);
  if (hit) return hit;
  const p = (async (): Promise<LoadedView> => {
    let r: Response;
    try {
      r = await fetch(url, { signal });
    } catch (e: any) {
      if (e?.name === 'AbortError') throw e;
      throw new ApiErr('UNAVAILABLE', 'Сервис недоступен');
    }
    if (!r.ok) {
      let j: any = null;
      try {
        j = await r.json();
      } catch {
        /* not JSON */
      }
      throw new ApiErr(j?.error?.code ?? 'HTTP', j?.error?.message ?? `HTTP ${r.status}`, r.status);
    }
    const blob = await r.blob();
    const objectUrl = URL.createObjectURL(blob);
    const [w, h] = (r.headers.get('X-View-Size') ?? '0x0').split('x').map(Number);
    const lvl = r.headers.get('X-Evidence-Level');
    const dp = r.headers.get('X-Detection-Pixels');
    return {
      kind,
      objectUrl,
      width: w,
      height: h,
      coordinates: scene.coordinates,
      level: lvl && lvl !== 'none' ? (lvl as EvidenceLevel) : null,
      datetime: r.headers.get('X-Scene-Datetime') || scene.datetime,
      variant: r.headers.get('X-View-Variant') || null,
      scale: r.headers.get('X-View-Scale'),
      detectionPixels: dp === null ? null : Number(dp),
      release: () => {
        URL.revokeObjectURL(objectUrl);
        _cache.delete(url);
      },
    };
  })();
  _cache.set(url, p);
  p.catch(() => _cache.delete(url));
  return p;
}

/** MapLibre `image` source spec for a view (no fetch; the browser loads the PNG itself). */
export function imageSource(scene: StudioScene, kind: ViewKind, opt: ViewOptions = {}) {
  const url = viewUrl(scene, kind, opt);
  return url ? { type: 'image' as const, url, coordinates: scene.coordinates } : null;
}

/** «03.04.2014 10:27 UTC · Sentinel-2A · sentinel-2-l2a» — date and source are always shown with a view. */
export function sceneCaption(s: StudioScene): string {
  const d = s.datetime ? new Date(s.datetime) : null;
  const pad = (n: number) => String(n).padStart(2, '0');
  const when = d
    ? `${pad(d.getUTCDate())}.${pad(d.getUTCMonth() + 1)}.${d.getUTCFullYear()} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`
    : 'дата неизвестна';
  return [when, s.platform ?? s.mission, s.collection].filter(Boolean).join(' · ');
}

export const studioApiBase = API_BASE;
