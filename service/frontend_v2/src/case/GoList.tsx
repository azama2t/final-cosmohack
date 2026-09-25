// «Куда идти обследовать»: sites of the current filter ranked by a stated rule (no invented numbers):
//  1) the survey strip of the site was clean by the quality masks on a past candidate scene (a scene can see it);
//  2) the site's field density is high within ITS size profile (percentile among the organisers' records of that profile);
//  then the nearest satellite pass and its synchronisation window from POST /api/v3/pairfinder (for the top rows only).
import { useEffect, useMemo, useState } from 'react';
import Info from '../components/Info';
import { send, type FC, type Feat, type Meta, type ObsProps, type ZoneProps } from './api3';
import { dateRu, eventRu, num, profileRu, sourceShort } from './fmt';
import { geomCenter } from './CaseMap';

export interface Site {
  event: string;
  source: string;
  profile: string | null;
  best: Feat<ObsProps>;
  value: number;
  pct: number;
  clean: { zone: Feat<ZoneProps>; valid: number | null } | null;
  at: [number, number] | null;
}

interface Win {
  loading: boolean;
  err?: string;
  mission?: string;
  pass?: string;
  from?: string;
  to?: string;
  timeUnknown?: boolean;
  none?: string;
}

const TOP = 8;

function isClean(z: Feat<ZoneProps>): boolean {
  const q = z.properties.quality;
  if (z.properties.suspicious_pixels?.quality_rejected) return false;
  const bad = (z.properties.pair_reject_reasons ?? []).some((r) => r === 'CLOUD' || r === 'GLINT' || r === 'NODATA');
  return !bad && (q?.valid_fraction ?? 0) >= 0.5 && (q?.cloud_fraction ?? 1) <= 0.2;
}

export function rankSites(all: FC<ObsProps> | null, shown: FC<ObsProps> | null, zones: FC<ZoneProps> | null): Site[] {
  if (!all || !shown) return [];
  // percentile of a density within its own profile (all organisers' records of that profile)
  const byProf = new Map<string, number[]>();
  for (const f of all.features) {
    const v = f.properties.concentration_items_km2;
    if (v === null || v === undefined || !f.properties.measurement_profile) continue;
    const k = f.properties.measurement_profile;
    if (!byProf.has(k)) byProf.set(k, []);
    byProf.get(k)!.push(v);
  }
  for (const v of byProf.values()) v.sort((a, b) => a - b);
  const pctOf = (prof: string, v: number) => {
    const a = byProf.get(prof) ?? [];
    if (!a.length) return 0;
    let n = 0;
    while (n < a.length && a[n] <= v) n++;
    return n / a.length;
  };
  const zoneBySample = new Map<string, Feat<ZoneProps>>();
  for (const z of zones?.features ?? []) for (const s of z.properties.support?.linked_sample_ids ?? []) zoneBySample.set(s, z);
  const byEvent = new Map<string, Site>();
  for (const f of shown.features) {
    const p = f.properties;
    const v = p.concentration_items_km2;
    if (v === null || v === undefined || v <= 0 || !p.measurement_profile) continue;
    const cur = byEvent.get(p.event_id);
    const z = zoneBySample.get(p.sample_id);
    const clean = z && isClean(z) ? { zone: z, valid: z.properties.quality?.valid_fraction ?? null } : null;
    if (!cur || v > cur.value) {
      byEvent.set(p.event_id, {
        event: p.event_id,
        source: p.source_id,
        profile: p.measurement_profile,
        best: f,
        value: v,
        pct: pctOf(p.measurement_profile, v),
        clean: clean ?? cur?.clean ?? null,
        at: ((p as any).track_center as [number, number]) ?? geomCenter(f.geometry),
      });
    } else if (clean && !cur.clean) cur.clean = clean;
  }
  return [...byEvent.values()].sort((a, b) => (b.clean ? 1 : 0) - (a.clean ? 1 : 0) || b.pct - a.pct || b.value - a.value);
}

const hm = (s?: string) => (s ? s.slice(11, 16) : '');

