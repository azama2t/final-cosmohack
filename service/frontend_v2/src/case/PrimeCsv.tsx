// L140 · §55 п.2 в / §55а: PRIME MODE = ДЕМО-МАКЕТ (по запросу жюри) — демо-«снимки» скоплений в точках событий CSV.
// Числа карточки — из самой строки CSV («демо-значение»); «найдено счётчиком (эксперимент)» — только если L154 дал n_found.
// Данные: GET /api/prime/csv_scenes (L154: data/case/prime_csv/index.json + reports/prime/metrics.json),
// картинки GET /api/prime/csv_img/… . Поля читаются терпимо (несколько имён), чтобы не зависеть от мелочей формата.
// Карта: свой источник/слой 'prime-csv*' (пурпурный) на window.__caseMap; снимаются при выключении PRIME.
import { useEffect, useMemo, useRef, useState } from 'react';
import { apiUrl } from './api3';

export interface CsvScene {
  id: string;
  event: string;
  lat: number;
  lon: number;
  region: string;
  date: string;
  category: string;
  size: string;
  source: string;
  nCsv: number | null;
  areaKm2: number | null;
  windowNote: string;
  nFound: number | null;
  concCsv: number | null;
  concPred: number | null;
  gsd: number;
  byGsd: { gsd: number; nFound: number | null; conc: number | null }[];
  imgDetail: string | null;
  img10: string | null;
  preview: string | null;
  categories: { category: string; items: number | null }[];
  windowKm2: number | null;
  windowSideM: number | null;
}
export interface CsvMetrics {
  file: string;
  n: number | null;
  gsd: number | null;
  mae: number | null;
  maeRel: number | null;
  bias: number | null;
  corr: number | null;
  within30: number | null;
  curve: { gsd: number; within30: number | null; mae: number | null; maeRel: number | null; recall: number | null }[];
  plot: string | null;
  note: string;
}
export interface CsvIndex {
  badge: string;
  about: string;
  generator: string;
  stub: boolean;
  scenes: CsvScene[];
  metrics: CsvMetrics | null;
}

export const DEMO_BADGE = 'ДЕМО: как сервис будет выглядеть на детальных снимках. Изображения и числа — демонстрационные, не результат модели';
export const DEMO_VAL = 'демо-значение (из CSV организаторов)';
type O = Record<string, any>;
const pick = (o: O | undefined | null, ...keys: string[]): any => {
  if (!o) return undefined;
  for (const k of keys) {
    const v = k.split('.').reduce<any>((a, x) => (a == null ? a : a[x]), o);
    if (v !== undefined && v !== null && v !== '') return v;
  }
  return undefined;
};
const num = (v: any): number | null => (v == null || v === '' || !isFinite(+v) ? null : +v);
const frac = (v: any): number | null => {
  const x = num(v);
  return x == null ? null : x > 1.0001 ? x / 100 : x; // 0..1 или проценты
};
const imgUrl = (v: any): string | null => {
  if (!v || typeof v !== 'string') return null;
  if (v.startsWith('/api/') || v.startsWith('http')) return v;
  return `/api/prime/csv_img/${v.replace(/^.*\//, '').replace(/\.png$/, '')}.png`;
};

