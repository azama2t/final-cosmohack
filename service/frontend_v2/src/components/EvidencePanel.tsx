import { useEffect, useMemo, useState } from 'react';
import type { DateEntry, DetProps, Feature, Region } from '../types';
import type { RightProps } from './RightPanel';
import { centroid, featureBBox } from '../map/layers';
import { artifactOf, artifactText } from '../lib/artifacts';
import { apiGet, apiPost, apiUrl } from '../lib/api';
import { isFlagged } from '../lib/data';
import { fmtArea, fmtDate, fmtNum, fmtPct, fmtPermille, fmtProb, fmtTs, modelLabel, modelTitle } from '../lib/style';
import { sceneTime, sensorOf, STATUS_RU } from './RegionPanel';
import Info from './Info';

type Props = RightProps & { feature: Feature<DetProps>; region: Region; dateEntry: DateEntry };
type Bands = 'rgb' | 'false' | 'swir';

const M_LAT = 111320;

/**
 * Evidence card (INBOX §4, main feature): large source crop of the Sentinel-2 image, capture date/time, coordinates,
 * both models and their agreement, cloud / quality, alternative explanations and «Отправить на проверку».
 * Wording: «признаки плавающего материала, приоритет проверки» — never «пластик».
 */
export default function EvidencePanel(p: Props) {
  const f = p.feature;
  const pr = f.properties;
  const art = artifactOf(pr);
  const [lon, lat] = centroid(f);
  const mLon = M_LAT * Math.cos((lat * Math.PI) / 180);
  const bb = featureBBox(f);
  const extM = Math.max((bb[2] - bb[0]) * mLon, (bb[3] - bb[1]) * M_LAT);
  const sizeM = Math.round(Math.max(extM * 1.8, 600));
  const elong = Math.max((bb[2] - bb[0]) * mLon, (bb[3] - bb[1]) * M_LAT) / Math.max(10, Math.min((bb[2] - bb[0]) * mLon, (bb[3] - bb[1]) * M_LAT));
  const [bands, setBands] = useState<Bands>('rgb');
  const [bad, setBad] = useState<Set<Bands>>(new Set());
  const [cell, setCell] = useState<{ id: string; obs: number | null; idx: number | null } | null>(null);

  useEffect(() => {
    let alive = true;
    import('h3-js').then(({ latLngToCell }) => {
      if (!alive) return;
      const id = latLngToCell(lat, lon, 8);
      const c = p.h3?.features.find((x) => x.properties.h3 === id)?.properties;
      setCell({ id, obs: c?.observed_frac ?? null, idx: c?.share_permille ?? null });
    });
    return () => {
      alive = false;
    };
  }, [p.h3, lat, lon]);

  const cropSrc = apiUrl('/api/crop', {
    region: pr.region,
    date: pr.date,
    lon: lon.toFixed(6),
    lat: lat.toFixed(6),
    size_m: sizeM,
    highlight: 0,
    bands,
    px: 720,
  });
  const cropApi = p.paths.has('/api/crop');
  // our own outline in the signal colour (server contours are drawn off: highlight=0)
  const outline = useMemo(() => {
    const g = f.geometry;
    const polys: number[][][][] = g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates];
    const half = sizeM / 2;
    const x = (q: number[]) => (((q[0] - lon) * mLon + half) / sizeM) * 100;
    const y = (q: number[]) => ((half - (q[1] - lat) * M_LAT) / sizeM) * 100;
    return polys.map((poly) => poly.map((ring) => ring.map((q) => `${x(q).toFixed(2)},${y(q).toFixed(2)}`).join(' ')));
  }, [f, lon, lat, mLon, sizeM]);

  // incident status + history
  const incApi = p.paths.has('/api/incidents/{iid}');
  const [inc, setInc] = useState<any | null | undefined>(undefined);
  const loadInc = () => apiGet<any>(`/api/incidents/${encodeURIComponent(pr.id)}`, {}, '/api/incidents/{iid}').then((x) => setInc(x));
  useEffect(() => {
    if (incApi) loadInc();
    else setInc(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pr.id, incApi]);
  const status: string = inc?.status ?? (art ? 'excluded' : 'detected');
  const lastOp = (inc?.history ?? []).filter((h: any) => h.actor === 'operator').slice(-1)[0];
  const [busy, setBusy] = useState(false);
  const sendToReview = async () => {
    setBusy(true);
    if (incApi) {
      const r = await fetch(`/api/incidents/${encodeURIComponent(pr.id)}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ to: 'under_review', note: 'отправлено на проверку из карточки доказательств' }),
      });
      const j = await r.json().catch(() => null);
      if (r.ok) {
        setInc(j);
        p.onIncidentChanged();
        p.onToast('Отправлено на проверку — находка появится во вкладке «Проверка»');
      } else p.onToast(`Не удалось: ${j?.detail ?? r.status}`);
    } else {
      const r = await apiPost('/api/review/flag', { id: pr.id, region: pr.region, date: pr.date, model: pr.model, lon, lat, max_prob: pr.max_prob, note: 'на проверку' });
      p.onToast(r.ok ? 'Отправлено на проверку' : `Не удалось: ${r.error}`);
    }
    setBusy(false);
  };

  // both models: the other model's object within 30 m
  const other = useMemo(() => {
    if (!p.otherModel || !p.otherDet) return null;
    let best: Feature<DetProps> | null = null;
    let bd = Infinity;
    for (const o of p.otherDet.features) {
      const [x, y] = centroid(o);
      const d = Math.hypot((x - lon) * mLon, (y - lat) * M_LAT);
      if (d < bd) {
        bd = d;
        best = o;
      }
    }
    return { f: best && bd <= Math.max(60, extM / 2 + 30) ? best : null, d: bd };
  }, [p.otherDet, p.otherModel, lon, lat, mLon, extM]);
  const mainT = modelTitle(pr.model, p.manifest);
  const otherT = p.otherModel ? modelTitle(p.otherModel, p.manifest) : null;

  const [a, u] = fmtArea(pr.area_m2);
  const coords = `${lat.toFixed(5)}, ${lon.toFixed(5)}`;
  const t = sceneTime(p.dateEntry.scene_id);
  const hazy = isFlagged(p.dateEntry);
  const pdfHref = cell ? apiUrl('/api/place_report.pdf', { region: pr.region, h3: cell.id, model: pr.model, date: pr.date }) : null;

  return (
    <>
      <section className="sec" data-testid="detection-card">
        <div className={`ev-status ${status === 'confirmed' ? 's-confirmed' : status === 'under_review' ? 's-review' : ''}`} data-testid="incident-status">
          <span className="dot" aria-hidden />
          <span>
            <b style={{ fontWeight: 500 }}>{status === 'detected' ? 'Не проверено человеком' : STATUS_RU[status] ?? status}</b>
            {lastOp && (
              <span className="faint">
                {' '}
                · {lastOp.user ?? 'оператор'}, {fmtTs(lastOp.ts)}
              </span>
            )}
          </span>
        </div>
        {art && (
          <div className="warnline" style={{ marginTop: 12 }} data-testid="detection-artifact">
            {artifactText(art).replace(/^в/, 'В')}. В счёт и зоны не входит.
          </div>
        )}
        <div className="crop" style={{ marginTop: 12 }}>
          {cropApi ? (
            <>
              <img
                key={cropSrc}
                src={cropSrc}
                alt="Исходная вырезка снимка Sentinel-2 вокруг находки"
                onError={() => {
                  setBad((s) => new Set(s).add(bands));
                  if (bands !== 'rgb') setBands('rgb');
                }}
                data-testid="det-crop-img"
              />
              <svg viewBox="0 0 100 100" preserveAspectRatio="none" style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}>
                {outline.map((poly, i) =>
                  poly.map((ring, j) => <polygon key={`${i}-${j}`} points={ring} fill="none" stroke={art ? '#a8b0ba' : 'var(--accent)'} strokeWidth={1.4} vectorEffect="non-scaling-stroke" />),
                )}
              </svg>
            </>
          ) : (
            <div className="note" style={{ padding: 16 }}>
              Вырезка доступна через API сервиса.
            </div>
          )}
          {cropApi && (
            <div className="seg crop-switch" role="group" aria-label="Каналы вырезки">
              {(
                [
                  ['rgb', 'RGB'],
                  ['false', 'ИК'],
                  ['swir', 'SWIR'],
                ] as [Bands, string][]
              ).map(([b, l]) => (
                <button key={b} className={bands === b ? 'on' : ''} disabled={bad.has(b)} onClick={() => setBands(b)} data-testid={`det-bands-${b}`}>
                  {l}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="crop-cap" title="Жёлтый контур — участок, отмеченный моделью">
          {sensorOf(p.dateEntry.scene_id, p.dateEntry.source).name}, {fmtNum(sizeM)} × {fmtNum(sizeM)} м
          {sensorOf(p.dateEntry.scene_id, p.dateEntry.source).px ? `, пиксель ${sensorOf(p.dateEntry.scene_id, p.dateEntry.source).px} м` : ''}
        </div>
      </section>

      <section className="sec">
        <dl className="rows" data-testid="evidence-facts">
          <dt>Дата съёмки</dt>
          <dd>
            {fmtDate(pr.date)}
            {t ? `, ${t}` : ''}
          </dd>
          <dt>Координаты</dt>
          <dd>
            <button
              className="link mono"
              onClick={() => navigator.clipboard?.writeText(coords).then(() => p.onToast('Координаты скопированы'), () => p.onToast(coords))}
              data-testid="copy-coords"
            >
              {coords}
            </button>
          </dd>
          <dt>
            Площадь
            <Info label="Что такое площадь" testid="info-area">
              Пиксели с вероятностью ≥ порога модели, не масса и не объём. Наблюдалось воды в ячейке H3:{' '}
              {cell ? `${fmtPct(cell.obs)}, индекс ${fmtPermille(cell.idx)} ‰` : '—'}. Сцена {p.dateEntry.scene_id}, id {pr.id}.
            </Info>
          </dt>
          <dd className={art ? '' : 'accent'}>
            {a} {u}
          </dd>
          <dt>Облачность</dt>
          <dd>{fmtPct(p.dateEntry.cloud_frac)}</dd>
          <dt>Дымка, блик</dt>
          <dd className={hazy ? 'warn-t' : ''}>{hazy ? 'есть — возможны ложные' : 'нет'}</dd>
        </dl>
      </section>

      <section className="sec" data-testid="evidence-models">
        <div className="sec-h">
          <h3>Модели</h3>
          <span className="aside">согласие — сигнал, не подтверждение</span>
        </div>
        <table className="t">
          <thead>
            <tr>
              <th>модель</th>
              <th className="r">уверенность</th>
              <th className="r">вывод</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td title={`${mainT.label}. ${mainT.hint}`}>{modelLabel(pr.model)}</td>
              <td className="r">
                {fmtProb(pr.mean_prob)} <span className="faint">макс. {fmtProb(pr.max_prob)}</span>
              </td>
              <td className="r">отметила</td>
            </tr>
            {p.otherModel && (
              <tr data-testid="evidence-other-model">
                <td title={`${otherT?.label}. ${otherT?.hint}`}>{modelLabel(p.otherModel)}</td>
                <td className="r">
                  {other?.f ? (
                    <>
                      {fmtProb(other.f.properties.mean_prob)} <span className="faint">макс. {fmtProb(other.f.properties.max_prob)}</span>
                    </>
                  ) : (
                    '—'
                  )}
                </td>
                <td className="r" data-testid="evidence-second-model">
                  {p.otherDet === null
                    ? '…'
                    : other?.f
                      ? `тоже видит, ${fmtNum(other.d)} м`
                      : pr.confirmed
                        ? 'тоже видит (≤ 20 м)'
                        : 'не видит'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
        {(mainT.variant || otherT?.variant) && (
          <p className="note" style={{ marginTop: 8 }} data-testid="evidence-model-variant">
            LGBM — вариант для снимков L2A
            <Info label="Про вариант модели" align="right">
              {mainT.variant ? mainT.hint : otherT?.hint}
            </Info>
          </p>
        )}
      </section>

      <section className="sec" data-testid="evidence-alternatives">
        <div className="sec-h">
          <h3>Что это ещё может быть</h3>

        </div>
        <dl className="alt">
          <dt className={art === 'wake' || art === 'ship' || elong > 5 ? 'hint' : ''}>След судна</dt>
          <dd>{elong > 5 && !art ? 'участок вытянутый — проверьте' : 'узкая полоса, судно ярче в SWIR'}</dd>
          <dt>Пена</dt>
          <dd>белые полосы у прибоя</dd>
          <dt>Водоросли</dt>
          <dd>ярче воды на «ИК»</dd>
          <dt className={hazy ? 'hint' : ''}>Блик, дымка, облако</dt>
          <dd>{hazy ? 'на снимке есть флаг' : 'ложные яркие пятна'}</dd>
        </dl>
      </section>

      <section className="sec" data-testid="evidence-actions">
        {!art && (
          <>
            <button
              className="btn primary"
              style={{ width: '100%', justifyContent: 'center', height: 40 }}
              disabled={busy || status !== 'detected'}
              onClick={sendToReview}
              data-testid="send-to-review"
            >
              {status === 'detected' ? 'Отправить на проверку' : status === 'under_review' ? 'Уже на проверке' : 'Решение оператора принято'}
            </button>
            <p className="note" style={{ marginTop: 8 }}>
              Подтверждает только человек во вкладке «Проверка».
            </p>
          </>
        )}
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 12 }}>
          {p.dateEntry.drift && (
            <button className="btn sm" onClick={() => p.onView('drift')} data-testid="evidence-drift">
              Дрейф
            </button>
          )}
          {cell && (
            <button className="btn sm" onClick={() => p.onPlace(cell.id)} data-testid="evidence-place">
              История места
            </button>
          )}
          {pdfHref && p.paths.has('/api/place_report.pdf') && (
            <a className="btn sm" href={pdfHref} download data-testid="evidence-pdf">
              Справка PDF
            </a>
          )}
        </div>
      </section>

      {inc?.history?.length > 0 && (
        <section className="sec" data-testid="incident-history">
          <div className="sec-h">
            <h3>Журнал</h3>
            <span className="aside">{inc.history.length}</span>
          </div>
          <table className="t">
            <tbody>
              {inc.history
                .slice(-4)
                .reverse()
                .map((h: any, i: number) => (
                  <tr key={i}>
                    <td className="faint" style={{ whiteSpace: 'nowrap' }}>
                      {fmtTs(h.ts)}
                    </td>
                    <td>
                      {STATUS_RU[h.to] ?? h.to}
                      <div className="faint small">
                        {h.actor === 'operator' ? `оператор${h.user ? ` ${h.user}` : ''}` : 'система'}
                        {h.note ? ` · ${h.note}` : ''}
                      </div>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
      )}
    </>
  );
}
