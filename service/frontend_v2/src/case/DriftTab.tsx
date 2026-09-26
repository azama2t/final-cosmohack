// §47 п.6 / §48 «Дрейф»: content of the card's «Дрейф» tab (the tab container itself is L132's — mount
// <DriftTab zone={selSz} onShowOnMap={toggleDrift} mapOn={!!drift} /> inside it, one line).
// Everything here comes from the API/published run — nothing invented:
//   - source + time of wind/current data: drift.json.forcing + start_time (published OpenDrift run);
//   - assumptions: wind_drift_factor, horizontal_diffusivity, Stokes drift (or its absence), model;
//   - uncertainty + the honest check: reports/drift_check.md via /api/drift_check (7/18 vs 7/18, verdict);
//   - no run for this scene → tab renders its inactive state with the reason (nothing simulated).
// See reports/tasklog/141_ux47.md for the code check this content is based on.
import { useEffect, useState } from 'react';
import { get } from './api3';
import { checkLine, driftKey, driftPaths } from './drift';
import { driftCaption, DRIFT_CORRIDOR_LABEL, MAX_HORIZON_H } from './DriftLayer';
import { loadDrift } from '../lib/data';
import type { DriftFile } from '../types';

export interface DriftTabZone {
  region?: string;
  datetime?: string | null;
  detection_status?: string;
}

export interface DriftTabProps {
  /** the open zone's own properties (same object CaseApp already reads region/datetime from for the map layer) */
  zone: DriftTabZone | null;
  /** optional: wire to CaseApp's existing toggleDrift() to also draw the layer on the map — omit to hide the button */
  onShowOnMap?: (() => void) | null;
  mapOn?: boolean;
}

type State = { loading: boolean; path: string | null; drift: DriftFile | null; sum: any; reason: string | null };

// §70 п.B: the old version could hang forever on «Проверяю, есть ли опубликованный прогноз…» if the manifest or
// drift.json fetch stalled (no per-request timeout) — under load that showed as an endless spinner on the
// published site. Hard 5 s backstop: whatever is still in flight when the timer fires, the tab reports «нет
// расчёта: …» instead of spinning — it never waits longer than this for a real result.
const TIMEOUT_MS = 5000;

