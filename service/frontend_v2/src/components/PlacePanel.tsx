import { useEffect, useRef, useState } from 'react';
import type { Manifest, Region } from '../types';
import { apiGet, apiUrl, hasApi, type PlaceApi, type PlaceRow, type PlaceStatus } from '../lib/api';
import { getImage, loadDetections, loadH3 } from '../lib/data';
import { centroid } from '../map/layers';
import { drawCrop, geomPolys } from '../lib/crop';
import { fmtDate, fmtDateShort, fmtNum, fmtPct, fmtPermille, modelLabel } from '../lib/style';
import { PRIORITY_NOTE } from '../lib/priority';

interface Props {
  manifest: Manifest;
  region: Region;
  model: string;
  h3: string;
  date: string | null;
  onDate: (d: string) => void;
}

export const STATUS_RU: Record<PlaceStatus, string> = {
  found: 'найдено',
  clean: 'чисто',
  no_observation: 'нет наблюдения',
  no_image: 'нет слоя модели',
  unreliable: 'ненадёжно',
};
/** v2 status dot: accent only for «найдено» (a finding) */
const STATUS_CLS: Record<PlaceStatus, string> = {
  found: 's-detected',
  clean: 's-false',
  no_observation: '',
  no_image: '',
  unreliable: 's-review',
};

