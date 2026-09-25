import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Info from './Info';
import type { Manifest } from '../types';
import { apiGet, apiPaths, apiPost, type LabelRec, type RetrainJob, type ReviewItem, type ReviewQueue } from '../lib/api';
import { shortName } from '../lib/data';
import { fmtArea, fmtDate, fmtNum, fmtProb, fmtThr, modelLabel } from '../lib/style';

interface Props {
  manifest: Manifest;
  initialRegion: string | null;
  onToast: (t: string) => void;
  onShowOnMap: (it: ReviewItem) => void;
  onClose: () => void;
}

const LABELS: { id: string; ru: string }[] = [
  { id: 'debris', ru: 'мусор' },
  { id: 'foam', ru: 'пена' },
  { id: 'algae', ru: 'водоросли' },
  { id: 'ship_wake', ru: 'судно / след' },
  { id: 'cloud', ru: 'облако' },
  { id: 'other', ru: 'другое' },
];
const LABEL_RU: Record<string, string> = Object.fromEntries(LABELS.map((l) => [l.id, l.ru]));
const REASON_RU: Record<string, string> = {
  user_flag: 'отмечено «ложное?»',
  disagreement: 'расхождение моделей',
  near_threshold: 'около порога',
  artifact_conflict: 'артефакт? спор',
};

// ---------------------------------------------------------------- incidents (docs/CONTRACTS.md «Инциденты и лента событий»)
const STATUS_ORDER = ['detected', 'under_review', 'confirmed', 'false_alarm', 'resolved', 'excluded'];
const STATUS_RU: Record<string, string> = {
  detected: 'обнаружено',
  under_review: 'на проверке',
  confirmed: 'подтверждено',
  false_alarm: 'ложное',
  resolved: 'убрано',
  excluded: 'исключено (артефакт)',
};
const STATUS_CLS: Record<string, string> = {
  detected: 's-detected',
  under_review: 's-review',
  confirmed: 's-confirmed',
  false_alarm: 's-false',
  resolved: 's-removed',
  excluded: 's-false',
};
/** accepts contract names and a few loose aliases (review, false, removed) */
const normStatus = (s: string | null | undefined): string => {
  const v = String(s ?? '').toLowerCase();
  if (v === 'review' || v === 'in_review') return 'under_review';
  if (v === 'false' || v === 'false_positive') return 'false_alarm';
  if (v === 'removed' || v === 'closed') return 'resolved';
  return v || 'detected';
};
const statusRu = (s: string | null | undefined) => STATUS_RU[normStatus(s)] ?? String(s ?? '—');
const ACTOR_RU: Record<string, string> = { system: 'система', operator: 'оператор' };
const SOURCE_RU: Record<string, string> = { build: 'сборка', review: 'проверка', api: 'API' };

interface IncEvent {
  ts?: string;
  actor?: string;
  user?: string;
  from?: string | null;
  to?: string | null;
  note?: string | null;
  source?: string;
  label?: string | null;
}
interface Incident {
  id: string;
  kind?: string;
  status: string;
  region: string;
  region_name?: string;
  date: string;
  model?: string;
  lon: number;
  lat: number;
  area_m2?: number | null;
  priority?: number | null;
  max_prob?: number | null;
  artifact_ru?: string | null;
  label?: string | null;
  updated?: string;
  allowed?: string[];
  history?: IncEvent[];
}
interface Summary {
  reviewed: number;
  confirmed: number;
  share: number | null;
  text?: string;
  byStatus?: Record<string, number>;
  source: 'api' | 'client';
}

function parseIncident(x: any): Incident | null {
  if (!x || typeof x !== 'object' || x.id === undefined) return null;
  const hist = x.history ?? x.log ?? x.events;
  return {
    id: String(x.id),
    kind: x.kind,
    status: normStatus(x.status),
    region: String(x.region ?? ''),
    region_name: x.region_name,
    date: String(x.date ?? ''),
    model: x.model,
    lon: Number(x.lon),
    lat: Number(x.lat),
    area_m2: typeof x.area_m2 === 'number' ? x.area_m2 : null,
    priority: typeof x.priority === 'number' ? x.priority : null,
    max_prob: typeof x.max_prob === 'number' ? x.max_prob : null,
    artifact_ru: x.artifact_ru ?? null,
    label: x.label ?? null,
    updated: x.updated,
    allowed: Array.isArray(x.allowed) ? x.allowed.map(String) : undefined,
    history: Array.isArray(hist) ? (hist as IncEvent[]) : undefined,
  };
}
function parseIncidents(j: any): { items: Incident[]; total: number | null } {
  const arr = Array.isArray(j) ? j : Array.isArray(j?.items) ? j.items : Array.isArray(j?.incidents) ? j.incidents : [];
  const items = (arr as any[]).map(parseIncident).filter((x): x is Incident => !!x);
  return { items, total: typeof j?.n_total === 'number' ? j.n_total : null };
}

