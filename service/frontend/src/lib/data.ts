import type { DateEntry, DetProps, DriftFile, FC, H3Props, Manifest, Region, TsRow, ZonesFile } from '../types';

const DATA_BASE = ((import.meta.env.VITE_DATA_URL as string | undefined) ?? '/data').replace(/\/$/, '');

export const dataUrl = (p: string) => `${DATA_BASE}/${p.replace(/^\//, '')}`;
export const modelPath = (region: string, date: string, model: string, file: string) =>
  `${region}/${date}/${model}/${file}`;

const jsonCache = new Map<string, Promise<unknown>>();
const imgCache = new Map<string, Promise<HTMLImageElement>>();

export class NotFound extends Error {}

export function getJSON<T>(path: string): Promise<T> {
  let p = jsonCache.get(path) as Promise<T> | undefined;
  if (!p) {
    p = fetch(dataUrl(path)).then(async (r) => {
      if (r.status === 404) throw new NotFound(path);
      if (!r.ok) throw new Error(`${r.status} ${path}`);
      return (await r.json()) as T;
    });
    p.catch(() => jsonCache.delete(path));
    jsonCache.set(path, p);
  }
  return p;
}

/** Returns null instead of throwing when file is absent (optional layers). */
export async function getJSONOpt<T>(path: string): Promise<T | null> {
  try {
    return await getJSON<T>(path);
  } catch {
    return null;
  }
}

export function getImage(path: string): Promise<HTMLImageElement> {
  let p = imgCache.get(path);
  if (!p) {
    p = new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image();
      img.decoding = 'async';
      img.crossOrigin = 'anonymous';
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error(`image ${path}`));
      img.src = dataUrl(path);
    });
    p.catch(() => imgCache.delete(path));
    imgCache.set(path, p);
  }
  return p;
}

export async function loadManifest(): Promise<Manifest | null> {
  try {
    const r = await fetch(dataUrl('manifest.json'), { cache: 'no-cache' });
    if (!r.ok) return null;
    const m = (await r.json()) as Manifest;
    if (!m || !Array.isArray(m.regions)) return null;
    manifestKind = m.kind;
    for (const reg of m.regions) reg.dates.sort((a, b) => a.date.localeCompare(b.date));
    return m;
  } catch {
    return null;
  }
}

export const loadDetections = (r: string, d: string, m: string) =>
  getJSONOpt<FC<DetProps>>(modelPath(r, d, m, 'detections.geojson'));
export const loadH3 = (r: string, d: string, m: string) => getJSONOpt<FC<H3Props>>(modelPath(r, d, m, 'h3.geojson'));
export const loadZones = (r: string, d: string, m: string) => getJSONOpt<ZonesFile>(modelPath(r, d, m, 'zones.json'));
export const loadTimeseries = (r: string) => getJSONOpt<TsRow[]>(`${r}/timeseries.json`);
export const loadDrift = (path: string) => getJSONOpt<DriftFile>(path);

/** Short display name: text before « (» («Гондурасский залив (Омоа – …)» → «Гондурасский залив»). */
export const shortName = (name: string) => {
  const i = name.indexOf(' (');
  return i > 0 ? name.slice(0, i) : name;
};

/** Haze / sun-glint flag of a date (field is optional: older data has no `quality`). */
export const isFlagged = (d: DateEntry | null | undefined) => !!(d?.quality && (d.quality.haze || d.quality.glint_or_haze));

/** Date shown for a region by default: summary.latest_date (latest without haze), else the last date. */
export const summaryDate = (r: Region): DateEntry | undefined =>
  r.dates.find((d) => d.date === r.summary?.latest_date) ?? r.dates[r.dates.length - 1];

/** Hint text if the region's latest scene (or the scene behind its summary) has the haze/glint flag, else ''. */
export function regionHaze(r: Region): string {
  const last = r.dates[r.dates.length - 1];
  const shown = summaryDate(r);
  if (r.summary?.haze || isFlagged(shown)) return 'снимок с дымкой/бликом — находки могут быть завышены';
  if (isFlagged(last) && shown && last)
    return `последний снимок (${last.date}) с дымкой/бликом; показатели — по снимку ${shown.date} без флага`;
  return '';
}