const LIMITATIONS = [
  'Индекс — доля наблюдаемой воды с признаками мусора на одном снимке (‰): индекс по снимку, не масса пластика.',
  'Приоритет обследования — ранжирование по формуле, не измеренная экологическая опасность.',
  'Согласие двух моделей — согласие моделей, не проверка на месте.',
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
      if (f.properties.artifact) return false;
      const [lo, la] = centroid(f);
      return latLngToCell(la, lo, 8) === h3id;
    });
    // same date rule as /api/calendar and /api/place (service/place.py: date_reliability)
    const water = (h3fc?.features ?? []).filter((f) => (f.properties.observed_water_px ?? 0) > 0 || f.properties.share_permille !== null);
    const ofw = water.length ? water.filter((f) => f.properties.share_permille !== null).length / water.length : 0;
    const reasons: string[] = [];
    if ((d.cloud_frac ?? 0) > 0.5) reasons.push(`облачность ${Math.round((d.cloud_frac ?? 0) * 100)} % > 50 %`);
    if (d.quality?.haze || d.quality?.glint_or_haze) reasons.push('дымка/блик — находки могут быть завышены');
    if (ofw < 0.3) reasons.push(`наблюдаемой воды ${Math.round(ofw * 100)} % < 30 %`);
    const hasFind = !!cell && cell.share_permille !== null && (cell.flagged_water_px > 0 || dets.length > 0);
    const status: PlaceStatus = reasons.length
      ? 'unreliable'
      : !cell || cell.share_permille === null
        ? 'no_observation'
        : hasFind
          ? 'found'
          : 'clean';
    const conf = dets.some((f) => typeof f.properties.confirmed === 'boolean');
    history.push({
      ...base,
      status,
      ...(reasons.length ? { reason: reasons.join('; '), has_findings: hasFind || dets.length > 0 } : {}),
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

export default function PlacePanel(p: Props) {
  const [data, setData] = useState<PlaceApi | null>(null);
  const [src, setSrc] = useState<'api' | 'local' | null>(null);
  const [pdfOk, setPdfOk] = useState(false);

  useEffect(() => {
    let alive = true;
    setData(null);
    setSrc(null);
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
  const pdfHref = apiUrl('/api/place_report.pdf', { region: p.region.id, h3: p.h3, model: p.model, date: p.date });
  const coords = data ? `${data.lat.toFixed(5)}, ${data.lon.toFixed(5)}` : '';
  // crops: the current date first (if it had findings), then the other dates with findings — newest first
  const cropRows = hist
    .filter((h) => h.status === 'found' || (h.status === 'unreliable' && h.has_findings))
    .sort((x, y) => (x.date === p.date ? -1 : y.date === p.date ? 1 : y.date.localeCompare(x.date)))
    .slice(0, 6);

  return (
    <div data-testid="place-card" style={{ display: 'contents' }}>
      <section className="sec">
        <div className="sec-h">
          <h3 title="шестиугольная ячейка сетки H3, разрешение 8, площадь ≈ 0,7 км²">Ячейка H3 · res 8</h3>
          <span className="aside">{modelLabel(p.model, p.manifest.models[p.model]?.name)}</span>
        </div>
        <div className="mono faint" style={{ marginBottom: 'var(--s1)' }} data-testid="place-id">
          {coords ? `${coords} · ` : ''}
          {p.h3}
        </div>
        {!data ? (
          <p className="note">Загрузка истории ячейки…</p>
        ) : (
          <>
            <div className="kv" data-testid="place-kpis">
              <div>
                <div className="k">дат с находками</div>
                <div className={`v ${data.summary.n_found ? 'accent' : ''}`} data-testid="place-val">
                  {fmtNum(data.summary.n_found)}
                  <small>из {fmtNum(data.summary.n_observed)} наблюдённых</small>
                </div>
              </div>
              <div>
                <div className="k">макс. индекс ячейки</div>
                <div className="v">
                  {fmtPermille(data.summary.max_index)}
                  <small>‰</small>
                </div>
              </div>
              <div>
                <div className="k">первое обнаружение</div>
                <div className="v" style={{ fontSize: 15 }}>
                  {data.summary.first_found ? fmtDate(data.summary.first_found) : '—'}
                </div>
              </div>
              <div>
                <div className="k">последнее</div>
                <div className="v" style={{ fontSize: 15 }}>
                  {data.summary.last_found ? fmtDate(data.summary.last_found) : '—'}
                </div>
              </div>
            </div>
            <dl className="rows" style={{ marginTop: 'var(--s1)' }}>
              <dt>Дат снимков всего</dt>
              <dd>{fmtNum(data.summary.n_dates)}</dd>
              <dt>Пятен за все даты</dt>
              <dd>{fmtNum(data.summary.n_detections_total)}</dd>
            </dl>
          </>
        )}
        {pdfOk && (
          <div style={{ marginTop: 'var(--s2)' }}>
            <a className="btn sm" href={pdfHref} download data-testid="place-pdf">
              Справка PDF
            </a>
          </div>
        )}
      </section>

      {data && (
        <section className="sec">
          <div className="sec-h">
            <h3>История по датам снимков</h3>
            <span className="aside">без интерполяции</span>
          </div>
          {cur && (
            <p className="small muted" style={{ marginBottom: 'var(--s1)' }} data-testid="place-compare">
              {prev ? (
                <>
                  По сравнению с {fmtDate(prev.date)}: индекс {fmtPermille(prev.index)} → {fmtPermille(cur.index)} ‰, пятен {prev.n_detections} →{' '}
                  {cur.n_detections}
                  {cur.status !== 'found' && cur.status !== 'clean' ? ` (на ${fmtDate(cur.date)} ${STATUS_RU[cur.status]})` : ''}.
                </>
              ) : (
                <>{fmtDate(cur.date)} — самое раннее наблюдение этой ячейки, сравнивать не с чем.</>
              )}
            </p>
          )}
          <table className="t" data-testid="place-table">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Статус</th>
                <th className="r">Индекс ‰</th>
                <th className="r">Пятна</th>
                <th className="r">Облака</th>
              </tr>
            </thead>
            <tbody>
              {hist.map((h) => {
                const on = h.date === p.date;
                const clickable = h.status !== 'no_image';
                return (
                  <tr
                    key={h.date}
                    onClick={() => clickable && p.onDate(h.date)}
                    style={{ cursor: clickable ? 'pointer' : 'default', background: on ? 'var(--panel-3)' : undefined }}
                    title={h.reason || (flaggedQ(h.quality) ? h.quality?.note || 'дымка/блик — находки могут быть завышены' : undefined)}
                    data-testid={`place-row-${h.date}`}
                  >
                    <td style={{ whiteSpace: 'nowrap', color: on ? 'var(--text)' : undefined }}>
                      {fmtDateShort(h.date)}
                      {flaggedQ(h.quality) && <span className="faint"> · блик</span>}
                    </td>
                    <td>
                      <span className={`status ${STATUS_CLS[h.status]}`}>{STATUS_RU[h.status]}</span>
                      {h.status === 'unreliable' && h.has_findings ? <span className="faint"> · были признаки</span> : null}
                      {h.zone_rank ? <span className="faint"> · зона №{h.zone_rank}</span> : null}
                    </td>
                    <td className="r">{fmtPermille(h.index)}</td>
                    <td className="r">
                      {h.n_detections}
                      {typeof h.n_confirmed === 'number' && h.n_confirmed > 0 ? (
                        <span className="faint" title="подтверждено второй моделью — согласие моделей, не проверка на месте">
                          {' '}
                          · {h.n_confirmed} согл.
                        </span>
                      ) : null}
                    </td>
                    <td className="r">{fmtPct(h.cloud_frac)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {hist.some((h) => h.status === 'unreliable') && (
            <p className="note" style={{ marginTop: 'var(--s1)' }} data-testid="place-unreliable-note">
              <b>Ненадёжно</b> — то же правило, что в календаре: облака &gt; 50 %, дымка или блик, или наблюдаемой воды &lt; 30 %. Такие даты не
              входят в «k из n».
            </p>
          )}
          <p className="note" style={{ marginTop: 'var(--s1)' }}>
            «согл.» — находку видит и вторая модель: согласие моделей, не проверка на месте.
          </p>
        </section>
      )}

      {data && cropRows.length > 0 && (
        <section className="sec" data-testid="place-crops">
          <div className="sec-h">
            <h3>Вырезки</h3>
            <span className="aside">1,5 × 1,5 км · контур — находка</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 'var(--s1)' }}>
            {cropRows.map((h) => (
              <button
                key={h.date}
                onClick={() => p.onDate(h.date)}
                style={{ padding: 0, border: 0, background: 'none', cursor: 'pointer', textAlign: 'left', color: 'inherit' }}
                data-testid={`place-crop-${h.date}`}
              >
                <div className="crop" style={{ outline: h.date === p.date ? '1px solid var(--text-2)' : undefined, outlineOffset: 2 }}>
                  {h.crop ? (
                    <img src={`${h.crop}&px=400`} alt={`Вырезка ${fmtDate(h.date)}`} loading="lazy" />
                  ) : (
                    <LocalCrop region={p.region} model={p.model} h3={p.h3} date={h.date} lon={data.lon} lat={data.lat} />
                  )}
                </div>
                <div className="crop-cap">
                  {fmtDate(h.date)} · индекс {fmtPermille(h.index)} ‰{h.status === 'unreliable' ? ' · ненадёжно' : ''}
                </div>
              </button>
            ))}
          </div>
        </section>
      )}

      {data && (
        <section className="sec" data-testid="place-limits">
          <div className="sec-h">
            <h3>Ограничения</h3>
          </div>
          <ul className="note" style={{ margin: 0, paddingLeft: 16 }}>
            {(data.limitations?.length ? data.limitations : LIMITATIONS).map((l) => (
              <li key={l} style={{ marginBottom: 4 }}>
                {l}
              </li>
            ))}
          </ul>
          <p className="note" style={{ marginTop: 'var(--s1)' }}>
            {PRIORITY_NOTE}; индекс по снимку, не масса пластика.
            {src === 'local' ? ' История посчитана в браузере (нет /api/place).' : ''}
          </p>
        </section>
      )}

      {data && data.sources?.length > 0 && (
        <section className="sec" data-testid="place-sources">
          <div className="sec-h">
            <h3>Источники</h3>
          </div>
          <ul className="note" style={{ margin: 0, paddingLeft: 16 }}>
            {data.sources.map((s) => (
              <li key={s.name} style={{ marginBottom: 4 }}>
                {s.url ? (
                  <a href={s.url} target="_blank" rel="noreferrer" className="link">
                    {s.name}
                  </a>
                ) : (
                  s.name
                )}
                {s.license ? <span> · {s.license}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/** Crop drawn in the browser from rgb.png (no /api/crop): detections of the cell in the accent colour + cell outline. */
function LocalCrop({ region, model, h3, date, lon, lat }: { region: Region; model: string; h3: string; date: string; lon: number; lat: number }) {
  const cv = useRef<HTMLCanvasElement>(null);
  const [fail, setFail] = useState(false);
  useEffect(() => {
    let alive = true;
    const de = region.dates.find((d) => d.date === date);
    if (!de) {
      setFail(true);
      return;
    }
    (async () => {
      try {
        const [{ cellToBoundary }, img, det] = await Promise.all([
          import('h3-js'),
          getImage(de.rgb),
          de.models.includes(model) ? loadDetections(region.id, date, model) : Promise.resolve(null),
        ]);
        if (!alive || !cv.current) return;
        const { latLngToCell } = await import('h3-js');
        const polys = (det?.features ?? [])
          .filter((f) => {
            if (f.properties.artifact) return false;
            const [lo, la] = centroid(f);
            return latLngToCell(la, lo, 8) === h3;
          })
          .flatMap((f) => geomPolys(f.geometry));
        const cell = cellToBoundary(h3).map(([la, lo]) => [lo, la] as [number, number]);
        drawCrop(cv.current, { img, bounds: de.bounds, lon, lat, sizeM: 1500, polys, cell, cssPx: 360 });
      } catch {
        if (alive) setFail(true);
      }
    })();
    return () => {
      alive = false;
    };
  }, [region, model, h3, date, lon, lat]);
  return fail ? <div className="empty small">вырезка недоступна</div> : <canvas ref={cv} />;
}
