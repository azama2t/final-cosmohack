import { useEffect, useState } from 'react';
import type { Manifest, Region } from '../types';
import { apiGet, apiUrl, hasApi, type PlaceApi, type PlaceRow, type PlaceStatus } from '../lib/api';
import { loadDetections, loadH3, shortName } from '../lib/data';
import { centroid } from '../map/layers';
import { fmtDate, fmtDateShort, fmtNum, fmtPct, fmtPermille, modelLabel } from '../lib/style';
import { PRIORITY_NOTE } from '../lib/priority';

interface Props {
  manifest: Manifest;
  region: Region;
  model: string;
  h3: string;
  date: string | null;
  onClose: () => void;
  onBack?: () => void;
  onDate: (d: string) => void;
}

export const STATUS_RU: Record<PlaceStatus, string> = {
  found: 'найдено',
  clean: 'чисто',
  no_observation: 'нет наблюдения',
  no_image: 'нет слоя модели',
};

const LIMITATIONS = [
  'Индекс — доля наблюдаемой воды с признаками мусора на одном снимке (‰), не масса и не концентрация пластика.',
  'Приоритет обследования — ранжирование по формуле, не измеренная экологическая опасность.',
  'Согласие двух моделей ≠ проверка на месте.',
  'Разрешение Sentinel-2 — 10 м: видны только крупные скопления, не отдельные предметы.',
  'Пена, водоросли, следы судов, блики и тонкие облака могут давать ложные срабатывания.',
  'История — только реальные даты снимков, без интерполяции между ними.',
];

/** Client fallback: history of one H3 cell over all dates of the region (from h3.geojson + detections.geojson). */
async function localPlace(manifest: Manifest, region: Region, model: string, h3id: string): Promise<PlaceApi> {
  const { cellToLatLng, latLngToCell } = await import('h3-js');
  const [lat, lon] = cellToLatLng(h3id);
  const history: PlaceRow[] = [];
  for (const d of region.dates) {
    const base = { date: d.date, scene_id: d.scene_id, cloud_frac: d.cloud_frac, quality: d.quality ?? null, model };
    if (!d.models.includes(model)) {
      history.push({ ...base, status: 'no_image', index: null, n_detections: 0 });
      continue;
    }
    const [h3fc, det] = await Promise.all([loadH3(region.id, d.date, model), loadDetections(region.id, d.date, model)]);
    const cell = h3fc?.features.find((f) => f.properties.h3 === h3id)?.properties ?? null;
    const dets = (det?.features ?? []).filter((f) => {
      const [lo, la] = centroid(f);
      return latLngToCell(la, lo, 8) === h3id;
    });
    const status: PlaceStatus =
      !cell || cell.share_permille === null ? 'no_observation' : cell.flagged_water_px > 0 || dets.length ? 'found' : 'clean';
    const conf = dets.some((f) => typeof f.properties.confirmed === 'boolean');
    history.push({
      ...base,
      status,
      index: cell?.share_permille ?? null,
      observed_frac: cell?.observed_frac ?? null,
      flagged_water_px: cell?.flagged_water_px ?? null,
      n_detections: dets.length,
      max_prob: dets.length ? Math.max(...dets.map((f) => f.properties.max_prob)) : null,
      ...(conf ? { n_confirmed: dets.filter((f) => f.properties.confirmed).length } : {}),
    });
  }
  const found = history.filter((h) => h.status === 'found');
  return {
    region: region.id,
    region_name: region.name,
    h3: h3id,
    lon,
    lat,
    model,
    model_name: manifest.models[model]?.name,
    threshold: manifest.models[model]?.threshold ?? 0.5,
    summary: {
      n_dates: history.length,
      n_observed: history.filter((h) => h.status === 'found' || h.status === 'clean').length,
      n_found: found.length,
      first_found: found[0]?.date ?? null,
      last_found: found.at(-1)?.date ?? null,
      n_detections_total: history.reduce((a, h) => a + (h.n_detections || 0), 0),
      max_index: history.reduce<number | null>((a, h) => (h.index === null ? a : Math.max(a ?? 0, h.index)), null),
    },
    history,
    sources: manifest.sources,
    limitations: LIMITATIONS,
  };
}

