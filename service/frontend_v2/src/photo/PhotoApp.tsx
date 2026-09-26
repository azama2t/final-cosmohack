// L109 (INBOX §15 «Агент 5», §16 п.2): «Счётчик предметов по фото» — отдельный модуль, НЕ спутниковая задача.
// Загрузка фото → POST /api/v3/photo/count (docs/CONTRACTS_V3.md, 3.10) → рамки + число предметов на кадр.
// Порог двигается на клиенте: запрос идёт с threshold=0.05, число = рамки со score ≥ порога.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './photo.css';

type Box = { x1: number; y1: number; x2: number; y2: number; score: number; label: string };
type CountResp = {
  count: number;
  unit: string;
  threshold: number;
  threshold_default: number;
  model_version: string;
  image: { width: number; height: number };
  boxes: Box[];
  survey: string;
  limitations: string[];
  device: string;
  elapsed_ms: number;
};
type Metric = {
  n_images: number;
  ap50: number;
  ap50_ci95?: number[];
  count_mae: number;
  count_mae_ci95?: number[];
  count_exact: number;
  count_exact_ci95?: number[];
  note?: string;
};
type Meta = {
  available: boolean;
  survey: string;
  unit: string;
  density_note: string;
  limitations: string[];
  model: null | {
    version: string;
    threshold: number;
    dataset: string;
    weights_license: string;
    metrics: { test_official?: Metric; test_grouped?: Metric };
    caveat?: string;
  };
};

const SAMPLES = [
  { src: 'photo_samples/fml_1.jpg', label: 'Пример 1' },
  { src: 'photo_samples/fml_2.jpg', label: 'Пример 2' },
  { src: 'photo_samples/fml_3.jpg', label: 'Пример 3' },
];
const API = '/api/v3/photo';
const f2 = (x: number | undefined | null, d = 2) => (x == null || !isFinite(x) ? '—' : x.toFixed(d).replace('.', ','));
const pct = (x: number | undefined | null) => (x == null ? '—' : `${Math.round(x * 100)} %`);
const ci = (c?: number[], d = 2, p = false) => (c && c.length === 2 ? ` [${p ? pct(c[0]) : f2(c[0], d)} – ${p ? pct(c[1]) : f2(c[1], d)}]` : '');
const numRu = (x: number) => x.toLocaleString('ru-RU', { maximumFractionDigits: 0 });

