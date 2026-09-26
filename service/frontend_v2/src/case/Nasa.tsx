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
