// §55 п.1 / §58 п.1–2: «Реальное время» = свежие Sentinel-2, обработанные нашей моделью (автоматически, не проверено
// человеком). Separate from Nasa.tsx (NASA daily overview — L140 since §58).
import { useEffect, useState } from 'react';
import { get } from './api3';
import { dateRu } from './fmt';

// ---- §55 п.1: папка «Реальное время» = Sentinel-2, обработанные нашей моделью (+ NASA · ежедневный обзор) ----
export interface FreshScene {
  key: string;
  region: string;
  region_name?: string;
  date: string;
  datetime?: string;
  tile?: string;
  bounds?: number[] | null;
  evaluable?: boolean;
  n_zones_total?: number;
  n_zones?: number;
  by_status?: { detected?: number; not_detected?: number; insufficient_data?: number };
  crop_cloud_frac?: number | null;
  wind10m_ms?: number | null;
  wind_high?: boolean;
  processed_at?: string | null;
  rgb_url?: string | null;
  zones_url?: string | null;
}
export interface FreshRegion {
  id: string;
  label: string;
  n_scenes: number;
  n_zones: number;
  n_finds: number;
  last_date?: string;
  bbox?: number[];
  dates: FreshScene[];
}
export interface FreshInfo {
  folder?: string;
  subfolder?: string;
  label?: string;
  note?: string;
  counter?: string;
  last_update?: string | null;
  new_scenes_last_run?: number | null;
  funnel_label?: string;
  last_success_label?: string;
  counter_note?: string;
  search_window?: { days?: number; label?: string };
  funnel?: { status_note?: string | null; complete?: boolean };
  honesty?: { nasa?: string; detector_on?: string; quantity?: string; class?: string };
  regions: FreshRegion[];
}

export function useFreshInfo(on: boolean): FreshInfo | null | 'error' {
  const [d, setD] = useState<FreshInfo | null | 'error'>(null);
  useEffect(() => {
    if (!on || (d && d !== 'error')) return;
    const ac = new AbortController();
    get<FreshInfo>('/api/v3/fresh_s2', {}, ac.signal).then(
      (x) => setD(x),
      () => {
        if (!ac.signal.aborted) setD('error');
      },
    );
    return () => ac.abort();
  }, [on]); // eslint-disable-line react-hooks/exhaustive-deps
  return d;
}

const hhmm = (iso?: string | null) => {
  if (!iso) return '—';
  const t = new Date(iso);
  return Number.isFinite(t.getTime()) ? `${dateRu(iso.slice(0, 10))} ${t.toTimeString().slice(0, 5)}` : iso;
};
const ZST: Record<string, string> = { detected: 'обнаружено', insufficient_data: 'недостаточно данных', not_detected: 'не обнаружено' };

