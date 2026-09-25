import { lazy, Suspense, useEffect, useState } from 'react';
import type { DateEntry, DetProps, FC, H3Props, Manifest, Region, SceneRef, TsRow, Zone, ZonesFile } from '../types';
import { fmtThr, fmtArea, fmtDate, fmtNum, fmtPct, fmtPermille, modelLabel } from '../lib/style';
import ExportBox from './ExportBox';
import ComparePanel from './ComparePanel';

const TsChart = lazy(() => import('./TsChart'));

interface Props {
  manifest: Manifest;
  region: Region | null;
  dateEntry: DateEntry | null;
  model: string;
  tsRow: TsRow | null;
  timeseries: TsRow[] | null;
  detections: FC<DetProps> | null;
  zones: ZonesFile | null;
  h3: FC<H3Props> | null;
  collapsed: boolean;
  onCollapse: () => void;
  onRegion: (id: string) => void;
  onDate: (d: string) => void;
  onZone: (z: Zone) => void;
  onCompare: () => void;
  compareActive: boolean;
  compare: { a: SceneRef; b: SceneRef } | null;
  setCompare: (c: { a: SceneRef; b: SceneRef } | null) => void;
  onToast: (t: string) => void;
}

export default function RightPanel(p: Props) {
  return (
    <aside className={`panel panel-right glass ${p.collapsed ? 'collapsed' : ''}`} data-testid="right-panel">
      <button
        className="collapse-btn right"
        onClick={p.onCollapse}
        data-testid="collapse-right"
        title={p.collapsed ? 'Показать панель' : 'Свернуть панель'}
      >
        {p.collapsed ? '‹' : '›'}
      </button>
      {!p.collapsed && (
        <div className="panel-scroll">
          {p.compare ? (
            <ComparePanel manifest={p.manifest} compare={p.compare} model={p.model} setCompare={p.setCompare} />
          ) : p.region && p.dateEntry ? (
            <RegionView {...p} region={p.region} dateEntry={p.dateEntry} />
          ) : (
            <Overview manifest={p.manifest} onRegion={p.onRegion} onCompare={p.onCompare} />
          )}
        </div>
      )}
    </aside>
  );
}

function Overview({ manifest, onRegion, onCompare }: { manifest: Manifest; onRegion: (id: string) => void; onCompare: () => void }) {
  const list = [...manifest.regions].sort((a, b) => (b.summary?.index_permille ?? -1) - (a.summary?.index_permille ?? -1));
  const max = Math.max(...list.map((r) => r.summary?.index_permille ?? 0), 1e-9);
  const totalDet = list.reduce((a, r) => a + (r.summary?.n_detections ?? 0), 0);
  const totalArea = list.reduce((a, r) => a + (r.summary?.total_debris_area_m2 ?? 0), 0);
  const [av, au] = fmtArea(totalArea);
  return (
    <>
      <section className="section">
        <div className="eyebrow">Где искать в первую очередь</div>
        <h2 className="big-title">Районы по индексу</h2>
        <p className="muted small lead">
          Индекс — {manifest.index.name} на последнем снимке района. Чем выше, тем больше воды с признаками мусора.
        </p>
        <div className="rank-list">
          {list.map((r, i) => {
            const v = r.summary?.index_permille ?? 0;
            return (
              <button key={r.id} className="rank-row" onClick={() => onRegion(r.id)} data-testid={`rank-row-${r.id}`}>
                <span className="rank-n">{i + 1}</span>
                <span className="rank-body">
                  <span className="rank-name">{r.name}</span>
                  <span className="rank-bar">
                    <span style={{ width: `${Math.max(4, (v / max) * 100)}%` }} />
                  </span>
                </span>
                <span className="rank-val">
                  {fmtPermille(r.summary?.index_permille)} <small>‰</small>
                </span>
              </button>
            );
          })}
        </div>
      </section>
      <section className="section kpi-grid two">
        <Kpi label="Пятен всего" value={fmtNum(totalDet)} hint="на последних снимках" accent />
        <Kpi label="Площадь пятен" value={av} unit={au} hint="помеченная область" />
      </section>
      <section className="section">
        <button className="btn block" onClick={onCompare} data-testid="compare-button">
          Сравнить районы
        </button>
        <p className="muted small hint">Выберите район слева или на карте, чтобы увидеть снимок, пятна и зоны обследования.</p>
      </section>
    </>
  );
}