function normScene(o: O, i: number): CsvScene | null {
  const lat = num(pick(o, 'lat', 'latitude', 'event.lat', 'coords.lat', 'center.1'));
  const lon = num(pick(o, 'lon', 'longitude', 'event.lon', 'coords.lon', 'center.0'));
  if (lat == null || lon == null) return null;
  const gsd = num(pick(o, 'gsd_main', 'gsd_m', 'gsd', 'main_gsd_m')) ?? 0.05;
  let byGsd: CsvScene['byGsd'] = [];
  const bg = pick(o, 'by_gsd', 'degradation', 'gsd_series', 'counts_by_gsd');
  if (Array.isArray(bg))
    byGsd = bg.map((x: O) => ({
      gsd: num(pick(x, 'gsd_m', 'gsd')) ?? 0,
      nFound: num(pick(x, 'n_found', 'found', 'count', 'n_pred')),
      conc: num(pick(x, 'conc_pred', 'conc_pred_items_km2', 'pred_items_km2', 'items_km2')),
    }));
  else if (bg && typeof bg === 'object')
    byGsd = Object.entries(bg).map(([k, v]: [string, any]) => ({
      gsd: +k,
      nFound: typeof v === 'object' ? num(pick(v, 'n_found', 'found', 'count')) : num(v),
      conc: typeof v === 'object' ? num(pick(v, 'conc_pred', 'conc_pred_items_km2', 'pred_items_km2', 'items_km2')) : null,
    }));
  byGsd = byGsd.filter((x) => x.gsd > 0).sort((a, b) => a.gsd - b.gsd);
  const imgs = pick(o, 'images', 'img', 'previews') as O | undefined;
  const im = (g: number, ...keys: string[]) => {
    const d = pick(o, ...keys);
    if (d) return imgUrl(d);
    if (imgs && typeof imgs === 'object') {
      const e = Object.entries(imgs).find(([k]) => Math.abs(parseFloat(k.replace(',', '.')) - g) < 1e-6);
      if (e) return imgUrl(e[1]);
    }
    return null;
  };
  return {
    id: String(pick(o, 'sample_id', 'id', 'csv_id', 'row_id') ?? `#${i + 1}`),
    event: String(pick(o, 'event_id', 'event') ?? ''),
    lat,
    lon,
    region: String(pick(o, 'region', 'sea_area', 'region_ru') ?? ''),
    date: String(pick(o, 'date', 'date_utc') ?? ''),
    category: String(pick(o, 'category', 'litter_category', 'categories') ?? ''),
    size: String(pick(o, 'size_class', 'sizes') ?? ''),
    source: String(pick(o, 'source_short', 'source') ?? ''),
    nCsv: num(pick(o, 'n_csv', 'items_count', 'csv.n', 'n_truth')),
    areaKm2: num(pick(o, 'area_km2', 'sampled_area_km2', 'csv.area_km2')),
    windowNote: String(pick(o, 'window_note', 'note', 'window.note') ?? ''),
    nFound: num(pick(o, 'n_found', 'found', 'n_pred', 'count_found')),
    concCsv: num(pick(o, 'conc_csv', 'conc_csv_items_km2', 'csv_items_km2', 'concentration_items_km2', 'csv.items_km2')),
    concPred: num(pick(o, 'conc_pred', 'conc_pred_items_km2', 'pred_items_km2', 'predicted_items_km2', 'pred.items_km2')),
    gsd,
    byGsd,
    imgDetail: im(gsd, 'img_detail', 'img_005', 'img_main'),
    img10: im(10, 'img_10m', 'img_10', 'img_s2', 'preview_10m'),
    preview: imgUrl(pick(o, 'preview', 'preview_url')),
    categories: Array.isArray(o.categories)
      ? o.categories.map((c: any) => (typeof c === 'string' ? { category: c, items: null } : { category: String(pick(c, 'category', 'name') ?? ''), items: num(pick(c, 'items', 'n')) }))
      : [],
    windowKm2: num(pick(o, 'window_area_km2')),
    windowSideM: num(pick(o, 'window_side_m')),
  };
}