function useDriftInfo(zone: DriftTabZone | null): State {
  const [state, setState] = useState<State>({ loading: true, path: null, drift: null, sum: null, reason: null });
  useEffect(() => {
    let alive = true;
    let settled = false;
    setState({ loading: true, path: null, drift: null, sum: null, reason: null });
    if (!zone) return;
    const done = (next: Omit<State, 'loading'>) => {
      if (settled || !alive) return;
      settled = true;
      clearTimeout(timer);
      setState({ loading: false, ...next });
    };
    const timer = setTimeout(() => done({ path: null, drift: null, sum: null, reason: 'проверка не ответила за 5 с' }), TIMEOUT_MS);
    (async () => {
      let paths;
      try {
        paths = await driftPaths();
      } catch {
        return done({ path: null, drift: null, sum: null, reason: 'не удалось проверить наличие прогноза' });
      }
      const path = paths.get(driftKey(zone)) ?? null;
      if (!path) return done({ path: null, drift: null, sum: null, reason: 'для этой сцены нет опубликованного прогноза дрейфа (OpenDrift не запускался на дату снимка)' });
      const [drift, sum] = await Promise.all([loadDrift(path).catch(() => null), get<any>('/api/drift_check').catch(() => null)]);
      if (!drift) return done({ path, drift: null, sum, reason: 'файл прогноза не загрузился' });
      done({ path, drift, sum, reason: null });
    })();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [zone?.region, zone?.datetime]);
  return state;
}

const shortSrc = (s?: string) => (s ? s.split(/,|\(| global| hourly| 10 m/)[0].trim() || s : '—');

function fmtTime(iso?: string | null): string {
  if (!iso) return '—';
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return iso;
  const d = new Date(t);
  return `${iso.slice(8, 10)}.${iso.slice(5, 7)}.${iso.slice(0, 4)} ${String(d.getUTCHours()).padStart(2, '0')}:${String(d.getUTCMinutes()).padStart(2, '0')} UTC`;
}

export default function DriftTab({ zone, onShowOnMap, mapOn }: DriftTabProps) {
  const { loading, path, drift, sum, reason } = useDriftInfo(zone);

  if (!zone) return null;

  // §70 п.B: ≤ 5 с — see useDriftInfo's TIMEOUT_MS; never spins forever.
  if (loading)
    return (
      <div className="sec sz-drift-tab" data-testid="drift-tab-loading">
        <div className="c-line tiny faint">Проверяю, есть ли опубликованный прогноз дрейфа для этой сцены… (до 5 с)</div>
      </div>
    );

  if (!path)
    return (
      <div className="sec sz-drift-tab" data-testid="drift-tab-inactive">
        <div className="c-line">
          <b>нет расчёта:</b> {reason ?? 'для этой сцены нет опубликованного прогноза дрейфа'}
        </div>
        <div className="c-line tiny faint">Ничего не имитируем — расчёт течений и ветра (OpenDrift) появится здесь только после реального запуска на дату снимка.</div>
      </div>
    );

  const f = drift?.forcing ?? {};
  const isReal = !!(f.currents || f.wind);
  const n = sum?.n_pairs, k = sum?.k_hit, z = sum?.k_hit_baseline, sh = sum?.k_hit_shift;

  return (
    <div className="sec sz-drift-tab" data-testid="drift-tab">
      <div className="c-line" data-testid="drift-tab-caption">
        {driftCaption(f)} Горизонт ≤ {MAX_HORIZON_H} ч.
      </div>
      <div className="c-line tiny faint" data-testid="drift-tab-corridor">
        {DRIFT_CORRIDOR_LABEL}
      </div>
      <div className="c-line tiny" data-testid="drift-tab-status">
        Статус: <b>исследовательская оценка</b>
        {onShowOnMap && (
          <button className={`btn sm sz-drift-btn ${mapOn ? 'on' : ''}`} onClick={onShowOnMap} data-testid="sz-drift-btn" aria-pressed={!!mapOn} style={{ marginLeft: 8 }}>
            {mapOn ? 'Скрыть на карте' : 'Показать на карте'}
          </button>
        )}
      </div>

      <div className="c-line tiny faint" data-testid="drift-tab-source">
        <b>Источник и время:</b> течения — {shortSrc(f.currents)}; ветер — {shortSrc(f.wind)}; момент съёмки — {fmtTime(drift?.start_time)}.
        {!isReal && ' Реального форсинга в этом файле нет — показываем как иллюстрацию, не прогноз.'}
      </div>

      <div className="c-line tiny faint" data-testid="drift-tab-assumptions">
        <b>Допущения:</b> модель {f.model ?? '—'}, ветровой коэффициент {f.wind_drift_factor ?? '—'}, горизонтальная диффузия{' '}
        {(drift as any)?.forcing?.horizontal_diffusivity_m2s ?? (drift as any)?.horizontal_diffusivity_m2s ?? '—'} м²/с, стоксов дрейф{' '}
        {(drift as any)?.forcing?.stokes_drift ?? 'не учтён'}. {drift?.note ?? ''}
      </div>

      <div className="c-line tiny faint" data-testid="drift-tab-check">
        <b>Неопределённость и проверка:</b>{' '}
        {typeof n === 'number' && typeof k === 'number' && typeof z === 'number'
          ? `по ${n} парам «снимок → следующий снимок» прогноз попал в ${k}/${n}, «нулевой дрейф» (без адвекции) — тоже в ${z}/${n}${
              typeof sh === 'number' ? `, облако без направления, сдвинутое назад — в ${sh}/${n}` : ''
            }; направление на горизонте 1–5 суток не подтверждено этой проверкой (reports/drift_check.md).`
          : checkLine(sum)}
      </div>
      <div className="c-line tiny faint" data-testid="drift-tab-verdict">
        Показываем прогноз как сценарий модели на реальных полях течений/ветра — не как предсказание, куда унесёт мусор, и не как число предметов.
      </div>
    </div>
  );
}
