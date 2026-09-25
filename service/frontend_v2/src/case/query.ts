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
  /** акватория = source_id of the organisers' CSV (null = all) */
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

export const DEFAULT_LAYERS: CaseLayers = { obs: true, zones: true, scenes: true, quality: true };
export const DEFAULT_QUERY: CaseQuery = { source: null, from: null, to: null, profile: null, scope: null, det: [], conc: [], layers: DEFAULT_LAYERS };

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
  return { source: q.source, profile: q.profile, scope: q.scope, date_from: q.from, date_to: q.to };
}
/** zones: source / scope via their linked field samples (API ≥ 6e601c2) */
export function zoneParams(q: CaseQuery): Params {
  return {
    source: q.source,
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
const strList = (v: any) => (Array.isArray(v) ? v.filter((x) => typeof x === 'string') : []);

export function normalize(o: any): CaseQuery {
  const l = o?.layers ?? {};
  return {
    source: strOrNull(o?.source),
    from: strOrNull(o?.from),
    to: strOrNull(o?.to),
    profile: strOrNull(o?.profile),
    scope: strOrNull(o?.scope),
    det: strList(o?.det),
    conc: strList(o?.conc),
    layers: {
      obs: l.obs !== false,
      zones: l.zones !== false,
      scenes: l.scenes !== false,
      quality: l.quality !== false,
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
  };
}

export function writeCaseUrl(u: CaseUrl) {
  const p = new URLSearchParams();
  const keep = new URLSearchParams(location.search);
  for (const k of ['api', 'mock']) if (keep.get(k)) p.set(k, keep.get(k)!);
  if (u.q) p.set('q', encodeQuery(u.q));
  if (u.sel) p.set('sel', u.sel);
  if (u.pair) p.set('pair', u.pair);
  if (u.pairs) p.set('pairs', '1');
  if (u.tab) p.set('tab', u.tab);
  if (u.cam) p.set('c', [u.cam.lon.toFixed(4), u.cam.lat.toFixed(4), u.cam.zoom.toFixed(2)].join(','));
  const s = p.toString().replace(/%2C/g, ',');
  history.replaceState(null, '', `${location.pathname}${s ? '?' + s : ''}`);
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
  return {
    bbox: null,
    date_from: q.from,
    date_to: q.to,
    statuses: [...q.det, ...q.conc.map((c) => CONC_TO_STATUS[c]).filter(Boolean)],
    sources: q.source ? [q.source] : [],
    profiles: q.profile ? [q.profile] : [],
    scopes: q.scope ? [q.scope] : [],
    layers,
    scene_id: null,
  };
}

export function fromApiQuery(a: Partial<ApiQuery>): CaseQuery {
  const st = a.statuses ?? [];
  const ly = a.layers ?? [];
  const has = (k: string) => !ly.length || ly.includes(k);
  return {
    source: a.sources?.[0] ?? null,
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
  return !q.source && !q.from && !q.to && !q.profile && !q.scope && !q.det.length && !q.conc.length;
}