/** Client fallback summary from /api/review/labels: the last label of an object decides (debris = confirmed). */
function clientSummary(raw: LabelRec[]): Summary {
  const last = new Map<string, LabelRec>();
  for (const r of raw) {
    if (r.kind !== 'label' && r.kind !== 'flag_false' && r.kind !== undefined) continue;
    last.set(r.id, r);
  }
  let confirmed = 0;
  for (const r of last.values()) if (r.kind !== 'flag_false' && r.label === 'debris') confirmed++;
  const reviewed = last.size;
  return { reviewed, confirmed, share: reviewed ? confirmed / reviewed : null, source: 'client' };
}

const fmtTs = (ts: string | undefined) => {
  if (!ts) return '—';
  const d = ts.slice(0, 10);
  const t = ts.slice(11, 16);
  return `${fmtDate(d)}${t ? ` ${t}` : ''}`;
};

export default function ReviewView({ manifest, initialRegion, onToast, onShowOnMap, onClose }: Props) {
  const regionsSorted = useMemo(
    () => [...manifest.regions].sort((a, b) => (b.summary?.n_detections ?? 0) - (a.summary?.n_detections ?? 0)),
    [manifest],
  );
  const [region, setRegion] = useState<string>(initialRegion ?? regionsSorted[0]?.id ?? '');
  const [model, setModel] = useState<string>('');
  const [queue, setQueue] = useState<ReviewQueue | null | undefined>(undefined);
  const [pos, setPos] = useState(0);
  const [rawLabels, setRawLabels] = useState<LabelRec[]>([]);
  const [session, setSession] = useState(0);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<RetrainJob | null>(null);
  const [jobErr, setJobErr] = useState<string | null>(null);
  const [imgOk, setImgOk] = useState<{ rgb: boolean; fc: boolean }>({ rgb: true, fc: true });
  const [bands, setBands] = useState<'rgb' | 'fc'>('rgb');
  const [tab, setTab] = useState<'queue' | 'incidents'>('queue');
  const [paths, setPaths] = useState<Set<string> | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [rev, setRev] = useState(0);
  const poll = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    apiPaths().then((p) => alive && setPaths(p));
    return () => {
      alive = false;
    };
  }, []);
  const hasIncidents = !!paths && [...paths].some((p) => p.startsWith('/api/incidents'));
  const hasIncSummary = !!paths && paths.has('/api/incidents/summary');

  const loadQueue = useCallback(async () => {
    setQueue(undefined);
    const q = await apiGet<ReviewQueue>('/api/review/queue', { region, model: model || null, limit: 200 });
    setQueue(q);
    setPos(0);
  }, [region, model]);
  const loadLabels = useCallback(async () => {
    const r = await apiGet<{ items: LabelRec[] }>('/api/review/labels');
    setRawLabels(r?.items ?? []);
  }, []);
  useEffect(() => {
    loadQueue();
  }, [loadQueue]);
  useEffect(() => {
    loadLabels();
  }, [loadLabels]);
  useEffect(() => () => void (poll.current && clearInterval(poll.current)), []);

  const labels = useMemo(() => rawLabels.filter((x) => x.kind === 'label' || x.kind === undefined), [rawLabels]);

  // summary «подтверждено X % проверенных»: /api/incidents/summary when present, else from the label journal
  useEffect(() => {
    if (!paths) return;
    let alive = true;
    (async () => {
      if (hasIncSummary) {
        const s = await apiGet<any>('/api/incidents/summary');
        if (s && typeof s === 'object') {
          if (!alive) return;
          const reviewed = Number(s.reviewed ?? 0);
          const confirmed = Number(s.confirmed_reviewed ?? 0);
          setSummary({
            reviewed,
            confirmed,
            share: typeof s.confirmed_share_of_reviewed === 'number' ? s.confirmed_share_of_reviewed : reviewed ? confirmed / reviewed : null,
            text: typeof s.confirmed_share_text === 'string' ? s.confirmed_share_text : undefined,
            byStatus: s.by_status && typeof s.by_status === 'object' ? s.by_status : undefined,
            source: 'api',
          });
          return;
        }
      }
      if (alive) setSummary(clientSummary(rawLabels));
    })();
    return () => {
      alive = false;
    };
  }, [paths, hasIncSummary, rawLabels, rev]);

  const items = queue?.items ?? [];
  const it: ReviewItem | undefined = items[pos];
  useEffect(() => setImgOk({ rgb: true, fc: true }), [it?.id]);

  const next = useCallback(() => setPos((p) => (items.length ? Math.min(p + 1, items.length) : 0)), [items.length]);
  const prev = useCallback(() => setPos((p) => Math.max(0, p - 1)), []);

  const label = useCallback(
    async (lab: string) => {
      if (!it || busy) return;
      setBusy(true);
      const r = await apiPost<LabelRec>('/api/review/label', {
        id: it.id,
        region: it.region,
        date: it.date,
        model: it.model,
        lon: it.lon,
        lat: it.lat,
        label: lab,
        max_prob: it.max_prob,
      });
      setBusy(false);
      if (!r.ok) {
        onToast(`Метка не сохранена: ${r.error}`);
        return;
      }
      setRawLabels((l) => [...l, r.data!]);
      setSession((s) => s + 1);
      // the item leaves the queue (labelled); the next one takes its place
      setQueue((q) => (q ? { ...q, items: q.items.filter((x) => x !== it), n_total: q.n_total - 1, n_labeled: q.n_labeled + 1 } : q));
      setPos((p) => Math.min(p, Math.max(0, items.length - 2)));
    },
    [it, busy, items.length, onToast],
  );

  // keyboard (queue tab only): 1–6 labels, → / space skip, ← back
  useEffect(() => {
    if (tab !== 'queue') return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const k = Number(e.key);
      if (k >= 1 && k <= 6) {
        e.preventDefault();
        label(LABELS[k - 1].id);
      } else if (e.key === 'ArrowRight' || e.key === ' ') {
        e.preventDefault();
        next();
      } else if (e.key === 'ArrowLeft') {
        e.preventDefault();
        prev();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [label, next, prev, tab]);

  const retrain = async () => {
    setJobErr(null);
    const r = await apiPost<RetrainJob>('/api/review/retrain', {});
    if (!r.ok || !r.data) {
      setJobErr(r.error ?? 'ошибка запуска');
      return;
    }
    setJob(r.data);
    if (poll.current) clearInterval(poll.current);
    poll.current = window.setInterval(async () => {
      const s = await apiGet<RetrainJob>(`/api/review/retrain/${encodeURIComponent(r.data!.id)}`, {}, '/api/review/retrain/{job}');
      if (!s) return;
      setJob(s);
      if (s.status !== 'running' && poll.current) {
        clearInterval(poll.current);
        poll.current = null;
      }
    }, 2000);
  };
  (window as any).__review = { n: items.length, pos, jobStatus: job?.status ?? null, labels: labels.length, tab, summary };

  const regionName = (id: string) => shortName(manifest.regions.find((r) => r.id === id)?.name ?? id);
  const regionLabels = labels.filter((l) => l.region === region);
  const recent = [...labels].reverse().slice(0, 40);
  const res = job?.result;
  const cropSrc = it ? (bands === 'fc' && it.crop_false_color ? it.crop_false_color : it.crop_rgb) : '';

  return (
    <div className="view" data-testid="review-view">
      <div className="view-head">
        <h2>Проверка человеком</h2>
        <select className="sel" value={region} onChange={(e) => setRegion(e.target.value)} data-testid="review-region" aria-label="Район">
          {regionsSorted.map((r) => (
            <option key={r.id} value={r.id}>
              {shortName(r.name)}
            </option>
          ))}
        </select>
        <select className="sel" value={model} onChange={(e) => setModel(e.target.value)} data-testid="review-model" aria-label="Модель">
          <option value="">все модели</option>
          {Object.keys(manifest.models).map((m) => (
            <option key={m} value={m}>
              {modelLabel(m, manifest.models[m]?.name)}
            </option>
          ))}
        </select>
        <div style={{ marginLeft: 'auto', textAlign: 'right' }} data-testid="review-summary">
          {summary ? (
            <>
              <div>
                {summary.share === null ? (
                  <span className="muted">проверенных пока нет</span>
                ) : (
                  <>
                    подтверждено <b style={{ fontWeight: 600 }}>{Math.round(summary.share * 100)} %</b> проверенных
                  </>
                )}
              </div>
              <div className="tiny faint">
                {summary.confirmed} из {summary.reviewed}
                {summary.source === 'api' ? ' · по журналу инцидентов' : ' · по журналу меток'}
              </div>
            </>
          ) : (
            <span className="faint small">…</span>
          )}
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Закрыть" title="Закрыть" data-testid="review-close">
          ×
        </button>
      </div>
      {hasIncidents && (
        <div className="tabs" style={{ padding: '0 var(--s3)', background: 'var(--panel)' }} data-testid="review-tabs">
          <button className={`tab ${tab === 'queue' ? 'on' : ''}`} onClick={() => setTab('queue')} data-testid="review-tab-queue">
            Очередь
          </button>
          <button className={`tab ${tab === 'incidents' ? 'on' : ''}`} onClick={() => setTab('incidents')} data-testid="review-tab-incidents">
            Инциденты
          </button>
        </div>
      )}

      {tab === 'incidents' && hasIncidents ? (
        <IncidentsTab
          paths={paths!}
          region={region}
          model={model}
          regionName={regionName}
          summary={summary}
          onToast={onToast}
          onShowOnMap={onShowOnMap}
          onChanged={() => setRev((v) => v + 1)}
          manifest={manifest}
        />
      ) : (
        <div className="view-body" style={{ display: 'grid', gridTemplateColumns: '320px minmax(0, 1fr) 320px', overflow: 'hidden' }}>
          {/* ---- left: queue ---- */}
          <aside style={{ borderRight: '1px solid var(--line)', background: 'var(--panel)', overflowY: 'auto', minHeight: 0 }}>
            <section className="sec">
              <div className="sec-h">
                <h3>Очередь сомнительных находок</h3>
              </div>
              <div className="kv three" data-testid="review-counter">
                <div>
                  <div className="v">{queue ? fmtNum(queue.n_total) : '…'}</div>
                  <div className="k">{queue && queue.n_total > (queue.items?.length ?? 0) ? `в очереди, показаны первые ${queue.items.length}` : 'в очереди'}</div>
                </div>
                <div>
                  <div className="v">{fmtNum(regionLabels.length)}</div>
                  <div className="k">размечено в районе</div>
                </div>
                <div>
                  <div className="v">{fmtNum(session)}</div>
                  <div className="k">за сессию</div>
                </div>
              </div>
              <p className="note" style={{ marginTop: 8 }}>
                Кто в очереди
                <Info label="Кто попадает в очередь" testid="info-queue">
                В очередь попадают сомнительные находки: уверенность модели близка к порогу, две модели не согласны, или находку отправили на
                проверку с карты.
                {queue?.n_artifacts_excluded ? (
                  <span data-testid="review-artifacts-note">
                    {' '}Исключённые артефакты (шов детекторов, кильватер, судно: {fmtNum(queue.n_artifacts_excluded)}) в очередь не идут, кроме
                    отмеченных «ложное?» и тех, что подтверждает вторая модель.
                  </span>
                ) : null}
                </Info>
              </p>
            </section>
            <div data-testid="review-list">
              {queue === undefined && <div className="empty">Загрузка…</div>}
              {queue === null && <div className="empty">Очередь недоступна (нет ответа /api/review/queue).</div>}
              {queue && !items.length && <div className="empty">Очередь пуста — всё размечено.</div>}
              {items.slice(0, 80).map((x, i) => (
                <button key={x.id} className={`li ${i === pos ? 'on' : ''}`} onClick={() => setPos(i)} data-testid={`review-item-${i}`}>
                  <span className="faint small num">{i + 1}</span>
                  <span className="l-main">
                    <span style={{ display: 'block' }}>
                      {fmtDate(x.date)} · {modelLabel(x.model)}
                    </span>
                    <span className="l-sub">{x.reasons.map((c) => REASON_RU[c] ?? c).join(' · ')}</span>
                  </span>
                  <span className="l-val mono" title="уверенность модели (максимум)">
                    {fmtProb(x.max_prob)}
                  </span>
                </button>
              ))}
            </div>
          </aside>

          {/* ---- centre: crop + labels ---- */}
          <main style={{ overflowY: 'auto', minHeight: 0 }}>
            {it ? (
              <div data-testid="review-card" style={{ maxWidth: 720, margin: '0 auto', padding: 'var(--s3)' }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--s2)' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="faint small">
                      Находка {pos + 1} из {items.length} · {shortName(queue?.region_name ?? regionName(region))}
                    </div>
                    <div style={{ fontSize: 16, fontWeight: 500, marginTop: 4 }}>
                      {fmtDate(it.date)} · {modelLabel(it.model, manifest.models?.[it.model]?.name)} · уверенность{' '}
                      <span className="accent">{fmtProb(it.max_prob)}</span>
                      <span className="muted small"> (порог {fmtThr(it.threshold)})</span>
                    </div>
                  </div>
                  <button className="btn sm" onClick={() => onShowOnMap(it)} data-testid="review-show-map">
                    на карте
                  </button>
                </div>
                <p className="small muted" style={{ marginTop: 8 }} data-testid="review-reason">
                  <span style={{ color: 'var(--text)' }}>Почему в очереди:</span> {it.reason}
                  {it.artifact_ru && <span className="faint"> · сборка пометила как «{it.artifact_ru}» (исключено из индекса и зон)</span>}
                </p>

                <div style={{ marginTop: 'var(--s2)' }}>
                  <div className="crop">
                    {bands === 'rgb' || !it.crop_false_color ? (
                      imgOk.rgb ? (
                        <img
                          key={`rgb-${it.id}`}
                          src={`${it.crop_rgb}&px=720`}
                          alt="RGB-вырезка"
                          onError={() => setImgOk((s) => ({ ...s, rgb: false }))}
                          data-testid="review-rgb"
                        />
                      ) : (
                        <div className="empty">вырезка недоступна</div>
                      )
                    ) : imgOk.fc ? (
                      <img
                        key={`fc-${it.id}`}
                        src={`${it.crop_false_color}&px=720`}
                        alt="Ложный цвет"
                        onError={() => setImgOk((s) => ({ ...s, fc: false }))}
                        data-testid="review-false"
                      />
                    ) : (
                      <div className="empty">ложный цвет недоступен</div>
                    )}
                    <div className="seg crop-switch" role="group" aria-label="Каналы">
                      <button className={bands === 'rgb' ? 'on' : ''} onClick={() => setBands('rgb')} data-testid="review-bands-rgb">
                        RGB
                      </button>
                      <button
                        className={bands === 'fc' ? 'on' : ''}
                        onClick={() => setBands('fc')}
                        disabled={!it.crop_false_color}
                        title={it.crop_false_color ? 'B8/B4/B3' : 'нет каналов B8 для этой сцены'}
                        data-testid="review-bands-fc"
                      >
                        ложный цвет
                      </button>
                    </div>
                  </div>
                  <CropCaption url={cropSrc} date={it.date} kind={bands === 'fc' && it.crop_false_color ? 'B8/B4/B3' : 'RGB B4/B3/B2'} />
                  {bands === 'fc' && it.crop_false_color && (
                    <div className="crop-cap">ложный цвет B8/B4/B3: растительность и водоросли — красные, пена — белая</div>
                  )}
                </div>

                <div className="small muted" style={{ marginTop: 'var(--s1)' }}>
                  ср. P {fmtProb(it.mean_prob)} · площадь {fmtNum(it.area_m2)} м² ·{' '}
                  {it.confirmed === true
                    ? 'вторая модель согласна (согласие моделей, не проверка на месте)'
                    : it.confirmed === false
                      ? 'вторая модель не видит'
                      : 'вторая модель на дате не запускалась'}{' '}
                  ·{' '}
                  <span className="mono">
                    {it.lat.toFixed(5)}, {it.lon.toFixed(5)}
                  </span>
                </div>

                <div
                  style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: 'var(--s1)', marginTop: 'var(--s2)' }}
                  data-testid="review-labels"
                >
                  {LABELS.map((l, i) => (
                    <button
                      key={l.id}
                      className="btn"
                      style={{ justifyContent: 'flex-start' }}
                      onClick={() => label(l.id)}
                      disabled={busy}
                      data-testid={`review-label-${l.id}`}
                    >
                      <span className="kbd">{i + 1}</span>
                      {l.ru}
                    </button>
                  ))}
                  <button className="btn ghost" style={{ justifyContent: 'flex-start' }} onClick={next} data-testid="review-skip">
                    <span className="kbd">→</span>пропустить
                  </button>
                </div>
                <p className="note" style={{ marginTop: 'var(--s1)' }} title="Метка записывается в журнал: кто, когда, по какому снимку">
                  Клавиши 1–6, → пропустить, ← назад
                </p>
              </div>
            ) : (
              <div className="empty" data-testid="review-card-empty">
                {queue === undefined
                  ? 'Загрузка очереди…'
                  : pos >= items.length && items.length
                    ? 'Конец очереди — можно вернуться клавишей ←.'
                    : 'Нет находок для проверки.'}
              </div>
            )}
          </main>

          {/* ---- right: journal + retrain ---- */}
          <aside style={{ borderLeft: '1px solid var(--line)', background: 'var(--panel)', overflowY: 'auto', minHeight: 0 }}>
            <section className="sec">
              <div className="sec-h">
                <h3>Журнал меток</h3>
                <span className="aside">всего {fmtNum(labels.length)}</span>
              </div>
              <div data-testid="review-done" style={{ margin: '0 calc(-1 * var(--s3))' }}>
                {!recent.length && <div className="note" style={{ padding: '0 var(--s3)' }}>Пока нет меток.</div>}
                {recent.map((l, i) => (
                  <div
                    key={`${l.id}-${l.provenance?.ts ?? i}`}
                    style={{ borderTop: '1px solid var(--line)', padding: '8px var(--s3)', display: 'flex', gap: 'var(--s1)', alignItems: 'baseline' }}
                  >
                    <span style={{ minWidth: 88, color: l.label === 'debris' ? 'var(--accent)' : 'var(--text)' }}>
                      {LABEL_RU[l.label ?? ''] ?? l.label}
                    </span>
                    <span className="small faint" style={{ minWidth: 0 }}>
                      {regionName(l.region)} · {fmtDate(l.date)} · {modelLabel(l.model)}
                      {l.provenance?.ts ? ` · ${l.provenance.ts.slice(11, 16)}` : ''}
                    </span>
                  </div>
                ))}
              </div>
            </section>
            <section className="sec">
              <div className="sec-h">
                <h3>
                  Дообучение LightGBM
                  <Info label="Как работает дообучение" align="right">
                    Ваши метки можно использовать, чтобы дообучить вторую модель. Качество сравнивается до и после на отложенной разметке; новая
                    модель принимается, только если стала заметно лучше. Модель сервиса сама не заменяется: новая версия сохраняется отдельно,
                    заменяет её человек.
                  </Info>
                </h3>
              </div>
              <button
                className="btn"
                style={{ marginTop: 'var(--s1)', width: '100%', justifyContent: 'center' }}
                onClick={retrain}
                disabled={!labels.length || job?.status === 'running'}
                data-testid="review-retrain"
                title={labels.length ? 'Дообучить LightGBM на всех метках (CPU, ≈ 20–60 с)' : 'Сначала поставьте хотя бы одну метку'}
              >
                {job?.status === 'running' ? 'Идёт дообучение…' : 'Дообучить'}
              </button>
              {jobErr && <div className="warnline" style={{ marginTop: 'var(--s1)' }}>{jobErr}</div>}
              {job && (
                <div data-testid="review-job" style={{ marginTop: 'var(--s2)' }}>
                  <div className="small faint">
                    задача <span className="mono">{job.id}</span> · {job.status === 'running' ? 'идёт' : job.status === 'done' ? 'готово' : job.status}
                    {job.n_labels ? ` · меток ${job.n_labels}` : ''}
                  </div>
                  {job.status === 'running' && (
                    <pre className="mono faint" style={{ margin: '8px 0 0', whiteSpace: 'pre-wrap', fontSize: 11 }}>
                      {(job.log_tail ?? '').trim().split('\n').slice(-5).join('\n') || '…'}
                    </pre>
                  )}
                  {res && !res.error && typeof res.val_f1_before === 'number' && (
                    <div data-testid="review-result" style={{ marginTop: 'var(--s1)' }}>
                      <div className="kv three">
                        <div>
                          <div className="k">F1 val до</div>
                          <div className="v">{res.val_f1_before.toFixed(4)}</div>
                        </div>
                        <div>
                          <div className="k">после</div>
                          <div className="v">{res.val_f1_after?.toFixed(4)}</div>
                        </div>
                        <div>
                          <div className="k">прирост</div>
                          <div className="v">
                            {(res.gain ?? 0) >= 0 ? '+' : ''}
                            {(res.gain ?? 0).toFixed(4)}
                          </div>
                        </div>
                      </div>
                      <div className="small" style={{ marginTop: 'var(--s1)' }} data-testid="review-decision">
                        <b style={{ fontWeight: 600 }}>{res.accepted ? 'Принято' : 'Не принято'}</b>:{' '}
                        {(res.decision ?? '').replace(/^\s*(не\s+)?принято\s*[:—-]?\s*/i, '')}
                      </div>
                      {res.rule && <div className="note">Правило: {res.rule}.</div>}
                    </div>
                  )}
                  {job.status !== 'running' && (
                    <div className="warnline" style={{ marginTop: 'var(--s1)' }} data-testid="review-caveat">
                      <span>
                        Метки со снимков L2A почти не влияют на val MARIDA (ACOLITE) — для оценки нужен размеченный набор с живых снимков.
                        {typeof res?.caveat === 'string' && res.caveat ? <span className="faint"> {res.caveat}</span> : null}
                      </span>
                    </div>
                  )}
                  {(res?.error || job.status === 'error') && (
                    <div className="warnline" style={{ marginTop: 'var(--s1)' }}>
                      Ошибка: {res?.error ?? (job.log_tail ?? '').trim().split('\n').slice(-2).join(' ')}
                    </div>
                  )}
                </div>
              )}
            </section>
          </aside>
        </div>
      )}
    </div>
  );
}

