// L140 · §51 п.3 «Динамика района» + п.5 интегральная оценка (площадь скоплений, доля покрытия по маске).
// Self-contained: fetches GET /api/v3/regions/{region}/dynamics (L131), renders as an overlay panel (desktop: right
// drawer over the map; ≤ 820 px: full-screen sheet). Numbers only from the API; «площадь, не число предметов»;
// шт./км² — only field records of the same date (±N сут, ≤ R км) as returned by the backend, never derived from area.
// Charts: small multiples (one measure per chart, no second axis) — finds, find area, field density.
import { useEffect, useMemo, useState } from 'react';
import { get } from './api3';
import { dateRu, num } from './fmt';
import './dynamics.css';

export interface DynRow {
  scene_key: string;
  date: string | null;
  datetime?: string | null;
  scene_kind?: string | null;
  evaluable: boolean;
  not_evaluated_reason?: string | null;
  cloud_pct?: number | null;
  n_finds?: number | null;
  n_large?: number | null;
  large_area_km2?: number | null;
  total_find_area_km2?: number | null;
  find_mask_area_km2?: number | null;
  valid_water_km2?: number | null;
  coverage_pct?: number | null;
  field_items_km2?: number | null;
  field?: { value: number; date: string; distance_km: number; source: string; profile?: string; trust_note?: string } | null;
  field_reason?: string | null;
}
export interface Dynamics {
  region: string;
  label: string;
  short?: string;
  count: number;
  rows: DynRow[];
  summary?: {
    n_snapshots?: number;
    n_evaluable?: number;
    n_finds?: number;
    n_large?: number;
    total_find_area_km2?: number;
    find_mask_area_km2?: number;
    valid_water_km2?: number;
    coverage_pct?: number;
    last_date?: string;
    note?: string;
  };
  params?: { field_region_km?: number; field_max_days?: number; large_zone_km2?: number };
  note?: string;
}

/** coverage values are tiny (10⁻⁴ %): keep 2 significant digits instead of rounding them to 0 */
export function pctSmall(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (v === 0) return '0 %';
  const d = Math.min(6, Math.max(0, 1 - Math.floor(Math.log10(Math.abs(v)))));
  return `${num(v, d)} %`;
}
const km2 = (v: number | null | undefined) => (v === null || v === undefined ? '—' : `${num(v, v >= 10 ? 1 : v >= 1 ? 2 : 3)} км²`);

/** §51 п.5 for one snapshot (scene header): finds area, large ones, coverage by the finds mask. */
export function SceneIntegral({ s }: { s: Partial<DynRow> & { large_threshold_km2?: number | null; integral_note?: string | null } }) {
  if (s.total_find_area_km2 === undefined) return null;
  return (
    <div className="dy-integral" data-testid="scene-integral">
      <span>
        площадь скоплений <b>{km2(s.total_find_area_km2)}</b>
      </span>
      {!!s.n_large && (
        <span>
          · крупных (маска ≥ {num(s.large_threshold_km2 ?? 0.1, 1)} км² или длина ≥ 500 м): <b>{s.n_large}</b>, {km2(s.large_area_km2)}
        </span>
      )}
      <span>
        · покрытие маской <b>{pctSmall(s.coverage_pct)}</b> пригодной воды
      </span>
      <span className="dy-honest">площадь, не число предметов</span>
    </div>
  );
}

type Metric = 'finds' | 'area' | 'field';
const METRIC: Record<Metric, { title: string; unit: string; cls: string; val: (r: DynRow) => number | null }> = {
  finds: { title: 'Находки на снимке', unit: 'шт. зон', cls: 'sat', val: (r) => (r.evaluable ? r.n_finds ?? 0 : null) },
  area: { title: 'Площадь скоплений, км² (площадь, не предметы)', unit: 'км²', cls: 'sat', val: (r) => (r.evaluable ? r.total_find_area_km2 ?? 0 : null) },
  field: { title: 'Поле: шт./км² в ту же дату (независимо от снимка)', unit: 'шт./км²', cls: 'field', val: (r) => r.field_items_km2 ?? null },
};

