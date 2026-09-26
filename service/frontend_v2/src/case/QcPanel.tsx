// §50 P2-9/10 (L142, Egor's screenshots 12–13): «Качество и статус» = a short summary for the demo
// (status, 2–4 key signals, honest conclusion, next action); the full reference — «Справка / Методика»:
// examples of success and error (moved here from the zone card), detector and concentration metrics — behind «Подробнее».
// Numbers only from GET /api/v3/metrics and /api/v3/defense_examples (saved last answer if the service is down).
import { useState } from 'react';
import DefenseExamples from './DefenseExamples';
import MetricsPanel from './MetricsPanel';
import { QcOffline, useCachedGet } from './QcFallback';
import { num } from './fmt';

const f2 = (v: any) => (typeof v === 'number' ? num(v, 2) : '—');
const ci2 = (a: any) => (Array.isArray(a) && a.length >= 2 ? ` [${num(a[0], 2)}–${num(a[1], 2)}]` : '');
const detName = (n: string) => (/random\s*forest/i.test(n) ? 'RandomForest' : n.split(/[ (]/)[0]);

/** window event: open «Справка / Методика» (used from the zone card) */
export const HELP_EVENT = 'case:open-help';
export function openHelp() {
  window.dispatchEvent(new Event(HELP_EVENT));
}

function Summary({ m, ex, onOpen, onMore }: { m: any | null; ex: any | null; onOpen: (e: { zone_id: string | null; scene_key: string }) => void; onMore: () => void }) {
  const d = m?.detector?.main;
  const b = m?.detector?.baseline;
  const c = m?.concentration;
  const s2 = c?.final_test_summary?.[c?.profile ?? ''] ?? null;
  const ce = m?.control_example;
  const exs: any[] = ex?.examples ?? [];
  const good = exs.find((e) => e.kind === 'success');
  const signals: { k: string; v: JSX.Element }[] = [];
  if (d)
    signals.push({
      k: 'Детектор зон',
      v: (
        <>
          {d.name}, отложенный test MARIDA ({d.n_scenes ?? '—'} сцен): F1 <b>{f2(d.f1 ?? d.test_f1)}</b>
          {ci2(d.ci95_f1)}
          {b ? ` против ${detName(b.name)} ${f2(b.f1 ?? b.test_f1)}` : ''}
        </>
      ),
    });
  if (s2?.main && s2?.baseline)
    signals.push({
      k: 'Концентрация (поле)',
      v: (
        <>
          отложенный test, {s2.n_test} событий: MAE {s2.main.name} {num(s2.main.mae, 1)} против медианы {num(s2.baseline.mae, 1)} шт./км² — {s2.decision}
        </>
      ),
    });
  if (ce && typeof ce.items === 'number')
    signals.push({
      k: 'Контроль расчёта',
      v: (
        <>
          {num(ce.items, 0)} предм. / {num(ce.area_km2, 2)} км² = {num(ce.computed, 1)} шт./км² {ce.computed === ce.expected ? '(совпало с ожидаемым)' : `(ожидалось ${num(ce.expected, 1)})`}
        </>
      ),
    });
  if (exs.length)
    signals.push({
      k: 'Разбор ошибок',
      v: (
        <>
          {exs.length} примеров: {exs.map((e) => e.verdict).join(' · ')}
        </>
      ),
    });
  return (
    <section className="c-qcs" data-testid="qc-summary">
      <div className="c-qcs-st">
        <span className="c-qcs-dot" aria-hidden />
        <b>Статус:</b> зоны на снимках — оценка детектора (проверен на отложенных данных); пластик и количество шт./км² по снимку не подтверждены.
      </div>
      {signals.length > 0 && (
        <ul className="c-qcs-l" data-testid="qc-signals">
          {signals.slice(0, 4).map((x) => (
            <li key={x.k}>
              <span className="c-qcs-k">{x.k}</span>
              <span>{x.v}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="c-qcs-c" data-testid="qc-conclusion">
        <b>Вывод:</b> карте можно доверять как поиску мест для проверки; количественная оценка — по полевым данным (независимая), не по пикселям снимка.
      </div>
      <div className="c-qcs-a">
        <span className="faint">Дальше:</span>
        {good && (
          <button className="btn sm" onClick={() => onOpen(good)} data-testid="qc-next-example">
            Открыть пример «верно» на карте
          </button>
        )}
        <button className="btn sm ghost" onClick={onMore} data-testid="qc-more">
          Подробнее: справка и методика
        </button>
      </div>
    </section>
  );
}

export default function QcPanel({ m, err, onOpen }: { m: any | null; err: string | null; onOpen: (e: { zone_id: string | null; scene_key: string }) => void }) {
  const mq = useCachedGet<any>('/api/v3/metrics', { data: m, err });
  const ex = useCachedGet<any>('/api/v3/defense_examples');
  const [more, setMore] = useState(() => new URLSearchParams(location.search).get('qc') === 'full');
  const off = mq.err && ex.err;
  return (
    <div data-testid="qc-two-layer">
      {off && <QcOffline lastOk={mq.lastOk ?? ex.lastOk} loading={mq.loading || ex.loading} onRetry={mq.retry} testid="qc-offline" />}
      {(mq.data || ex.data) && <Summary m={mq.data} ex={ex.data} onOpen={onOpen} onMore={() => setMore(true)} />}
      {!mq.data && !ex.data && !off && <div className="note c-pad">Загрузка…</div>}
      <details className="c-qcs-more" open={more} onToggle={(e) => setMore((e.target as HTMLDetailsElement).open)} data-testid="qc-details">
        <summary>
          Справка / Методика: примеры удач и ошибок, метрики детектора и концентрации
        </summary>
        <div className="c-qcs-more-b">
          <div className="c-qcs-h">Как выглядит удача и ошибка</div>
          <DefenseExamples onOpen={onOpen} />
          <div className="c-qcs-h">Метрики</div>
          <MetricsPanel m={m} err={err} />
        </div>
      </details>
    </div>
  );
}