function normMetrics(o: O | undefined | null): CsvMetrics | null {
  if (!o || typeof o !== 'object') return null;
  const main = (pick(o, 'main', 'overall', 'gsd_005', 'at_main_gsd') as O) ?? o;
  let curve: CsvMetrics['curve'] = [];
  const c = pick(o, 'metrics_by_gsd', 'by_gsd', 'curve', 'resolution_curve', 'degradation');
  const one = (g: number, x: O) => ({
    gsd: g,
    within30: frac(pick(x, 'within_30pct', 'within30', 'frac_within_30', 'share_within_30pct')),
    mae: num(pick(x, 'mae_items_km2', 'mae')),
    maeRel: frac(pick(x, 'mae_rel', 'median_rel_err', 'mape')),
    recall: frac(pick(x, 'recall_obj_tiles', 'recall')),
  });
  if (Array.isArray(c)) curve = c.map((x: O) => one(num(pick(x, 'gsd_m', 'gsd')) ?? 0, x));
  else if (c && typeof c === 'object') curve = Object.entries(c).map(([k, v]) => one(num(pick(v as O, 'gsd')) ?? +k, v as O));
  curve = curve.filter((x) => x.gsd > 0).sort((a, b) => a.gsd - b.gsd);
  return {
    file: String(pick(o, 'file', 'path') ?? 'reports/prime/metrics.json'),
    n: num(pick(o, 'n', 'n_scenes', 'n_events')),
    gsd: num(pick(main, 'gsd_m', 'gsd')) ?? num(pick(o, 'gsd_m')),
    mae: num(pick(main, 'mae_items_km2', 'mae')),
    maeRel: frac(pick(main, 'mae_rel', 'median_rel_err', 'mape')),
    bias: num(pick(main, 'bias_items_km2', 'bias')),
    corr: num(pick(main, 'corr', 'pearson', 'spearman', 'r')),
    within30: frac(pick(main, 'within_30pct', 'within30', 'frac_within_30', 'share_within_30pct')),
    curve,
    plot: (() => {
      const f = pick(o, 'plot', 'png', 'curve_png', 'figure', 'meta.figure');
      return typeof f === 'string' && f.startsWith('/api/') ? f : null;
    })(),
    note: String(pick(o, 'note', 'caveat', 'not_proves') ?? ''),
  };
}

export function normIndex(o: O): CsvIndex {
  const arr: O[] = Array.isArray(o) ? o : (pick(o, 'scenes', 'items', 'rows') ?? []);
  return {
    badge: DEMO_BADGE,
    about: String(pick(o, 'about', 'description') ?? ''),
    generator: String(pick(o, 'generator', 'script', 'meta.generator') ?? 'scripts/case/prime_csv.py'),
    stub: !!pick(o, 'stub'),
    scenes: arr.map(normScene).filter((x): x is CsvScene => !!x),
    metrics: normMetrics(pick(o, 'metrics', 'summary') ?? (o && o.metrics_by_gsd ? { metrics_by_gsd: o.metrics_by_gsd, file: 'reports/prime/metrics.json' } : null)),
  };
}

export function useCsvIndex(on: boolean) {
  const [ix, setIx] = useState<CsvIndex | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    if (!on || ix) return;
    let dead = false;
    fetch(apiUrl('/api/prime/csv_scenes'))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.status === 404 ? 'данные ещё не собраны' : `HTTP ${r.status}`))))
      .then(async (j) => {
        const x = normIndex(j);
        if (!x.metrics) {
          // метрики могут отдаваться отдельным маршрутом
          for (const u of ['/api/prime/csv_metrics', '/api/prime/metrics']) {
            const r = await fetch(apiUrl(u)).catch(() => null);
            if (r?.ok) {
              x.metrics = normMetrics(await r.json());
              break;
            }
          }
        }
        if (!dead) setIx(x);
      })
      .catch((e) => !dead && setErr(String(e.message ?? e)));
    return () => {
      dead = true;
    };
  }, [on, ix]);
  return { ix, err };
}

// ---------------- карта: пурпурный слой точек событий CSV ----------------
const SRC = 'prime-csv';
const L_HALO = 'prime-csv-halo';
const L_PT = 'prime-csv-pt';
const L_SEL = 'prime-csv-sel';