export default function PhotoApp() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaErr, setMetaErr] = useState<string | null>(null);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [name, setName] = useState<string>('');
  const [res, setRes] = useState<CountResp | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [thr, setThr] = useState<number | null>(null);
  const [area, setArea] = useState<string>('');
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch(`${API}/meta`)
      .then((r) => (r.ok ? r.json() : r.json().then((j) => Promise.reject(j?.error?.message ?? `HTTP ${r.status}`))))
      .then(setMeta)
      .catch((e) => setMetaErr(typeof e === 'string' ? e : 'API /api/v3/photo не отвечает'));
  }, []);

  const send = useCallback(async (blob: Blob, label: string) => {
    setBusy(true);
    setErr(null);
    setRes(null);
    setName(label);
    setImgUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return URL.createObjectURL(blob);
    });
    try {
      const r = await fetch(`${API}/count?threshold=0.05`, {
        method: 'POST',
        headers: { 'Content-Type': blob.type || 'application/octet-stream' },
        body: blob,
      });
      const j = await r.json().catch(() => null);
      if (!r.ok) throw new Error(j?.error?.message ?? `HTTP ${r.status}`);
      setRes(j as CountResp);
      setThr((t) => (t == null ? (j as CountResp).threshold_default : t));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const onFiles = (fl: FileList | null) => {
    const f = fl?.[0];
    if (!f) return;
    if (!f.type.startsWith('image/')) {
      setErr('Нужен файл изображения (JPEG/PNG/WebP)');
      return;
    }
    void send(f, f.name);
  };
  const loadSample = async (src: string, label: string) => {
    try {
      const b = await fetch(src).then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`нет файла ${src}`))));
      void send(b, label);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };

  const t = thr ?? res?.threshold_default ?? meta?.model?.threshold ?? 0.9;
  const shown = useMemo(() => (res ? res.boxes.filter((b) => b.score >= t) : []), [res, t]);
  const areaM2 = Number(area.replace(',', '.'));
  const dens = res && area && isFinite(areaM2) && areaM2 > 0 ? shown.length / (areaM2 / 1e6) : null;
  const mo = meta?.model?.metrics?.test_official;
  const mg = meta?.model?.metrics?.test_grouped;

  return (
    <div className="ph-app" data-testid="photo-app">
      <header className="ph-top">
        <div className="seg" role="tablist" aria-label="Режим">
          <button onClick={() => (location.href = '?')} data-testid="mode-case">
            Кейс
          </button>
          <button onClick={() => (location.href = '?mode=live')} data-testid="mode-live">
            Живые снимки
          </button>
          <button className="on" aria-selected data-testid="mode-photo">
            Фото
          </button>
        </div>
        <div className="ph-title">
          Счётчик предметов по фото
          <span className="ph-sub">отдельный модуль · не спутник · камера у воды</span>
        </div>
      </header>

      <main className="ph-main">
        <section className="ph-view">
          <div
            className={`ph-drop ${drag ? 'drag' : ''} ${imgUrl ? 'has' : ''}`}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              onFiles(e.dataTransfer.files);
            }}
            data-testid="photo-drop"
          >
            {imgUrl ? (
              <div className="ph-stage">
                <img src={imgUrl} alt={name} className="ph-img" data-testid="photo-img" />
                {res && (
                  <svg className="ph-svg" viewBox={`0 0 ${res.image.width} ${res.image.height}`} preserveAspectRatio="none" data-testid="photo-boxes">
                    {shown.map((b, i) => (
                      <g key={i}>
                        <rect x={b.x1} y={b.y1} width={b.x2 - b.x1} height={b.y2 - b.y1} className="ph-box" vectorEffect="non-scaling-stroke" />
                        <text x={b.x1} y={Math.max(b.y1 - 6, 14)} className="ph-lbl" fontSize={Math.max(res.image.height / 45, 12)}>
                          {f2(b.score)}
                        </text>
                      </g>
                    ))}
                  </svg>
                )}
                {busy && <div className="ph-busy">Считаю…</div>}
              </div>
            ) : (
              <div className="ph-empty">
                <div className="ph-empty-t">Перетащите фото сюда или выберите файл</div>
                <div className="ph-empty-s">Похожие снимки: вода с камеры у поверхности (катер, надводный аппарат, берег). JPEG/PNG/WebP до 25 МБ.</div>
              </div>
            )}
          </div>
          <div className="ph-actions">
            <button className="btn primary" onClick={() => fileRef.current?.click()} disabled={busy} data-testid="photo-pick">
              Загрузить фото
            </button>
            <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => onFiles(e.target.files)} data-testid="photo-file" />
            {SAMPLES.map((s) => (
              <button key={s.src} className="btn ghost sm" onClick={() => loadSample(s.src, s.label)} disabled={busy} data-testid="photo-sample">
                {s.label}
              </button>
            ))}
            {name && <span className="ph-name faint">{name}</span>}
          </div>
          {err && (
            <div className="ph-err" role="alert" data-testid="photo-error">
              {err}
            </div>
          )}
        </section>

        <aside className="ph-side">
          <div className="ph-count" data-testid="photo-count">
            <div className="ph-n">{res ? shown.length : '—'}</div>
            <div className="ph-unit">предметов на кадр</div>
          </div>
          <label className="ph-row">
            <span>
              Порог уверенности <b>{f2(t)}</b>
              {res && Math.abs(t - res.threshold_default) > 1e-9 && (
                <button className="ph-link" onClick={() => setThr(res.threshold_default)}>
                  сбросить к {f2(res.threshold_default)}
                </button>
              )}
            </span>
            <input type="range" min={0.05} max={0.95} step={0.05} value={t} onChange={(e) => setThr(Number(e.target.value))} data-testid="photo-thr" />
            <span className="faint ph-hint">По умолчанию {f2(res?.threshold_default ?? meta?.model?.threshold)} — выбран на val FML по ошибке числа предметов.</span>
          </label>
          <label className="ph-row">
            <span>Площадь воды в кадре, м² (если известна)</span>
            <input className="ph-in" inputMode="decimal" placeholder="не задана" value={area} onChange={(e) => setArea(e.target.value)} data-testid="photo-area" />
            {dens != null ? (
              <span className="ph-dens" data-testid="photo-density">
                ≈ {numRu(dens)} шт./км² — на площади кадра, не спутник
              </span>
            ) : (
              <span className="faint ph-hint">Без известной площади кадра — только штуки на кадр (у FML площади кадра нет).</span>
            )}
          </label>
          {res && (
            <div className="ph-kv faint" data-testid="photo-model">
              Модель {res.model_version} · {res.device === 'cuda' ? 'GPU' : 'CPU'} · {f2(res.elapsed_ms / 1000, 1)} с
            </div>
          )}

          <div className="ph-block">
            <div className="ph-h">Качество (отложенный тест, IoU 0,5)</div>
            {metaErr && <div className="ph-err">{metaErr}</div>}
            {mg && (
              <div className="ph-m" data-testid="photo-metric-grouped">
                <b>Новые сессии съёмки</b> ({mg.n_images} фото): mAP@0,5 {f2(mg.ap50)}
                {ci(mg.ap50_ci95)}; ошибка числа {f2(mg.count_mae)}
                {ci(mg.count_mae_ci95)} шт./кадр; точное число {pct(mg.count_exact)}
                {ci(mg.count_exact_ci95, 2, true)}
              </div>
            )}
            {mo && (
              <div className="ph-m" data-testid="photo-metric-official">
                <b>Официальный test FML</b> ({mo.n_images} фото): mAP@0,5 {f2(mo.ap50)}
                {ci(mo.ap50_ci95)}; ошибка числа {f2(mo.count_mae)}
                {ci(mo.count_mae_ci95)}; точное число {pct(mo.count_exact)}
                {ci(mo.count_exact_ci95, 2, true)}
                <div className="faint ph-hint">Соседние кадры видео с обучением (~90 % в пределах 2 с) — оценка оптимистична.</div>
              </div>
            )}
          </div>

          <div className="ph-block">
            <div className="ph-h">Ограничения</div>
            <ul className="ph-lim" data-testid="photo-limits">
              {(res?.limitations ?? meta?.limitations ?? []).map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
            <div className="faint ph-hint">
              Данные: FML (Lazzerini и др., SEANOE doi:10.17882/106148, CC BY 4.0). Веса: {meta?.model?.version ?? '—'}.
            </div>
          </div>
        </aside>
      </main>
    </div>
  );
}
