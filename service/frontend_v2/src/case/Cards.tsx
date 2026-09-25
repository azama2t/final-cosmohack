// Right-panel cards: a zone (model estimate) and an observation (field measurement).
// Concentration always with unit + size profile + interval; null → «Концентрация недоступна» + why (1 line + «i»);
// field_estimate separately («оценка по полевым данным, не по снимку»); zone area separately from concentration.
import { useEffect, useState, type ReactNode } from 'react';
import Info from '../components/Info';
import { get, API_BASE, ApiErr, type Feat, type Meta, type ObsProps, type Pair, type Scene, type ZoneDetail, type ZoneProps } from './api3';
import { plural, color, dateRu, dateTimeRu, driftTitle, driftTxt, dtTitle, dtTxt, eventRu, flagRu, label, missionShort, num, pairDecision, pairReasons, pct, poissonCI, profileRu, reasonRu, scopeRu, sourceShort, unitRu, zoneFlagRu } from './fmt';

function useFetch<T>(fn: (() => Promise<T>) | null, deps: unknown[]): { data: T | null; err: string | null; loading: boolean } {
  const [s, setS] = useState<{ data: T | null; err: string | null; loading: boolean }>({ data: null, err: null, loading: !!fn });
  useEffect(() => {
    if (!fn) return setS({ data: null, err: null, loading: false });
    let alive = true;
    setS((x) => ({ ...x, loading: true, err: null }));
    fn().then(
      (d) => alive && setS({ data: d, err: null, loading: false }),
      (e) => alive && setS({ data: null, err: e instanceof ApiErr ? e.message : String(e), loading: false }),
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return s;
}

export function Chip({ c, children, testid, hollow }: { c: string; children: ReactNode; testid?: string; hollow?: boolean }) {
  return (
    <span className={`c-chip ${hollow ? 'hollow' : ''}`} data-testid={testid}>
      <i style={hollow ? { borderColor: c } : { background: c }} />
      {children}
    </span>
  );
}

function Head({ kicker, title, sub, onClose, kind }: { kicker: string; title: string; sub?: string; onClose: () => void; kind: 'obs' | 'zone' }) {
  return (
    <div className="rp-head">
      <div className="rp-titles">
        <div className="rp-kicker">
          <span className={`c-kind ${kind}`} aria-hidden />
          {kicker}
        </div>
        <div className="rp-title" data-testid="card-title">
          {title}
        </div>
        {sub && <div className="rp-sub">{sub}</div>}
      </div>
      <button className="icon-btn" onClick={onClose} aria-label="Закрыть" data-testid="card-close">
        ✕
      </button>
    </div>
  );
}

/** pairs observation ↔ scene: dt, drift shift, decision and why */
function PairList({ meta, pairs, activePair, onPair, testid }: { meta: Meta; pairs: Pair[]; activePair: string | null; onPair: (p: Pair) => void; testid: string }) {
  const withScene = pairs.filter((p) => p.scene_id);
  const noScene = pairs.length - withScene.length;
  const rows = [...withScene].sort((a, b) => (b.quality_decision ? 1 : 0) - (a.quality_decision ? 1 : 0) || Math.abs(a.dt_hours ?? 1e9) - Math.abs(b.dt_hours ?? 1e9));
  return (
    <div data-testid={testid}>
      {!rows.length && <div className="note">Снимков-кандидатов нет{noScene ? ` (${noScene} окон без снимка)` : ''}</div>}
      {rows.length > 0 && (
        <table className="c-ptable">
          <thead>
            <tr>
              <th>Снимок</th>
              <th className="r">Δt, ч</th>
              <th className="r" title="смещение мусора за Δt / допуск">Дрейф / допуск, км</th>
              <th>Решение</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 12).map((p) => (
              <tr key={p.pair_id} className={`${activePair === p.pair_id ? 'on' : ''}`} onClick={() => onPair(p)} data-testid="card-pair-row">
                <td>
                  {missionShort(p.mission)} · {dateRu(p.scene_datetime)}
                </td>
                <td className="r" title={dtTitle(p)}>
                  {dtTxt(p)}
                </td>
                <td className="r" title={driftTitle(p)}>
                  {driftTxt(p)}
                </td>
                <td>
                  <span className={`c-dec ${p.status}`}>{pairDecision(p)}</span>
                  <div className="c-why" title={pairReasons(meta, p).join('; ')}>
                    {pairReasons(meta, p).join('; ')}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {rows.length > 12 && <div className="note">ещё {rows.length - 12} в реестре пар</div>}
      {rows.length > 0 && noScene > 0 && <div className="note">+ {noScene} окон без снимка</div>}
    </div>
  );
}

function Conc({ meta, v, testid }: { meta: Meta; v: ZoneProps['concentration']; testid: string }) {
  if (!v || v.value === null) return null;
  return (
    <div data-testid={testid}>
      <div className="c-big">
        {num(v.value)} <small>{unitRu(v.unit)}</small>
      </div>
      <div className="c-line">
        {v.lo !== null || v.hi !== null ? `интервал ${num(v.lo)}–${num(v.hi)}${v.interval ? ` (${v.interval})` : ''}` : 'интервал не рассчитан'}
      </div>
      <div className="c-line">
        {profileRu(meta, v.measurement_profile)}
        {v.target_scope ? ` · ${scopeRu(meta, v.target_scope)}` : ''}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ zone
export function ZoneCard({
  meta,
  zone,
  scenes,
  activePair,
  detector,
  detail,
  onClose,
  onObs,
  onPair,
}: {
  meta: Meta;
  detector: string | null;
  detail: ZoneDetail | null;
  zone: Feat<ZoneProps>;
  scenes: Scene[];
  activePair: string | null;
  onClose: () => void;
  onObs: (id: string) => void;
  onPair: (p: Pair) => void;
}) {
  const p = zone.properties;
  const pairs = useFetch<{ pairs: Pair[] }>(p.scene_id ? () => get(`/api/v3/pairs`, { scene_id: p.scene_id }) : null, [p.scene_id]);
  const linked = new Set(p.support?.linked_sample_ids ?? []);
  const zp = (pairs.data?.pairs ?? []).filter((x) => linked.has(x.sample_id));
  // one pair per event: the representative sample (same scene, same geometry) — others differ only by scope
  const fieldId = p.support?.field_sample_id ?? null;
  const zpShown = fieldId ? zp.filter((x) => x.sample_id === fieldId) : zp;
  const scene = scenes.find((s) => s.scene_id === p.scene_id) ?? null;
  const d = detail;
  const link = pairLink(meta, p);
  const sp = suspicious(p, d);
  const conc = p.concentration;
  const fe = p.field_estimate;

  return (
    <div className="right-inner" data-testid="zone-card">
      <Head kind="zone" kicker="Снимок-кандидат · полоса обследования" title={eventRu(p.event_id ?? zone.id)} sub={`${p.mission ?? '—'} · ${dateTimeRu(p.datetime)}`} onClose={onClose} />
      <div className="rp-body">
        <div className="sec" data-testid="zone-link">
          <div className={`c-link-t ${link.ok ? 'ok' : ''}`} data-testid="zone-link-title">
            {link.title}
          </div>
          {link.why && (
            <div className="c-line c-with-i" data-testid="zone-link-why">
              <span>{link.why}</span>
              {p.status_reason && (
                <Info label="Полный текст" align="right">
                  {p.status_reason}
                </Info>
              )}
            </div>
          )}
          <div className="c-sp" data-testid="zone-suspicious">
            <span className="c-kind px" aria-hidden />
            <span>{sp}</span>
          </div>
          {link.ok && (
            <div className="c-status-row">
              <Chip c={color(meta.detection_statuses, p.detection_status)} testid="zone-det-status">
                {label(meta.detection_statuses, p.detection_status)}
              </Chip>
            </div>
          )}
          <span hidden data-testid="zone-conc-status">
            {label(meta.concentration_statuses, p.concentration_status)}
          </span>
        </div>

        <div className="sec" data-testid="zone-conc">
          <div className="sec-h">
            <h3>Концентрация по снимку</h3>
          </div>
          {conc && conc.value !== null ? (
            <Conc meta={meta} v={conc} testid="zone-conc-value" />
          ) : (
            <div className="c-na" data-testid="zone-conc-na">
              <div className="c-na-t">
                Концентрация недоступна
                <Info label="Почему" testid="zone-conc-why" align="right">
                  {p.concentration_reason ?? 'Спутниковая оценка не выдаётся.'}
                  {d?.explain?.length ? (
                    <>
                      <br />
                      {d.explain.map((x, i) => (
                        <span key={i} className="c-explain">
                          · {x}
                        </span>
                      ))}
                    </>
                  ) : null}
                </Info>
              </div>
              <div className="c-line">{firstLine(p.concentration_reason) ?? 'перенос «снимок → шт./км²» не подтверждён'}</div>
            </div>
          )}
        </div>

        <div className="sec" data-testid="zone-field-estimate">
          <div className="sec-h">
            <h3>Оценка по полевым данным, не по снимку</h3>
            <Info label="Что это" align="right">
              Оценка по полевым данным для района и профиля (по итогам отложенного test — медиана профиля). Не измерение и не результат снимка.
            </Info>
          </div>
          {fe && fe.value !== null ? (
            <>
              <div className="c-big">
                {num(fe.value)} <small>{unitRu(fe.unit)}</small>
              </div>
              <div className="c-line">
                {fe.lo !== null || fe.hi !== null ? `интервал ${num(fe.lo)}–${num(fe.hi)}` : 'интервал не рассчитан'} · {profileRu(meta, fe.measurement_profile)}
                {fe.model ? ` · ${fe.model}` : ''}
              </div>
            </>
          ) : (
            <div className="c-line">нет оценки для профиля этой полосы</div>
          )}
        </div>

        <div className="sec">
          <dl className="rows">
            <dt>
              Площадь полосы{' '}
              <Info label="Площадь">
                {p.area_basis ?? 'Площадь полигона полосы обследования.'}
                {p.strip_area_raster_km2 !== null && p.strip_area_raster_km2 !== undefined ? ` Растровая площадь полосы: ${num(p.strip_area_raster_km2, 2)} км².` : ''} Это не
                концентрация и не площадь пятна.
              </Info>
            </dt>
            <dd data-testid="zone-area">
              {num(p.area_km2, 2)} км²
            </dd>
            <dt>Подозрительные пиксели, площадь</dt>
            <dd>{p.detected_area_m2 === null ? '—' : `${num(p.detected_area_m2, 0)} м²`}</dd>
            <dt>Вероятность детектора, ср. / макс.</dt>
            <dd>
              {num(p.detector?.prob_mean ?? null, 2)} / {num(p.detector?.prob_max ?? null, 2)}
            </dd>
            <dt>Пригодная вода в полосе</dt>
            <dd>{pct(p.quality?.valid_fraction)}</dd>
            <dt>Облака в полосе</dt>
            <dd>{pct(p.quality?.cloud_fraction)}</dd>
            {p.quality?.flags?.length ? (
              <>
                <dt>Флаги</dt>
                <dd>{p.quality.flags.map(zoneFlagRu).join(', ')}</dd>
              </>
            ) : null}
          </dl>
        </div>

        {p.support && (
          <div className="sec" data-testid="zone-support">
            <div className="sec-h">
              <h3>Полевое измерение в полосе</h3>
              <span className="aside">{plural(p.support.n_linked_samples, 'запись', 'записи', 'записей')}</span>
            </div>
            {p.support.field_sample_id ? (
              <button className="c-link-row" onClick={() => onObs(p.support!.field_sample_id!)} data-testid="zone-field-obs">
                <span className="c-kind obs" aria-hidden />
                <span>
                  <b>{num(p.support.field_items_km2 ?? null)} шт./км²</b> · измерение · {scopeRu(meta, p.support.field_target_scope)}
                </span>
                <span className="faint">{p.support.field_sample_id} →</span>
              </button>
            ) : (
              <div className="c-line">нет</div>
            )}
          </div>
        )}

        <div className="sec">
          <div className="sec-h">
            <h3>Пары наблюдение ↔ снимок</h3>
            <Info label="Пары" align="right">
              Δt — снимок минус наблюдение. Дрейф — оценка смещения мусора за Δt (если рассчитан). Решение и причина — из реестра пар.
            </Info>
          </div>
          {pairs.err ? <div className="c-err">{pairs.err}</div> : pairs.loading ? <div className="note">…</div> : <PairList meta={meta} pairs={zpShown} activePair={activePair} onPair={onPair} testid="zone-pairs" />}
        </div>

        {(d?.crop_url || scene?.quality_url || d?.prob_crop_url) && (
          <div className="sec">
            <div className="sec-h">
              <h3>Снимок, маски качества и детектора</h3>
            </div>
            <div className="c-crops">
              {!d?.crop_url && scene?.quality_url && (
                <figure>
                  <div className="c-noimg">превью {p.mission?.startsWith('Landsat') ? 'Landsat' : ''} нет</div>
                  <figcaption>снимок</figcaption>
                </figure>
              )}
              {d?.crop_url && (
                <figure>
                  <img src={API_BASE + d.crop_url} alt="Вырезка снимка" loading="lazy" />
                  <figcaption>снимок</figcaption>
                </figure>
              )}
              {scene?.quality_url && (
                <figure>
                  <img src={API_BASE + scene.quality_url} alt="Маска качества" loading="lazy" className="q" />
                  <figcaption>маска качества</figcaption>
                </figure>
              )}
              {d?.prob_crop_url && (
                <figure>
                  <img src={API_BASE + d.prob_crop_url} alt="Маска детектора" loading="lazy" />
                  <figcaption>маска детектора</figcaption>
                </figure>
              )}
            </div>
          </div>
        )}

        <div className="sec c-src">
          <div className="note">
            Снимок: {p.mission ?? '—'} · {p.scene_id ?? '—'}
            {scene?.collection ? ` · ${scene.source}/${scene.collection}` : ''}
          </div>
          <div className="note" data-testid="zone-detector-class">
            Класс детектора Marine Debris = любой плавающий мусор, пластик не выделяется
          </div>
          {detector && <div className="note">Детектор: {detector} · метрики — вкладка «Метрики»</div>}
        </div>
      </div>
    </div>
  );
}


function firstLine(s: string | null | undefined): string | null {
  if (!s) return null;
  const t = s.split(';')[0].trim();
  return t.length > 80 ? t.slice(0, 78) + '…' : t;
}

// ------------------------------------------------------------------ observation
export function parseCsv(t: string): string[][] {
  const out: string[][] = [];
  let row: string[] = [];
  let f = '';
  let q = false;
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (q) {
      if (c === '"') {
        if (t[i + 1] === '"') {
          f += '"';
          i++;
        } else q = false;
      } else f += c;
    } else if (c === '"') q = true;
    else if (c === ',') {
      row.push(f);
      f = '';
    } else if (c === '\n' || c === '\r') {
      if (c === '\r' && t[i + 1] === '\n') i++;
      row.push(f);
      out.push(row);
      row = [];
      f = '';
    } else f += c;
  }
  if (f || row.length) {
    row.push(f);
    out.push(row);
  }
  return out;
}

export function ObsCard({
  meta,
  id,
  sameEvent,
  activePair,
  onClose,
  onObs,
  onPair,
}: {
  meta: Meta;
  id: string;
  sameEvent: Feat<ObsProps>[];
  activePair: string | null;
  onClose: () => void;
  onObs: (id: string) => void;
  onPair: (p: Pair) => void;
}) {
  const r = useFetch<Feat<ObsProps> & { pairs: Pair[] }>(() => get(`/api/v3/observations/${encodeURIComponent(id)}`), [id]);
  if (r.err)
    return (
      <div className="right-inner" data-testid="obs-card">
        <Head kind="obs" kicker="Измерение" title={id} onClose={onClose} />
        <div className="sec c-err">{r.err}</div>
      </div>
    );
  if (!r.data)
    return (
      <div className="right-inner" data-testid="obs-card">
        <Head kind="obs" kicker="Измерение" title={id} onClose={onClose} />
        <div className="sec note">…</div>
      </div>
    );
  const p = r.data.properties;
  const v = p.concentration_items_km2;
  const t = p.transect;
  const others = sameEvent.filter((f) => f.id !== id);
  return (
    <div className="right-inner" data-testid="obs-card">
      <Head
        kind="obs"
        kicker="Измерение · полевое наблюдение"
        title={sourceShort(meta, p.source_id)}
        sub={`${dateRu(p.date_utc)}${p.time_start_utc ? ` · ${p.time_start_utc.slice(0, 5)}${p.time_end_utc ? '–' + p.time_end_utc.slice(0, 5) : ''} UTC` : ''} · ${eventRu(p.event_id)}`}
        onClose={onClose}
      />
      <div className="rp-body">
        <div className="sec" data-testid="obs-conc">
          {v === null ? (
            <>
              <div className="c-na-t">Плотности нет</div>
              <div className="c-line">{label(meta.record_types, p.record_type).toLowerCase()} — не плотность на площади</div>
            </>
          ) : (
            <>
              <div className="c-big">
                {num(v)} <small>шт./км²</small>
              </div>
              {v === 0 && <div className="c-line">измеренный ноль{p.zero_scope ? ' — только для указанной категории' : ''}</div>}
              <div className="c-line" data-testid="obs-interval">
                {obsInterval(p)}
              </div>
            </>
          )}
          <div className="c-line" data-testid="obs-profile">
            {profileRu(meta, p.measurement_profile)} · {scopeRu(meta, p.target_scope)}
          </div>
        </div>
        <ModelEstimate meta={meta} p={p} />
        <div className="sec">
          <dl className="rows">
            <dt>
              Тип записи{' '}
              <Info label="Описание записи">
                {[p.litter_item_type, p.material, p.notes].filter(Boolean).join(' · ') || '—'}
              </Info>
            </dt>
            <dd>{label(meta.record_types, p.record_type)}</dd>
            <dt>Обследованная площадь</dt>
            <dd>{p.sampled_area_km2 === null ? '—' : `${num(p.sampled_area_km2, 3)} км²`}</dd>
            {t && t.length_km !== null && (
              <>
                <dt>Трансекта</dt>
                <dd>
                  {num(t.length_km, 1)} км × {num(t.width_m, 0)} м
                </dd>
              </>
            )}
            {p.quality_flags?.length ? (
              <>
                <dt>
                  Флаги качества{' '}
                  <Info label="Коды флагов">{p.quality_flags.join(', ')}</Info>
                </dt>
                <dd className="c-wrap" data-testid="obs-flags">
                  {p.quality_flags.map(flagRu).join('; ')}
                </dd>
              </>
            ) : null}
          </dl>
        </div>

        <div className="sec">
          <div className="sec-h">
            <h3>Пары наблюдение ↔ снимок</h3>
            <Info label="Пары" align="right">
              Δt — снимок минус наблюдение. Дрейф — оценка смещения мусора за Δt (если рассчитан). Решение и причина — из реестра пар.
            </Info>
          </div>
          <PairList meta={meta} pairs={r.data.pairs ?? []} activePair={activePair} onPair={onPair} testid="obs-pairs" />
        </div>

        {others.length > 0 && (
          <div className="sec">
            <div className="sec-h">
              <h3>Другие записи этой трансекты</h3>
              <span className="aside">{others.length}</span>
            </div>
            {others.slice(0, 8).map((f) => (
              <button key={f.id} className="c-link-row" onClick={() => onObs(f.id)}>
                <span className="c-kind obs" aria-hidden />
                <span>
                  {f.properties.concentration_items_km2 === null ? scopeRu(meta, f.properties.target_scope) : `${num(f.properties.concentration_items_km2)} шт./км² · ${scopeRu(meta, f.properties.target_scope)}`}
                </span>
                <span className="faint">{f.id}</span>
              </button>
            ))}
          </div>
        )}

        <div className="sec c-src" data-testid="obs-source">
          <div className="note">
            Источник: {p.source_short || sourceShort(meta, p.source_id)}
            {p.source_doi && (
              <>
                {' · '}
                <a href={`https://doi.org/${p.source_doi}`} target="_blank" rel="noreferrer">
                  doi:{p.source_doi}
                </a>
              </>
            )}
          </div>
          <div className="note" data-testid="obs-license">
            Лицензия: {p.source_license || '—'}
          </div>
        </div>
      </div>
    </div>
  );
}

/** 95 % interval of a field density: from the API (ci95_lo/hi), else exact Poisson from N (numerator) and the area */
function obsInterval(p: ObsProps): string {
  const n = p.density_numerator_items ?? p.items_count ?? null;
  const nTxt = n !== null ? `, Пуассон по N=${num(n, 0)}` : ', Пуассон';
  if (p.ci95_lo !== null && p.ci95_lo !== undefined && p.ci95_hi !== null && p.ci95_hi !== undefined)
    return `95 % интервал [${num(p.ci95_lo)}; ${num(p.ci95_hi)}] шт./км²${nTxt}`;
  if (n !== null && p.sampled_area_km2) {
    const [lo, hi] = poissonCI(n, p.sampled_area_km2);
    return `95 % интервал [${num(lo)}; ${num(hi)}] шт./км²${nTxt}`;
  }
  return 'интервал: нет данных N';
}

/** the concentration model's out-of-fold estimate for this observation (a model estimate, not a measurement) */
function ModelEstimate({ meta, p }: { meta: Meta; p: ObsProps }) {
  const m = p.model_estimate;
  if (m === null || m === undefined) return null;
  const o = typeof m === 'number' ? { value: m, lo: null, hi: null, unit: 'items/km2', model: null, measurement_profile: p.measurement_profile } : m;
  if (o.value === null || o.value === undefined) return null;
  return (
    <div className="sec" data-testid="obs-model-estimate">
      <div className="sec-h">
        <h3>
          <span className="c-kind est" aria-hidden /> Оценка модели по полевым данным
        </h3>
        <Info label="Что это" align="right">
          Прогноз основной модели концентрации для этой точки, посчитанный без её участка маршрута (кросс-валидация). Это оценка, не измерение.
          {'note' in o && o.note ? ` ${o.note}` : ''}
        </Info>
      </div>
      <div className="c-big c-est">
        {num(o.value)} <small>{unitRu(o.unit || 'items/km2')}</small>
      </div>
      <div className="c-line">
        {o.lo !== null && o.lo !== undefined && o.hi !== null && o.hi !== undefined ? `интервал [${num(o.lo)}; ${num(o.hi)}] · ` : ''}
        {profileRu(meta, o.measurement_profile ?? p.measurement_profile)}
        {o.model ? ` · ${o.model}` : ''}
      </div>
    </div>
  );
}

/** is the scene ↔ field link confirmed? (L62h: detection_reason; fallback: pair_status / pair_reject_reasons) */
function pairLink(meta: Meta, p: ZoneProps): { ok: boolean; title: string; why: string | null } {
  const ok = p.pair_status === 'accepted';
  if (ok) return { ok, title: 'Связь снимка с полевым измерением подтверждена', why: reasonRu(p.status_reason) };
  const parts: string[] = [];
  for (const r of p.pair_reject_reasons ?? []) {
    if (r === 'DRIFT_TOO_LARGE')
      parts.push(
        p.pair_drift_shift_km !== null && p.pair_drift_shift_km !== undefined
          ? `дрейф ${num(p.pair_drift_shift_km, 1)} км > допуск ${num(p.pair_tolerance_km ?? null, 1)} км`
          : 'дрейф больше допуска',
      );
    else if (r === 'TIME_UNKNOWN') parts.push('время наблюдения неизвестно, ±12 ч');
    else if (r === 'CLOUD') parts.push('облака');
    else if (r === 'GLINT') parts.push('блик');
    else parts.push(label(meta.reject_reasons, r).toLowerCase());
  }
  const why = parts.length ? parts.join(' · ') : reasonRu(p.detection_reason ?? p.status_reason);
  return { ok, title: 'Связь снимка с полевым измерением не подтверждена', why };
}

/** «подозрительные пиксели …»: never «обнаружено» unless the pair is confirmed */
function suspicious(p: ZoneProps, d: ZoneDetail | null): string {
  const s = p.suspicious_pixels;
  const n = s?.n_objects ?? p.detector?.n_objects ?? null;
  const a = s?.area_m2 ?? p.detected_area_m2 ?? null;
  const tail = p.pair_status === 'accepted' ? '' : ', без полевого подтверждения';
  if (n === null && a === null) return `детектор: ${reasonRu(p.status_reason) ?? 'нет результата'}`;
  if (!n && !a) return 'подозрительных пикселей в полосе нет';
  const nOut = d?.detections?.features?.filter((f) => f.properties.in_strip === false).length ?? 0;
  return `подозрительные пиксели на снимке-кандидате${tail}: ${plural(n ?? 0, 'объект', 'объекта', 'объектов')}, ${num(a ?? null, 0)} м²${nOut ? ` (ещё ${nOut} вне полосы)` : ''}`;
}
