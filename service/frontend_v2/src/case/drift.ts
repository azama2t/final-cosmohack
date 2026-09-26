// INBOX §44 п.3: the drift forecast back in «Кейс» — an experiment, shown apart from observations (own style, «прогноз»).
// Data: the published OpenDrift run of the snapshot (service/data/<region>/<date>/drift.json, the same file as the old
// live mode); the check result — /api/drift_check (reports/drift_check.md: 7/18 = «нулевой дрейф» 7/18).
// §48 (egor fix pack, screenshot 03): the per-hour ring cloud (one dot per particle) read as "hundreds of new
// finds" — the actual layers (trajectory / corridor / ≤12 sampled points) now live in DriftLayer.ts; this file
// only keeps the animation clock (anim.hour) and the API-facing helpers CaseApp already imports.
import { ctl, anim } from '../map/controller';
import { loadDrift, loadManifest } from '../lib/data';
import type { DriftFile } from '../types';
import { renderDriftLayer, driftCaption, MAX_HORIZON_H } from './DriftLayer';
export { driftCaption, MAX_HORIZON_H };

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
  // §51 п.8: горизонт максимум 72 ч — clamp even if a future published run were longer
  anim.maxHour = Math.min(MAX_HORIZON_H, d?.hours?.length ? d.hours[d.hours.length - 1] : MAX_HORIZON_H);
  renderDrift();
  return d;
}
export function closeDrift() {
  cur = null;
  anim.playing = false;
  renderDrift();
}
export const driftOpen = () => !!cur;

/** (re)draw the forecast layers on the case map; called by DriftPlayer on every tick.
 *  §48: median trajectory + hourly uncertainty corridor + ≤12 sampled points (DriftLayer.ts) — not a per-particle
 *  ring cloud. `anim.spread` (the old "неопределённость" toggle in DriftPlayer) now decides whether the ensemble
 *  runs (other wind-drift-factor scenarios) widen the corridor; the main run's median line is always shown. */
export function renderDrift() {
  const map: any = ctl.map;
  const d = cur && !anim.spread ? { ...cur, ensemble: [] } : cur;
  renderDriftLayer(map, d, anim.hour);
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
