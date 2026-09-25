import { useCallback, useEffect, useRef, useState } from 'react';
import type { Manifest } from '../types';
import { apiGet, apiPost, type LabelRec, type RetrainJob, type ReviewItem, type ReviewQueue } from '../lib/api';
import { shortName } from '../lib/data';
import { fmtDate, fmtNum, fmtThr, modelLabel } from '../lib/style';

interface Props {
  manifest: Manifest;
  initialRegion: string | null;
  onToast: (t: string) => void;
  onShowOnMap: (it: ReviewItem) => void;
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
};

export default function ReviewView({ manifest, initialRegion, onToast, onShowOnMap }: Props) {
  const regionsSorted = [...manifest.regions].sort((a, b) => (b.summary?.n_detections ?? 0) - (a.summary?.n_detections ?? 0));
  const [region, setRegion] = useState<string>(initialRegion ?? regionsSorted[0]?.id ?? '');
  const [model, setModel] = useState<string>('');
  const [queue, setQueue] = useState<ReviewQueue | null | undefined>(undefined);
  const [pos, setPos] = useState(0);
  const [labels, setLabels] = useState<LabelRec[]>([]);
  const [session, setSession] = useState(0);
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState<RetrainJob | null>(null);
  const [jobErr, setJobErr] = useState<string | null>(null);
  const [imgOk, setImgOk] = useState<{ rgb: boolean; fc: boolean }>({ rgb: true, fc: true });
  const poll = useRef<number | null>(null);

  const loadQueue = useCallback(async () => {
    setQueue(undefined);
    const q = await apiGet<ReviewQueue>('/api/review/queue', { region, model: model || null, limit: 200 });
    setQueue(q);
    setPos(0);
  }, [region, model]);
  const loadLabels = useCallback(async () => {
    const r = await apiGet<{ items: LabelRec[] }>('/api/review/labels');
    setLabels((r?.items ?? []).filter((x) => x.kind === 'label' || x.kind === undefined));
  }, []);
  useEffect(() => {
    loadQueue();
  }, [loadQueue]);
  useEffect(() => {
    loadLabels();
  }, [loadLabels]);
  useEffect(() => () => void (poll.current && clearInterval(poll.current)), []);

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
      setLabels((l) => [...l, r.data!]);
      setSession((s) => s + 1);
      // the item leaves the queue (labelled); the next one takes its place
      setQueue((q) => (q ? { ...q, items: q.items.filter((x) => x !== it), n_total: q.n_total - 1, n_labeled: q.n_labeled + 1 } : q));
      setPos((p) => Math.min(p, Math.max(0, items.length - 2)));
    },
    [it, busy, items.length, onToast],
  );

  // keyboard: 1–6 labels, → / space skip, ← back
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA')) return;
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
  }, [label, next, prev]);

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
  (window as any).__review = { n: items.length, pos, jobStatus: job?.status ?? null, labels: labels.length };

  const regionLabels = labels.filter((l) => l.region === region);
  const recent = [...labels].reverse().slice(0, 40);
  const res = job?.result;

  return (
    <div className="review" data-testid="review-view">
      {/* ---- left: queue ---- */}
      <aside className="rv-col rv-left glass">
        <div className="eyebrow">Проверка человеком</div>
        <h2 className="big-title">Очередь сомнительных находок</h2>
        <div className="rv-filters">
          <label>
            <span className="muted small">Район</span>
            <select value={region} onChange={(e) => setRegion(e.target.value)} data-testid="review-region">
              {regionsSorted.map((r) => (
                <option key={r.id} value={r.id}>
                  {shortName(r.name)}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="muted small">Модель</span>
            <select value={model} onChange={(e) => setModel(e.target.value)} data-testid="review-model">
              <option value="">все</option>
              {Object.keys(manifest.models).map((m) => (
                <option key={m} value={m}>
                  {modelLabel(m, manifest.models[m]?.name)}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="rv-count" data-testid="review-counter">
          <div>
            <b>{queue ? fmtNum(queue.n_total) : '…'}</b>
            <small>в очереди</small>
          </div>
          <div>
            <b className="accent-text">{fmtNum(regionLabels.length)}</b>
            <small>размечено в районе</small>
          </div>
          <div>
            <b>{fmtNum(session)}</b>
            <small>за эту сессию</small>
          </div>
        </div>
        <p className="muted small rv-rules">
          В очередь попадают: находки около порога (|max P − порог| ≤ 0,15), расхождения MDD и нашей модели, отмеченные «ложное?» на карте.
        </p>
        <div className="rv-list" data-testid="review-list">
          {queue === undefined && <div className="muted small">Загрузка…</div>}
          {queue === null && <div className="muted small">Очередь недоступна (нет ответа /api/review/queue).</div>}
          {queue && !items.length && <div className="muted small">Очередь пуста — всё размечено.</div>}
          {items.slice(0, 80).map((x, i) => (
            <button key={`${x.id}`} className={`rv-item ${i === pos ? 'on' : ''}`} onClick={() => setPos(i)} data-testid={`review-item-${i}`}>
              <span className="rv-item-top">
                <span className="rv-item-t">
                  {fmtDate(x.date)} · {modelLabel(x.model)}
                </span>
                <span className="rv-item-p mono" title="max P">
                  {x.max_prob === null ? '—' : x.max_prob.toFixed(2)}
                </span>
              </span>
              <span className="rv-item-r">
                {x.reasons.map((c) => (
                  <span key={c} className={`rv-tag ${c}`}>
                    {REASON_RU[c] ?? c}
                  </span>
                ))}
              </span>
            </button>
          ))}
        </div>
      </aside>

      {/* ---- centre: card ---- */}
      <section className="rv-center">
        {it ? (
          <div className="rv-card glass" data-testid="review-card">
            <div className="rv-card-head">
              <div>
                <div className="eyebrow">
                  Находка {pos + 1} из {items.length} · {shortName(queue?.region_name ?? region)}
                </div>
                <div className="rv-title">
                  {fmtDate(it.date)} · {modelLabel(it.model, manifest.models[it.model]?.name)} · max P{' '}
                  <b className="accent-text">{it.max_prob === null ? '—' : it.max_prob.toFixed(2)}</b>
                  <span className="muted"> (порог {fmtThr(it.threshold)})</span>
                </div>
              </div>
              <button className="btn small ghost" onClick={() => onShowOnMap(it)} data-testid="review-show-map">
                на карте
              </button>
            </div>
            <div className="rv-reason" data-testid="review-reason">
              <b>Почему в очереди:</b> {it.reason}
            </div>
            <div className="rv-imgs">
              <figure>
                {imgOk.rgb ? (
                  <img src={`${it.crop_rgb}&px=720`} alt="RGB-вырезка" onError={() => setImgOk((s) => ({ ...s, rgb: false }))} data-testid="review-rgb" />
                ) : (
                  <div className="rv-noimg">вырезка недоступна</div>
                )}
                <figcaption>RGB (B4/B3/B2) · 1 × 1 км, контур — находка</figcaption>
              </figure>
              <figure>
                {it.crop_false_color && imgOk.fc ? (
                  <img src={`${it.crop_false_color}&px=720`} alt="Ложный цвет" onError={() => setImgOk((s) => ({ ...s, fc: false }))} data-testid="review-false" />
                ) : (
                  <div className="rv-noimg">нет каналов B8 для этой сцены — только RGB</div>
                )}
                <figcaption>ложный цвет B8/B4/B3: растительность и водоросли — красные, пена — белая</figcaption>
              </figure>
            </div>
            <div className="rv-meta muted small">
              ср. P {it.mean_prob === null ? '—' : it.mean_prob.toFixed(2)} · площадь {fmtNum(it.area_m2)} м² ·{' '}
              {it.confirmed === true ? 'вторая модель согласна' : it.confirmed === false ? 'вторая модель не видит' : 'вторая модель на дате не запускалась'} ·{' '}
              <span className="mono">
                {it.lat.toFixed(5)}, {it.lon.toFixed(5)}
              </span>
            </div>
            <div className="rv-labels" data-testid="review-labels">
              {LABELS.map((l, i) => (
                <button
                  key={l.id}
                  className={`rv-lab ${l.id === 'debris' ? 'accent' : ''}`}
                  onClick={() => label(l.id)}
                  disabled={busy}
                  data-testid={`review-label-${l.id}`}
                >
                  <kbd>{i + 1}</kbd>
                  {l.ru}
                </button>
              ))}
              <button className="rv-lab skip" onClick={next} data-testid="review-skip">
                <kbd>→</kbd>пропустить
              </button>
            </div>
            <div className="muted small rv-hint">Клавиши 1–6 — метка, → или пробел — пропустить, ← — назад. Метка сохраняется с происхождением (сцена, модель, max P, время).</div>
          </div>
        ) : (
          <div className="rv-card glass rv-empty">
            {queue === undefined ? 'Загрузка очереди…' : pos >= items.length && items.length ? 'Конец очереди — можно вернуться клавишей ←.' : 'Нет находок для проверки.'}
          </div>
        )}
      </section>

      {/* ---- right: labels + retrain ---- */}
      <aside className="rv-col rv-right glass">
        <div className="section">
          <div className="section-head">
            <h3>Сделанные метки</h3>
            <span className="muted small">всего {fmtNum(labels.length)}</span>
          </div>
          <div className="rv-done" data-testid="review-done">
            {!recent.length && <div className="muted small">Пока нет меток.</div>}
            {recent.map((l, i) => (
              <div key={`${l.id}-${l.provenance?.ts ?? i}`} className="rv-done-row">
                <span className={`rv-chip ${l.label === 'debris' ? 'accent' : ''}`}>{LABEL_RU[l.label ?? ''] ?? l.label}</span>
                <span className="muted small">
                  {shortName(manifest.regions.find((r) => r.id === l.region)?.name ?? l.region)} · {fmtDate(l.date)} · {modelLabel(l.model)}
                </span>
              </div>
            ))}
          </div>
        </div>
        <div className="section">
          <div className="section-head">
            <h3>Дообучение LightGBM</h3>
          </div>
          <p className="muted small">
            Метки превращаются в пиксели обучения (круг 20 м), модель дообучается на CPU, F1 считается на val MARIDA до и после. Решение — по правилу
            принятия: прирост ≥ 0,01.
          </p>
          <button
            className="btn block accent-outline"
            onClick={retrain}
            disabled={!labels.length || job?.status === 'running'}
            data-testid="review-retrain"
            title={labels.length ? 'Дообучить LightGBM на всех метках (CPU, ≈ 20–60 с)' : 'Сначала поставьте хотя бы одну метку'}
          >
            {job?.status === 'running' ? 'Идёт дообучение…' : 'Дообучить'}
          </button>
          {jobErr && <div className="rv-err small">{jobErr}</div>}
          {job && (
            <div className="rv-job" data-testid="review-job">
              <div className="muted small">
                задача <span className="mono">{job.id}</span> · {job.status === 'running' ? 'идёт' : job.status === 'done' ? 'готово' : job.status}
                {job.n_labels ? ` · меток ${job.n_labels}` : ''}
              </div>
              {job.status === 'running' && <pre className="rv-log">{(job.log_tail ?? '').trim().split('\n').slice(-5).join('\n') || '…'}</pre>}
              {res && !res.error && typeof res.val_f1_before === 'number' && (
                <div className="rv-res" data-testid="review-result">
                  <div className="rv-f1">
                    <div>
                      <small>F1 val до</small>
                      <b>{res.val_f1_before.toFixed(4)}</b>
                    </div>
                    <span className="op">→</span>
                    <div>
                      <small>после</small>
                      <b>{res.val_f1_after?.toFixed(4)}</b>
                    </div>
                    <div>
                      <small>прирост</small>
                      <b className={res.accepted ? 'good-text' : ''}>{(res.gain ?? 0) >= 0 ? '+' : ''}{(res.gain ?? 0).toFixed(4)}</b>
                    </div>
                  </div>
                  <div className={`rv-decision ${res.accepted ? 'ok' : 'no'}`} data-testid="review-decision">
                    {res.accepted ? 'Принято' : 'Не принято'}: {(res.decision ?? '').replace(/^\s*(не\s+)?принято\s*[:—-]?\s*/i, '')}
                  </div>
                  {res.rule && <div className="muted small">Правило: {res.rule}.</div>}
                </div>
              )}
              {job.status !== 'running' && (
                <div className="rv-caveat" data-testid="review-caveat">
                  Метки со снимков L2A почти не влияют на val MARIDA (ACOLITE) — для оценки нужен размеченный набор с живых снимков.
                  {typeof res?.caveat === 'string' && res.caveat ? <div className="muted small">{res.caveat}</div> : null}
                </div>
              )}
              {(res?.error || job.status === 'error') && (
                <div className="rv-err small">Ошибка: {res?.error ?? (job.log_tail ?? '').trim().split('\n').slice(-2).join(' ')}</div>
              )}
            </div>
          )}
          <div className="muted small rv-honest">
            Веса сервиса автоматически не подменяются: новая модель сохраняется отдельно, замену делает человек командой из result.json.
          </div>
        </div>
      </aside>
    </div>
  );
}