function RegionView(p: Props & { region: Region; dateEntry: DateEntry }) {
  const det = p.detections?.features ?? [];
  const area = p.tsRow?.total_debris_area_m2 ?? det.reduce((a, f) => a + f.properties.area_m2, 0);
  const n = p.tsRow?.n_detections ?? det.length;
  const idx = p.tsRow?.mean_index ?? null;
  const cloud = p.dateEntry.cloud_frac ?? p.tsRow?.cloud_frac ?? null;
  const [av, au] = fmtArea(area);
  const zones = p.zones?.zones ?? [];
  const [chartOn, setChartOn] = useState(false);
  useEffect(() => {
    // render chart after first paint (keeps first load light)
    const t = setTimeout(() => setChartOn(true), 60);
    return () => clearTimeout(t);
  }, []);
  const top = zones[0];
  const empty = !!p.detections && det.length === 0 && n === 0;

  return (
    <>
      <section className="section">
        <div className="eyebrow">
          {p.region.country ? `${p.region.country} · ` : ''}тайл {p.region.tile ?? '—'}
        </div>
        <h2 className="big-title">{p.region.name}</h2>
        <div className="muted small">
          {fmtDate(p.dateEntry.date)} · {modelLabel(p.model, p.manifest.models[p.model]?.name)} · порог{' '}
          {fmtThr(p.manifest.models[p.model]?.threshold)}
        </div>
        {top && (
          <button className="verdict" onClick={() => p.onZone(top)} data-testid="verdict">
            <span className="verdict-dot" />
            <span>
              <b>Куда отправить обследование:</b> зона №1 — {fmtPermille(top.index)} ‰, {top.lat.toFixed(4)},{' '}
              {top.lon.toFixed(4)}
            </span>
          </button>
        )}
        {empty && (
          <EmptyScene
            threshold={p.manifest.models[p.model]?.threshold ?? p.zones?.threshold ?? null}
            h3={p.h3}
            cloud={cloud}
          />
        )}
      </section>

      <section className="section kpi-grid" data-testid="kpis">
        <Kpi label="Индекс, ‰" value={fmtPermille(idx)} hint="средний по ячейкам H3" accent />
        <Kpi label="Обнаружений" value={fmtNum(n)} hint="пятен на снимке" />
        <Kpi label="Площадь пятен" value={av} unit={au} hint="помеченная область" />
        <Kpi label="Облачность" value={fmtPct(cloud)} hint="доля сцены" warn={(cloud ?? 0) > 0.3} />
      </section>

      <section className="section">
        <div className="section-head">
          <h3>Индекс по датам</h3>
          <span className="muted small">‰, все модели</span>
        </div>
        <div className="chart-box" data-testid="ts-chart">
          {chartOn && p.timeseries ? (
            <Suspense fallback={<div className="chart-ph" />}>
              <TsChart
                rows={p.timeseries}
                model={p.model}
                date={p.dateEntry.date}
                models={p.manifest.models}
                onDate={p.onDate}
              />
            </Suspense>
          ) : (
            <div className="chart-ph" />
          )}
        </div>
      </section>

      {!empty && (
      <section className="section">
        <div className="section-head">
          <h3>Приоритет обследования</h3>
          <span className="muted small">топ {zones.length}</span>
        </div>
        {zones.length ? (
          <table className="zones-table" data-testid="zones-table">
            <thead>
              <tr>
                <th>#</th>
                <th>Индекс ‰</th>
                <th>Пятна</th>
                <th>Повтор</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {zones.slice(0, 10).map((z) => {
                const [za, zu] = fmtArea(z.area_m2);
                return (
                  <tr key={z.rank} data-testid={`zone-row-${z.rank}`} title={z.reason}>
                    <td>
                      <span className={`rank-badge ${z.rank <= 3 ? 'top' : ''}`}>{z.rank}</span>
                    </td>
                    <td className="num">{fmtPermille(z.index)}</td>
                    <td className="num">
                      {za} <small>{zu}</small>
                    </td>
                    <td className="num">{z.repeat_dates ?? '—'}</td>
                    <td>
                      <button className="btn tiny" onClick={() => p.onZone(z)} data-testid={`zone-show-${z.rank}`}>
                        показать
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <div className="muted small">Нет зон для этой даты и модели.</div>
        )}
      </section>
      )}

      <section className="section actions">
        <button className="btn block accent-outline" onClick={p.onCompare} data-testid="compare-button">
          Сравнить с другим районом / датой
        </button>
        <ExportBox region={p.region.id} date={p.dateEntry.date} model={p.model} detections={p.detections} zones={p.zones} onToast={p.onToast} />
      </section>
    </>
  );
}

export function Kpi({
  label,
  value,
  unit,
  hint,
  accent,
  warn,
}: {
  label: string;
  value: string;
  unit?: string;
  hint?: string;
  accent?: boolean;
  warn?: boolean;
}) {
  return (
    <div className={`kpi ${accent ? 'accent' : ''} ${warn ? 'warn' : ''}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">
        {value}
        {unit && <small> {unit}</small>}
      </div>
      {hint && <div className="kpi-hint">{hint}</div>}
    </div>
  );
}

/** Calm «nothing found» card instead of an empty zones table. */
function EmptyScene({ threshold, h3, cloud }: { threshold: number | null; h3: FC<H3Props> | null; cloud: number | null }) {
  let obs: string;
  if (h3?.features.length) {
    const water = h3.features.filter((f) => f.properties.observed_water_px > 0);
    const withData = water.filter((f) => f.properties.share_permille !== null).length;
    const km2 = water.reduce((a, f) => a + f.properties.observed_water_px, 0) * 100 / 1e6;
    obs = `данные есть для ${fmtPct(water.length ? withData / water.length : null)} водных ячеек H3 (≈ ${fmtNum(km2, km2 < 10 ? 1 : 0)} км² воды без облаков)`;
  } else {
    obs = `наблюдалось ≈ ${fmtPct(cloud === null ? null : 1 - cloud)} сцены без облаков`;
  }
  return (
    <div className="empty-scene" data-testid="empty-scene">
      <div className="es-icon" aria-hidden>
        ✓
      </div>
      <div>
        <div className="es-title">На этом снимке признаков мусора не найдено</div>
        <div className="es-text muted small">
          Порог {fmtThr(threshold)}; {obs}. Это не значит, что мусора нет: его может не быть на поверхности в момент съёмки
          или пятна мельче пикселя 10 м.
        </div>
      </div>
    </div>
  );
}
