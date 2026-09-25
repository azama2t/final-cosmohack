import { useEffect, useState } from 'react';
import type { Manifest, SceneRef } from '../types';
import { apiCompare, mergeApi, sceneStats, type SceneStats } from '../lib/stats';
import { isFlagged, shortName } from '../lib/data';
import { fmtArea, fmtDate, fmtNum, fmtPct, fmtPermille, modelLabel } from '../lib/style';

interface Props {
  manifest: Manifest;
  compare: { a: SceneRef; b: SceneRef };
  model: string;
  setCompare: (c: { a: SceneRef; b: SceneRef } | null) => void;
}

type Row = { label: string; get: (s: SceneStats) => number | null; fmt: (v: number | null) => string; unit?: string; higherIsWorse?: boolean };

const ROWS: Row[] = [
  { label: 'Индекс (средний по ячейкам)', get: (s) => s.meanIndex, fmt: fmtPermille, unit: '‰', higherIsWorse: true },
  { label: 'Индекс (Σ помечено / Σ вода)', get: (s) => s.pooledIndex, fmt: fmtPermille, unit: '‰', higherIsWorse: true },
  { label: 'Макс. индекс ячейки', get: (s) => s.maxCell, fmt: fmtPermille, unit: '‰', higherIsWorse: true },
  { label: 'Обнаружений', get: (s) => s.nDetections, fmt: (v) => fmtNum(v), higherIsWorse: true },
  { label: 'Площадь пятен', get: (s) => s.areaM2, fmt: (v) => (v === null ? '—' : (v < 0 ? '−' : '') + fmtArea(Math.abs(v)).join(' ')), higherIsWorse: true },
  { label: 'Зона №1, индекс', get: (s) => s.topZone, fmt: fmtPermille, unit: '‰', higherIsWorse: true },
  { label: 'Облачность', get: (s) => s.cloud, fmt: (v) => fmtPct(v) },
  { label: 'Ячеек с данными', get: (s) => s.cellsObserved, fmt: (v) => fmtNum(v) },
];

export default function ComparePanel({ manifest, compare, model, setCompare }: Props) {
  const [stats, setStats] = useState<[SceneStats | null, SceneStats | null] | null>(null);

  useEffect(() => {
    let alive = true;
    setStats(null);
    Promise.all([sceneStats(manifest, compare.a, model), sceneStats(manifest, compare.b, model), apiCompare(compare.a, compare.b, model)]).then(
      ([a, b, api]) => {
        if (!alive) return;
        setStats([a && mergeApi(a, api?.a), b && mergeApi(b, api?.b)]);
      },
    );
    return () => {
      alive = false;
    };
  }, [manifest, compare, model]);

  const set = (side: 'a' | 'b', ref: Partial<SceneRef>) => {
    const cur = { ...compare[side], ...ref };
    if (ref.region) {
      const r = manifest.regions.find((x) => x.id === ref.region);
      cur.date = r?.dates[r.dates.length - 1]?.date ?? cur.date;
    }
    setCompare({ ...compare, [side]: cur });
  };

  const [a, b] = stats ?? [null, null];
  return (
    <div className="compare-panel" data-testid="compare-panel">
      <section className="section">
        <div className="eyebrow">Режим сравнения · {modelLabel(model, manifest.models[model]?.name)}</div>
        <h2 className="big-title">Сравнение</h2>
        <div className="cmp-pickers">
          {(['a', 'b'] as const).map((side) => {
            const ref = compare[side];
            const r = manifest.regions.find((x) => x.id === ref.region);
            return (
              <div key={side} className={`cmp-picker side-${side}`}>
                <span className="cmp-tag">{side.toUpperCase()}</span>
                <select value={ref.region} onChange={(e) => set(side, { region: e.target.value })} data-testid={`compare-region-${side}`}>
                  {manifest.regions.map((x) => (
                    <option key={x.id} value={x.id} title={x.name}>
                      {shortName(x.name)}
                    </option>
                  ))}
                </select>
                <select value={ref.date} onChange={(e) => set(side, { date: e.target.value })} data-testid={`compare-date-${side}`}>
                  {r?.dates.map((d) => (
                    <option key={d.date} value={d.date}>
                      {fmtDate(d.date)}
                      {isFlagged(d) ? ' · дымка/блик' : ''}
                    </option>
                  ))}
                </select>
              </div>
            );
          })}
        </div>
      </section>
      <section className="section">
        <table className="cmp-table" data-testid="compare-table">
          <colgroup>
            <col className="c-label" />
            <col className="c-num" />
            <col className="c-num" />
            <col className="c-delta" />
          </colgroup>
          <thead>
            <tr>
              <th />
              <th className="num">A</th>
              <th className="num">B</th>
              <th className="num">B − A</th>
            </tr>
          </thead>
          <tbody>
            {ROWS.map((row) => {
              const va = a ? row.get(a) : null;
              const vb = b ? row.get(b) : null;
              const d = va !== null && vb !== null ? vb - va : null;
              // «+400 %» from 0.0001 → 0.0005 is noise: hide the relative change when A or B − A rounds to zero
              const looksZero = (v: number | null) => v === null || /^0(\s|$|,0*(\s|$))/.test(row.fmt(Math.abs(v)));
              const tiny = (v: number | null) => looksZero(v) || row.fmt(Math.abs(v as number)).startsWith('<');
              const rel = d !== null && va && !tiny(va) && !tiny(d) ? d / Math.abs(va) : null;
              const dZero = d !== null && looksZero(d);
              const cls = d === null || !row.higherIsWorse || Math.abs(d) < 1e-9 || looksZero(d) ? '' : d > 0 ? 'worse' : 'better';
              return (
                <tr key={row.label}>
                  <td>{row.label}</td>
                  <td className="num">{stats ? row.fmt(va) : '…'}</td>
                  <td className="num">{stats ? row.fmt(vb) : '…'}</td>
                  <td className={`num delta ${cls}`}>
                    {d === null ? '—' : dZero ? row.fmt(0) : tiny(d) ? '≈ 0' : `${d > 0 ? '+' : ''}${row.fmt(d)}`}
                    {rel !== null && Number.isFinite(rel) && (
                      <small>
                        {rel > 0 ? '+' : ''}
                        {Math.round(rel * 100)} %
                      </small>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <p className="muted small hint">
          Коралловым — где B выше A. Индекс по снимку, не масса пластика; при разной облачности сравнение менее надёжно.
          {a?.source === 'api' ? ' Источник: API сервиса.' : ' Посчитано в браузере из файлов слоя данных.'}
        </p>
        <button className="btn block" onClick={() => setCompare(null)} data-testid="compare-close">
          Выйти из сравнения
        </button>
      </section>
    </div>
  );
}