export function RealtimeFolder({
  fresh,
  freshKey,
  freshZones,
  onFresh,
  onRegion,
  onFreshZone,
}: {
  fresh: FreshInfo | null | 'error';
  freshKey: string | null;
  freshZones: { features: any[] } | null;
  onFresh: (s: FreshScene | null) => void;
  onRegion: (bbox: number[]) => void;
  onFreshZone: (f: any) => void;
}) {
  const [openReg, setOpenReg] = useState<string | null>(null);
  const fi = fresh && fresh !== 'error' ? fresh : null;
  return (
    <details className="c-nasa-folder c-rt-folder" data-testid="realtime-folder">
      <summary>
        <span className="c-mk rt" aria-hidden /> {fi?.folder ?? 'Реальное время'} · свежие Sentinel-2 (авто)
        <span className="faint c-rt-sum" data-testid="realtime-summary">
          {' '}
          · {fi ? fi.search_window?.label ?? fi.counter : fresh === 'error' ? 'недоступно' : 'загрузка…'}
          {fi?.last_success_label ? ` · ${fi.last_success_label}` : ''}
        </span>
      </summary>
      <details className="c-rt-sub" open data-testid="realtime-s2">
        <summary>{fi?.subfolder ?? 'Sentinel-2 · обработано нашей моделью'}</summary>
        {fresh === 'error' && <div className="note c-pad-s">сервис свежих снимков недоступен</div>}
        {!fresh && <div className="note c-pad-s">загрузка…</div>}
        {fi && (
          <>
            {fi.funnel_label && (
              <div className="c-rt-counter" data-testid="realtime-funnel">
                {fi.funnel_label}
              </div>
            )}
            {fi.funnel?.status_note && <div className="note c-pad-s">{fi.funnel.status_note}</div>}
            <div className="c-rt-counter" data-testid="realtime-counter">
              {fi.counter}
            </div>
            {fi.counter_note && <div className="note c-pad-s">{fi.counter_note}</div>}
            <div className="note c-pad-s" data-testid="realtime-updated">
              {fi.last_success_label ?? `последнее обновление: ${hhmm(fi.last_update)}`}, новых снимков в последнем запуске: {fi.new_scenes_last_run ?? 0}
            </div>
            <div className="c-rt-label" data-testid="realtime-label">
              {fi.label ?? 'автоматически, не проверено человеком'}
            </div>
            {fi.regions.map((r) => (
              <div key={r.id} className="c-rt-reg">
                <button
                  className="reg-item c-nasa-reg"
                  onClick={() => {
                    setOpenReg((v) => (v === r.id ? null : r.id));
                    if (r.bbox) onRegion(r.bbox);
                  }}
                  data-testid="realtime-region"
                  data-region={r.id}
                  aria-expanded={openReg === r.id}
                >
                  <span className="ri-name">
                    {openReg === r.id ? '▾' : '▸'} {r.label}
                  </span>
                  <span className="ri-sub">
                    {r.n_scenes} сн. · зон {r.n_zones} · находок {r.n_finds}
                    {r.last_date ? ` · последний ${dateRu(r.last_date)}` : ''}
                  </span>
                </button>
                {openReg === r.id &&
                  r.dates.map((s) => {
                    const on = s.key === freshKey;
                    const bs = s.by_status ?? {};
                    return (
                      <div key={s.key} className={`c-rt-date ${on ? 'on' : ''}`}>
                        <button className="link" onClick={() => onFresh(on ? null : s)} data-testid="realtime-date" data-scene={s.key} aria-pressed={on}>
                          {dateRu(s.date)} · Sentinel-2 {s.tile ?? ''}
                        </button>
                        <span className="faint">
                          {' '}
                          · обнаружено {bs.detected ?? 0} · недост. данных {bs.insufficient_data ?? 0} · не обнаружено {bs.not_detected ?? 0}
                          {s.crop_cloud_frac !== null && s.crop_cloud_frac !== undefined ? ` · облачность ${Math.round(s.crop_cloud_frac * 100)} %` : ''}
                          {s.processed_at ? ` · обработан ${hhmm(s.processed_at)}` : ''}
                        </span>
                        {on && (
                          <div className="c-rt-zones" data-testid="realtime-zones">
                            {!freshZones && <span className="faint">зоны…</span>}
                            {freshZones && !freshZones.features.length && <span className="faint">зон нет</span>}
                            {freshZones?.features.map((f: any, i: number) => (
                              <button key={f.properties?.zone_id ?? i} className="link c-rt-zone" onClick={() => onFreshZone(f)} data-testid="realtime-zone">
                                зона {i + 1}: {ZST[f.properties?.detection_status] ?? f.properties?.detection_status}
                                {f.properties?.flags?.length ? ` · признаки: ${f.properties.flags.join(', ')}` : ''}
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })}
              </div>
            ))}
            <div className="note c-pad-s">
              {fi.honesty?.quantity ?? 'количества по спутниковому снимку нет'}; {fi.honesty?.class ?? 'класс детектора — любой плавающий материал'}
            </div>
          </>
        )}
      </details>
    </details>
  );
}