export default function GoList({
  meta,
  sites,
  onPick,
  sel,
}: {
  meta: Meta;
  sites: Site[];
  onPick: (s: Site) => void;
  sel: string | null;
}) {
  const [win, setWin] = useState<Record<string, Win>>({});
  const top = useMemo(() => sites.slice(0, TOP), [sites]);
  const key = top.map((s) => s.event).join('|');
  useEffect(() => {
    let alive = true;
    // a date 6 days ahead → the pairfinder «forecast» mode (its ±5.5-day window starts after today): past passes projected
    // forward with the orbit repeat period (S2 10 d, Landsat 16 d); the earliest one is the nearest pass
    const day = new Date(Date.now() + 6 * 86400e3).toISOString().slice(0, 10);
    const now = new Date().toISOString();
    const todo = top.filter((s) => !win[s.event] && s.at);
    if (!todo.length) return;
    setWin((w) => ({ ...w, ...Object.fromEntries(todo.map((s) => [s.event, { loading: true }])) }));
    let i = 0;
    const worker = async () => {
      while (alive && i < todo.length) {
        const s = todo[i++];
        try {
          const t = s.best.properties.transect;
          const d = await send<any>('POST', '/api/v3/pairfinder', {
            geometry: { type: 'Point', coordinates: s.at },
            datetime: day,
            ...(t?.width_m ? { width_m: t.width_m } : {}),
          });
          const next = (d?.candidates ?? [])
            .filter((c: any) => c.scene_datetime && c.scene_datetime >= now)
            .sort((a: any, b: any) => a.scene_datetime.localeCompare(b.scene_datetime))[0];
          const w: Win = next
            ? { loading: false, mission: next.mission, pass: next.scene_datetime, from: next.sync_time_window?.from, to: next.sync_time_window?.to, timeUnknown: true }
            : { loading: false, none: d?.empty_reason ?? 'пролётов в ближайшие 11 сут не найдено' };
          if (alive) setWin((x) => ({ ...x, [s.event]: w }));
        } catch (e: any) {
          if (alive) setWin((x) => ({ ...x, [s.event]: { loading: false, err: e?.message ?? 'ошибка поиска' } }));
        }
      }
    };
    Promise.all([worker(), worker(), worker()]);
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return (
    <div data-testid="go-list">
      <div className="c-list-note">
        Сначала чистая полоса в прошлом, затем плотность в своём профиле
        <Info label="Правило">
          Ранжирование: (1) полоса участка была чистой по маскам качества на прошлом снимке-кандидате (вода ≥ 50 %, облака ≤ 20 %, без блика) — снимок
          её видит; (2) плотность замера — процентиль среди всех записей того же размерного профиля (профили несравнимы между собой). Для первых {TOP}
          участков — ближайший прогнозный пролёт Sentinel-2 / Landsat и окно синхронизации из «Подбора снимка» (/api/v3/pairfinder, прогноз по периоду повторения орбиты):
          быть на участке в эти часы, чтобы дрейф не превысил допуск. Облачность будущего снимка неизвестна.
        </Info>
      </div>
      {!sites.length && <div className="empty">Нет замеров плотности под выбранные фильтры</div>}
      {sites.slice(0, 30).map((s, i) => {
        const w = win[s.event];
        return (
          <button key={s.event} className={`c-zi c-go ${sel === s.best.id ? 'on' : ''}`} onClick={() => onPick(s)} data-testid="go-item">
            <span className="c-go-n">{i + 1}</span>
            <span className="c-zi-main">
              <span className="c-zi-t" title={sourceShort(meta, s.source)}>
                {eventRu(s.event)}
              </span>
              <span className="c-zi-s" title={`${num(s.value)} шт./км², ${profileRu(meta, s.profile)}`}>
                {num(s.value)} шт./км² · верх {num(Math.max(1, Math.round((1 - s.pct) * 100)), 0)} %{s.clean ? ` · полоса чистая ${dateRu(s.clean.zone.properties.datetime)}` : ''}
              </span>
              {i < TOP && (
                <span className="c-zi-s c-go-w" data-testid="go-window">
                  {!w || w.loading
                    ? 'ищу ближайший пролёт…'
                    : w.err
                      ? `окно: ${w.err}`
                      : w.none
                        ? `окно: ${w.none}`
                        : `окно ${dateRu(w.from).slice(0, 5)} ${hm(w.from)}–${hm(w.to)} UTC · ${w.mission} ${hm(w.pass)}`}
                </span>
              )}
            </span>
          </button>
        );
      })}
    </div>
  );
}
