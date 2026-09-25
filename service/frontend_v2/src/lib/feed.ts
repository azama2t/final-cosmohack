// Event feed (left column). Source: /api/feed (routes_incidents.py); fallback without it — built on the client
// from manifest + zones.json (+ labels). The feed is curated for calm reading: one model, at most a few events
// per region/date, operator events always kept.
import type { Manifest, ZonesFile } from '../types';
import { apiGet, hasApi } from './api';
import { getJSONOpt, isFlagged, modelPath, shortName, summaryDate } from './data';
import { fmtNum } from './style';

export type FeedKind =
  | 'new'
  | 'zone'
  | 'excluded'
  | 'under_review'
  | 'confirmed'
  | 'false_alarm'
  | 'resolved'
  | 'reopened'
  | 'detected'
  | 'threat'
  | 'haze';

export interface FeedEvent {
  key: string;
  ts: string;
  kind: FeedKind;
  text: string;
  region: string;
  regionName: string;
  date: string;
  model?: string;
  lon?: number;
  lat?: number;
  priority?: number | null;
  area_m2?: number | null;
  incidentId?: string;
  incidentKind?: 'detection' | 'zone';
  status?: string;
  actor?: string;
}

export const KIND_CLASS: Record<string, string> = {
  new: 'k-spot',
  zone: 'k-spot',
  detected: 'k-spot',
  excluded: 'k-artifact',
  false_alarm: 'k-false',
  confirmed: 'k-confirmed',
  under_review: 'k-review',
  reopened: 'k-review',
  resolved: 'k-review',
  threat: 'k-threat',
  haze: 'k-haze',
};

const OPERATOR = new Set(['under_review', 'confirmed', 'false_alarm', 'resolved', 'reopened']);

/** Keep operator events; per region/date: ≤ 3 new spots (best priority), ≤ 1 exclusion; zones only if no spots. */
export function curate(items: FeedEvent[], model: string, max = 80): FeedEvent[] {
  const byRD = new Map<string, FeedEvent[]>();
  const out: FeedEvent[] = [];
  for (const it of items) {
    if (OPERATOR.has(it.kind) || it.kind === 'haze') {
      out.push(it);
      continue;
    }
    if (it.model && it.model !== model) continue;
    const k = `${it.region}|${it.date}`;
    if (!byRD.has(k)) byRD.set(k, []);
    byRD.get(k)!.push(it);
  }
  for (const list of byRD.values()) {
    const pr = (e: FeedEvent) => e.priority ?? 99;
    const spots = list.filter((e) => e.kind === 'new').sort((a, b) => pr(a) - pr(b) || (b.area_m2 ?? 0) - (a.area_m2 ?? 0));
    const zones = list.filter((e) => e.kind === 'zone').sort((a, b) => pr(a) - pr(b));
    out.push(...(spots.length ? spots.slice(0, 3) : zones.slice(0, 2)));
  }
  return out
    .sort((a, b) => b.ts.localeCompare(a.ts) || (a.priority ?? 99) - (b.priority ?? 99))
    .slice(0, max);
}

export async function loadFeed(m: Manifest, model: string): Promise<{ items: FeedEvent[]; source: 'api' | 'client' }> {
  if (await hasApi('/api/feed')) {
    const r = await apiGet<{ items: any[] }>('/api/feed', { limit: 1000 });
    if (r && Array.isArray(r.items)) {
      const items: FeedEvent[] = r.items.map((x, i) => ({
        key: `${x.incident_id ?? i}|${x.kind}|${x.ts}`,
        ts: String(x.ts ?? x.date ?? ''),
        kind: x.kind as FeedKind,
        // wording (INBOX §4): «признаки плавающего материала», not «пятно мусора»
        text: String(x.text ?? '').replace(/^новое пятно/, 'признаки материала'),
        region: x.region,
        regionName: x.region_name ?? x.region,
        date: x.date,
        model: x.model,
        lon: x.lon,
        lat: x.lat,
        priority: x.priority,
        area_m2: x.area_m2,
        incidentId: x.incident_id,
        incidentKind: x.incident_kind,
        status: x.status,
        actor: x.actor,
      }));
      return { items: curate(items, model), source: 'api' };
    }
  }
  return { items: curate(await clientFeed(m, model), model), source: 'client' };
}

/** Fallback: top zones of each region's summary date (zones.json) + haze notes. */
async function clientFeed(m: Manifest, model: string): Promise<FeedEvent[]> {
  const out: FeedEvent[] = [];
  await Promise.all(
    m.regions.map(async (r) => {
      const d = summaryDate(r);
      if (!d) return;
      const mdl = d.models.includes(model) ? model : d.models[0];
      const z = await getJSONOpt<ZonesFile>(modelPath(r.id, d.date, mdl, 'zones.json'));
      const name = shortName(r.name);
      for (const zz of (z?.zones ?? []).slice(0, 3))
        out.push({
          key: `${r.id}|${d.date}|z${zz.rank}`,
          ts: `${d.date}T12:00:00Z`,
          kind: 'new',
          text: `признаки материала · ${name} · ${fmtNum(zz.area_m2)} м² · приоритет ${zz.rank}`,
          region: r.id,
          regionName: name,
          date: d.date,
          model: mdl,
          lon: zz.lon,
          lat: zz.lat,
          priority: zz.rank,
          area_m2: zz.area_m2,
        });
      if (isFlagged(r.dates[r.dates.length - 1]))
        out.push({
          key: `${r.id}|haze`,
          ts: `${r.dates[r.dates.length - 1].date}T12:00:00Z`,
          kind: 'haze',
          text: `снимок с дымкой/бликом · ${name} · находки могут быть завышены`,
          region: r.id,
          regionName: name,
          date: r.dates[r.dates.length - 1].date,
        });
    }),
  );
  return out;
}

const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
export function feedDate(iso: string): string {
  const [y, mo, d] = iso.slice(0, 10).split('-').map(Number);
  if (!y) return iso;
  return `${d} ${MONTHS[mo - 1]} ${y}`;
}
