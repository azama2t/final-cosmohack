import { lazy, Suspense, useEffect, useState } from 'react';
import type { DateEntry, DetProps, FC, H3Props, Manifest, Region, SceneRef, TsRow, Zone, ZonesFile } from '../types';
import { fmtThr, fmtArea, fmtDate, fmtNum, fmtPct, fmtPermille, modelLabel } from '../lib/style';
import ExportBox from './ExportBox';
import { isFlagged, rankRegions, regionHaze, regionReliability, shortName } from '../lib/data';
import ComparePanel from './ComparePanel';
import ObsCalendar from './ObsCalendar';
import { verdict } from '../lib/priority';
import { fmtKm, haversineKm, type RoutePlan } from '../lib/route';

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
  /** L15: confirmed detections of this model on this date (null = no data: old files / one model) */
  nConfirmed?: number | null;
  /** L38: objects excluded as artifacts (seam / wake / ship) on this scene; 0 for old data */
  nArtifacts?: number;
  /** L38: detections incl. artifacts (export keeps them, with the `artifact` column) */
  detectionsAll?: FC<DetProps> | null;
  onlyConfirmed?: boolean;
  collapsed: boolean;
  onCollapse: () => void;
  onRegion: (id: string) => void;
  onDate: (d: string) => void;
  onZone: (z: Zone) => void;
  /** L27: «Порядок посещения зон» */
  route?: RoutePlan | null;
  routeOn?: boolean;
  onRoute?: (on: boolean) => void;
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
        // keyed by view: switching overview / region / compare starts the panel from the top
        <div className="panel-scroll" key={p.compare ? 'compare' : p.region ? `region-${p.region.id}` : 'overview'}>
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
  const list = manifest.regions;
  // L34: regions whose latest scene has haze/glint (or > 50 % clouds) are ranked separately, below the reliable ones
  const { ok, bad } = rankRegions(list);
  const max = Math.max(...ok.map((r) => r.summary?.index_permille ?? 0), 1e-9);
  const totalDet = list.reduce((a, r) => a + (r.summary?.n_detections ?? 0), 0);
  const totalArea = list.reduce((a, r) => a + (r.summary?.total_debris_area_m2 ?? 0), 0);
  const [av, au] = fmtArea(totalArea);
  const row = (r: Region, i: number, unreliable: boolean) => {
    const v = r.summary?.index_permille ?? 0;
    const why = unreliable ? regionReliability(r).why : regionHaze(r);
    return (
      <button
        key={r.id}
        className={`rank-row ${unreliable ? 'unreliable' : ''}`}
        onClick={() => onRegion(r.id)}
        data-testid={`rank-row-${r.id}`}
        title={`${r.name}${why ? ' — ' + why : ''}`}
      >
        <span className="rank-n">{unreliable ? <span className="q-dot" /> : i + 1}</span>
        <span className="rank-body">
          <span className="rank-name">
            <span className="rank-name-t">{shortName(r.name)}</span>
            {!unreliable && regionHaze(r) && (
              <span className="ri-haze" title={regionHaze(r)}>
                дымка
              </span>
            )}
          </span>
          {unreliable ? (
            <span className="rank-why">{why}</span>
          ) : (
            <span className="rank-bar">
              <span style={{ width: `${Math.max(4, (v / max) * 100)}%` }} />
            </span>
          )}
        </span>
        <span className="rank-val">
          {fmtPermille(r.summary?.index_permille)} <small>‰</small>
        </span>
      </button>
    );
  };
  return (
    <>
      <section className="section">
        <div className="eyebrow">Где искать в первую очередь</div>
        <h2 className="big-title">Районы по индексу</h2>
        <p className="muted small lead">
          Индекс — {manifest.index.name} на последнем надёжном снимке района. Чем выше, тем больше воды с признаками мусора.
        </p>
        <div className="rank-list" data-testid="rank-reliable">
          {ok.map((r, i) => row(r, i, false))}
        </div>
        {bad.length > 0 && (
          <div className="rank-group-bad" data-testid="rank-unreliable">
            <div className="rank-group-head" title="Последний снимок района с флагом дымки/блика или облачностью > 50 %: индекс по нему может быть завышен, поэтому район не участвует в общем рейтинге.">
              <span className="q-dot" /> ненадёжные снимки (дымка/блик) <span className="muted">· {bad.length}</span>
            </div>
            <p className="rank-group-hint">
              Последний снимок с дымкой или бликом: модель может принять их за мусор, индекс завышен. Показан для справки, в рейтинге не участвует.
            </p>
            <div className="rank-list">{bad.map((r, i) => row(r, i, true))}</div>
          </div>
        )}
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
  // L38: a timeseries row written before the artifact filter (no n_artifacts) still counts artifacts — then the
  // client numbers (artifacts already removed from p.detections) are used
  const tsRow = p.nArtifacts && p.tsRow && p.tsRow.n_artifacts === undefined ? null : p.tsRow;
  const area = tsRow?.total_debris_area_m2 ?? det.reduce((a, f) => a + f.properties.area_m2, 0);
  const n = tsRow?.n_detections ?? det.length;
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
  // with «only confirmed» on, the verdict points to the best zone that has a confirmed detection (if any)
  // L20: the best zone with a finding confirmed by the second model, else zone №1 marked «не подтверждена»
  // (with «only confirmed» on and no confirmed zone the verdict is hidden)
  // L27: zone №1 is always the main line (same as the table); a confirmed alternative goes to the second line
  const vd = verdict(zones);
  const top = vd?.top;
  const alt = vd?.alt ?? null;
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
        {isFlagged(p.dateEntry) && (
          <div className="q-badge" data-testid="quality-badge" title={p.dateEntry.quality?.note || undefined}>
            <span className="q-icon" aria-hidden>
              !
            </span>
            <span>
              <b>дымка/блик</b> — находки могут быть завышены
            </span>
          </div>
        )}
        {top && vd && (
          <div className="verdict-box" data-testid="verdict-box">
            <button className="verdict" onClick={() => p.onZone(top)} data-testid="verdict">
              <span className="verdict-dot" />
              <span>
                <b>Куда отправить обследование:</b> зона №{top.rank} — {fmtPermille(top.index)} ‰, {top.lat.toFixed(4)},{' '}
                {top.lon.toFixed(4)}
                {vd.topConfirmed === false ? (
                  <small
                    className="verdict-badge unconf"
                    data-testid="verdict-unconfirmed"
                    title="в этой ячейке вторая модель не видит признаков в радиусе 20 м — согласие моделей, не проверка на месте"
                  >
                    не подтверждена второй моделью
                  </small>
                ) : vd.topConfirmed ? (
                  <small className="verdict-badge conf" data-testid="verdict-confirmed" title="согласие моделей, не проверка на месте">
                    ✓ подтверждена второй моделью ({top.n_confirmed})
                  </small>
                ) : null}
              </span>
            </button>
            {alt && (
              <button
                className="verdict-alt"
                onClick={() => p.onZone(alt)}
                data-testid="verdict-alt"
                title="лучшая по рангу зона, где находку подтверждает вторая модель (согласие моделей, не проверка на месте) — клик: карточка зоны"
              >
                <span className="conf-badge">✓{alt.n_confirmed}</span>
                <span>
                  ближайшая подтверждённая: <b>№{alt.rank}</b> ({fmtPermille(alt.index)} ‰, {fmtArea(alt.area_m2).join(' ')}) ·{' '}
                  {fmtKm(haversineKm([top.lon, top.lat], [alt.lon, alt.lat]))} км от №{top.rank}
                </span>
              </button>
            )}
            {vd.topConfirmed === false && !alt && (
              <div className="verdict-alt none" data-testid="verdict-no-alt">
                подтверждённых второй моделью зон на этой дате нет
              </div>
            )}
          </div>
        )}
        {!empty && p.onlyConfirmed && p.nConfirmed === 0 && (
          <div className="empty-scene conf-empty" data-testid="confirmed-empty">
            <div className="es-icon" aria-hidden>
              ○
            </div>
            <div>
              <div className="es-title">
                Уверенных находок нет, возможных — {fmtNum(n)}
              </div>
              <div className="es-text muted small">
                Ни одна находка модели «{modelLabel(p.model, p.manifest.models[p.model]?.name)}» не подтверждена второй моделью в
                радиусе 20 м. Выключите «Только подтверждённые», чтобы увидеть возможные находки одной модели.
              </div>
            </div>
          </div>
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
        <Kpi
          label="Обнаружений"
          value={fmtNum(n)}
          hint={typeof p.nConfirmed === 'number' ? `из них подтверждено: ${fmtNum(p.nConfirmed)}` : 'пятен на снимке'}
          hintTitle={typeof p.nConfirmed === 'number' ? 'вторая модель видит признаки в радиусе 20 м — согласие моделей, не проверка на месте' : undefined}
          testid="kpi-detections"
          extra={
            p.nArtifacts ? (
              <div
                className="kpi-hint kpi-arts"
                data-testid="kpi-artifacts"
                title="вероятно шов детекторов Sentinel-2, кильватер или судно — не входят в число обнаружений, индекс и зоны; показать на карте — переключатель в легенде"
              >
                исключено как артефакты: {fmtNum(p.nArtifacts)}
              </div>
            ) : null
          }
        />
        <Kpi label="Площадь пятен" value={av} unit={au} hint="помеченная область" />
        <Kpi label="Облачность" value={fmtPct(cloud)} hint="доля сцены" warn={(cloud ?? 0) > 0.3} />
      </section>

      <ObsCalendar region={p.region} model={p.model} date={p.dateEntry.date} timeseries={p.timeseries} onDate={p.onDate} />

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
        {zones.length > 0 && p.onRoute && (
          <div className="route-box">
            <button
              className={`btn small block route-btn ${p.routeOn ? 'on' : ''}`}
              onClick={() => p.onRoute!(!p.routeOn)}
              data-testid="route-toggle"
              aria-pressed={!!p.routeOn}
            >
              {p.routeOn ? 'Скрыть порядок посещения' : 'Порядок посещения зон'}
            </button>
            {p.routeOn && p.route && (
              <div className="route-info" data-testid="route-info">
                <div className="route-caption">черновой порядок, не навигационный маршрут</div>
                <div className="route-order">
                  <span className="route-port-name">{p.route.port.name}</span>
                  {p.route.legs.map((l) => (
                    <span key={l.n} className="route-step">
                      {' → '}
                      <button
                        className="route-stop"
                        onClick={() => p.onZone(l.zone)}
                        title={`${l.n}-я по порядку · ${fmtKm(l.km)} км от предыдущей точки`}
                      >
                        <i>{l.n}</i>№{l.zone.rank}
                      </button>
                    </span>
                  ))}
                </div>
                <div className="route-total">
                  <b data-testid="route-km">{fmtKm(p.route.totalKm)} км</b> по прямой · {p.route.legs.length} зон · жадный «ближайший
                  сосед» от ближайшего порта
                </div>
                <div className="route-note">
                  Без учёта берега, глубин, судоходства и погоды. Порт — из небольшого справочника в коде (координаты ±1 км).
                </div>
              </div>
            )}
          </div>
        )}
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
                  <tr key={z.rank} data-testid={`zone-row-${z.rank}`} title={`${z.reason} — клик: карточка зоны`} className="clickable" onClick={() => p.onZone(z)}>
                    <td>
                      <span className={`rank-badge ${z.rank <= 3 ? 'top' : ''}`}>{z.rank}</span>
                      {(z.n_confirmed ?? 0) > 0 && (
                        <span className="conf-badge" title={`подтверждено обеими моделями: ${z.n_confirmed} (согласие моделей, не проверка на месте)`}>
                          ✓{z.n_confirmed}
                        </span>
                      )}
                    </td>
                    <td className="num">{fmtPermille(z.index)}</td>
                    <td className="num">
                      {za} <small>{zu}</small>
                    </td>
                    <td className="num">{z.repeat_dates ?? '—'}</td>
                    <td>
                      <button
                        className="btn tiny"
                        onClick={(e) => {
                          e.stopPropagation();
                          p.onZone(z);
                        }}
                        data-testid={`zone-show-${z.rank}`}
                      >
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
        <ExportBox region={p.region.id} date={p.dateEntry.date} model={p.model} detections={p.detectionsAll ?? p.detections} zones={p.zones} onToast={p.onToast} />
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
  hintTitle,
  testid,
  extra,
}: {
  label: string;
  value: string;
  unit?: string;
  hint?: string;
  accent?: boolean;
  warn?: boolean;
  hintTitle?: string;
  testid?: string;
  extra?: import('react').ReactNode;
}) {
  return (
    <div className={`kpi ${accent ? 'accent' : ''} ${warn ? 'warn' : ''}`} data-testid={testid} title={hintTitle}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">
        {value}
        {unit && <small> {unit}</small>}
      </div>
      {hint && <div className="kpi-hint">{hint}</div>}
      {extra}
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