function MiniBars({ rows, m, cur, onPick }: { rows: DynRow[]; m: Metric; cur?: string | null; onPick?: (k: string) => void }) {
  const W = 100; // percent-based viewBox width per bar slot is computed below
  const vals = rows.map((r) => METRIC[m].val(r));
  const max = Math.max(0, ...vals.map((v) => v ?? 0));
  const n = rows.length;
  const H = 64;
  const slot = W / Math.max(1, n);
  const bw = Math.max(1.2, Math.min(8, slot * 0.62));
  const any = vals.some((v) => v !== null && v !== undefined);
  return (
    <figure className="dy-chart" data-testid={`dyn-chart-${m}`}>
      <figcaption>{METRIC[m].title}</figcaption>
      {!any ? (
        <div className="dy-none">{m === 'field' ? 'полевых измерений в эти даты рядом нет' : 'нет оцениваемых снимков'}</div>
      ) : n < 2 ? (
        // L145 §51 п.3: one date — a lone full-height bar read as an empty white box; the value + why there is no chart
        <div className="dy-none" data-testid={`dyn-single-${m}`}>
          {dateRu(rows[0]?.date)}: <b>{vals[0] === null || vals[0] === undefined ? '—' : `${num(vals[0])} ${METRIC[m].unit}`}</b> · у района одна дата снимка — сравнивать по датам не с чем
        </div>
      ) : (
        <svg viewBox={`0 0 ${W} ${H + 14}`} preserveAspectRatio="none" className="dy-svg" role="img" aria-label={METRIC[m].title}>
          <line x1={0} x2={W} y1={H} y2={H} className="dy-base" />
          {rows.map((r, i) => {
            const v = vals[i];
            const x = i * slot + (slot - bw) / 2;
            const h = v === null || v === undefined || max <= 0 ? 0 : Math.max(v > 0 ? 1.5 : 0, (v / max) * (H - 4));
            const on = r.scene_key === cur;
            return (
              <g key={r.scene_key} className={`dy-bar ${METRIC[m].cls} ${on ? 'on' : ''} ${v === null ? 'na' : ''}`} onClick={() => onPick?.(r.scene_key)}>
                <title>
                  {dateRu(r.date)} · {v === null || v === undefined ? (m === 'field' ? r.field_reason ?? 'нет' : r.not_evaluated_reason ?? 'не оценивался') : `${num(v)} ${METRIC[m].unit}`}
                </title>
                {/* hit target: the whole column */}
                <rect x={i * slot} y={0} width={slot} height={H} className="dy-hit" />
                {v === null || v === undefined ? (
                  <line x1={x + bw / 2} x2={x + bw / 2} y1={H - 3} y2={H} className="dy-na" />
                ) : (
                  <rect x={x} y={H - h} width={bw} height={h} rx={Math.min(1, bw / 3)} />
                )}
              </g>
            );
          })}
        </svg>
      )}
      {any && n >= 2 && (
        <div className="dy-axis">
          <span>{dateRu(rows[0]?.date)}</span>
          <span>макс. {num(max)} {METRIC[m].unit}</span>
          <span>{dateRu(rows[n - 1]?.date)}</span>
        </div>
      )}
    </figure>
  );
}

interface Props {
  region: string;
  /** the snapshot open now (highlighted) */
  scene?: string | null;
  onScene?: (sceneKey: string) => void;
  onClose: () => void;
}

