// Case-mode query state: ONE object drives the map layers, the lists, the export links and the saved queries,
// so what is shown = what is exported = what is saved. Serialised into the URL (?q=<base64url JSON>).
import type { ApiQuery, Params } from './api3';

export type Bbox = [number, number, number, number];

export interface CaseLayers {
  obs: boolean;
  zones: boolean;
  scenes: boolean;
  quality: boolean;
}

export interface CaseQuery {
  /** §34 п.3: акватория of the view — 'r:<район снимков>' | '<source_id поля>' | 'bbox' (from a saved query) | null = все */
  area: string | null;
  /** the акватория frame [W,S,E,N]: ONE box for the scene-zone list, the map and the export (bbox= of the API) */
  bbox: Bbox | null;
  /** field source (set with a field акватория = its id; filters the field layer only) */
  source: string | null;
  /** one date range for the measurement date AND the scene date (same as the backend runs saved queries) */
  from: string | null;
  to: string | null;
  profile: string | null;
  /** target_scope (совокупность): all_litter / total_plastic … */
  scope: string | null;
  /** zone detection_status filter (empty = all) */
  det: string[];
  /** zone concentration_status filter (empty = all) */
  conc: string[];
  layers: CaseLayers;
}

/** §34 п.3: field measurements are a layer switched on by one button (off on the Earth overview); scene zones and the
 *  image of the chosen snapshot are on; the quality mask is on demand (studio / «Слои») */
