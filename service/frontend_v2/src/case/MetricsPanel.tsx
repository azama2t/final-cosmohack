// «Метрики»: numbers ONLY from GET /api/v3/metrics (no constants here). Rows without data in the API are not shown.
import Info from '../components/Info';
import { num, signed } from './fmt';

const f3 = (v: any) => (typeof v === 'number' ? num(v, 3) : '—');
const f1 = (v: any) => (typeof v === 'number' ? v.toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : '—');
const pctTxt = (v: any) => (typeof v === 'number' ? `${num(v <= 1 ? v * 100 : v, 0)} %` : '—');
const ci = (a: any) => (Array.isArray(a) && a.length >= 2 ? `[${num(a[a.length - 2], 3)}–${num(a[a.length - 1], 3)}]` : '');

function pick(o: any, ...keys: string[]) {
  for (const k of keys) if (o && typeof o[k] === 'number') return o[k];
  return null;
}
/** short column names that match README / deck */
function detName(n: string): string {
  if (/random\s*forest/i.test(n)) return 'RF (MARIDA)';
  if (/ndvi/i.test(n)) return 'окно FDI×NDVI';
  if (/fdi/i.test(n)) return 'один порог FDI';
  if (/lightgbm/i.test(n)) return 'LightGBM';
  return n.split(/[ (]/)[0];
}
const isModel = (o: any) => o && typeof o === 'object' && !Array.isArray(o) && typeof o.name === 'string';

/** detector models: main first, then every other named object (baseline, rf, …) and a `baselines` array */
function detModels(d: any): any[] {
  const out: any[] = [];
  if (isModel(d.main)) out.push(d.main);
  for (const [k, v] of Object.entries(d ?? {})) if (k !== 'main' && isModel(v)) out.push(v);
  if (Array.isArray(d?.baselines)) for (const v of d.baselines) if (isModel(v) && !out.some((x) => x.name === v.name)) out.push(v);
  return out;
}

const DET_ROWS: [string, string[], string, string][] = [
  ['Precision, test', ['test_precision', 'precision_test'], 'test_ci95_precision', 'precision'],
  ['Recall, test', ['test_recall', 'recall_test'], 'test_ci95_recall', 'recall'],
  ['F1, test', ['test_f1', 'f1_test'], 'test_ci95_f1', 'f1'],
  ['IoU, test', ['test_iou', 'iou_test'], 'test_ci95_iou', 'iou'],
];

export default function MetricsPanel({ m, err }: { m: any | null; err: string | null }) {
  if (err) return <div className="c-err c-pad">{err}</div>;
  if (!m) return <div className="note c-pad">Загрузка…</div>;
  const d = m.detector ?? {};
  const models = detModels(d);
  const c = m.concentration ?? {};
  const profiles: [string, any][] = c.profiles && Object.keys(c.profiles).length ? Object.entries(c.profiles) : c.main ? [[c.profile ?? '', { baseline: c.baseline, main: c.main, main_split: c.main?.split }]] : [];
  profiles.sort((a, b) => (a[0] === c.profile ? -1 : b[0] === c.profile ? 1 : 0));
  // test rows first; val rows only for models that have them
  const rows: { k: string; vals: (number | null)[]; ci: string }[] = [];
  for (const [k, keys, ciKey, plain] of DET_ROWS) {
    // a model evaluated on test only may give plain keys (f1, precision …) with split = test
    const vals = models.map((x) => pick(x, ...keys) ?? (x.split === 'test' ? pick(x, plain) : null));
    if (vals.some((v) => v !== null)) rows.push({ k, vals, ci: ci(models[0]?.[ciKey] ?? models[0]?.['ci95_' + plain]) });
  }
  for (const [k, key] of [
    ['F1, val', 'f1'],
    ['IoU, val', 'iou'],
  ] as [string, string][]) {
    const vals = models.map((x) => pick(x, 'val_' + key) ?? (x.split === 'test' ? null : pick(x, key)));
    if (vals.some((v) => v !== null)) rows.push({ k, vals, ci: '' });
  }
  const ce = m.control_example;
  return (
    <div className="c-metrics" data-testid="metrics-panel">
      <div className="sec">
        <div className="sec-h">
          <h3>Детектор · MARIDA</h3>
          <Info label="Сплит" align="right">
            {typeof d.split === 'string' ? d.split : '—'}
            {models
              .filter((x) => x.note)
              .map((x) => (
                <span key={x.name} className="c-explain">
                  · {x.name}: {x.note}
                </span>
              ))}
          </Info>
        </div>
        <table className="c-mtable">
          <thead>
            <tr>
              <th />
              {models.map((x) => (
                <th key={x.name} className="r" title={x.name + (x.setting ? ` · ${x.setting}` : '')}>
                  {detName(x.name)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.k}>
                <td>{r.k}</td>
                {r.vals.map((v, i) => (
                  <td key={i} className="r" data-testid={i === 0 ? `m-det-${r.k}` : undefined}>
                    {i === 0 ? <b>{f3(v)}</b> : f3(v)}
                    {i === 0 && r.ci ? <div className="faint tiny">{r.ci}</div> : null}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <FinalTest ft={c.final_test} status={c.final_test_status} />

      {profiles.map(([pid, pr]) => {
        const b = pr.baseline ?? {};
        const mm = pr.main ?? {};
        const dl = mm.delta_mae_vs_median_ci95;
        const logB = pick(b, 'mae_log1p', 'mae_log');
        const logM = pick(mm, 'mae_log1p', 'mae_log');
        return (
          <div className="sec" key={pid} data-testid="metrics-conc">
            <div className="sec-h">
              <h3>Концентрация, dev CV · {pid.replace(/_/g, ' ')}</h3>
              <Info label="Сплит" align="right">
                Сплит: {pr.main_split ?? mm.split ?? '—'} (кросс-валидация по участкам маршрута), n = {num(mm.n ?? b.n ?? null, 0)}. {typeof c.note === 'string' ? c.note : ''}
              </Info>
            </div>
            <table className="c-mtable">
              <thead>
                <tr>
                  <th />
                  <th className="r">{mm.name ?? 'основная'}</th>
                  <th className="r">{b.name ? 'медиана' : '—'}</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>MAE, шт./км²</td>
                  <td className="r">
                    <b>{f1(mm.mae)}</b>
                  </td>
                  <td className="r">{f1(b.mae)}</td>
                </tr>
                {(typeof b.rmse === 'number' || typeof mm.rmse === 'number') && (
                  <tr>
                    <td>RMSE, шт./км²</td>
                    <td className="r">{f1(mm.rmse)}</td>
                    <td className="r">{f1(b.rmse)}</td>
                  </tr>
                )}
                {(logB !== null || logM !== null) && (
                  <tr>
                    <td>Лог-ошибка (log1p)</td>
                    <td className="r">{f3(logM)}</td>
                    <td className="r">{f3(logB)}</td>
                  </tr>
                )}
                {(typeof mm.coverage === 'number' || typeof b.coverage === 'number') && (
                  <tr>
                    <td>Покрытие интервала, CV</td>
                    <td className="r">≈{pctTxt(mm.coverage)}</td>
                    <td className="r">{typeof b.coverage === 'number' ? `≈${pctTxt(b.coverage)}` : '—'}</td>
                  </tr>
                )}
              </tbody>
            </table>
            {Array.isArray(dl) && dl.length === 3 && (
              <div className="c-line">
                Δ MAE {signed(dl[0], 1)} [{signed(dl[1], 1)}; {signed(dl[2], 1)}] · {mm.significant ? 'значимо' : 'незначимо'}
              </div>
            )}
          </div>
        );
      })}

      {ce && (
        <div className="sec" data-testid="metrics-control">
          <div className="sec-h">
            <h3>Контрольный пример</h3>
          </div>
          <div className="c-line">
            {num(ce.items, 0)} шт. / {num(ce.area_km2, 2)} км² = {num(ce.expected)} шт./км² → вычислено {num(ce.computed)}{' '}
            {ce.expected === ce.computed ? '✓' : '✗'}
          </div>
        </div>
      )}
    </div>
  );
}

/** the held-out concentration test (evaluated once after the freeze): main model vs the profile median */
function FinalTest({ ft, status }: { ft: any; status: any }) {
  if (!ft || !ft.profiles) return null;
  return (
    <>
      {Object.entries(ft.profiles as Record<string, any>).map(([pid, pr]) => {
        const mm = pr.main ?? {};
        const b = pr.baseline_metrics ?? {};
        const mv = pr.main_vs_baseline ?? {};
        const worse = typeof mm.mae === 'number' && typeof b.mae === 'number' && mm.mae >= b.mae;
        const verdict = worse ? 'модель не лучше медианы' : pr.main_better_significant ? 'модель лучше медианы' : 'разница с медианой незначима';
        return (
          <div className="sec" key={pid} data-testid="metrics-final-test">
            <div className="sec-h">
              <h3>Отложенный test · {pid.replace(/_/g, ' ')}</h3>
              <Info label="Протокол" align="right">
                {typeof ft.note === 'string' ? ft.note : ''} {typeof status === 'string' ? `Статус: ${status}.` : ''} n test = {num(pr.n_test ?? mm.n ?? null, 0)}, n dev ={' '}
                {num(pr.n_dev ?? null, 0)}. {typeof pr.note === 'string' ? pr.note : ''}
              </Info>
            </div>
            <table className="c-mtable">
              <thead>
                <tr>
                  <th />
                  <th className="r">{pr.main_model ?? 'модель'}</th>
                  <th className="r">{pr.baseline ?? 'медиана'}</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>MAE, шт./км²</td>
                  <td className="r">
                    <b>{f1(mm.mae)}</b>
                  </td>
                  <td className="r">
                    <b>{f1(b.mae)}</b>
                  </td>
                </tr>
                {(typeof mm.rmse === 'number' || typeof b.rmse === 'number') && (
                  <tr>
                    <td>RMSE, шт./км²</td>
                    <td className="r">{f1(mm.rmse)}</td>
                    <td className="r">{f1(b.rmse)}</td>
                  </tr>
                )}
                {(typeof mm.log1p_mae === 'number' || typeof b.log1p_mae === 'number') && (
                  <tr>
                    <td>Лог-ошибка (log1p)</td>
                    <td className="r">{f3(mm.log1p_mae)}</td>
                    <td className="r">{f3(b.log1p_mae)}</td>
                  </tr>
                )}
                {(typeof mm.coverage90 === 'number' || typeof b.coverage90 === 'number') && (
                  <tr>
                    <td>Покрытие 90 % интервала</td>
                    <td className="r">{pctTxt(mm.coverage90)}</td>
                    <td className="r">{pctTxt(b.coverage90)}</td>
                  </tr>
                )}
              </tbody>
            </table>
            <div className="c-line" data-testid="final-test-verdict">
              <b>{verdict}</b>
              {typeof mv.d_mae === 'number' && Array.isArray(mv.ci95) ? ` · Δ MAE ${signed(mv.d_mae, 1)} [${signed(mv.ci95[0], 1)}; ${signed(mv.ci95[1], 1)}]` : ''}
            </div>
          </div>
        );
      })}
      {!Object.values(ft.profiles as Record<string, any>).some((pr) => pr.main_better_significant) && (
        <div className="sec">
          <div className="c-line">Поэтому оценка по полю на карте — медиана профиля («по полевым данным, не по снимку»).</div>
        </div>
      )}
    </>
  );
}
