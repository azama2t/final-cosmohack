// §48 (egor fix pack, screenshot 03): the old drift layer drew every particle as a hollow ring at every animated
// hour — with 300+ particles the map read as "hundreds of new finds", not a forecast. §51 п.8 (Матвей, чекпоинт,
// п.11): горизонт ≤ 72 ч (уже так во всех опубликованных прогонах — MAX_HORIZON_H ниже это ещё и гарантирует), и
// коридор — это вероятность (доля частиц ансамбля), не произвольная «оболочка». This file draws four honest
// layers, colour = violet only (наблюдения/поле — синий, зоны — янтарный, не здесь):
//   1. median trajectory — one line through the per-hour median position, with an arrowhead at the last point and
//      small hour ticks (e.g. +24ч, +48ч, +72ч);
//   2. вероятностный коридор — на текущий час анимации, две изолинии: 50 % и 90 % частиц ансамбля (главный прогон +
//      все запуски с другим ветровым коэффициентом, drift.json.ensemble) — область, ближайшая к медиане, куда
//      попадает эта доля частиц. Подпись «вероятность по ансамблю модели» — DRIFT_CORRIDOR_LABEL.
//   3. ≤ 12 представительных частиц — a deterministic, evenly-spaced sample of the main run's particle IDs, drawn
//      as small dots at the current animated hour (never hundreds, never labelled as items of debris).
// Source data is unchanged: data/live/<region>/<date>/drift.json (real HYCOM ESPC-D-V02 currents + NCEP GFS wind,
// see drift.ts and reports/tasklog/141_ux47.md). This module only decides HOW to draw it.
import type { DriftFile, DriftParticle } from '../types';

export const DRIFT_VIOLET = '#b197fc';
export const DRIFT_VIOLET_SOFT = 'rgba(177,151,252,0.16)';
/** §51 п.8: «направление переноса показывать не более чем на 3 суток» — hard cap, independent of the published file */
export const MAX_HORIZON_H = 72;
/** §51 п.8: the exact required label for the probability corridor (map + panel) */
export const DRIFT_CORRIDOR_LABEL = 'Коридор — вероятность по ансамблю модели: изолинии 50 % и 90 % частиц.';

/** Caption required by §48 for the map layer and the drift panel — the only place this sentence is authored. */
export function driftCaption(f?: DriftFile['forcing']): string {
  const model = f?.model || 'модель дрейфа';
  const hasCurrents = !!f?.currents;
  const hasWind = !!f?.wind;
  const what = hasCurrents && hasWind ? `${model}: течения + ветер` : hasCurrents ? `${model}: течения` : hasWind ? `${model}: ветер` : model;
  return `Экспериментальный прогноз (${what}). Не наблюдение. Точки — сценарии модели, не предметы мусора.`;
}

/** §51 п.8: clamp any hour value to the ≤ 72 h rule (defensive — every published run is already ≤ 72 h) */
export const clampHorizon = (h: number) => Math.min(MAX_HORIZON_H, Math.max(0, h));
export function horizonHours(d: DriftFile): number[] {
  const hs = d.hours?.length ? d.hours : [0, 72];
  return hs.filter((h) => h <= MAX_HORIZON_H);
}

/** position of one particle at hour h, linear between the published hourly samples (same rule as drift.ts:at) */
function at(path: [number, number, number][], h: number): [number, number] | null {
  if (!path.length) return null;
  let i = 0;
  while (i < path.length - 1 && path[i + 1][2] <= h) i++;
  const a = path[i];
  const b = path[Math.min(i + 1, path.length - 1)];
  if (b[2] <= a[2] || h <= a[2]) return [a[0], a[1]];
  const t = Math.min(1, (h - a[2]) / (b[2] - a[2]));
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
}

function median(vals: number[]): number {
  const s = [...vals].sort((x, y) => x - y);
  const n = s.length;
  if (!n) return NaN;
  const m = n >> 1;
  return n % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** median lon/lat of the main-run particles at hour h (robust to the odd particle blown far off by the wind term) */
export function medianAt(particles: DriftParticle[], h: number): [number, number] | null {
  const pts = particles.map((p) => at(p.path, h)).filter((x): x is [number, number] => !!x);
  if (!pts.length) return null;
  return [median(pts.map((p) => p[0])), median(pts.map((p) => p[1]))];
}

/** the median trajectory as a polyline over the published hours (main run only — the ensemble is the corridor, not the line) */
export function medianTrajectory(d: DriftFile): [number, number][] {
  const hours = horizonHours(d);
  const out: [number, number][] = [];
  for (const h of hours) {
    const m = medianAt(d.particles ?? [], h);
    if (m) out.push(m);
  }
  return out;
}

/** monotone-chain convex hull (small local extents here — plain lon/lat is fine, no projection needed) */
export function convexHull(pts: [number, number][]): [number, number][] {
  const uniq = Array.from(new Map(pts.map((p) => [`${p[0].toFixed(6)},${p[1].toFixed(6)}`, p])).values());
  if (uniq.length < 3) return uniq;
  uniq.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o: [number, number], a: [number, number], b: [number, number]) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: [number, number][] = [];
  for (const p of uniq) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) lower.pop();
    lower.push(p);
  }
  const upper: [number, number][] = [];
  for (let i = uniq.length - 1; i >= 0; i--) {
    const p = uniq[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) upper.pop();
    upper.push(p);
  }
  upper.pop();
  lower.pop();
  return lower.concat(upper);
}

