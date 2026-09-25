import type { DetProps, FC, H3Props, Manifest, SceneRef, ZonesFile } from '../types';
import { apiJSON, loadDetections, loadH3, loadZones } from './data';

export interface SceneStats {
  ref: SceneRef;
  regionName: string;
  meanIndex: number | null; // mean share_permille over cells with observed_frac >= 0.5 (as timeseries.mean_index)
  pooledIndex: number | null; // 1000 * Σflagged / Σobserved over the same cells
  maxCell: number | null;
  nDetections: number;
  areaM2: number;
  maxProb: number | null;
  cloud: number | null;
  cellsObserved: number;
  cellsTotal: number;
  topZone: number | null;
  source: 'client' | 'api';
}

export async function sceneStats(m: Manifest, ref: SceneRef, model: string): Promise<SceneStats | null> {
  const r = m.regions.find((x) => x.id === ref.region);
  const de = r?.dates.find((d) => d.date === ref.date);
  if (!r || !de) return null;
  const mdl = de.models.includes(model) ? model : de.models[0];
  const [det, h3, zones] = await Promise.all([
    loadDetections(r.id, de.date, mdl),
    loadH3(r.id, de.date, mdl),
    loadZones(r.id, de.date, mdl),
  ]);
  return computeStats(r.name, ref, de.cloud_frac, det, h3, zones);
}

export function computeStats(
  regionName: string,
  ref: SceneRef,
  cloud: number | null,
  det: FC<DetProps> | null,
  h3: FC<H3Props> | null,
  zones: ZonesFile | null,
): SceneStats {
  let sum = 0,
    n = 0,
    fl = 0,
    ob = 0,
    mx: number | null = null;
  for (const f of h3?.features ?? []) {
    const p = f.properties;
    if (p.share_permille === null || p.share_permille === undefined) continue;
    sum += p.share_permille;
    n++;
    fl += p.flagged_water_px;
    ob += p.observed_water_px;
    mx = mx === null ? p.share_permille : Math.max(mx, p.share_permille);
  }
  const feats = det?.features ?? [];
  return {
    ref,
    regionName,
    meanIndex: n ? sum / n : null,
    pooledIndex: ob ? (1000 * fl) / ob : null,
    maxCell: mx,
    nDetections: feats.length,
    areaM2: feats.reduce((a, f) => a + (f.properties.area_m2 || 0), 0),
    maxProb: feats.length ? Math.max(...feats.map((f) => f.properties.max_prob)) : null,
    cloud,
    cellsObserved: n,
    cellsTotal: h3?.features.length ?? 0,
    topZone: zones?.zones?.[0]?.index ?? null,
    source: 'client',
  };
}

/** Optional backend comparison; used only if the API answers with recognisable numbers. */
export async function apiCompare(a: SceneRef, b: SceneRef, model: string) {
  const res = await apiJSON<any>(
    `/api/compare?a=${encodeURIComponent(`${a.region}:${a.date}`)}&b=${encodeURIComponent(`${b.region}:${b.date}`)}&model=${model}`,
  );
  if (!res || typeof res !== 'object' || !res.a || !res.b) return null;
  return res as { a: Record<string, unknown>; b: Record<string, unknown> };
}

export function mergeApi(s: SceneStats, api: Record<string, unknown> | undefined): SceneStats {
  if (!api) return s;
  // backend format: {region, date, model, kpi: {total_debris_area_m2, n_detections, mean_index, max_index, cloud_frac, observed_cells, ...}}
  const src = (api.kpi && typeof api.kpi === 'object' ? api.kpi : api) as Record<string, unknown>;
  const num = (k: string) => (typeof src[k] === 'number' ? (src[k] as number) : undefined);
  return {
    ...s,
    meanIndex: num('mean_index') ?? num('index_permille') ?? s.meanIndex,
    nDetections: num('n_detections') ?? s.nDetections,
    areaM2: num('total_debris_area_m2') ?? s.areaM2,
    cloud: num('cloud_frac') ?? s.cloud,
    maxCell: num('max_index') ?? s.maxCell,
    cellsObserved: num('observed_cells') ?? s.cellsObserved,
    source: 'api',
  };
}
