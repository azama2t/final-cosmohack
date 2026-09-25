import { useState } from 'react';
import type { Layers, Manifest } from '../types';
import { DEFAULT_SCALE, fmtThr, H3_LINE_NODATA, H3_LINE_ZERO, modelLabel, probLegendGradient, rgbStr, type H3Scale } from '../lib/style';

interface Props {
  manifest: Manifest;
  model: string;
  threshold: number | null;
  layers: Layers;
  date: string | null;
  scale: H3Scale;
  /** L15: confirmation data exists for the current date */
  confAvail?: boolean;
  /** L38: objects excluded as artifacts on this scene (0 / undefined: old data — no switch) */
  nArtifacts?: number;
  showArtifacts?: boolean;
  onShowArtifacts?: (v: boolean) => void;
}

export default function Legend({ manifest, model, threshold, layers, scale, confAvail, nArtifacts, showArtifacts, onShowArtifacts }: Props) {
  const [open, setOpen] = useState(true);
  const idx = manifest.index;
  return (
    <div className={`legend glass ${open ? '' : 'closed'}`} data-testid="legend">
      <button className="legend-head" onClick={() => setOpen((v) => !v)} data-testid="legend-toggle">
        <span>Легенда</span>
        <span className="muted">{open ? '–' : '+'}</span>
      </button>
      {open && (
        <div className="legend-body">
          {layers.detections && (
            <div className="lg-row">
              <span className="sw-halo">
                <span className="sw-spot-core" />
              </span>
              <span>
                <b>Находка</b> — вероятность ≥ {fmtThr(threshold)} ({modelLabel(model, manifest.models[model]?.name)}); кольцо ~ площадь
              </span>
            </div>
          )}
          {layers.detections && confAvail && (
            <div className="lg-row" data-testid="legend-confirmed">
              <span className="sw-double" aria-hidden />
              <span>
                <b>Двойное кольцо</b> — вторая модель видит объект в радиусе 20 м; согласие моделей, не проверка на месте
              </span>
            </div>
          )}
          {layers.detections && !!nArtifacts && (
            <div className="lg-row lg-arts" data-testid="legend-artifacts">
              <button
                className={`switch small ${showArtifacts ? 'on' : ''}`}
                role="switch"
                aria-checked={!!showArtifacts}
                aria-label="Показывать исключённые артефакты"
                onClick={() => onShowArtifacts?.(!showArtifacts)}
                data-testid="artifacts-toggle"
              >
                <span className="knob" />
              </button>
              <span className="lg-arts-label" onClick={() => onShowArtifacts?.(!showArtifacts)}>
                Показывать исключённые артефакты <b>({nArtifacts})</b>
                <small className="muted">
                  <span className="sw-art" aria-hidden /> серый контур — шов детекторов, кильватер или судно; не входят в индекс и зоны
                </small>
              </span>
            </div>
          )}
          {layers.zones && (
            <div className="lg-row">
              <span className="sw-zone">1</span>
              <span>
                <b>Приоритет обследования</b> — ранг зоны
              </span>
            </div>
          )}
          {layers.prob && (
            <div className="lg-block" data-testid="legend-prob">
              <div className="lg-title">Вероятность модели</div>
              <div className="lg-grad prob" style={{ background: probLegendGradient(threshold ?? 0.5) }} />
              <div className="lg-ticks prob">
                <span>0.05</span>
                <span
                  className="lg-thr"
                  style={{ left: `${(((Math.min(0.98, Math.max(0.08, threshold ?? 0.5)) - 0.05) / 0.95) * 100).toFixed(1)}%` }}
                >
                  порог {fmtThr(threshold)}
                </span>
                <span>1.0</span>
              </div>
              <div className="muted tiny-text">&lt; 0.05 — прозрачно · коралловый — вероятность ≥ порога</div>
            </div>
          )}
          {layers.h3 && (
            <div className="lg-block">
              <div className="lg-title">
                {idx.name}, {idx.unit}
              </div>
              <div className="lg-ramp">
                {scale.colors.map((c, i) => (
                  <span key={i} style={{ background: rgbStr(c) }} />
                ))}
              </div>
              <div className="lg-ticks ramp">
                <span>0</span>
                {scale.breaks.map((b) => (
                  <span key={b}>{b}</span>
                ))}
              </div>
              {layers.h3_3d && <div className="lg-row small">▮ высота столбика ~ индекс (лог), максимум сцены — самый высокий</div>}
              <div className="lg-row small">
                <span className="sw-cell zero" style={{ borderColor: rgbStr(H3_LINE_ZERO, 0.9) }} />
                <span>0 — вода видна, признаков нет</span>
              </div>
              {!layers.h3_3d && (
                <div className="lg-row small">
                  <span className="sw-cell nodata" style={{ borderColor: rgbStr(H3_LINE_NODATA, 1) }} />
                  <span>нет данных — видно &lt; 50 % ячейки</span>
                </div>
              )}
              <div className="muted tiny-text">
                шкала логарифмическая{scale === DEFAULT_SCALE ? '' : ' (верх — 98-й перцентиль ячеек снимка)'}; 1 пиксель 10 м ≈ 0.14 ‰
                ячейки; суша и вне снимка не показаны
              </div>
            </div>
          )}
          <div className="lg-formula">
            <div>
              <b>Индекс</b> = <span className="mono">{idx.formula}</span>, ‰
            </div>
            <div className="lg-note">{idx.note}</div>
          </div>
        </div>
      )}
    </div>
  );
}