/** all particles we have for the uncertainty range: main run + every ensemble (other wind-drift-factor) run —
 *  together this is «ансамбль модели» of §51 п.8 */
function allParticles(d: DriftFile): DriftParticle[] {
  const ens = (d.ensemble ?? []).flatMap((e) => e.particles ?? []);
  return [...(d.particles ?? []), ...ens];
}

/** the convex hull of the `frac` share of points closest to `center` — an honest, simple stand-in for a KDE
 *  probability contour: "the smallest compact region containing frac × 100 % of the ensemble's particles". This is
 *  literally «доля частиц» (§51 п.8), computed the same way reports/drift_check.md counts hits (particle positions,
 *  not a fitted density surface) — no numbers are invented, just a nearest-to-median subset. */
function percentileHull(pts: [number, number][], center: [number, number], frac: number): [number, number][] {
  if (pts.length < 3) return [];
  const d2 = (p: [number, number]) => (p[0] - center[0]) ** 2 + (p[1] - center[1]) ** 2;
  const sorted = [...pts].sort((a, b) => d2(a) - d2(b));
  const n = Math.max(3, Math.round(sorted.length * frac));
  return convexHull(sorted.slice(0, n));
}

/** §51 п.8: 50 % / 90 % probability isolines of the ensemble at hour `h` — DRIFT_CORRIDOR_LABEL describes these */
export function probabilityIsolines(d: DriftFile, h: number): { p50: [number, number][]; p90: [number, number][] } {
  const hh = clampHorizon(h);
  const parts = allParticles(d);
  const pts = parts.map((p) => at(p.path, hh)).filter((x): x is [number, number] => !!x);
  const center = medianAt(d.particles ?? [], hh) ?? (pts[0] as [number, number] | undefined);
  if (!center || pts.length < 3) return { p50: [], p90: [] };
  return { p50: percentileHull(pts, center, 0.5), p90: percentileHull(pts, center, 0.9) };
}

/** the two isoline polygons as GeoJSON, ready for two fill layers (90 % outer, 50 % inner, denser) */
export function corridorPolygons(d: DriftFile, h: number): GeoJSON.FeatureCollection {
  const { p50, p90 } = probabilityIsolines(d, h);
  const feats: GeoJSON.Feature[] = [];
  if (p90.length >= 3) feats.push({ type: 'Feature', properties: { level: 90 }, geometry: { type: 'Polygon', coordinates: [[...p90, p90[0]]] } });
  if (p50.length >= 3) feats.push({ type: 'Feature', properties: { level: 50 }, geometry: { type: 'Polygon', coordinates: [[...p50, p50[0]]] } });
  return { type: 'FeatureCollection', features: feats };
}

/** the median line + an arrowhead triangle at the end + point features for hour ticks (every ~1/4 of the horizon) */
export function trajectoryLayers(d: DriftFile): { line: GeoJSON.FeatureCollection; ticks: GeoJSON.FeatureCollection; arrow: GeoJSON.FeatureCollection } {
  const line = medianTrajectory(d);
  const hours = horizonHours(d);
  const lineFc: GeoJSON.FeatureCollection = {
    type: 'FeatureCollection',
    features: line.length >= 2 ? [{ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: line } }] : [],
  };
  // hour ticks at ~4 evenly spaced points along the published hours (skip hour 0 — the start dot already marks it)
  const tickHours = [0.25, 0.5, 0.75, 1].map((f) => hours[Math.round(f * (hours.length - 1))]).filter((h, i, a) => h > 0 && a.indexOf(h) === i);
  const ticks: GeoJSON.FeatureCollection = {
    type: 'FeatureCollection',
    features: tickHours
      .map((h) => ({ h, p: medianAt(d.particles ?? [], h) }))
      .filter((x): x is { h: number; p: [number, number] } => !!x.p)
      .map((x) => ({ type: 'Feature' as const, properties: { label: `+${Math.round(x.h)} ч` }, geometry: { type: 'Point' as const, coordinates: x.p } })),
  };
  let arrow: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };
  if (line.length >= 2) {
    const a = line[line.length - 2];
    const b = line[line.length - 1];
    const bearing = (Math.atan2(b[0] - a[0], b[1] - a[1]) * 180) / Math.PI;
    arrow = { type: 'FeatureCollection', features: [{ type: 'Feature', properties: { bearing }, geometry: { type: 'Point', coordinates: b } }] };
  }
  return { line: lineFc, ticks, arrow };
}

