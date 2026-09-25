import type { DetProps, DriftFile, FC, H3Props, Manifest, Region, TsRow, ZonesFile } from '../types';

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

/** Region for the demo tour: most detections on its latest date (summary), ties broken by index. */
export function bestRegion(m: Manifest): Region | null {
  if (!m.regions.length) return null;
  return [...m.regions].sort(
    (a, b) =>
      (b.summary?.n_detections ?? 0) - (a.summary?.n_detections ?? 0) ||
      (b.summary?.index_permille ?? -1) - (a.summary?.index_permille ?? -1),
  )[0];
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