/**
 * Readable caption under a crop (date · bands · scale bar). Scale bar: same «nice» length as the backend
 * (size_m / 4), drawn as a share of the crop width.
 */
function CropCaption({ url, date, kind }: { url: string; date: string; kind: string }) {
  let size = 1000;
  try {
    size = Number(new URL(url, location.href).searchParams.get('size_m')) || 1000;
  } catch {
    /* keep default */
  }
  const L = [20, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000].find((v) => v >= (size / 4) * 0.7) ?? 10000;
  return (
    <div className="crop-cap" style={{ display: 'flex', alignItems: 'center', gap: 'var(--s1)' }} data-testid="review-crop-legend">
      <span>
        {fmtDate(date)} · {kind} · {size >= 1000 ? `${fmtNum(size / 1000, 1)} км` : `${size} м`} по стороне, контур — находка
      </span>
      <span style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 6, width: '30%', justifyContent: 'flex-end' }}>
        <i style={{ display: 'block', height: 2, background: 'var(--text-2)', width: `${Math.min(100, (L / size) * 100 * (100 / 30)).toFixed(1)}%` }} />
        <span>{L < 1000 ? `${L} м` : `${L / 1000} км`}</span>
      </span>
    </div>
  );
}

// ---------------------------------------------------------------- incidents tab
function IncidentsTab({
  paths,
  region,
  model,
  regionName,
  summary,
  onToast,
  onShowOnMap,
  onChanged,
  manifest,
}: {
  paths: Set<string>;
  region: string;
  model: string;
  regionName: (id: string) => string;
  summary: Summary | null;
  onToast: (t: string) => void;
  onShowOnMap: (it: ReviewItem) => void;
  onChanged: () => void;
  manifest: Manifest;
}) {
  const [statusF, setStatusF] = useState<string>('');
  const [list, setList] = useState<Incident[] | null | undefined>(undefined);
  const [total, setTotal] = useState<number | null>(null);
  const [selId, setSelId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Incident | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);

  const detailTpl = useMemo(() => [...paths].find((p) => /^\/api\/incidents\/\{[^}]+\}$/.test(p)) ?? null, [paths]);
  const statusTpl = useMemo(() => [...paths].find((p) => /^\/api\/incidents\/\{[^}]+\}\/status$/.test(p)) ?? null, [paths]);

  useEffect(() => {
    let alive = true;
    setList(undefined);
    apiGet<any>('/api/incidents', { region, model: model || null, status: statusF || null, limit: 200 }).then((j) => {
      if (!alive) return;
      if (!j) {
        setList(null);
        return;
      }
      const p = parseIncidents(j);
      setList(p.items);
      setTotal(p.total);
      setSelId((cur) => (cur && p.items.some((x) => x.id === cur) ? cur : p.items[0]?.id ?? null));
    });
    return () => {
      alive = false;
    };
  }, [region, model, statusF, tick]);

  const sel = list?.find((x) => x.id === selId) ?? null;
  useEffect(() => {
    let alive = true;
    setDetail(null);
    if (!selId || !detailTpl) return;
    apiGet<any>(`/api/incidents/${encodeURIComponent(selId)}`, {}, detailTpl).then((j) => {
      if (alive && j) setDetail(parseIncident(j));
    });
    return () => {
      alive = false;
    };
  }, [selId, detailTpl, tick]);
  const inc = detail ?? sel;

  const setStatus = async (to: string) => {
    if (!inc || !statusTpl || busy) return;
    setBusy(true);
    try {
      const r = await fetch(`/api/incidents/${encodeURIComponent(inc.id)}/status`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ to }),
      });
      const j = await r.json().catch(() => null);
      if (!r.ok) {
        onToast(`Статус не изменён: ${(j && (j.detail?.toString?.() ?? j.error)) || `HTTP ${r.status}`}`);
      } else {
        const d = parseIncident(j);
        if (d) setDetail(d);
        onToast(`Статус: ${statusRu(to)}`);
        setTick((t) => t + 1);
        onChanged();
      }
    } catch (e) {
      onToast(`Статус не изменён: ${String(e)}`);
    }
    setBusy(false);
  };

  const byStatus = useMemo(() => {
    if (summary?.byStatus) return summary.byStatus;
    const m: Record<string, number> = {};
    for (const x of list ?? []) m[x.status] = (m[x.status] ?? 0) + 1;
    return m;
  }, [summary, list]);

  const showOnMap = (x: Incident) =>
    onShowOnMap({
      id: x.id,
      region: x.region,
      date: x.date,
      model: x.model ?? Object.keys(manifest.models)[0] ?? '',
      lon: x.lon,
      lat: x.lat,
      max_prob: x.max_prob ?? null,
      mean_prob: null,
      area_m2: x.area_m2 ?? null,
      threshold: manifest.models[x.model ?? '']?.threshold ?? 0.5,
      reasons: [],
      reason: '',
      priority: x.priority ?? 0,
      crop_rgb: '',
      crop_false_color: null,
    });

  const hist = [...(inc?.history ?? [])].sort((a, b) => String(b.ts ?? '').localeCompare(String(a.ts ?? '')));

  return (
    <div className="view-body" style={{ display: 'grid', gridTemplateColumns: '360px minmax(0, 1fr) 320px', overflow: 'hidden' }} data-testid="incidents">
      <aside style={{ borderRight: '1px solid var(--line)', background: 'var(--panel)', overflowY: 'auto', minHeight: 0 }}>
        <section className="sec">
          <div className="sec-h">
            <h3>Инциденты · {regionName(region)}</h3>
            <span className="aside">{list ? `${fmtNum(list.length)}${total !== null && total > list.length ? ` из ${fmtNum(total)}` : ''}` : '…'}</span>
          </div>
          <select className="sel" value={statusF} onChange={(e) => setStatusF(e.target.value)} data-testid="incidents-status" aria-label="Статус">
            <option value="">все статусы</option>
            {STATUS_ORDER.map((s) => (
              <option key={s} value={s}>
                {STATUS_RU[s]}
              </option>
            ))}
          </select>
          <p className="note" style={{ marginTop: 8 }}>
            обнаружено → на проверке → подтверждено / ложное
          </p>
        </section>
        <div data-testid="incidents-list">
          {list === undefined && <div className="empty">Загрузка…</div>}
          {list === null && <div className="empty">Список недоступен (нет ответа /api/incidents).</div>}
          {list && !list.length && <div className="empty">Инцидентов нет.</div>}
          {(list ?? []).slice(0, 200).map((x, i) => (
            <button key={x.id} className={`li ${x.id === selId ? 'on' : ''}`} onClick={() => setSelId(x.id)} data-testid={`incident-item-${i}`}>
              <span className="faint small num">{x.priority ? `№${x.priority}` : ''}</span>
              <span className="l-main">
                <span style={{ display: 'block' }}>
                  {x.kind === 'zone' ? 'зона' : 'пятно'} · {fmtDate(x.date)}
                  {x.model ? ` · ${modelLabel(x.model)}` : ''}
                </span>
                <span className={`status ${STATUS_CLS[x.status] ?? ''}`}>{statusRu(x.status)}</span>
              </span>
              <span className="l-val">{x.area_m2 !== null && x.area_m2 !== undefined ? fmtArea(x.area_m2).join(' ') : ''}</span>
            </button>
          ))}
        </div>
      </aside>

      <main style={{ overflowY: 'auto', minHeight: 0 }}>
        {inc ? (
          <div data-testid="incident-card" style={{ maxWidth: 720, margin: '0 auto', padding: 'var(--s3)' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 'var(--s2)' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="faint small mono" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {inc.id}
                </div>
                <div style={{ fontSize: 16, fontWeight: 500, marginTop: 4 }}>
                  {inc.kind === 'zone' ? 'Зона' : 'Пятно'} · {regionName(inc.region)} · {fmtDate(inc.date)}
                </div>
                <div style={{ marginTop: 4 }}>
                  <span className={`status ${STATUS_CLS[inc.status] ?? ''}`} data-testid="incident-status">
                    {statusRu(inc.status)}
                  </span>
                </div>
              </div>
              {Number.isFinite(inc.lon) && Number.isFinite(inc.lat) && (
                <button className="btn sm" onClick={() => showOnMap(inc)} data-testid="incident-show-map">
                  на карте
                </button>
              )}
            </div>
            <dl className="rows" style={{ marginTop: 'var(--s2)' }}>
              <dt>Площадь</dt>
              <dd>{inc.area_m2 !== null && inc.area_m2 !== undefined ? fmtArea(inc.area_m2).join(' ') : '—'}</dd>
              <dt>Приоритет обследования</dt>
              <dd>{inc.priority ? `№${inc.priority}` : '—'}</dd>
              {inc.max_prob !== null && inc.max_prob !== undefined && (
                <>
                  <dt>уверенность (макс.)</dt>
                  <dd>{fmtProb(inc.max_prob)}</dd>
                </>
              )}
              {inc.model && (
                <>
                  <dt>Модель</dt>
                  <dd>{modelLabel(inc.model, manifest.models[inc.model]?.name)}</dd>
                </>
              )}
              {inc.artifact_ru && (
                <>
                  <dt>Артефакт</dt>
                  <dd>{inc.artifact_ru}</dd>
                </>
              )}
              {inc.label && (
                <>
                  <dt>Метка проверки</dt>
                  <dd>{LABEL_RU[inc.label] ?? inc.label}</dd>
                </>
              )}
              <dt>Координаты</dt>
              <dd className="mono">{Number.isFinite(inc.lat) ? `${inc.lat.toFixed(5)}, ${inc.lon.toFixed(5)}` : '—'}</dd>
            </dl>

            {statusTpl && inc.allowed && inc.allowed.length > 0 && (
              <div style={{ marginTop: 'var(--s2)' }}>
                <div className="faint small" style={{ marginBottom: 8 }}>
                  Сменить статус
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--s1)' }} data-testid="incident-actions">
                  {inc.allowed.map((s) => (
                    <button key={s} className="btn sm" disabled={busy} onClick={() => setStatus(s)} data-testid={`incident-to-${s}`}>
                      → {statusRu(s)}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div style={{ marginTop: 'var(--s3)' }}>
              <div className="sec-h">
                <h3>Журнал изменений</h3>
                <span className="aside">{hist.length ? `${hist.length} событий` : ''}</span>
              </div>
              {!hist.length ? (
                <p className="note">{detailTpl ? 'Загрузка журнала…' : 'Журнал недоступен.'}</p>
              ) : (
                <table className="t" data-testid="incident-history">
                  <thead>
                    <tr>
                      <th>Когда</th>
                      <th>Кто</th>
                      <th>Что</th>
                    </tr>
                  </thead>
                  <tbody>
                    {hist.map((h, i) => (
                      <tr key={`${h.ts}-${i}`}>
                        <td style={{ whiteSpace: 'nowrap' }}>{fmtTs(h.ts)}</td>
                        <td>
                          {ACTOR_RU[h.actor ?? ''] ?? h.actor ?? '—'}
                          {h.source ? <span className="faint"> · {SOURCE_RU[h.source] ?? h.source}</span> : null}
                        </td>
                        <td>
                          {h.from ? `${statusRu(h.from)} → ` : ''}
                          {h.to ? statusRu(h.to) : ''}
                          {h.note ? <div className="faint">{h.note}</div> : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        ) : (
          <div className="empty">{list === undefined ? 'Загрузка…' : 'Выберите инцидент слева.'}</div>
        )}
      </main>

      <aside style={{ borderLeft: '1px solid var(--line)', background: 'var(--panel)', overflowY: 'auto', minHeight: 0 }}>
        <section className="sec" data-testid="incidents-summary">
          <div className="sec-h">
            <h3>Сводка</h3>
            <span className="aside">{summary?.source === 'api' ? 'все районы' : 'по журналу меток'}</span>
          </div>
          <div className="kv">
            <div>
              <div className="v">{summary?.share === null || !summary ? '—' : `${Math.round(summary.share * 100)} %`}</div>
              <div className="k">подтверждено из проверенных</div>
            </div>
            <div>
              <div className="v">{summary ? fmtNum(summary.reviewed) : '…'}</div>
              <div className="k">проверено человеком</div>
            </div>
          </div>
          {summary?.text && <p className="note" style={{ marginTop: 8 }}>{summary.text}</p>}
        </section>
        <section className="sec">
          <div className="sec-h">
            <h3>По статусам</h3>
            <span className="aside">{summary?.byStatus ? 'все районы' : 'в списке'}</span>
          </div>
          <table className="t">
            <tbody>
              {STATUS_ORDER.map((s) => (
                <tr key={s}>
                  <td>
                    <span className={`status ${STATUS_CLS[s]}`}>{STATUS_RU[s]}</span>
                  </td>
                  <td className="r">{fmtNum(byStatus[s] ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="note" style={{ marginTop: 8 }}>
            Что значат статусы
            <Info label="Статусы" align="right">
              «Исключено» ставит сборка (артефакт), это не вердикт человека и в «проверенные» не входит. Подтверждение — решение оператора по
              снимку, не проверка на месте.
            </Info>
          </p>
        </section>
      </aside>
    </div>
  );
}
