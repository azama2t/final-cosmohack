import type { Manifest } from '../types';
import type { CheckPair } from '../App';
import { shortName } from '../lib/data';
import { fmtDateShort, fmtNum } from '../lib/style';

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
        <p className="lead">
          Эксперимент<span className="exp-tag">не валидация</span>
        </p>
        <p className="note" style={{ marginTop: 8 }} data-testid="check-none">
          В этом наборе данных пар для проверки нет.
        </p>
      </section>
    );
  return (
    <>
      <section className="sec" data-testid="check-summary">
        <p className="lead">
          Эксперимент<span className="exp-tag">не валидация</span>
        </p>
        <p style={{ marginTop: 8 }}>
          Для пар «снимок → следующий снимок того же района через 1–5 дней» смотрим, лежат ли находки второго снимка внутри облака частиц
          прогноза ({s.contour_pct ?? 90}%-й контур) — и сравниваем с базовой линией «материал остался на месте».
        </p>
        {n < 3 ? (
          <div className="warnline" style={{ marginTop: 12 }} data-testid="check-few">
            Пар меньше трёх — числа не показываем: по такой выборке вывод делать нельзя.
          </div>
        ) : (
          <>
            <div className="kv three" style={{ marginTop: 16 }}>
              <div>
                <div className="k">Пар снимков</div>
                <div className="v">{n}</div>
                <div className="h">с находками на 2-м: {s.n_pairs_with_det2 ?? '—'}</div>
              </div>
              <div>
                <div className="k">Прогноз дрейфа</div>
                <div className="v">
                  {k}
                  <small>из {n}</small>
                </div>
                <div className="h">пар с находкой в облаке</div>
              </div>
              <div>
                <div className="k">«На месте»</div>
                <div className="v">
                  {s.k_hit_baseline ?? '—'}
                  <small>из {n}</small>
                </div>
                <div className="h">базовая линия</div>
              </div>
            </div>
            <p style={{ marginTop: 12 }} data-testid="check-verdict">
              {n} пар: прогноз {k}/{n}, базовая линия «на месте» {s.k_hit_baseline ?? '—'}/{n}.{' '}
              {(s.k_hit_baseline ?? -1) >= k ? 'Прогноз не лучше базовой линии.' : 'Прогноз немного лучше базовой линии, но выборка мала.'}
            </p>
          </>
        )}
        <p className="note" style={{ marginTop: 8 }}>
          {s.note ? `${s.note[0].toUpperCase()}${s.note.slice(1)}.` : ''} Демонстрационный прогноз, не валидирован; это не оценка точности.
        </p>
      </section>

      <section className="sec">
        <div className="sec-h">
          <h3>Пары «снимок → следующий снимок»</h3>
          <span className="aside">1–5 дней</span>
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
                    {fmtDateShort(x.date1)} → {fmtDateShort(x.date2)} · {Math.round(x.dt_h)} ч · находок на 2-м {x.n_det2}
                  </span>
                </span>
                <span className="l-val">
                  {x.n_det2 === 0 ? (
                    <span className="faint">нет находок</span>
                  ) : x.pair_hit ? (
                    <span className="status s-detected">в облаке {x.hits}</span>
                  ) : (
                    <span className="status s-false">вне облака</span>
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
            <h3>
              {name(p.pair.region)} · {fmtDateShort(p.pair.date1)} → {fmtDateShort(p.pair.date2)}
            </h3>
            <span className="aside">{p.data ? '' : 'загрузка…'}</span>
          </div>
          {st && (
            <dl className="rows">
              <dt>Находок на втором снимке</dt>
              <dd>{st.n_det2}</dd>
              <dt>Внутри облака прогноза</dt>
              <dd>
                {st.hits} <span className="faint">(«на месте» — {st.hits_baseline})</span>
              </dd>
              <dt>Площадь контура</dt>
              <dd>{fmtNum(st.contour_area_km2, 1)} км²</dd>
              <dt>Ожидалось случайно</dt>
              <dd>{fmtNum(st.expected_random_hits, 1)}</dd>
              <dt>Среднее смещение частиц</dt>
              <dd>{fmtNum(st.mean_displacement_km, 1)} км</dd>
            </dl>
          )}
          <p className="note" style={{ marginTop: 8 }}>
            На карте: серые точки — частицы прогноза на момент второго снимка, тонкий контур — {p.data?.contour_pct ?? 90}% облака; кружки — находки
            второго снимка (заполненный — внутри облака). Снимок на подложке — ближайший из набора района.
          </p>
          {p.pair.figure_url && (
            <a className="link small" href={p.pair.figure_url} target="_blank" rel="noreferrer" data-testid="check-figure">
              Картинка пары (PNG)
            </a>
          )}
        </section>
      )}
      <section className="sec">
        <details>
          <summary className="note" style={{ cursor: 'pointer' }}>
            Критерий попадания (записан до подсчёта)
          </summary>
          <p className="note" style={{ marginTop: 6 }}>
            {s.criterion}
          </p>
        </details>
      </section>
    </>
  );
}
