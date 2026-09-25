import { useEffect, useRef, useState } from 'react';
import type { DateEntry, DetProps, Feature, FC, H3Props, Manifest } from '../types';
import { centroid, featureBBox } from '../map/layers';
import { agreeText, AGREE_NOTE } from '../lib/confirm';
import { apiPaths, apiPost } from '../lib/api';
import { ACCENT, fmtThr, fmtNum, fmtArea, fmtDate, fmtPct, fmtPermille, modelLabel } from '../lib/style';

interface Props {
  feature: Feature<DetProps>;
  dateEntry: DateEntry;
  rgbImg: HTMLImageElement | null;
  h3: FC<H3Props> | null;
  manifest: Manifest;
  threshold: number | null;
  onClose: () => void;
  onToast: (t: string) => void;
}

const SIZE = 344; // css px of the crop

export default function DetectionCard({ feature, dateEntry, rgbImg, h3, manifest, threshold, onClose, onToast }: Props) {
  const p = feature.properties;
  const canvas = useRef<HTMLCanvasElement>(null);
  const [cell, setCell] = useState<H3Props | null | undefined>(undefined);
  const [cropM, setCropM] = useState(0);
  const [lon, lat] = centroid(feature);

  // H3 cell of the spot centroid → observation quality
  useEffect(() => {
    if (!h3) return;
    let alive = true;
    import('h3-js').then(({ latLngToCell }) => {
      if (!alive) return;
      const id = latLngToCell(lat, lon, 8);
      setCell(h3.features.find((f) => f.properties.h3 === id)?.properties ?? null);
    });
    return () => {
      alive = false;
    };
  }, [h3, lat, lon]);

  // crop of rgb.png around the spot (pixel-exact, no smoothing)
  useEffect(() => {
    const cv = canvas.current;
    if (!cv || !rgbImg) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    cv.width = SIZE * dpr;
    cv.height = SIZE * dpr;
    const ctx = cv.getContext('2d')!;
    const [w, s, e, n] = dateEntry.bounds;
    const W = rgbImg.naturalWidth,
      H = rgbImg.naturalHeight;
    const bb = featureBBox(feature);
    const mPerDegLat = 111320;
    const mPerDegLon = 111320 * Math.cos((lat * Math.PI) / 180);
    const extM = Math.max((bb[2] - bb[0]) * mPerDegLon, (bb[3] - bb[1]) * mPerDegLat);
    const half = Math.max(extM * 1.4, 350) / 2; // metres; at least 700 m window
    setCropM(half * 2);
    const x0 = ((lon - half / mPerDegLon - w) / (e - w)) * W;
    const x1 = ((lon + half / mPerDegLon - w) / (e - w)) * W;
    const y0 = ((n - (lat + half / mPerDegLat)) / (n - s)) * H;
    const y1 = ((n - (lat - half / mPerDegLat)) / (n - s)) * H;
    ctx.fillStyle = '#07111f';
    ctx.fillRect(0, 0, cv.width, cv.height);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(rgbImg, x0, y0, x1 - x0, y1 - y0, 0, 0, cv.width, cv.height);
    // spot outline
    const toPx = (pt: number[]) => [((pt[0] - (lon - half / mPerDegLon)) / ((2 * half) / mPerDegLon)) * cv.width, (((lat + half / mPerDegLat) - pt[1]) / ((2 * half) / mPerDegLat)) * cv.height];
    const g = feature.geometry;
    const polys: number[][][][] = g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates];
    ctx.lineWidth = 2 * dpr;
    ctx.strokeStyle = ACCENT;
    ctx.fillStyle = 'rgba(255,107,74,0.18)';
    ctx.shadowColor = 'rgba(0,0,0,0.6)';
    ctx.shadowBlur = 4 * dpr;
    for (const poly of polys) {
      ctx.beginPath();
      for (const ring of poly)
        ring.forEach((pt, i) => {
          const [x, y] = toPx(pt);
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
      ctx.closePath();
      ctx.fill();
      ctx.stroke();
    }
    ctx.shadowBlur = 0;
    // scale bar: 100 m
    const barM = half * 2 > 1500 ? 500 : 100;
    const barPx = (barM / (half * 2)) * cv.width;
    const bx = 14 * dpr,
      by = cv.height - 16 * dpr;
    ctx.fillStyle = 'rgba(7,17,31,0.7)';
    ctx.fillRect(bx - 6 * dpr, by - 18 * dpr, barPx + 12 * dpr, 26 * dpr);
    ctx.fillStyle = '#fff';
    ctx.fillRect(bx, by, barPx, 3 * dpr);
    ctx.font = `${11 * dpr}px Inter, sans-serif`;
    ctx.fillText(`${barM} м`, bx, by - 5 * dpr);
  }, [rgbImg, feature, dateEntry.bounds, lat, lon]);

  // «ложное?» → review queue: POST /api/review/flag (or, on an older backend, /api/review/label with label «other»)
  const [flagMode, setFlagMode] = useState<'flag' | 'label' | null>(null);
  const [flagged, setFlagged] = useState<boolean | null>(false);
  useEffect(() => {
    let alive = true;
    apiPaths().then((s) => alive && setFlagMode(s.has('/api/review/flag') ? 'flag' : s.has('/api/review/label') ? 'label' : null));
    return () => {
      alive = false;
    };
  }, []);
  const flagFalse = async () => {
    if (!flagMode) return;
    setFlagged(null);
    const body = { id: p.id, region: p.region, date: p.date, model: p.model, lon, lat, max_prob: p.max_prob };
    const r =
      flagMode === 'flag'
        ? await apiPost('/api/review/flag', { ...body, note: 'ложное? (с карты)' })
        : await apiPost('/api/review/label', { ...body, label: 'other', note: 'ложное? (с карты)' });
    setFlagged(r.ok);
    onToast(r.ok ? 'Находка добавлена в очередь «Проверка»' : `Не удалось отметить: ${r.error}`);
  };

  const [a, u] = fmtArea(p.area_m2);
  const conf = p.mean_prob;
  const confLabel = conf >= 0.75 ? 'высокая' : conf >= 0.5 ? 'средняя' : 'низкая';
  const coords = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;

  return (
    <div className="det-card glass" data-testid="detection-card">
      <button className="icon-btn close" onClick={onClose} aria-label="Закрыть" data-testid="detection-card-close">
        ×
      </button>
      <div className="eyebrow accent-text">Находка · признаки плавающего мусора</div>
      <div className="dc-title">
        {fmtDate(p.date)} · {modelLabel(p.model, manifest.models[p.model]?.name)}
      </div>
      {agreeText(p) && (
        <div className={`dc-agree ${p.confirmed ? 'on' : ''}`} data-testid="detection-agreement">
          <span className={p.confirmed ? 'sw-double small' : 'sw-single small'} aria-hidden />
          <span>
            <b>Согласие моделей:</b> {agreeText(p)}
            <small className="muted"> · {AGREE_NOTE}</small>
          </span>
        </div>
      )}
      <div className="dc-crop">
        <canvas ref={canvas} style={{ width: SIZE, height: SIZE }} />
        <div className="dc-crop-cap">
          Вырезка снимка Sentinel-2 ≈ {Math.round(cropM)} × {Math.round(cropM)} м, пиксель 10 м
        </div>
      </div>
      <div className="dc-grid">
        <div className="dc-kpi">
          <div className="kpi-label">Площадь помеченной области</div>
          <div className="kpi-value accent-text">
            {a}
            <small> {u}</small>
          </div>
          {u === 'га' && <div className="kpi-hint">{fmtNum(p.area_m2)} м²</div>}
        </div>
        <div className="dc-kpi">
          <div className="kpi-label">Уверенность модели</div>
          <div className="kpi-value">
            {conf.toFixed(2)}
            <small> {confLabel}</small>
          </div>
          <div className="kpi-hint">
            средняя; макс. {p.max_prob.toFixed(2)}; порог {fmtThr(threshold)}
          </div>
        </div>
        <div className="dc-kpi">
          <div className="kpi-label">Облачность сцены</div>
          <div className="kpi-value">{fmtPct(dateEntry.cloud_frac)}</div>
        </div>
        <div className="dc-kpi">
          <div className="kpi-label">Наблюдалось ячейки H3</div>
          <div className="kpi-value">{cell === undefined ? '…' : cell ? fmtPct(cell.observed_frac) : '—'}</div>
          <div className="kpi-hint">
            {cell ? `индекс ячейки ${fmtPermille(cell.share_permille)} ‰` : cell === null ? 'ячейка вне сетки' : 'загрузка сетки'}
          </div>
        </div>
      </div>
      <div className="dc-foot">
        <span className="mono">{coords}</span>
        <button
          className="btn tiny"
          data-testid="copy-coords"
          onClick={() => {
            navigator.clipboard?.writeText(coords).then(
              () => onToast('Координаты скопированы'),
              () => onToast(coords),
            );
          }}
        >
          координаты
        </button>
      </div>
      {flagMode && (
        <div className="dc-flag">
          <button
            className={`btn tiny ${flagged ? '' : 'ghost'}`}
            disabled={flagged !== false}
            onClick={flagFalse}
            data-testid="flag-false"
            title="Добавить находку в очередь «Проверка» как возможно ложную"
          >
            {flagged === true ? '✓ в очереди проверки' : flagged === null ? 'сохраняю…' : 'ложное?'}
          </button>
          <span className="muted small">сомневаетесь — отправьте на проверку человеком</span>
        </div>
      )}
      <div className="dc-note muted">Площадь — пикселей с prob ≥ порога, а не масса пластика. Сцена {dateEntry.scene_id}.</div>
    </div>
  );
}
