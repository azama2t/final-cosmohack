// §54 п.1: NASA «ежедневно» — GIBS daily true-colour overview (VIIRS / MODIS), any day, loaded from GIBS on the fly.
// Not a detection: our detector does not run on these images; the caption says so. API: /api/v3/nasa/{layers,latest,regions}
// (L131); without the API — the direct GIBS URL (the same WMTS template).
import { useEffect, useState } from 'react';
import './nasa_block.css';
import { get, API_BASE } from './api3';
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
// §60: the browser never goes to GIBS directly — tiles come through our server (same domain, L151 proxy);
// the template is taken from /api/v3/nasa/layers; this fallback (API down) also points at our proxy, not at GIBS.
const proxyTile = (id: string) => `/api/v3/nasa/tile/${id}/{date}/{z}/{y}/{x}.jpg`;
/** MapLibre loads tiles in a worker (blob: base) — a relative template must be made absolute */
const absTile = (u: string) =>
  /^https?:\/\//.test(u) ? u : new URL((u.startsWith('/') ? API_BASE : '') + u, window.location.href).toString().replace(/%7B/g, '{').replace(/%7D/g, '}');
const FALLBACK_LAYERS: NasaLayer[] = [
  { id: 'VIIRS_SNPP_CorrectedReflectance_TrueColor', title: 'VIIRS (Suomi NPP) · истинные цвета', sensor: 'VIIRS', resolution_m: 375, tile_url: proxyTile('VIIRS_SNPP_CorrectedReflectance_TrueColor') },
  { id: 'MODIS_Terra_CorrectedReflectance_TrueColor', title: 'MODIS (Terra) · истинные цвета', sensor: 'MODIS', resolution_m: 250, tile_url: proxyTile('MODIS_Terra_CorrectedReflectance_TrueColor') },
  { id: 'MODIS_Aqua_CorrectedReflectance_TrueColor', title: 'MODIS (Aqua) · истинные цвета', sensor: 'MODIS', resolution_m: 250, tile_url: proxyTile('MODIS_Aqua_CorrectedReflectance_TrueColor') },
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
        layers: (layers.length ? layers : FALLBACK_LAYERS).map((x) => ({ ...x, tile_url: absTile(x.tile_url) })),
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

/** @deprecated §58 п.3: the right floating panel is replaced by <NasaBlock/> in the left column (kept until the mount moves) */
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
  sceneDate?: string | null;
  onLayer: (id: string) => void;
  onDate: (d: string) => void;
  onClose: () => void;
}) {
  void sceneDate; // §58 п.3: the NASA date is NOT linked to the Sentinel-2 scene date
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

// ---- §58 п.3: блок «NASA · ежедневный обзор» в левой колонке (вместо правой плавающей панели) ----
/** min zoom of the NASA raster: on the world-scale 3D globe GIBS tiles give a black patch at the pole
 *  (no-data / polar-night pixels are black JPEG, stretched over the pole cap) → NASA only on the regional map */
export const NASA_MIN_ZOOM = 3;

/** keeps the case map's 'c-nasa' layer regional (zoom ≥ NASA_MIN_ZOOM) — no NASA texture on the world globe */
function useNasaRegionalOnly(active: boolean) {
  useEffect(() => {
    if (!active) return;
    let m: any = null;
    const fix = () => {
      try {
        if (!m?.style || !m.getLayer('c-nasa')) return;
        const l = m.getLayer('c-nasa');
        if ((l.minzoom ?? 0) !== NASA_MIN_ZOOM) m.setLayerZoomRange('c-nasa', NASA_MIN_ZOOM, 24);
      } catch {
        /* style reloading */
      }
    };
    // §70 C: the same layer 'c-nasa' is only adjusted (never re-created here); 'styledata' fires right after addLayer,
    // so the world-scale globe never gets a frame of NASA texture before the zoom range is set
    const attach = () => {
      const mm = (window as any).__caseMap;
      if (!mm?.on || mm === m) return;
      m?.off?.('styledata', fix);
      m = mm;
      m.on('styledata', fix);
      fix();
    };
    attach();
    const t = window.setInterval(attach, 1000); // the map may mount / be replaced later
    return () => {
      window.clearInterval(t);
      m?.off?.('styledata', fix);
    };
  }, [active]);
}

function useMapZoom(active: boolean) {
  const [z, setZ] = useState<number | null>(null);
  useEffect(() => {
    if (!active) return;
    let m: any = null;
    const upd = () => m?.getZoom && setZ(Math.round(m.getZoom() * 10) / 10);
    const attach = () => {
      const mm = (window as any).__caseMap;
      if (!mm?.on || mm === m) return;
      m?.off?.('zoomend', upd);
      m = mm;
      m.on('zoomend', upd);
      upd();
    };
    attach();
    const t = window.setInterval(attach, 1000);
    return () => {
      window.clearInterval(t);
      m?.off?.('zoomend', upd);
    };
  }, [active]);
  return z;
}

export function NasaBlock({
  on,
  info,
  layerOn,
  layerId,
  date,
  onLayer,
  onDate,
  onLayerOn,
  sceneDate,
}: {
  on: boolean; // «Реальное время» mode (nasaOn in CaseApp)
  info: NasaInfo | null;
  layerOn: boolean; // the NASA overview itself (nasaLayerOn)
  layerId: string;
  date: string;
  onLayer: (id: string) => void;
  onDate: (d: string) => void;
  onLayerOn: (v: boolean) => void;
  /** date of the open Sentinel-2 snapshot (YYYY-MM-DD…), if any — for the caption «не часть снимка Sentinel-2 от …» */
  sceneDate?: string | null;
}) {
  const active = on && layerOn;
  const [why, setWhy] = useState(false); // §63 п.1: the explanation lives behind (i)
  useNasaRegionalOnly(active);
  const zoom = useMapZoom(active);
  if (!on) return null;
  if (!layerOn)
    return (
      <div className="c-nasa-block off" data-testid="nasa-block">
        <button className="c-nasa-block-on" onClick={() => onLayerOn(true)} data-testid="nasa-block-on">
          <span className="c-nasa-dot" aria-hidden /> NASA · ежедневный обзор — включить
        </button>
      </div>
    );
  const layers = info?.layers ?? FALLBACK_LAYERS;
  const L = layers.find((x) => x.id === layerId) ?? layers[0];
  const max = info?.latest ?? date;
  const partial = !!info && date === info.latest && info.latest !== info.latestFull;
  const lowZoom = zoom != null && zoom < NASA_MIN_ZOOM;
  // §63 п.1: a normal block in the column flow (no overlay / sticky), compact: header · one control row · one status line
  return (
    <details className="c-nasa-block" open data-testid="nasa-block">
      <summary>
        <span className="c-nasa-dot" aria-hidden />
        <span className="c-nasa-title">NASA · ежедневный обзор</span>
        <button
          className={`c-nasa-i ${why ? 'on' : ''}`}
          aria-expanded={why}
          aria-label="Что это за слой"
          title="Что это за слой"
          data-testid="nasa-why"
          onClick={(e) => {
            e.preventDefault();
            setWhy(!why);
          }}
        >
          i
        </button>
        <button
          className="c-nasa-block-off"
          onClick={(e) => {
            e.preventDefault();
            onLayerOn(false);
          }}
          data-testid="nasa-off"
          title="Выключить NASA: подложка, элемент таймлайна и это управление"
        >
          Выкл.
        </button>
      </summary>
      <div className="c-nasa-block-body">
        {why && (
          <div className="note c-nasa-why" data-testid="nasa-caption">
            Обзорный снимок {L.sensor ?? ''} ~{L.resolution_m} м — облака, цветение, пятна. <b>Не обнаружение пластика</b>: наш детектор на кадрах NASA не
            запускается. Дата обзора — день подложки, не связана с периодом снимков Sentinel-2. Чёрные полосы — в этот день там нет съёмки. На глобусе слой не
            рисуется (у полюса тайлы дают чёрное пятно), только при приближении. {info?.attribution ?? 'Снимки: NASA EOSDIS GIBS'}.
          </div>
        )}
        <div className="c-nasa-row">
          <span className="c-nasa-date">
            <button onClick={() => onDate(shiftDay(date, -1))} data-testid="nasa-prev" title="Предыдущий день" aria-label="Предыдущий день">
              ◀
            </button>
            <input
              type="date"
              value={date}
              min="2012-01-19"
              max={max}
              onChange={(e) => e.target.value && onDate(e.target.value)}
              data-testid="nasa-date"
              aria-label="Дата обзора NASA (день подложки; не связана с периодом Sentinel-2)"
              title="Дата обзора NASA (день подложки; не связана с периодом Sentinel-2)"
            />
            <button onClick={() => onDate(shiftDay(date, 1))} disabled={date >= max} data-testid="nasa-next" title="Следующий день" aria-label="Следующий день">
              ▶
            </button>
          </span>
          <span className="c-nasa-sw" role="radiogroup" aria-label="Спутник NASA">
            {layers.map((x) => (
              <button
                key={x.id}
                role="radio"
                aria-checked={x.id === L.id}
                className={x.id === L.id ? 'on' : ''}
                onClick={() => onLayer(x.id)}
                data-testid={`nasa-layer-${x.sensor?.toLowerCase() ?? x.id}`}
                title={x.title}
              >
                {x.id.startsWith('VIIRS') ? 'VIIRS' : x.id.includes('Aqua') ? 'Aqua' : 'Terra'}
              </button>
            ))}
          </span>
        </div>
        <div className="c-nasa-line" data-testid="nasa-date-line" title={`${L.title} · ~${L.resolution_m} м · не обнаружение пластика`}>
          NASA обзор, дата {dateRu(date)} — не часть снимка Sentinel-2{sceneDate ? ` от ${dateRu(sceneDate.slice(0, 10))}` : ''}
          {partial ? ' · день ещё собирается' : ''}
          {info && date !== info.latestFull && (
            <>
              {' · '}
              <button className="link" onClick={() => onDate(info.latestFull)} data-testid="nasa-latest" title="Последний полный день">
                последний
              </button>
            </>
          )}
        </div>
        {lowZoom && (
          <div className="c-nasa-zoomhint" data-testid="nasa-zoomhint" title="На глобусе обзор NASA не рисуется (у полюса тайлы дают чёрное пятно)">
            На этом масштабе обзор NASA не показывается — приблизьте карту к району
          </div>
        )}
      </div>
    </details>
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
