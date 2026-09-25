import { useEffect, useMemo, useRef, useState } from 'react';
import type { DateEntry, DetProps, FC, H3Props, Manifest, Region, Zone } from '../types';
import { apiGet, hasApi, apiUrl, type Why, type ZoneApi } from '../lib/api';
import { drawCrop, geomPolys } from '../lib/crop';
import { centroid } from '../map/layers';
import { localWhy, PRIORITY_NOTE, repeatFactor, scoreTerms, zonePx, zoneScore } from '../lib/priority';
import { AGREE_NOTE } from '../lib/confirm';
import { isFlagged } from '../lib/data';
import { fmtArea, fmtNum, fmtPct, fmtPermille, fmtThr } from '../lib/style';

interface Props {
  manifest: Manifest;
  region: Region;
  dateEntry: DateEntry;
  model: string;
  zone: Zone;
  zones: Zone[];
  detections: FC<DetProps> | null;
  h3: FC<H3Props> | null;
  rgbImg: HTMLImageElement | null;
  threshold: number | null;
  onPlace: (h3: string) => void;
  onToast: (t: string) => void;
}

const SIZE_M = 1500;

export default function ZonePanel(p: Props) {
  const z = p.zone;
  const [api, setApi] = useState<ZoneApi | null | undefined>(undefined);
  const [pdfOk, setPdfOk] = useState(false);
  const [bands, setBands] = useState<'rgb' | 'false'>('rgb');
  const [imgErr, setImgErr] = useState(false);
  const [local, setLocal] = useState<{ polys: number[][][][]; cell: [number, number][]; maxProb: number | null; n: number } | null>(null);
  const cv = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let alive = true;
    setApi(undefined);
    setImgErr(false);
    apiGet<ZoneApi>('/api/zone', { region: p.region.id, h3: z.h3, date: p.dateEntry.date, model: p.model }).then((r) => alive && setApi(r));
    hasApi('/api/place_report.pdf').then((v) => alive && setPdfOk(v));
    return () => {
      alive = false;
    };
  }, [p.region.id, z.h3, p.dateEntry.date, p.model]);

  useEffect(() => {
    let alive = true;
    import('h3-js').then(({ latLngToCell, cellToBoundary }) => {
      if (!alive) return;
      const cell = cellToBoundary(z.h3).map(([la, lo]) => [lo, la] as [number, number]);
      const inCell = (p.detections?.features ?? []).filter((f) => {
        const [lo, la] = centroid(f);
        return latLngToCell(la, lo, 8) === z.h3;
      });
      setLocal({
        cell: [...cell, cell[0]],
        polys: (p.detections?.features ?? []).flatMap((f) => geomPolys(f.geometry)),
        maxProb: inCell.length ? Math.max(...inCell.map((f) => f.properties.max_prob)) : null,
        n: inCell.length,
      });
    });
    return () => {
      alive = false;
    };
  }, [z.h3, p.detections]);

  const useCanvas = api === null || imgErr;
  useEffect(() => {
    if (!useCanvas || !cv.current || !p.rgbImg || !local) return;
    drawCrop(cv.current, { img: p.rgbImg, bounds: p.dateEntry.bounds, lon: z.lon, lat: z.lat, sizeM: SIZE_M, polys: local.polys, cell: local.cell, cssPx: 352 });
  }, [useCanvas, p.rgbImg, local, p.dateEntry.bounds, z.lon, z.lat]);

  const nDates = api?.n_dates ?? p.region.dates.filter((d) => d.models.includes(p.model)).length;
  const why: Why | null = api?.why ?? localWhy(z, p.zones, nDates);
  const terms = useMemo(() => {
    if (why?.score_terms?.length) {
      const get = (n: string) => why.score_terms!.find((t) => t.name === n);
      return {
        base: why.base_score ?? get('base')?.value ?? 0,
        agreement: get('agreement')?.value ?? 1,
        penalty: get('date_penalty')?.value ?? 1,
        mode: why.score_mode ?? 'mult',
        score: why.score,
        notes: { agreement: get('agreement')?.note, date_penalty: get('date_penalty')?.note },
      };
    }
    const st = scoreTerms(z);
    return st
      ? { base: st.base, agreement: st.agreement, penalty: st.date_penalty, mode: st.mode, score: st.score, notes: { agreement: undefined, date_penalty: undefined } }
      : null;
  }, [why, z]);
  const obs = api?.observed_frac ?? z.observed_frac ?? p.h3?.features.find((f) => f.properties.h3 === z.h3)?.properties.observed_frac ?? null;
  const maxProb = api?.max_prob ?? local?.maxProb ?? null;
  const nDet = api?.n_detections ?? z.n_detections ?? local?.n ?? null;
  const nConf = api?.n_confirmed ?? z.n_confirmed;
  const nArts = api?.n_artifacts ?? 0;
  const [av, au] = fmtArea(z.area_m2);
  const cloud = api?.cloud_frac ?? p.dateEntry.cloud_frac;
  const coords = `${z.lat.toFixed(5)}, ${z.lon.toFixed(5)}`;
  // server contours off (highlight=0): the outline is drawn in the signal colour on top (SVG)
  const cropSrc = api ? ((bands === 'false' && api.crop_false_color ? api.crop_false_color : api.crop) + '&px=640').replace('highlight=1', 'highlight=0') : null;
  const sizeM = Number(/size_m=([\d.]+)/.exec(api?.crop ?? '')?.[1] ?? SIZE_M);
  const mLon = 111320 * Math.cos((z.lat * Math.PI) / 180);
  const toSvg = (ring: number[][]) =>
    ring
      .map((q) => `${((((q[0] - z.lon) * mLon + sizeM / 2) / sizeM) * 100).toFixed(2)},${(((sizeM / 2 - (q[1] - z.lat) * 111320) / sizeM) * 100).toFixed(2)}`)
      .join(' ');
  const pdfHref = apiUrl('/api/place_report.pdf', { region: p.region.id, h3: z.h3, model: p.model, date: p.dateEntry.date });
  const ranked = useMemo(() => {
    const list = p.zones.map((x) => ({ rank: x.rank, score: zoneScore(x) }));
    const max = Math.max(1e-9, ...list.map((x) => x.score));
    return { list: list.slice(0, 6), max };
  }, [p.zones]);

  return (
    <>
      <section className="sec" data-testid="zone-card">
        <p className="note" data-testid="zone-note" style={{ marginBottom: 12 }}>
          {PRIORITY_NOTE}.
        </p>
        {isFlagged(p.dateEntry) && (
          <div className="warnline" style={{ marginBottom: 12 }}>
            Дымка или блик на снимке — находки могут быть завышены
          </div>
        )}
        <div className="crop">
          {api === undefined ? null : cropSrc && !imgErr ? (
            <>
              <img src={cropSrc} alt="Вырезка снимка вокруг зоны" onError={() => setImgErr(true)} data-testid="zone-crop-img" />
              {local && (
                <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
                  <polygon points={toSvg(local.cell)} fill="none" stroke="rgba(236,238,240,0.85)" strokeWidth={1} strokeDasharray="4 3" vectorEffect="non-scaling-stroke" />
                  {local.polys.map((poly, i) =>
                    poly.map((ring, j) => <polygon key={`${i}-${j}`} points={toSvg(ring)} fill="none" stroke="var(--accent)" strokeWidth={1.4} vectorEffect="non-scaling-stroke" />),
                  )}
                </svg>
              )}
            </>
          ) : (
            <canvas ref={cv} data-testid="zone-crop-canvas" />
          )}
          {api?.crop_false_color && !imgErr && (
            <div className="seg crop-switch" role="group" aria-label="Каналы вырезки">
              <button className={bands === 'rgb' ? 'on' : ''} onClick={() => setBands('rgb')} data-testid="zone-crop-rgb">
                RGB
              </button>
              <button className={bands === 'false' ? 'on' : ''} onClick={() => setBands('false')} data-testid="zone-crop-false">
                ИК
              </button>
            </div>
          )}
        </div>
        <div className="crop-cap">≈ 1,5 × 1,5 км · контур — пятна, пунктир — ячейка H3</div>
      </section>

      {why && (
        <section className="sec" data-testid="zone-why">
          <div className="sec-h">
            <h3>{z.rank === 1 ? 'Почему это место первое' : 'Почему это место в приоритете'}</h3>
          </div>
          <div className="eq" data-testid="zone-why-eq">
            <Term v={fmtNum(why.terms[0]?.value ?? zonePx(z))} l="пикс. с признаками" />
            <span className="op">×</span>
            <Term v={(why.terms[1]?.value ?? z.mean_prob ?? 0).toFixed(2)} l="ср. уверенность" />
            <span className="op">×</span>
            <Term v={`${(why.terms[2]?.contribution ?? repeatFactor(z.repeat_dates)).toFixed(1)}`} l={`повтор, ${why.terms[2]?.value ?? z.repeat_dates ?? 1} дат`} />
            {terms && (
              <>
                <span className="op">×</span>
                <Term v={fmtNum(terms.agreement, 2)} l="согласие моделей" />
                <span className="op">{terms.mode === 'sub' ? '−' : '×'}</span>
                <Term v={fmtNum(terms.penalty, terms.mode === 'sub' ? 1 : 2)} l="штраф даты" />
              </>
            )}
            <span className="op">=</span>
            <Term v={fmtNum(terms ? terms.score : why.score, 1)} l="балл" />
          </div>
          <div className="bars" aria-label="Балл зон этой даты">
            {ranked.list.map((x) => (
              <div key={x.rank} className={`bar-row ${x.rank === z.rank ? 'on' : ''}`}>
                <span>№{x.rank}</span>
                <span className="bar">
                  <span style={{ width: `${Math.max(2, (x.score / ranked.max) * 100)}%` }} />
                </span>
                <span className="bv">{fmtNum(x.score, 1)}</span>
              </div>
            ))}
          </div>
          <p className="note" style={{ marginTop: 12, color: 'var(--text-2)' }}>
            {why.text.replace(/\s*Это приоритет обследования.*$/, '')}
          </p>
          <details style={{ marginTop: 8 }}>
            <summary className="note" style={{ cursor: 'pointer' }}>
              Формула
            </summary>
            <p className="note mono" style={{ marginTop: 6, fontSize: 11 }}>
              {why.formula}
            </p>
            <p className="note" style={{ marginTop: 6 }}>
              {why.formula_text.replace(/\s*Это порядок обследования.*$/, '')}
            </p>
          </details>
        </section>
      )}

      <section className="sec">
        <div className="kv">
          <div>
            <div className="k">Индекс ячейки</div>
            <div className="v">
              {fmtPermille(z.index)}
              <small>‰</small>
            </div>
            <div className="h">
              {nDet ?? '—'} пятен · {av} {au}
            </div>
          </div>
          <div>
            <div className="k">Уверенность</div>
            <div className="v">{z.mean_prob === undefined ? '—' : z.mean_prob.toFixed(2)}</div>
            <div className="h">
              макс. {maxProb === null ? '—' : maxProb.toFixed(2)} · порог {fmtThr(p.threshold)}
            </div>
          </div>
        </div>
        <dl className="rows" style={{ marginTop: 12 }}>
          <dt>Облачность сцены</dt>
          <dd>{fmtPct(cloud)}</dd>
          <dt>Наблюдалось ячейки</dt>
          <dd>{fmtPct(obs)}</dd>
          {typeof nConf === 'number' && (
            <>
              <dt>Уверенные находки</dt>
              <dd data-testid="zone-confirmed">
                {nConf} из {nDet ?? '—'}
              </dd>
            </>
          )}
          {nArts > 0 && (
            <>
              <dt>Исключено артефактов</dt>
              <dd data-testid="zone-artifacts">{nArts}</dd>
            </>
          )}
          <dt>Координаты</dt>
          <dd>
            <button
              className="link mono"
              onClick={() => navigator.clipboard?.writeText(coords).then(() => p.onToast('Координаты скопированы'), () => p.onToast(coords))}
              data-testid="zone-copy-coords"
            >
              {coords}
            </button>
          </dd>
        </dl>
        {typeof nConf === 'number' && <p className="note" style={{ marginTop: 8 }}>Уверенные — {AGREE_NOTE}.</p>}
      </section>

      <section className="sec">
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button className="btn sm" onClick={() => p.onPlace(z.h3)} data-testid="zone-open-place">
            История места
          </button>
          {pdfOk && (
            <a className="btn sm" href={pdfHref} download data-testid="zone-pdf">
              Справка PDF
            </a>
          )}
        </div>
        <p className="note" style={{ marginTop: 8 }}>
          H3 {z.h3}
          {api === null ? ' · расчёт в браузере (нет /api/zone)' : ''}
        </p>
      </section>
    </>
  );
}

function Term({ v, l }: { v: string; l: string }) {
  return (
    <span className="t">
      <b>{v}</b>
      <small>{l}</small>
    </span>
  );
}
