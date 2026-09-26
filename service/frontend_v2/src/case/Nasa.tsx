// §54 п.1: NASA «ежедневно» — GIBS daily true-colour overview (VIIRS / MODIS), any day, loaded from GIBS on the fly.
// Not a detection: our detector does not run on these images; the caption says so. API: /api/v3/nasa/{layers,latest,regions}
// (L131); without the API — the direct GIBS URL (the same WMTS template).
import { useEffect, useState } from 'react';
import { get } from './api3';
import { dateRu } from './fmt';

export interface NasaLayer {
  id: string;
  title: string;
  sensor?: string;
  satellite?: string;
  resolution_m: number;
  tile_url: string;
  max_native_zoom?: number;
  tile_size?: number;
}
export interface NasaRegion {
  id: string;
  label: string;
  short?: string;
  bbox: [number, number, number, number];
  center?: [number, number];
  zoom?: number;
  n_scenes_s2?: number;
}
export interface NasaInfo {
  folder: string;
  caption: string;
  notWhat: string;
  attribution: string;
  layers: NasaLayer[];
  defaultLayer: string;
  latest: string;
  latestFull: string;
  regions: NasaRegion[];
}

const FALLBACK_CAPTION = 'ежедневный обзорный снимок NASA 250–375 м; пластик на таком разрешении не обнаруживается — для обзора облачности/цветения/пятен';
const gibs = (id: string) => `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/${id}/default/{date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`;
const FALLBACK_LAYERS: NasaLayer[] = [
  { id: 'VIIRS_SNPP_CorrectedReflectance_TrueColor', title: 'VIIRS (Suomi NPP) · истинные цвета', sensor: 'VIIRS', resolution_m: 375, tile_url: gibs('VIIRS_SNPP_CorrectedReflectance_TrueColor') },
  { id: 'MODIS_Terra_CorrectedReflectance_TrueColor', title: 'MODIS (Terra) · истинные цвета', sensor: 'MODIS', resolution_m: 250, tile_url: gibs('MODIS_Terra_CorrectedReflectance_TrueColor') },
  { id: 'MODIS_Aqua_CorrectedReflectance_TrueColor', title: 'MODIS (Aqua) · истинные цвета', sensor: 'MODIS', resolution_m: 250, tile_url: gibs('MODIS_Aqua_CorrectedReflectance_TrueColor') },
];
const isoDay = (t: number) => new Date(t).toISOString().slice(0, 10);
export const shiftDay = (d: string, n: number) => isoDay(Date.parse(d + 'T00:00:00Z') + n * 864e5);

/** loaded once, on the first switch-on */
export function useNasaInfo(on: boolean): NasaInfo | null {
  const [info, setInfo] = useState<NasaInfo | null>(null);
  useEffect(() => {
    if (!on || info) return;
    const ac = new AbortController();
    const s = ac.signal;
    const y = isoDay(Date.now() - 864e5);
    Promise.all([
      get<any>('/api/v3/nasa/layers', {}, s).catch(() => null),
      get<any>('/api/v3/nasa/latest', {}, s).catch(() => null),
      get<any>('/api/v3/nasa/regions', {}, s).catch(() => null),
    ]).then(([l, t, r]) => {
      if (s.aborted) return;
      // HLS (30 м) is not a daily global mosaic — only the daily true-colour layers go to the switch
      const layers: NasaLayer[] = (l?.layers ?? []).filter((x: NasaLayer) => /CorrectedReflectance_TrueColor/.test(x.id));
      setInfo({
        folder: l?.folder ?? 'NASA · ежедневно',
        caption: l?.caption ?? FALLBACK_CAPTION,
        notWhat: l?.not_what ?? 'не обнаружение пластика и не оценка количества; наш детектор на этих снимках не запускается',
        attribution: l?.attribution ?? 'Снимки: NASA EOSDIS GIBS',
        layers: layers.length ? layers : FALLBACK_LAYERS,
        defaultLayer: l?.default_layer ?? FALLBACK_LAYERS[0].id,
        latest: t?.date ?? y,
        latestFull: t?.latest_full ?? t?.date ?? y,
        regions: r?.regions ?? [],
      });
    });
    return () => ac.abort();
  }, [on, info]);
  return info;
}