export const DEFAULT_LAYERS: CaseLayers = { obs: false, zones: true, scenes: true, quality: false };
export const DEFAULT_QUERY: CaseQuery = { area: null, bbox: null, source: null, from: null, to: null, profile: null, scope: null, det: [], conc: [], layers: DEFAULT_LAYERS };
export const layersDefault = (l: CaseLayers) => (Object.keys(DEFAULT_LAYERS) as (keyof CaseLayers)[]).every((k) => l[k] === DEFAULT_LAYERS[k]);
const bboxStr = (b: Bbox | null) => (b ? b.map((v) => +v.toFixed(4)).join(',') : null);

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
export function validDate(s: string | null): boolean {
  if (!s) return true;
  if (!DATE_RE.test(s)) return false;
  const d = new Date(s + 'T00:00:00Z');
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

/** null = ok, else a short message for the user */
export function dateError(q: CaseQuery): string | null {
  if (!validDate(q.from)) return `Некорректная дата «с»: ${q.from}`;
  if (!validDate(q.to)) return `Некорректная дата «по»: ${q.to}`;
  if (q.from && q.to && q.from > q.to) return 'Дата «с» позже даты «по»';
  return null;
}

// ---- API params (identical for the layer request and its export) ----
export function obsParams(q: CaseQuery): Params {
  // a field акватория filters by its source; a scene район — by its frame
  return { source: q.source, bbox: q.source ? null : bboxStr(q.bbox), profile: q.profile, scope: q.scope, date_from: q.from, date_to: q.to };
}
/** §34 п.3: satellite scene zones — акватория (frame), dates, zone status; the same params for the list, the map and the export */
export function szParams(q: CaseQuery): Params {
  // a field акватория (source of the organisers) → source= : the API gives no scene zones for it (they have no field
  // source) — the list, the map and the export agree; a район of snapshots → its frame (bbox=)
  return q.source
    ? { source: q.source, date_from: q.from, date_to: q.to, detection_status: q.det, concentration_status: q.conc }
    : { bbox: bboxStr(q.bbox), date_from: q.from, date_to: q.to, detection_status: q.det, concentration_status: q.conc };
}
/** zones: source / scope via their linked field samples (API ≥ 6e601c2) */
export function zoneParams(q: CaseQuery): Params {
  // acceptance 15:49: the район frame (bbox=) filters the survey strips too — «то, что отфильтровано» = the район
  return {
    source: q.source,
    bbox: q.source ? null : bboxStr(q.bbox),
    scope: q.scope,
    date_from: q.from,
    date_to: q.to,
    detection_status: q.det,
    concentration_status: q.conc,
    profile: q.profile,
  };
}
export function sceneParams(q: CaseQuery, bbox: Bbox | null): Params {
  return { bbox: bbox ? bbox.map((v) => +v.toFixed(4)).join(',') : null, date_from: q.from, date_to: q.to, status: 'all' };
}
export function pairParams(q: CaseQuery, status: string): Params {
  return { source: q.source, date_from: q.from, date_to: q.to, status };
}

// ---- URL ----
function b64urlEncode(s: string): string {
  const bytes = new TextEncoder().encode(s);
  let bin = '';
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}
function b64urlDecode(s: string): string {
  const b = s.replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(b + '==='.slice((b.length + 3) % 4));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

const strOrNull = (v: any) => (typeof v === 'string' && v ? v : null);
const boxOrNull = (v: any): Bbox | null => (Array.isArray(v) && v.length === 4 && v.every((x) => typeof x === 'number' && Number.isFinite(x)) ? (v as Bbox) : null);
const strList = (v: any) => (Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []);

export function normalize(o: any): CaseQuery {
  const l = o?.layers ?? {};
  const source = strOrNull(o?.source);
  return {
    area: strOrNull(o?.area) ?? source,
    bbox: boxOrNull(o?.bbox),
    source,
    from: strOrNull(o?.from),
    to: strOrNull(o?.to),
    profile: strOrNull(o?.profile),
    scope: strOrNull(o?.scope),
    det: strList(o?.det),
    conc: strList(o?.conc),
    layers: {
      obs: typeof l.obs === 'boolean' ? l.obs : DEFAULT_LAYERS.obs,
      zones: typeof l.zones === 'boolean' ? l.zones : DEFAULT_LAYERS.zones,
      scenes: typeof l.scenes === 'boolean' ? l.scenes : DEFAULT_LAYERS.scenes,
      quality: typeof l.quality === 'boolean' ? l.quality : DEFAULT_LAYERS.quality,
    },
  };
}

export function encodeQuery(q: CaseQuery): string {
  return b64urlEncode(JSON.stringify(q));
}
export function decodeQuery(s: string | null): CaseQuery | null {
  if (!s) return null;
  try {
    return normalize(JSON.parse(b64urlDecode(s)));
  } catch {
    return null;
  }
}

export interface CaseUrl {
  q: CaseQuery | null;
  sel: string | null;
  pair: string | null;
  cam: { lon: number; lat: number; zoom: number } | null;
  pairs: boolean;
  tab: string | null;
  /** §34 п.3: the snapshot opened in the left list (scene_key of /scene_zones/scenes) */
  scene?: string | null;
}

export function readCaseUrl(): CaseUrl {
  const p = new URLSearchParams(location.search);
  const c = (p.get('c') ?? '').split(',').map(Number);
  return {
    q: decodeQuery(p.get('q')),
    sel: p.get('sel'),
    pair: p.get('pair'),
    cam: c.length >= 3 && c.slice(0, 3).every(Number.isFinite) ? { lon: c[0], lat: c[1], zoom: c[2] } : null,
    pairs: p.get('pairs') === '1',
    tab: p.get('tab'),
    scene: p.get('scene'),
  };
}

export function writeCaseUrl(u: CaseUrl, push = false) {
  const p = new URLSearchParams();
  const keep = new URLSearchParams(location.search);
  for (const k of ['api', 'mock']) if (keep.get(k)) p.set(k, keep.get(k)!);
  if (u.q) p.set('q', encodeQuery(u.q));
  if (u.sel) p.set('sel', u.sel);
  if (u.pair) p.set('pair', u.pair);
  if (u.pairs) p.set('pairs', '1');
  if (u.tab) p.set('tab', u.tab);
  if (u.scene) p.set('scene', u.scene);
  if (u.cam) p.set('c', [u.cam.lon.toFixed(4), u.cam.lat.toFixed(4), u.cam.zoom.toFixed(2)].join(','));
  const s = p.toString().replace(/%2C/g, ',');
  const href = `${location.pathname}${s ? '?' + s : ''}`;
  if (push) history.pushState(null, '', href);
  else history.replaceState(null, '', href);
}

// ---- saved queries (contract object, section 8) ----
const CONC_TO_STATUS: Record<string, string> = { unavailable: 'concentration_unavailable', research_estimate: 'research_estimate' };
const STATUS_TO_CONC: Record<string, string> = { concentration_unavailable: 'unavailable', research_estimate: 'research_estimate' };
const DET = ['detected', 'not_detected', 'insufficient_data'];

export function toApiQuery(q: CaseQuery): ApiQuery {
  const layers = [
    ...(q.layers.scenes ? ['scene'] : []),
    ...(q.layers.quality ? ['quality'] : []),
    ...(q.layers.obs ? ['observations'] : []),
    ...(q.layers.zones ? ['zones'] : []),
  ];
  // §34 п.3: a saved query keeps what defines the zone list — акватория (район → its frame; field source → source), dates, zone status — so that
  // «запустить» gives the same zones as the list, the map and the export (a field source/profile/scope would make the
  // backend drop all scene zones: they have no field source); field-layer filters stay in the link of the view
  return {
    bbox: q.bbox && !q.source ? q.bbox.map((v) => +v.toFixed(4)) : null,
    date_from: q.from,
    date_to: q.to,
    statuses: [...q.det, ...q.conc.map((c) => CONC_TO_STATUS[c]).filter(Boolean)],
    sources: q.source ? [q.source] : [],
    profiles: [],
    scopes: [],
    layers,
    scene_id: null,
  };
}

export function fromApiQuery(a: Partial<ApiQuery>): CaseQuery {
  const st = a.statuses ?? [];
  const ly = a.layers ?? [];
  const has = (k: string) => !ly.length || ly.includes(k);
  const bb = boxOrNull(a.bbox);
  const src = a.sources?.[0] ?? null;
  return {
    area: bb ? 'bbox' : src,
    bbox: bb,
    source: bb ? null : src,
    from: a.date_from ?? null,
    to: a.date_to ?? null,
    profile: a.profiles?.[0] ?? null,
    scope: a.scopes?.[0] ?? null,
    det: st.filter((s) => DET.includes(s)),
    conc: st.map((s) => STATUS_TO_CONC[s]).filter(Boolean),
    layers: { obs: has('observations'), zones: has('zones'), scenes: has('scene'), quality: has('quality') },
  };
}

/** whether saving would lose part of the filter (the contract query has no «measured_nearby») */
export function lossyForApi(q: CaseQuery): boolean {
  return q.conc.some((c) => !CONC_TO_STATUS[c]);
}

const LS_KEY = 'mp.case.queries';
export function localQueries(): { query_id: string; name: string; created_at: string; query: ApiQuery; local: true }[] {
  try {
    const v = JSON.parse(localStorage.getItem(LS_KEY) || '[]');
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}
export function saveLocalQuery(name: string, q: ApiQuery) {
  try {
    const all = localQueries();
    all.unshift({ query_id: 'local_' + Date.now().toString(36), name, created_at: new Date().toISOString(), query: q, local: true });
    localStorage.setItem(LS_KEY, JSON.stringify(all.slice(0, 30)));
  } catch {
    /* storage unavailable */
  }
}
export function deleteLocalQuery(id: string) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(localQueries().filter((x) => x.query_id !== id)));
  } catch {
    /* ignore */
  }
}

export function isDefault(q: CaseQuery): boolean {
  return !q.area && !q.bbox && !q.source && !q.from && !q.to && !q.profile && !q.scope && !q.det.length && !q.conc.length;
}
