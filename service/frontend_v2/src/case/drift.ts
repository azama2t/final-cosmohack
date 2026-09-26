// INBOX §44 п.3: the drift forecast back in «Кейс» — an experiment, shown apart from observations (own style, «прогноз»).
// Data: the published OpenDrift run of the snapshot (service/data/<region>/<date>/drift.json, the same file as the old
// live mode); the check result — /api/drift_check (reports/drift_check.md: 7/18 = «нулевой дрейф» 7/18).
import { ctl, anim } from '../map/controller';
import { loadDrift, loadManifest } from '../lib/data';
import type { DriftFile } from '../types';

let cur: DriftFile | null = null;

/** region/date → drift.json path, from the data manifest (only dates that have a published run) */
let paths: Promise<Map<string, string>> | null = null;
export function driftPaths(): Promise<Map<string, string>> {
  if (!paths)
    paths = loadManifest()
      .then((m) => {
        const out = new Map<string, string>();
        for (const r of (m?.regions ?? []) as any[]) for (const d of r.dates ?? []) if (d.drift) out.set(`${r.id}|${d.date}`, d.drift);
        return out;
      })
      .catch(() => new Map());
  return paths;
}
export const driftKey = (p: { region?: string; datetime?: string | null }) => `${p.region ?? ''}|${(p.datetime ?? '').slice(0, 10)}`;

export async function openDrift(path: string): Promise<DriftFile | null> {
  const d = await loadDrift(path);
  cur = d;
  anim.hour = 0;
  anim.maxHour = d?.hours?.length ? d.hours[d.hours.length - 1] : 72;
  renderDrift();
  return d;
}
export function closeDrift() {
  cur = null;
  anim.playing = false;
  renderDrift();
}
export const driftOpen = () => !!cur;

/** particle position at hour h (linear between the hourly outputs) */
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

function fc(h: number) {
  const feats: any[] = [];
  const starts: any[] = [];
  if (cur) {
    const add = (ps: { path: [number, number, number][] }[], m: number) => {
      for (const p of ps) {
        const c = at(p.path, h);
        if (c) feats.push({ type: 'Feature', geometry: { type: 'Point', coordinates: c }, properties: { m } });
        if (m === 0 && p.path.length) starts.push({ type: 'Feature', geometry: { type: 'Point', coordinates: [p.path[0][0], p.path[0][1]] }, properties: {} });
      }
    };
    add(cur.particles ?? [], 0);
    if (anim.spread) for (const e of cur.ensemble ?? []) add(e.particles ?? [], 1);
  }
  return { now: { type: 'FeatureCollection', features: feats }, start: { type: 'FeatureCollection', features: starts } };
}

/** (re)draw the forecast layers on the case map; called by DriftPlayer on every tick */
export function renderDrift() {
  const map: any = ctl.map;
  if (!map || !map.style?._loaded) return;
  const d = fc(anim.hour);
  const src = (id: string, data: any) => {
    const s = map.getSource(id);
    if (s) s.setData(data);
    else map.addSource(id, { type: 'geojson', data });
  };
  src('c-drift', d.now);
  src('c-drift-start', d.start);
  if (!map.getLayer('c-drift-start'))
    map.addLayer({
      id: 'c-drift-start',
      type: 'circle',
      source: 'c-drift-start',
      paint: { 'circle-radius': 2, 'circle-color': '#ffffff', 'circle-opacity': 0.5 },
    });
  if (!map.getLayer('c-drift'))
    map.addLayer({
      id: 'c-drift',
      type: 'circle',
      source: 'c-drift',
      paint: {
        // «прогноз»: hollow violet rings — not the filled class colours of observations/finds
        'circle-radius': ['case', ['==', ['get', 'm'], 1], 2.2, 3],
        'circle-color': '#b197fc',
        'circle-opacity': ['case', ['==', ['get', 'm'], 1], 0.25, 0.08],
        'circle-stroke-color': '#b197fc',
        'circle-stroke-width': 1.2,
        'circle-stroke-opacity': ['case', ['==', ['get', 'm'], 1], 0.45, 0.95],
      },
    });
  const v = cur ? 'visible' : 'none';
  map.setLayoutProperty('c-drift', 'visibility', v);
  map.setLayoutProperty('c-drift-start', 'visibility', v);
  map.triggerRepaint?.();
}

/** bounds of the whole run (all hours) — to fit the map */
export function driftBounds(d: DriftFile): [number, number, number, number] | null {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of d.particles ?? []) for (const [x, y] of p.path) {
    if (x < x0) x0 = x;
    if (x > x1) x1 = x;
    if (y < y0) y0 = y;
    if (y > y1) y1 = y;
  }
  return Number.isFinite(x0) ? [x0, y0, x1, y1] : null;
}

/** the honest check line (reports/drift_check.md via /api/drift_check); fallback = the published numbers */
export function checkLine(s: any): string {
  const n = s?.n_pairs, k = s?.k_hit, z = s?.k_hit_baseline;
  if (typeof n === 'number' && typeof k === 'number' && typeof z === 'number')
    return `Проверка следующим снимком: прогноз попал в ${k}/${n} пар, «нулевой дрейф» — в ${z}/${n}; направление не подтверждено, показываем как сценарий`;
  return 'Проверка следующим снимком: прогноз попал в 7/18 пар, «нулевой дрейф» — в 7/18; направление не подтверждено, показываем как сценарий';
}