export function NasaMapPanel({
  info,
  layerId,
  date,
  sceneDate,
  onLayer,
  onDate,
  onClose,
}: {
  info: NasaInfo | null;
  layerId: string;
  date: string;
  sceneDate: string | null;
  onLayer: (id: string) => void;
  onDate: (d: string) => void;
  onClose: () => void;
}) {
  const layers = info?.layers ?? FALLBACK_LAYERS;
  const L = layers.find((x) => x.id === layerId) ?? layers[0];
  const max = info?.latest ?? date;
  const partial = !!info && date === info.latest && info.latest !== info.latestFull;
  return (
    <div className="c-nasa-note" data-testid="nasa-note">
      <b>{info?.folder ?? 'NASA · ежедневно'} — обзорный снимок, не обнаружение пластика</b>
      <span data-testid="nasa-caption">{info?.caption ?? FALLBACK_CAPTION}</span>
      <span className="c-nasa-date">
        <button onClick={() => onDate(shiftDay(date, -1))} data-testid="nasa-prev" title="Предыдущий день" aria-label="Предыдущий день">
          ◀
        </button>
        <input type="date" value={date} min="2012-01-19" max={max} onChange={(e) => e.target.value && onDate(e.target.value)} data-testid="nasa-date" aria-label="Дата снимка NASA" />
        <button onClick={() => onDate(shiftDay(date, 1))} disabled={date >= max} data-testid="nasa-next" title="Следующий день" aria-label="Следующий день">
          ▶
        </button>
      </span>
      <span className="faint" data-testid="nasa-date-line">
        {L.sensor ?? ''} {L.satellite ? `(${L.satellite})` : ''} · {dateRu(date)} (UTC) · ~{L.resolution_m} м
        {partial ? ' · день ещё собирается, часть Земли может быть пустой' : ''}
      </span>
      <span className="c-nasa-sw">
        {layers.map((x) => (
          <button key={x.id} className={x.id === L.id ? 'on' : ''} onClick={() => onLayer(x.id)} data-testid={`nasa-layer-${x.sensor?.toLowerCase() ?? x.id}`} title={x.title}>
            {x.id.startsWith('VIIRS') ? 'VIIRS' : x.id.includes('Aqua') ? 'MODIS Aqua' : 'MODIS Terra'}
          </button>
        ))}
        {sceneDate && sceneDate !== date && (
          <button onClick={() => onDate(sceneDate)} data-testid="nasa-scene-date" title="Та же дата, что у открытого снимка Sentinel-2">
            к дате снимка
          </button>
        )}
        {info && date !== info.latestFull && (
          <button onClick={() => onDate(info.latestFull)} data-testid="nasa-latest" title="Последний полный день">
            последний
          </button>
        )}
        <button onClick={onClose} data-testid="nasa-off" title="Скрыть NASA (папку, слой и подписи)">
          ✕
        </button>
      </span>
      <span className="faint c-nasa-attr">{info?.attribution ?? 'Снимки: NASA EOSDIS GIBS'}</span>
    </div>
  );
}

/** the folder «NASA · ежедневно» of the районы tree: the same районы as the Sentinel-2 case; click → the район on the map */
export function NasaFolder({ info, onRegion }: { info: NasaInfo | null; onRegion: (r: NasaRegion) => void }) {
  return (
    <details className="c-nasa-folder" open data-testid="nasa-folder">
      <summary>
        <span className="c-nasa-dot" aria-hidden /> {info?.folder ?? 'NASA · ежедневно'}
        <span className="faint"> · {info ? `${info.regions.length} районов` : 'загрузка…'}</span>
      </summary>
      <div className="note c-pad-s">обзор облачности / цветения / пятен, не обнаружение</div>
      {(info?.regions ?? []).map((r) => (
        <button key={r.id} className="reg-item c-nasa-reg" onClick={() => onRegion(r)} data-testid="nasa-region" data-region={r.id} title={r.label}>
          <span className="ri-name">{r.short ?? r.label}</span>
          <span className="ri-sub">NASA · ежедневно{r.n_scenes_s2 ? ` · Sentinel-2: ${r.n_scenes_s2}` : ''}</span>
        </button>
      ))}
    </details>
  );
}

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
  nasa,
  nasaLayerOn,
  onNasaLayer,
  freshKey,
  freshZones,
  onFresh,
  onRegion,
  onFreshZone,
}: {
  fresh: FreshInfo | null | 'error';
  nasa: NasaInfo | null;
  nasaLayerOn: boolean;
  onNasaLayer: () => void;
  freshKey: string | null;
  freshZones: { features: any[] } | null;
  onFresh: (s: FreshScene | null) => void;
  onRegion: (bbox: number[]) => void;
  onFreshZone: (f: any) => void;
}) {
  const [openReg, setOpenReg] = useState<string | null>(null);
  const fi = fresh && fresh !== 'error' ? fresh : null;
  return (
    <details className="c-nasa-folder c-rt-folder" open data-testid="realtime-folder">
      <summary>
        <span className="c-rt-dot" aria-hidden /> {fi?.folder ?? 'Реальное время'}
      </summary>
      <details className="c-rt-sub" open data-testid="realtime-s2">
        <summary>{fi?.subfolder ?? 'Sentinel-2 · обработано нашей моделью'}</summary>
        {fresh === 'error' && <div className="note c-pad-s">сервис свежих снимков недоступен</div>}
        {!fresh && <div className="note c-pad-s">загрузка…</div>}
        {fi && (
          <>
            <div className="c-rt-counter" data-testid="realtime-counter">
              {fi.counter}
            </div>
            <div className="note c-pad-s" data-testid="realtime-updated">
              последнее обновление: {hhmm(fi.last_update)}, новых снимков: {fi.new_scenes_last_run ?? 0}
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
      <details className="c-rt-sub" open data-testid="realtime-nasa">
        <summary>NASA · ежедневный обзор</summary>
        <div className="note c-pad-s" data-testid="realtime-nasa-honesty">
          {fi?.honesty?.nasa ?? 'NASA MODIS/VIIRS (250–375 м) — только ежедневный обзор; модель на кадрах NASA не запускаем'}
        </div>
        <button className="menu-row" onClick={onNasaLayer} aria-pressed={nasaLayerOn} data-testid="realtime-nasa-layer">
          <span className={`check ${nasaLayerOn ? 'on' : ''}`} aria-hidden /> слой NASA на карте
        </button>
        {(nasa?.regions ?? []).map((r) => (
          <button key={r.id} className="reg-item c-nasa-reg" onClick={() => onRegion(r.bbox)} data-testid="nasa-region" data-region={r.id} title={r.label}>
            <span className="ri-name">{r.short ?? r.label}</span>
            <span className="ri-sub">NASA · ежедневно{r.n_scenes_s2 ? ` · Sentinel-2 кейса: ${r.n_scenes_s2}` : ''}</span>
          </button>
        ))}
      </details>
    </details>
  );
}