export function usePrimeCsvLayer(on: boolean, scenes: CsvScene[] | undefined, sel: string | null, onPick: (id: string) => void) {
  const pickRef = useRef(onPick);
  pickRef.current = onPick;
  const selRef = useRef(sel);
  selRef.current = sel;
  const fitted = useRef(false);
  useEffect(() => {
    if (!on || !scenes?.length) return;
    const fc = {
      type: 'FeatureCollection',
      features: scenes.map((s) => ({
        type: 'Feature',
        properties: { id: s.id },
        geometry: { type: 'Point', coordinates: [s.lon, s.lat] },
      })),
    };
    const bound = new WeakSet<object>();
    let map: any = null;
    const ensure = () => {
      map = (window as any).__caseMap;
      if (!map?.getStyle || !map.isStyleLoaded?.()) return;
      try {
        if (!map.getSource(SRC)) map.addSource(SRC, { type: 'geojson', data: fc });
        if (!map.getLayer(L_HALO))
          map.addLayer({
            id: L_HALO,
            type: 'circle',
            source: SRC,
            paint: { 'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 7, 8, 14], 'circle-color': '#ec40c4', 'circle-opacity': 0.25, 'circle-blur': 0.4 },
          });
        if (!map.getLayer(L_PT))
          map.addLayer({
            id: L_PT,
            type: 'circle',
            source: SRC,
            paint: {
              'circle-radius': ['interpolate', ['linear'], ['zoom'], 1, 3.5, 8, 7],
              'circle-color': '#ec40c4',
              'circle-stroke-color': '#fff',
              'circle-stroke-width': 1,
            },
          });
        if (!map.getLayer(L_SEL))
          map.addLayer({
            id: L_SEL,
            type: 'circle',
            source: SRC,
            filter: ['==', ['get', 'id'], selRef.current ?? ''],
            paint: { 'circle-radius': 11, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#ffd6f4', 'circle-stroke-width': 3 },
          });
        else map.setFilter(L_SEL, ['==', ['get', 'id'], selRef.current ?? '']);
        if (!bound.has(map)) {
          bound.add(map);
          map.on('click', L_PT, (e: any) => {
            const id = e.features?.[0]?.properties?.id;
            if (id) pickRef.current(String(id));
          });
          map.on('mouseenter', L_PT, () => (map.getCanvas().style.cursor = 'pointer'));
          map.on('mouseleave', L_PT, () => (map.getCanvas().style.cursor = ''));
        }
        if (!fitted.current) {
          fitted.current = true;
          const lons = scenes.map((s) => s.lon), lats = scenes.map((s) => s.lat);
          const mobile = window.innerWidth <= 820;
          const W = map.getContainer().clientWidth;
          map.fitBounds(
            [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]],
            { padding: mobile ? { top: 40, bottom: 40, left: 20, right: 20 } : { top: 60, bottom: 60, left: Math.min(460, W * 0.45), right: 40 }, maxZoom: 5, duration: 600 },
          );
        }
      } catch {
        /* стиль перезагружается — повторим по таймеру */
      }
    };
    ensure();
    const t = window.setInterval(ensure, 700);
    return () => {
      window.clearInterval(t);
      fitted.current = false;
      const m = (window as any).__caseMap;
      if (m?.getLayer) {
        for (const id of [L_SEL, L_PT, L_HALO]) if (m.getLayer(id)) m.removeLayer(id);
        if (m.getSource(SRC)) m.removeSource(SRC);
      }
    };
  }, [on, scenes]);
  useEffect(() => {
    const m = (window as any).__caseMap;
    if (on && m?.getLayer?.(L_SEL)) m.setFilter(L_SEL, ['==', ['get', 'id'], sel ?? '']);
  }, [on, sel]);
}

export function flyToScene(s: CsvScene) {
  const m = (window as any).__caseMap;
  if (!m?.flyTo) return;
  const mobile = window.innerWidth <= 820;
  const W = m.getContainer().clientWidth;
  m.flyTo({
    center: [s.lon, s.lat],
    zoom: Math.max(m.getZoom(), 5),
    duration: 700,
    padding: mobile ? { bottom: Math.round(window.innerHeight * 0.6), top: 0, left: 0, right: 0 } : { left: Math.min(460, W * 0.45), top: 0, right: 0, bottom: 0 },
  });
}

// ---------------- UI ----------------
const nf = (v: number | null | undefined, d = 0) =>
  v == null ? '—' : v.toLocaleString('ru-RU', { maximumFractionDigits: d, minimumFractionDigits: 0 });
const nfa = (v: number | null | undefined) => (v == null ? '—' : nf(v, Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2));
const dateRu = (s: string) => (/^\d{4}-\d{2}-\d{2}/.test(s) ? s.slice(0, 10).split('-').reverse().join('.') : s);
const gsdTxt = (g: number) => `${nf(g, 2)} м`;

export function CsvCard({ s, onBack }: { s: CsvScene; onBack: () => void }) {
  const cats = s.categories.length ? s.categories : s.category ? [{ category: s.category, items: s.nCsv }] : [];
  return (
    <article className="pc-card" data-testid="prime-csv-card" data-id={s.id}>
      <div className="pc-card-top">
        <button className="pc-back" onClick={onBack} data-testid="prime-csv-back">
          ← к списку
        </button>
        <span className="prime-badge sm">ДЕМО</span>
      </div>
      <h3 className="pc-card-h">
        Скопление в точке события CSV <b>#{s.id}</b>
      </h3>
      <p className="prime-muted pc-where">
        {s.region}
        {s.date && ` · ${dateRu(s.date)}`}
        {s.source && ` · ${s.source}`}
      </p>
      {s.preview ? (
        <figure className="pc-prev" data-testid="prime-csv-imgs">
          <img src={apiUrl(s.preview)} alt={`Демо-снимок по строке CSV ${s.id}`} loading="lazy" />
          <figcaption>
            Демо-снимок: реальная вода + вырезки реальных предметов из дрон-наборов; рамки — так будут выглядеть найденные предметы. Слева
            направо — детальный кадр и он же в худшем разрешении (до 10 м, как Sentinel-2).
          </figcaption>
          <span className="prime-wm">ДЕМО</span>
        </figure>
      ) : (
        <div className="prime-imgs" data-testid="prime-csv-imgs">
          <figure>
            {s.imgDetail ? <img src={apiUrl(s.imgDetail)} alt={`Демо-снимок ${s.id}, ${gsdTxt(s.gsd)}`} loading="lazy" /> : <div className="pc-noimg">нет превью</div>}
            <figcaption>Детальный кадр {gsdTxt(s.gsd)}/пикс.</figcaption>
            <span className="prime-wm">ДЕМО</span>
          </figure>
          <figure>
            {s.img10 ? <img className="px" src={apiUrl(s.img10)} alt={`Демо-снимок ${s.id}, 10 м`} loading="lazy" /> : <div className="pc-noimg">нет превью 10 м</div>}
            <figcaption>Тот же кадр в 10 м (как Sentinel-2)</figcaption>
            <span className="prime-wm">ДЕМО</span>
          </figure>
        </div>
      )}
      <dl className="prime-facts pc-facts" data-testid="prime-csv-facts">
        <div className="pc-wide">
          <dt>класс предметов</dt>
          <dd className="pc-cats">
            {cats.map((c) => (
              <span key={c.category} className="pc-chip">
                {c.category}
                {c.items != null && cats.length > 1 && <b> · {nf(c.items)} шт.</b>}
              </span>
            ))}
            {s.size && <span className="pc-chip pc-size">{s.size}</span>}
          </dd>
        </div>
        <div>
          <dt>количество</dt>
          <dd>{nf(s.nCsv)} шт.</dd>
        </div>
        <div>
          <dt>плотность</dt>
          <dd>
            {nfa(s.concCsv)} <small>шт./км²</small>
          </dd>
        </div>
        <div>
          <dt>площадь участка</dt>
          <dd>
            {nfa(s.areaKm2)} <small>км²</small>
          </dd>
        </div>
        <div>
          <dt>разрешение кадра</dt>
          <dd>{gsdTxt(s.gsd)}</dd>
        </div>
      </dl>
      <p className="pc-demo" data-testid="prime-demo-val">
        Числа — {DEMO_VAL}, строка {s.id}
        {s.event && `, событие ${s.event}`}; не результат модели.
      </p>
      {s.nFound != null && (
        <p className="pc-exp" data-testid="prime-csv-found">
          найдено счётчиком (эксперимент): <b>{nf(s.nFound)}</b> шт. на кадре {gsdTxt(s.gsd)}
        </p>
      )}
    </article>
  );
}

function Curve({ c }: { c: CsvMetrics['curve'] }) {
  const key: 'recall' | 'within30' = c.filter((x) => x.recall != null).length >= 2 ? 'recall' : 'within30';
  const pts = c.filter((x) => x[key] != null);
  if (pts.length < 2) return null;
  const W = 320, H = 130, l = 34, r = 8, t = 10, b = 26;
  const lg = (g: number) => Math.log10(g);
  const x0 = lg(pts[0].gsd), x1 = lg(pts[pts.length - 1].gsd);
  const X = (g: number) => l + ((lg(g) - x0) / (x1 - x0 || 1)) * (W - l - r);
  const Y = (v: number) => t + (1 - v) * (H - t - b);
  const d = pts.map((p, i) => `${i ? 'L' : 'M'}${X(p.gsd).toFixed(1)},${Y(p[key]!).toFixed(1)}`).join(' ');
  return (
    <>
      <h4>{key === 'recall' ? 'Доля найденных предметов' : 'Доля строк в пределах ±30 %'} от разрешения кадра</h4>
      <svg className="pc-curve" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Кривая от разрешения (эксперимент)" data-testid="prime-csv-curve">
        {[0, 0.5, 1].map((v) => (
          <g key={v}>
            <line x1={l} x2={W - r} y1={Y(v)} y2={Y(v)} stroke="rgba(255,255,255,.12)" />
            <text x={l - 4} y={Y(v) + 4} textAnchor="end">{`${v * 100}%`}</text>
          </g>
        ))}
        <path d={d} fill="none" stroke="#ec40c4" strokeWidth={2.5} />
        {pts.map((p) => (
          <g key={p.gsd}>
            <circle cx={X(p.gsd)} cy={Y(p[key]!)} r={3.5} fill="#ec40c4" />
            <text x={X(p.gsd)} y={H - 8} textAnchor="middle">{nf(p.gsd, 2)}</text>
          </g>
        ))}
        <text x={W - r} y={H - 8 - 12} textAnchor="end">GSD, м</text>
      </svg>
    </>
  );
}

/** «эксперимент»: только кривая от разрешения, если L154 прогнал счётчик; метрик качества в PRIME нет */
export function CsvExperiment({ m, open, setOpen }: { m: CsvMetrics | null; open: boolean; setOpen: (v: boolean) => void }) {
  if (!m || m.curve.length < 2) return null;
  return (
    <section className="pc-metrics" data-testid="prime-csv-metrics">
      <button className="pc-mlink" aria-expanded={open} onClick={() => setOpen(!open)} data-testid="prime-csv-metrics-open">
        Эксперимент: наш счётчик на этих демо-кадрах и разрешение {open ? '▴' : '▾'}
      </button>
      {open && (
        <div className="pc-mbody">
          <Curve c={m.curve} />
          <p className="prime-muted">
            Эксперимент на демо-кадрах, не оценка качества сервиса: реальных детальных снимков этих скоплений у нас нет. Файл: <code>{m.file}</code>
          </p>
        </div>
      )}
    </section>
  );
}

export function CsvList({ scenes, onPick }: { scenes: CsvScene[]; onPick: (s: CsvScene) => void }) {
  const [q, setQ] = useState('');
  const shown = useMemo(() => {
    const k = q.trim().toLowerCase();
    return k ? scenes.filter((s) => `${s.id} ${s.region} ${s.event}`.toLowerCase().includes(k)) : scenes;
  }, [q, scenes]);
  return (
    <>
      <input
        className="pc-q"
        placeholder="Поиск: строка CSV, район…"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        aria-label="Поиск по демо-сценам"
        data-testid="prime-csv-q"
      />
      <ul className="pc-list" data-testid="prime-csv-list">
        {shown.map((s) => (
          <li key={s.id}>
            <button onClick={() => onPick(s)} data-testid="prime-csv-item" data-id={s.id}>
              <span className="pc-id">#{s.id}</span>
              <span className="pc-reg">{s.region}</span>
              <span className="pc-n">
                <b className="prime-c">{nf(s.nCsv)} шт.</b> · {nfa(s.concCsv)} шт./км²
              </span>
            </button>
          </li>
        ))}
      </ul>
    </>
  );
}