/** ≤ `n` representative particles (main run only) at hour h — an evenly spaced sample by particle id, not the whole cloud */
export function sampleParticles(d: DriftFile, h: number, n = 12): GeoJSON.FeatureCollection {
  const ps = d.particles ?? [];
  if (!ps.length) return { type: 'FeatureCollection', features: [] };
  const hh = clampHorizon(h);
  const step = Math.max(1, Math.floor(ps.length / n));
  const feats: GeoJSON.Feature[] = [];
  for (let i = 0; i < ps.length && feats.length < n; i += step) {
    const c = at(ps[i].path, hh);
    if (c) feats.push({ type: 'Feature', properties: {}, geometry: { type: 'Point', coordinates: c } });
  }
  return { type: 'FeatureCollection', features: feats };
}

/** add/update the maplibre sources+layers for one drift run; safe to call repeatedly (re-renders on every tick) */
export function renderDriftLayer(map: any, d: DriftFile | null, hour: number) {
  if (!map || !map.style?._loaded) return;
  const setSrc = (id: string, data: any) => {
    const s = map.getSource(id);
    if (s) s.setData(data);
    else map.addSource(id, { type: 'geojson', data });
  };
  const empty = { type: 'FeatureCollection', features: [] } as GeoJSON.FeatureCollection;
  const hh = clampHorizon(hour);
  const corridor = d ? corridorPolygons(d, hh) : empty;
  const { line, ticks, arrow } = d ? trajectoryLayers(d) : { line: empty, ticks: empty, arrow: empty };
  const samples = d ? sampleParticles(d, hh, 12) : empty;
  const start = d?.particles?.length ? { type: 'FeatureCollection' as const, features: [{ type: 'Feature' as const, properties: {}, geometry: { type: 'Point' as const, coordinates: medianAt(d.particles, d.hours?.[0] ?? 0) ?? [0, 0] } }] } : empty;

  setSrc('drift-corridor', corridor);
  setSrc('drift-line', line);
  setSrc('drift-ticks', ticks);
  setSrc('drift-arrow', arrow);
  setSrc('drift-samples', samples);
  setSrc('drift-start', start);

  if (!map.getLayer('drift-corridor'))
    // §51 п.8: two isolines from one source — 90 % (outer, faint) drawn first, 50 % (inner, denser) on top by
    // sort order (Polygon features: 90 % pushed in before 50 % in corridorPolygons, so paint order matches)
    map.addLayer({
      id: 'drift-corridor',
      type: 'fill',
      source: 'drift-corridor',
      paint: { 'fill-color': DRIFT_VIOLET, 'fill-opacity': ['case', ['==', ['get', 'level'], 50], 0.22, 0.09] },
    });
  if (!map.getLayer('drift-corridor-line'))
    map.addLayer({
      id: 'drift-corridor-line',
      type: 'line',
      source: 'drift-corridor',
      paint: { 'line-color': DRIFT_VIOLET, 'line-width': 1, 'line-opacity': 0.5, 'line-dasharray': [1, 1] },
    });
  if (!map.getLayer('drift-line'))
    map.addLayer({ id: 'drift-line', type: 'line', source: 'drift-line', paint: { 'line-color': DRIFT_VIOLET, 'line-width': 2, 'line-dasharray': [2, 1.4] } });
  if (!map.getLayer('drift-samples'))
    map.addLayer({
      id: 'drift-samples',
      type: 'circle',
      source: 'drift-samples',
      paint: { 'circle-radius': 3.5, 'circle-color': DRIFT_VIOLET, 'circle-opacity': 0.55, 'circle-stroke-color': DRIFT_VIOLET, 'circle-stroke-width': 1, 'circle-stroke-opacity': 0.9 },
    });
  if (!map.getLayer('drift-start'))
    map.addLayer({ id: 'drift-start', type: 'circle', source: 'drift-start', paint: { 'circle-radius': 3, 'circle-color': '#ffffff', 'circle-opacity': 0.7 } });
  if (!map.getLayer('drift-ticks'))
    map.addLayer({
      id: 'drift-ticks',
      type: 'symbol',
      source: 'drift-ticks',
      layout: { 'text-field': ['get', 'label'], 'text-size': 11, 'text-offset': [0, 1], 'text-anchor': 'top', 'text-allow-overlap': true },
      paint: { 'text-color': DRIFT_VIOLET, 'text-halo-color': '#0d0e10', 'text-halo-width': 1.2 },
    });
  if (!map.getLayer('drift-arrow'))
    map.addLayer({
      id: 'drift-arrow',
      type: 'symbol',
      source: 'drift-arrow',
      layout: { 'text-field': '▲', 'text-size': 14, 'text-rotate': ['get', 'bearing'], 'text-rotation-alignment': 'map', 'text-allow-overlap': true, 'text-ignore-placement': true },
      paint: { 'text-color': DRIFT_VIOLET, 'text-halo-color': '#0d0e10', 'text-halo-width': 1 },
    });

  const v = d ? 'visible' : 'none';
  for (const id of ['drift-corridor', 'drift-corridor-line', 'drift-line', 'drift-samples', 'drift-start', 'drift-ticks', 'drift-arrow']) map.setLayoutProperty(id, 'visibility', v);
  map.triggerRepaint?.();
}

export const DRIFT_LAYER_IDS = ['drift-corridor', 'drift-corridor-line', 'drift-line', 'drift-samples', 'drift-start', 'drift-ticks', 'drift-arrow'];
