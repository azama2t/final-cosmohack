import type { Manifest } from '../types';
import type { CheckPair } from '../App';
import { shortName } from '../lib/data';
import { fmtDateShort, fmtNum } from '../lib/style';
import Info from './Info';

export interface CheckProps {
  summary: any | null | undefined;
  pair: CheckPair | null;
  data: any | null;
  onPair: (p: CheckPair) => void;
  onClose: () => void;
}

export default function CheckPanel(p: CheckProps & { manifest: Manifest }) {
  const s = p.summary;
  const name = (id: string) => shortName(p.manifest.regions.find((r) => r.id === id)?.name ?? id);
  if (s === undefined) return <section className="sec note">Загрузка…</section>;
  if (!s) return <section className="sec note">Нет данных проверки (эндпоинт /api/drift_check не ответил).</section>;
  const pairs: CheckPair[] = s.pairs ?? [];
  const n = s.n_pairs ?? pairs.length;
  const k = s.k_hit ?? pairs.filter((x) => x.pair_hit).length;
  const nAll: number = s.n_pairs_all ?? pairs.length;
  const st = p.data?.stats;
  if (!pairs.length)
    return (
      <section className="sec" data-testid="check-summary">
        <p className="note" data-testid="check-none">
          В этом наборе данных пар для проверки нет.
        </p>
      </section>
    );
  return (
    <>
      <section className="sec" data-testid="check-summary">
        {n < 3 ? (
          <div className="warnline" style={{ marginTop: 12 }} data-testid="check-few">
            Пар меньше трёх — числа не показываем: по такой выборке вывод делать нельзя.
          </div>
        ) : (
          <>
            <p className="big-line" data-testid="check-verdict">
              Прогноз {k}/{n} {(s.k_hit_baseline ?? -1) >= k ? (s.k_hit_baseline === k ? '=' : '<') : '>'} «на месте» {s.k_hit_baseline ?? '—'}/{n}
              <Info label="Как устроен эксперимент" testid="info-check" align="right">
                Для пар «снимок → следующий снимок того же района через 1–5 дней» смотрим, лежат ли находки второго снимка внутри облака частиц
                прогноза ({s.contour_pct ?? 90}%-й контур), и сравниваем с базовой линией «материал остался на месте». {n} пар, с находками на
                втором снимке {s.n_pairs_with_det2 ?? '—'}. {(s.k_hit_baseline ?? -1) >= k ? 'Прогноз не лучше базовой линии.' : 'Прогноз немного лучше базовой линии, но выборка мала.'}{' '}
                {s.note ? `${s.note[0].toUpperCase()}${s.note.slice(1)}.` : ''} Демонстрационный прогноз, не валидирован; это не оценка точности.
                {s.criterion ? ` Критерий (записан до подсчёта): ${s.criterion}` : ''}
              </Info>
            </p>
          </>
        )}
      </section>

      <section className="sec">
        <div className="sec-h">
          <h3>Пары снимков</h3>
          <span className="aside">в облаке?</span>
        </div>
        {nAll > pairs.length && (
          <p className="note" style={{ marginBottom: 8 }} data-testid="check-scope">
            Показаны пары районов этого набора данных: {pairs.length} из {nAll}.
          </p>
        )}
        <div className="list" data-testid="check-pairs">
          {pairs.map((x, i) => {
            const on = p.pair?.url === x.url;
            return (
              <button key={x.url} className={`li ${on ? 'on' : ''}`} onClick={() => p.onPair(x)} data-testid={`check-pair-${i}`} style={{ gridTemplateColumns: '1fr auto' }}>
                <span className="l-main">
                  <span>{name(x.region)}</span>
                  <span className="l-sub" style={{ display: 'block' }}>
                    {fmtDateShort(x.date1)} → {fmtDateShort(x.date2)} · {Math.round(x.dt_h)} ч
                  </span>
                </span>
                <span className="l-val">
                  {x.n_det2 === 0 ? (
                    <span className="faint">—</span>
                  ) : x.pair_hit ? (
                    <span className="status s-detected">
                      да, {x.hits} из {x.n_det2}
                    </span>
                  ) : (
                    <span className="status s-false">нет, 0 из {x.n_det2}</span>
                  )}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {p.pair && (
        <section className="sec" data-testid="check-pair-detail">
          <div className="sec-h">
            <h3>{name(p.pair.region)}</h3>
            <span className="aside">{p.data ? '' : 'загрузка…'}</span>
          </div>
          {st && (
            <dl className="rows">
              <dt>В облаке прогноза</dt>
              <dd>
                {st.hits} из {st.n_det2} <span className="faint">(«на месте» — {st.hits_baseline})</span>
              </dd>
              <dt>
                Смещение частиц
                <Info label="Детали пары" align="left">
                  Площадь контура {fmtNum(st.contour_area_km2, 1)} км², случайно ожидалось {fmtNum(st.expected_random_hits, 1)} попаданий. На карте: точки —
                  частицы прогноза на момент второго снимка, контур — {p.data?.contour_pct ?? 90}% облака; кружки — находки второго снимка (заполненный —
                  внутри облака).
                </Info>
              </dt>
              <dd>{fmtNum(st.mean_displacement_km, 1)} км</dd>
            </dl>
          )}
          {p.pair.figure_url && (
            <a className="link small" href={p.pair.figure_url} target="_blank" rel="noreferrer" data-testid="check-figure">
              Картинка пары (PNG)
            </a>
          )}
        </section>
      )}
    </>
  );
}
