import { useEffect, useMemo, useRef, useState } from 'react';
import type { DateEntry, DetProps, FC, H3Props, Manifest, Region, Zone } from '../types';
import { apiGet, hasApi, apiUrl, type Why, type ZoneApi } from '../lib/api';
import { drawCrop, geomPolys } from '../lib/crop';
import { centroid } from '../map/layers';
import { localWhy, PRIORITY_NOTE, repeatFactor, zonePx, zoneScore } from '../lib/priority';
import { AGREE_NOTE } from '../lib/confirm';
import { isFlagged } from '../lib/data';
import { fmtArea, fmtDate, fmtNum, fmtPct, fmtPermille, fmtThr, modelLabel } from '../lib/style';

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
  onClose: () => void;
  onPlace: (h3: string) => void;
  onToast: (t: string) => void;
}

const SIZE_M = 1500;

export default function ZoneCard(p: Props) {
  const z = p.zone;
  const [api, setApi] = useState<ZoneApi | null | undefined>(undefined);
  const [pdfOk, setPdfOk] = useState(false);
  const [bands, setBands] = useState<'rgb' | 'false'>('rgb');
  const [imgErr, setImgErr] = useState(false);
  const [local, setLocal] = useState<{ polys: number[][][][]; cell: [number, number][]; maxProb: number | null; n: number } | null>(null);
  const [whyOpen, setWhyOpen] = useState(() => window.innerHeight > 900);
  const cv = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let alive = true;
    setApi(undefined);
    setImgErr(false);
    apiGet<ZoneApi>('/api/zone', { region: p.region.id, h3: z.h3, date: p.dateEntry.date, model: p.model }).then(
      (r) => alive && setApi(r),
    );
    hasApi('/api/place_report.pdf').then((v) => alive && setPdfOk(v));
    return () => {
      alive = false;
    };
  }, [p.region.id, z.h3, p.dateEntry.date, p.model]);

  // client side: cell outline + detections whose centroid is in the cell (fallback crop, max P)
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
    drawCrop(cv.current, {
      img: p.rgbImg,
      bounds: p.dateEntry.bounds,
      lon: z.lon,
      lat: z.lat,
      sizeM: SIZE_M,
      polys: local.polys,
      cell: local.cell,
      cssPx: 360,
    });
  }, [useCanvas, p.rgbImg, local, p.dateEntry.bounds, z.lon, z.lat]);

  const nDates = api?.n_dates ?? p.region.dates.filter((d) => d.models.includes(p.model)).length;
  const why: Why | null = api?.why ?? localWhy(z, p.zones, nDates);
  const obs = api?.observed_frac ?? z.observed_frac ?? p.h3?.features.find((f) => f.properties.h3 === z.h3)?.properties.observed_frac ?? null;
  const maxProb = api?.max_prob ?? local?.maxProb ?? null;
  const nDet = api?.n_detections ?? z.n_detections ?? local?.n ?? null;
  const nConf = api?.n_confirmed ?? z.n_confirmed;
  const [av, au] = fmtArea(z.area_m2);
  const cloud = api?.cloud_frac ?? p.dateEntry.cloud_frac;
  const flagged = isFlagged(p.dateEntry);
  const coords = `${z.lat.toFixed(5)}, ${z.lon.toFixed(5)}`;
  const cropSrc = api ? (bands === 'false' && api.crop_false_color ? api.crop_false_color : api.crop) + '&px=688' : null;
  const pdfHref = apiUrl('/api/place_report.pdf', { region: p.region.id, h3: z.h3, model: p.model, date: p.dateEntry.date });

  // scores of all zones of this date: where this one stands
  const ranked = useMemo(() => {
    const list = p.zones.map((x) => ({ rank: x.rank, score: zoneScore(x) }));
    const max = Math.max(1e-9, ...list.map((x) => x.score));
    return { list: list.slice(0, 6), max };
  }, [p.zones]);

  return (
    <div className="det-card zone-card glass" data-testid="zone-card">
      <button className="icon-btn close" onClick={p.onClose} aria-label="Закрыть" data-testid="zone-card-close">
        ×
      </button>
      <div className="eyebrow accent-text">Приоритет обследования</div>
      <div className="zc-title">
        <span className={`rank-badge big ${z.rank <= 3 ? 'top' : ''}`}>{z.rank}</span>
        <span>
          Зона №{z.rank}
          <small className="muted">
            {' '}
            · {fmtDate(p.dateEntry.date)} · {modelLabel(p.model, p.manifest.models[p.model]?.name)}
          </small>
        </span>
      </div>
      <div className="zc-note" data-testid="zone-note">
        {PRIORITY_NOTE}
      </div>
      {flagged && (
        <div className="q-badge" title={p.dateEntry.quality?.note || undefined}>
          <span className="q-icon" aria-hidden>
            !
          </span>
          <span>
            <b>дымка/блик</b> — находки могут быть завышены
          </span>
        </div>
      )}

      <div className="zc-body">
      <div className="zc-left">
      <div className="dc-crop zc-crop">
        {api === undefined ? (
          <div className="crop-ph" />
        ) : cropSrc && !imgErr ? (
          <img src={cropSrc} width={360} height={360} alt="Вырезка снимка вокруг зоны" onError={() => setImgErr(true)} data-testid="zone-crop-img" />
        ) : (
          <canvas ref={cv} data-testid="zone-crop-canvas" />
        )}
        {api?.crop_false_color && !imgErr && (
          <div className="crop-switch" role="group" aria-label="Каналы вырезки">
            <button className={bands === 'rgb' ? 'on' : ''} onClick={() => setBands('rgb')} data-testid="zone-crop-rgb">
              RGB
            </button>
            <button className={bands === 'false' ? 'on' : ''} onClick={() => setBands('false')} data-testid="zone-crop-false">
              ложный цвет
            </button>
          </div>
        )}
        <div className="dc-crop-cap">
          ≈ {fmtNum(SIZE_M / 1000, 1)} × {fmtNum(SIZE_M / 1000, 1)} км · коралловый контур — пятна, пунктир — ячейка H3
        </div>
      </div>
      {typeof nConf === 'number' && (
        <div className={`dc-agree zc-agree ${nConf > 0 ? 'on' : ''}`} data-testid="zone-confirmed">
          <span className={nConf > 0 ? 'sw-double small' : 'sw-single small'} aria-hidden />
          <span>
            <b>Уверенные находки:</b> {nConf} из {nDet ?? '—'} <small className="muted">· {AGREE_NOTE}</small>
          </span>
        </div>
      )}

      <div className="zc-actions">
        <button className="btn small accent-outline" onClick={() => p.onPlace(z.h3)} data-testid="zone-open-place">
          Карточка места
        </button>
        {pdfOk && (
          <a className="btn small" href={pdfHref} download data-testid="zone-pdf">
            Справка PDF
          </a>
        )}
        <button
          className="btn small ghost"
          data-testid="zone-copy-coords"
          title={coords}
          onClick={() =>
            navigator.clipboard?.writeText(coords).then(
              () => p.onToast('Координаты скопированы'),
              () => p.onToast(coords),
            )
          }
        >
          координаты
        </button>
      </div>
      <div className="dc-note muted">
        <span className="mono">{coords}</span> · H3 {z.h3}
        {api === null ? ' · расчёт в браузере (нет /api/zone)' : ''}
      </div>
      </div>
      <div className="zc-right">

      <div className="dc-grid">
        <div className="dc-kpi">
          <div className="kpi-label">Индекс ячейки</div>
          <div className="kpi-value accent-text">
            {fmtPermille(z.index)}
            <small> ‰</small>
          </div>
          <div className="kpi-hint">
            {nDet ?? '—'} пятен · {av} {au}
          </div>
        </div>
        <div className="dc-kpi">
          <div className="kpi-label">Уверенность модели</div>
          <div className="kpi-value">{z.mean_prob === undefined ? '—' : z.mean_prob.toFixed(2)}</div>
          <div className="kpi-hint">
            средняя; макс. {maxProb === null ? '—' : maxProb.toFixed(2)}; порог {fmtThr(p.threshold)}
          </div>
        </div>
        <div className="dc-kpi">
          <div className="kpi-label">Облачность сцены</div>
          <div className={`kpi-value ${(cloud ?? 0) > 0.3 ? 'warn-text' : ''}`}>{fmtPct(cloud)}</div>
        </div>
        <div className="dc-kpi">
          <div className="kpi-label">Наблюдалось ячейки</div>
          <div className="kpi-value">{fmtPct(obs)}</div>
          <div className="kpi-hint">observed_frac, вода без облаков</div>
        </div>
      </div>
      {why && (
        <div className={`why ${whyOpen ? 'open' : ''}`} data-testid="zone-why">
          <button className="why-head" onClick={() => setWhyOpen((v) => !v)} aria-expanded={whyOpen} data-testid="zone-why-toggle">
            <span>{z.rank === 1 ? 'Почему это место первое' : 'Почему это место в приоритете'}</span>
            <span className="why-chev" aria-hidden>
              {whyOpen ? '−' : '+'}
            </span>
          </button>
          {whyOpen && (
            <div className="why-body">
              <div className="why-eq" data-testid="zone-why-eq">
                <Term v={fmtNum(why.terms[0]?.value ?? zonePx(z))} l="пикс. с признаками" />
                <span className="op">×</span>
                <Term v={(why.terms[1]?.value ?? z.mean_prob ?? 0).toFixed(2)} l="ср. уверенность" />
                <span className="op">×</span>
                <Term v={`×${(why.terms[2]?.contribution ?? repeatFactor(z.repeat_dates)).toFixed(1)}`} l={`повтор: ${why.terms[2]?.value ?? z.repeat_dates ?? 1} дат`} />
                <span className="op">=</span>
                <Term v={fmtNum(why.score, 1)} l="балл" accent />
              </div>
              <div className="why-bars" aria-label="Балл зон этой даты">
                {ranked.list.map((x) => (
                  <div key={x.rank} className={`wb-row ${x.rank === z.rank ? 'on' : ''}`}>
                    <span className="wb-n">№{x.rank}</span>
                    <span className="wb-bar">
                      <span style={{ width: `${Math.max(2, (x.score / ranked.max) * 100)}%` }} />
                    </span>
                    <span className="wb-v">{fmtNum(x.score, 1)}</span>
                  </div>
                ))}
              </div>
              <p className="why-text">{why.text.replace(/\s*Это приоритет обследования.*$/, '')}</p>
              <p className="why-formula muted">
                <span className="mono">{why.formula}</span>
                <br />
                {why.formula_text.replace(/\s*Это порядок обследования.*$/, '')}
              </p>
            </div>
          )}
        </div>
      )}

      </div>
      </div>
    </div>
  );
}

function Term({ v, l, accent }: { v: string; l: string; accent?: boolean }) {
  return (
    <span className={`term ${accent ? 'accent' : ''}`}>
      <b>{v}</b>
      <small>{l}</small>
    </span>
  );
}
