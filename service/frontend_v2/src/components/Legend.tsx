import type { Layers, Manifest } from '../types';
import type { FlowField } from '../map/flow';
import { fmtPermille, fmtThr, rankColor, rgbStr, type H3Scale } from '../lib/style';

interface Props {
  manifest: Manifest;
  model: string;
  threshold: number | null;
  layers: Layers;
  scale: H3Scale;
  confAvail: boolean;
  nArtifacts: number;
  flow: FlowField[];
  check: boolean;
}

export default function Legend(p: Props) {
  const L = p.layers;
  return (
    <div className="legend" data-testid="legend">
      {p.check ? (
        <>
          <div className="lg-row">
            <span className="lg-sw" style={{ background: 'var(--accent)' }} /> находка 2-го снимка внутри облака
          </div>
          <div className="lg-row">
            <span className="lg-sw" /> находка вне облака
          </div>
          <div className="lg-row">
            <span className="lg-sw art" style={{ width: 6, height: 6, background: '#a8b0ba', border: 0 }} /> частицы прогноза на момент 2-го снимка
          </div>
        </>
      ) : (
        <>
          {L.detections && (
            <div className="lg-row">
              <span className="lg-sw" /> признаки плавающего материала (порог {fmtThr(p.threshold)})
            </div>
          )}
          {L.detections && p.confAvail && (
            <div className="lg-row">
              <span className="lg-sw conf" /> вторая модель согласна (сигнал)
            </div>
          )}
          {L.detections && L.artifacts && p.nArtifacts > 0 && (
            <div className="lg-row">
              <span className="lg-sw art" /> исключённый артефакт
            </div>
          )}
          {L.prob && (
            <div className="lg-row">
              <span className="lg-sw sq" style={{ background: 'linear-gradient(90deg,#2b6cb0,#b794f4,#f6ad55)', opacity: 0.8 }} /> вероятность ниже порога
            </div>
          )}
          {L.zones && (
            <div className="lg-row">
              <span style={{ display: 'flex', gap: 2 }}>
                {[1, 4, 8].map((r) => (
                  <span key={r} className="lg-sw sq" style={{ background: rgbStr(rankColor(r, 10)) }} />
                ))}
              </span>
              приоритет обследования 1 → 10
            </div>
          )}
          {L.h3 && (
            <div style={{ marginTop: 8 }}>
              <div className="lg-title">Индекс по ячейкам H3, ‰{L.h3_3d ? ' · высота — лог' : ''}</div>
              <div className="lg-ramp">
                {p.scale.colors.map((c, i) => (
                  <span key={i} style={{ background: rgbStr(c) }} />
                ))}
              </div>
              <div className="lg-ticks">
                <span>0</span>
                {p.scale.breaks
                  .filter((_, i) => i % 2 === 1)
                  .map((b) => (
                    <span key={b}>{fmtPermille(b)}</span>
                  ))}
              </div>
            </div>
          )}
          {L.drift && (
            <div className="lg-row" style={{ marginTop: 6 }}>
              <span className="lg-sw" style={{ borderColor: '#eceef0', background: 'transparent', width: 14, height: 2, borderRadius: 0, borderWidth: '1px 0 0' }} /> дрейф частиц, 0–72 ч
            </div>
          )}
          {p.flow.map((f) => (
            <div className="lg-row" key={f.kind} style={{ marginTop: 6 }}>
              <span className="lg-sw" style={{ borderColor: f.kind === 'wind' ? '#c8ccd2' : '#6eb4e1', background: 'transparent', width: 14, height: 2, borderRadius: 0, borderWidth: '1px 0 0' }} />
              {f.kind === 'wind' ? 'ветер 10 м' : 'поверхностные течения'}, до {fmtPermille(f.maxSpeed)} м/с
            </div>
          ))}
          {L.osm && (
            <div className="lg-row" style={{ marginTop: 6 }}>
              <span className="lg-sw" style={{ borderColor: '#a8b0ba', background: 'transparent', width: 8, height: 8 }} /> объекты OSM (фермы, пляжи, порты)
            </div>
          )}
        </>
      )}
      <div className="lg-honest" data-testid="legend-honest">
        {p.manifest.index?.note ? p.manifest.index.note[0].toUpperCase() + p.manifest.index.note.slice(1) : 'Индекс по снимку, не масса пластика'}.
        {L.drift || p.check ? ' Дрейф — демонстрационный прогноз, не валидирован.' : ''}
      </div>
    </div>
  );
}