const flaggedQ = (q: PlaceRow['quality']) => !!(q && (q.haze || q.glint_or_haze));

export default function PlaceCard(p: Props) {
  const [data, setData] = useState<PlaceApi | null>(null);
  const [src, setSrc] = useState<'api' | 'local' | null>(null);
  const [pdfOk, setPdfOk] = useState(false);
  const [srcOpen, setSrcOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    setData(null);
    (async () => {
      const a = await apiGet<PlaceApi>('/api/place', { region: p.region.id, h3: p.h3, model: p.model });
      if (!alive) return;
      if (a) {
        setData(a);
        setSrc('api');
        return;
      }
      const l = await localPlace(p.manifest, p.region, p.model, p.h3);
      if (alive) {
        setData(l);
        setSrc('local');
      }
    })();
    hasApi('/api/place_report.pdf').then((v) => alive && setPdfOk(v));
    return () => {
      alive = false;
    };
  }, [p.region, p.model, p.h3, p.manifest]);

  const hist = data?.history ?? [];
  const cur = hist.find((h) => h.date === p.date);
  const curIdx = cur ? hist.indexOf(cur) : -1;
  const prev = curIdx > 0 ? [...hist.slice(0, curIdx)].reverse().find((h) => h.status === 'found' || h.status === 'clean') : undefined;
  const maxIdx = Math.max(1e-9, ...hist.map((h) => h.index ?? 0));
  const pdfHref = apiUrl('/api/place_report.pdf', { region: p.region.id, h3: p.h3, model: p.model, date: p.date });
  const coords = data ? `${data.lat.toFixed(5)}, ${data.lon.toFixed(5)}` : '';

  return (
    <div className="det-card place-card glass" data-testid="place-card">
      <button className="icon-btn close" onClick={p.onClose} aria-label="Закрыть" data-testid="place-card-close">
        ×
      </button>
      <div className="eyebrow" title="шестиугольная ячейка сетки H3, разрешение 8, площадь ≈ 0,7 км²">Карточка места · ячейка H3 res 8</div>
      <div className="zc-title">
        <span>
          {shortName(p.region.name)}
          <small className="muted"> · {modelLabel(p.model, p.manifest.models[p.model]?.name)}</small>
        </span>
      </div>
      <div className="muted small mono pc-id">
        {coords} · {p.h3}
      </div>
      {!data ? (
        <div className="crop-ph short" />
      ) : (
        <>
          <div className="pc-kpis">
            <div>
              <b className={data.summary.n_found ? 'accent-text' : ''}>
                {data.summary.n_found} из {data.summary.n_observed}
              </b>
              <small>дат с находками из наблюдённых</small>
            </div>
            <div>
              <b>{fmtPermille(data.summary.max_index)} ‰</b>
              <small>макс. индекс ячейки</small>
            </div>
            <div>
              <b>{fmtNum(data.summary.n_detections_total)}</b>
              <small>пятен за все даты</small>
            </div>
          </div>

          <div className="section-head pc-head">
            <h3>История по датам снимков</h3>
            <span className="muted small">без интерполяции</span>
          </div>
          <div className="pc-strip" data-testid="place-strip">
            {hist.map((h) => {
              const hgt = h.status === 'found' ? 8 + 44 * Math.sqrt(Math.max(0, h.index ?? 0) / maxIdx) : h.status === 'clean' ? 3 : 0;
              return (
                <button
                  key={h.date}
                  className={`pcs-col st-${h.status} ${h.date === p.date ? 'cur' : ''}`}
                  onClick={() => p.onDate(h.date)}
                  title={`${fmtDate(h.date)}: ${STATUS_RU[h.status]}${h.index !== null ? `, индекс ${fmtPermille(h.index)} ‰` : ''}${flaggedQ(h.quality) ? ' · дымка/блик' : ''}`}
                  disabled={h.status === 'no_image'}
                >
                  <span className="pcs-bar-wrap">
                    {h.status === 'no_observation' ? <span className="pcs-none">?</span> : <span className="pcs-bar" style={{ height: hgt }} />}
                  </span>
                  <span className="pcs-date">{fmtDateShort(h.date)}</span>
                  {flaggedQ(h.quality) && <span className="pcs-flag" aria-label="дымка/блик">!</span>}
                </button>
              );
            })}
          </div>

          {cur && (
            <div className="pc-cmp" data-testid="place-compare">
              {prev ? (
                <>
                  <b>По сравнению с {fmtDate(prev.date)}:</b> индекс {fmtPermille(prev.index)} → {fmtPermille(cur.index)} ‰, пятен {prev.n_detections} →{' '}
                  {cur.n_detections}
                  {cur.status !== 'found' && cur.status !== 'clean' ? ` (на ${fmtDate(cur.date)} ${STATUS_RU[cur.status]})` : ''}.
                </>
              ) : (
                <>
                  <b>{fmtDate(cur.date)}</b> — самое раннее наблюдение этой ячейки, сравнивать не с чем.
                </>
              )}
            </div>
          )}

          <table className="zones-table pc-table" data-testid="place-table">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Статус</th>
                <th>Индекс ‰</th>
                <th>Пятна</th>
                <th>Облака</th>
              </tr>
            </thead>
            <tbody>
              {hist.map((h) => (
                <tr
                  key={h.date}
                  className={h.date === p.date ? 'cur' : ''}
                  onClick={() => h.status !== 'no_image' && p.onDate(h.date)}
                  data-testid={`place-row-${h.date}`}
                >
                  <td className="num">
                    {fmtDateShort(h.date)}
                    {flaggedQ(h.quality) && (
                      <span className="q-mini" title={h.quality?.note || 'дымка/блик — находки могут быть завышены'}>
                        !
                      </span>
                    )}
                  </td>
                  <td>
                    <span className={`st-chip st-${h.status}`}>{STATUS_RU[h.status]}</span>
                    {h.zone_rank ? <span className="muted small"> · зона №{h.zone_rank}</span> : null}
                  </td>
                  <td className="num">{fmtPermille(h.index)}</td>
                  <td className="num">
                    {h.n_detections}
                    {typeof h.n_confirmed === 'number' && h.n_confirmed > 0 ? (
                      <span className="conf-badge" title="подтверждено второй моделью — согласие моделей, не проверка на месте">
                        ✓{h.n_confirmed}
                      </span>
                    ) : null}
                  </td>
                  <td className="num">{fmtPct(h.cloud_frac)}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className={`why ${srcOpen ? 'open' : ''}`}>
            <button className="why-head" onClick={() => setSrcOpen((v) => !v)} aria-expanded={srcOpen} data-testid="place-sources-toggle">
              <span>Источники и ограничения</span>
              <span className="why-chev" aria-hidden>
                {srcOpen ? '−' : '+'}
              </span>
            </button>
            {srcOpen && (
              <div className="why-body">
                <ul className="pc-list">
                  {data.limitations.map((l) => (
                    <li key={l}>{l}</li>
                  ))}
                </ul>
                <ul className="pc-list sources">
                  {data.sources.map((s) => (
                    <li key={s.name}>
                      {s.url ? (
                        <a href={s.url} target="_blank" rel="noreferrer">
                          {s.name}
                        </a>
                      ) : (
                        s.name
                      )}
                      {s.license ? <span className="muted"> · {s.license}</span> : null}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        </>
      )}

      <div className="zc-actions">
        {p.onBack && (
          <button className="btn small ghost" onClick={p.onBack} data-testid="place-back">
            ← к зоне
          </button>
        )}
        {pdfOk && (
          <a className="btn small accent" href={pdfHref} download data-testid="place-pdf">
            Справка PDF
          </a>
        )}
      </div>
      <div className="dc-note muted">
        {PRIORITY_NOTE}; индекс — по снимку, не масса пластика.
        {src === 'local' ? ' История посчитана в браузере (нет /api/place).' : ''}
      </div>
    </div>
  );
}