/** L34: a date is unreliable for ranking — haze/glint flag or cloud > 50 % (same thresholds as the calendar). */
export const isUnreliableDate = (d: DateEntry | null | undefined) => !d || isFlagged(d) || (d.cloud_frac ?? 0) > 0.5;

const dShort = (iso: string) => {
  const [y, m, d] = iso.split('-');
  return `${d}.${m}.${y?.slice(2)}`;
};

/**
 * L34: ranking reliability of a region. Reliable — its latest available scene has no haze/glint flag and ≤ 50 % clouds.
 * Otherwise the region goes to the «ненадёжные снимки» group; `why` says why (and which date the index is from).
 */
export function regionReliability(r: Region): { ok: boolean; why: string } {
  const last = r.dates[r.dates.length - 1];
  if (!isUnreliableDate(last)) return { ok: true, why: '' };
  const what = last && isFlagged(last) ? 'дымка/блик' : 'облачность > 50 %';
  const anyOk = r.dates.find((d) => !isUnreliableDate(d));
  const shown = summaryDate(r);
  if (!anyOk) return { ok: false, why: `надёжных снимков нет: ${what} на последнем (${dShort(last.date)}) — находки могут быть завышены` };
  return {
    ok: false,
    why: `последний снимок ${dShort(last.date)} — ${what}; индекс по надёжному снимку ${shown ? dShort(shown.date) : '—'}, он старше`,
  };
}

/** L34: regions split into reliable (by index, desc) and unreliable (by index, desc). */
export function rankRegions(regions: Region[]): { ok: Region[]; bad: Region[] } {
  const byIdx = (a: Region, b: Region) => (b.summary?.index_permille ?? -1) - (a.summary?.index_permille ?? -1);
  const s = [...regions].sort(byIdx);
  return { ok: s.filter((r) => regionReliability(r).ok), bad: s.filter((r) => !regionReliability(r).ok) };
}

/**
 * Region for the demo tour and screenshots.
 * 1) candidates: regions whose freshest scene has no haze/glint flag (the summary date is then that scene);
 *    a region whose newest scene is flagged is not shown as «the freshest finding»;
 * 2) max n_detections on that date; tie → region with drift.json; then index;
 * 3) if no candidate — old rule (summary n_detections, then index) over all regions.
 * L43: an explicit manifest.demo.region (build_service_data.py --demo-region) wins when that region exists.
 */
export function bestRegion(m: Manifest): Region | null {
  if (!m.regions.length) return null;
  const demo = m.demo?.region ? m.regions.find((r) => r.id === m.demo!.region) : undefined;
  if (demo) return demo;
  const det = (r: Region) => r.summary?.n_detections ?? 0;
  const idx = (r: Region) => r.summary?.index_permille ?? -1;
  const drift = (r: Region) => (summaryDate(r)?.drift ? 1 : 0);
  const clean = m.regions.filter((r) => {
    const last = r.dates[r.dates.length - 1];
    const d = summaryDate(r);
    return !!d && !isUnreliableDate(d) && !r.summary?.haze && d === last && det(r) > 0;
  });
  // L34: among fresh reliable candidates prefer those with a drift forecast (the tour has a drift step)
  const withDrift = clean.filter((r) => drift(r));
  const pool = withDrift.length ? withDrift : clean;
  if (pool.length) return [...pool].sort((a, b) => det(b) - det(a) || drift(b) - drift(a) || idx(b) - idx(a))[0];
  return [...m.regions].sort((a, b) => det(b) - det(a) || idx(b) - idx(a))[0];
}

// ---- backend API (optional) ----
let apiState: Promise<boolean> | null = null;
let manifestKind: string | undefined;
/** true if FastAPI backend answers /health with JSON. In static dev mode returns false. */
export function apiAvailable(): Promise<boolean> {
  if (!apiState) {
    apiState = fetch('/health', { cache: 'no-cache' })
      .then(async (r) => {
        if (r.status !== 200 || r.headers.get('X-No-Backend')) return false;
        // the backend must serve the same data layer as /data (dev: fixtures via vite vs real data on :8000)
        const h = await r.json().catch(() => null);
        return !(h && h.data_kind && manifestKind && h.data_kind !== manifestKind);
      })
      .catch(() => false);
  }
  return apiState;
}

export async function apiJSON<T>(url: string): Promise<T | null> {
  if (!(await apiAvailable())) return null;
  try {
    const r = await fetch(url);
    if (r.status !== 200) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}
