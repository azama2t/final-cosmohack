import { useState } from 'react';
import type { Layers, Manifest } from '../types';
import { DEFAULT_SCALE, fmtThr, modelLabel, NO_DATA_RGB, rgbStr, type H3Scale } from '../lib/style';

interface Props {
  manifest: Manifest;
  model: string;
  threshold: number | null;
  layers: Layers;
  date: string | null;
  scale: H3Scale;
}

export default function Legend({ manifest, model, threshold, layers, scale }: Props) {
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
              <span className="sw-spot" />
              <span>
                <b>Пятно</b> — вероятность ≥ {fmtThr(threshold)} ({modelLabel(model, manifest.models[model]?.name)})
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
            <div className="lg-block">
              <div className="lg-title">Вероятность модели</div>
              <div className="lg-grad prob" />
              <div className="lg-ticks">
                <span>0.05</span>
                <span>0.5</span>
                <span>1.0</span>
              </div>
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
              <div className="lg-row small">
                <span className="sw-nodata" style={{ background: rgbStr(NO_DATA_RGB, 0.55) }} />
                <span>нет данных — наблюдалось &lt; 50 % ячейки (не ноль)</span>
              </div>
              <div className="muted tiny-text">
                шкала логарифмическая{scale === DEFAULT_SCALE ? '' : ' (верх — 98-й перцентиль ячеек снимка)'}; 1 пиксель 10 м ≈ 0.14 ‰ ячейки
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