export default function DynamicsPanel({ region, scene, onScene, onClose }: Props) {
  const [d, setD] = useState<Dynamics | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const ac = new AbortController();
    setD(null);
    setErr(null);
    get<Dynamics>(`/api/v3/regions/${encodeURIComponent(region)}/dynamics`, {}, ac.signal).then(setD, (e) => {
      if (!ac.signal.aborted) setErr(e?.message ?? String(e));
    });
    return () => ac.abort();
  }, [region]);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [onClose]);
  const rows = useMemo(() => (d?.rows ?? []).slice().sort((a, b) => (a.datetime ?? a.date ?? '').localeCompare(b.datetime ?? b.date ?? '')), [d]);
  const s = d?.summary;
  const p = d?.params;

  return (
    <div className="dy-panel" role="dialog" aria-label="Динамика района" data-testid="dynamics-panel">
      <div className="dy-head">
        <div>
          <div className="dy-kicker">Динамика района по датам снимков</div>
          <div className="dy-title" data-testid="dynamics-title">
            {d?.short ?? d?.label ?? region}
          </div>
        </div>
        <button className="icon-btn dy-close" onClick={onClose} aria-label="Закрыть" data-testid="dynamics-close">
          ✕
        </button>
      </div>
      <div className="dy-body">
        {err && (
          <div className="c-err" role="alert" data-testid="dynamics-error">
            Динамика не загрузилась: {err}
          </div>
        )}
        {!d && !err && <div className="note">Загрузка…</div>}
        {d && (
          <>
            {s && (
              <div className="dy-sum" data-testid="dynamics-summary">
                <div className="dy-kv">
                  <span>снимков</span>
                  <b>
                    {s.n_snapshots ?? d.count}
                    {s.n_evaluable !== undefined && s.n_evaluable !== s.n_snapshots ? ` (оцениваемых ${s.n_evaluable})` : ''}
                  </b>
                </div>
                <div className="dy-kv">
                  <span>находок</span>
                  <b>{s.n_finds ?? '—'}</b>
                </div>
                <div className="dy-kv">
                  <span>крупных (≥ {num(p?.large_zone_km2 ?? 0.1, 1)} км²)</span>
                  <b>{s.n_large ?? '—'}</b>
                </div>
                <div className="dy-kv">
                  <span>площадь скоплений, сумма</span>
                  <b>{km2(s.total_find_area_km2)}</b>
                </div>
                <div className="dy-kv">
                  <span>покрытие маской находок</span>
                  <b>{pctSmall(s.coverage_pct)}</b>
                </div>
                <div className="dy-honest" data-testid="dynamics-honest">
                  {/не число предметов/i.test(s.note ?? '') ? s.note : `Площадь, не число предметов. ${s.note ?? ''}`}
                </div>
              </div>
            )}
            <MiniBars rows={rows} m="finds" cur={scene} onPick={onScene} />
            <MiniBars rows={rows} m="area" cur={scene} onPick={onScene} />
            <MiniBars rows={rows} m="field" cur={scene} onPick={onScene} />
            <div className="dy-table-wrap">
              <table className="dy-table" data-testid="dynamics-table">
                <thead>
                  <tr>
                    <th>Дата снимка</th>
                    <th className="r">Находки</th>
                    <th className="r">Площадь, км²</th>
                    <th className="r dy-opt">Покрытие</th>
                    <th className="r dy-opt">Облачн.</th>
                    <th className="r">Поле, шт./км²</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr
                      key={r.scene_key}
                      className={`${r.scene_key === scene ? 'on' : ''} ${onScene ? 'click' : ''}`}
                      onClick={() => onScene?.(r.scene_key)}
                      data-testid="dynamics-row"
                    >
                      <td>
                        {dateRu(r.date)}
                        {!r.evaluable && <div className="dy-sub">не оценивался: {r.not_evaluated_reason ?? '—'}</div>}
                        {/* phone: the two optional columns fold into the date cell */}
                        <div className="dy-sub dy-mob">
                          облачн. {r.cloud_pct === null || r.cloud_pct === undefined ? '—' : `${num(r.cloud_pct, 1)} %`}
                          {r.evaluable ? ` · покрытие ${pctSmall(r.coverage_pct)}` : ''}
                        </div>
                      </td>
                      <td className="r">
                        {r.evaluable ? r.n_finds ?? 0 : '—'}
                        {!!r.n_large && <div className="dy-sub">крупных {r.n_large}</div>}
                      </td>
                      <td className="r">{r.evaluable ? num(r.total_find_area_km2 ?? 0, 3) : '—'}</td>
                      <td className="r dy-opt">{r.evaluable ? pctSmall(r.coverage_pct) : '—'}</td>
                      <td className="r dy-opt">{r.cloud_pct === null || r.cloud_pct === undefined ? '—' : `${num(r.cloud_pct, 1)} %`}</td>
                      <td className="r">
                        {r.field ? (
                          <>
                            <b>{num(r.field.value)}</b>
                            <div className="dy-sub">
                              {dateRu(r.field.date)} · {num(r.field.distance_km, 0)} км · {r.field.source}
                            </div>
                          </>
                        ) : (
                          <span className="faint">нет</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="dy-note" data-testid="dynamics-note">
              {d.note}
              {p?.field_region_km !== undefined && ` Поле: записи в пределах ${num(p.field_region_km, 0)} км и ±${p.field_max_days} сут от снимка.`}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
