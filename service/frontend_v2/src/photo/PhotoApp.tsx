// L109 (INBOX §15 «Агент 5», §16 п.2, §23 п.4, §24): «Счётчик предметов по фото» — отдельный модуль, НЕ спутниковая задача.
// Загрузка фото → POST /api/v3/photo/count (docs/CONTRACTS_V3.md, 3.10) → рамки + число предметов на кадр.
// Два типа съёмки: «камера у воды» (FML) и «аэро/дрон, надир» (Winans 2023, GSD → площадь кадра → шт./км² на кадре).
// Порог двигается на клиенте: запрос идёт с threshold=0.05, число = рамки со score ≥ порога.
// Поправка на пропуски p(размер) (аэро) считается на клиенте по коэффициентам из /photo/meta — только при пороге по умолчанию.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import './photo.css';

type Survey = 'water_camera' | 'aerial';
type Box = { x1: number; y1: number; x2: number; y2: number; score: number; label: string };
type Composition = { status: string; text: string; reason?: string; by_class?: Record<string, number>; box_materials?: (string | null)[] | null };
type CountInterval = { by_pred_count: { pred_from: number; pred_to: number; q025: number; q975: number }[]; overall_val: number[]; coverage_on_test?: number };
type CountResp = {
  count: number;
  composition?: Composition;
  unit: string;
  threshold: number;
  threshold_default: number;
  model_version: string;
  survey_type?: Survey;
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
type AerialMetric = {
  n_frames: number;
  n_items: number;
  ap50: number;
  count_mae: number;
  count_mae_ci95: number[];
  count_mae_corrected: number;
  baseline_median_mae: number;
  density_mae_km2: number;
  true_km2: number;
  raw_km2: number;
  corrected_km2: number;
  corrected_km2_ci95: number[];
  note?: string;
};
type SurveyMeta = {
  label: string;
  available: boolean;
  version?: string;
  threshold?: number;
  gsd_train_m?: number;
  dataset?: string;
  weights_license?: string;
  metrics?: AerialMetric;
  correction?: { bins: string[]; bins_cm: number[]; factor: number[]; factor_ci95: number[][] };
  baseline?: { name: string; status: string };
  count_interval?: CountInterval;
  limitations: string[];
};
type Meta = {
  available: boolean;
  survey: string;
  unit: string;
  density_note: string;
  limitations: string[];
  surveys?: { water_camera: SurveyMeta; aerial: SurveyMeta };
  model: null | {
    version: string;
    threshold: number;
    dataset: string;
    weights_license: string;
    metrics: { test_official?: Metric; test_grouped?: Metric };
    caveat?: string;
  };
};

type Sample = { src: string; label: string; domain: string; survey: Survey; gsd?: number; truth: number; warn?: string; license: string };
const SAMPLES: Sample[] = [
  { src: 'photo_samples/fml_1.jpg', label: 'Море · FML', domain: 'камера у воды (надводный аппарат), Адриатика', survey: 'water_camera', truth: 6, license: 'FML, CC BY 4.0' },
  {
    src: 'photo_samples/tocl_1.jpg',
    label: 'Река · TOCL',
    domain: 'камера над рекой Кланг, почти надир, кадр 50,8 м²',
    survey: 'aerial',
    gsd: 0.006,
    truth: 14,
    warn: 'Модель на этот домен не перенесена: на 97 отложенных кадрах TOCL найдено 0,045 шт./м² при истинных 0,164 — число ниже не измерение.',
    license: 'The Ocean Cleanup RMS, CC BY-NC 4.0 — только исследовательское использование',
  },
  { src: 'photo_samples/winans_2.jpg', label: 'Берег · Winans', domain: 'аэрофото, надир, берег Гавайев, GSD 0,02 м (163,8 м²)', survey: 'aerial', gsd: 0.02, truth: 9, license: 'Winans 2023, CC BY 4.0' },
  { src: 'photo_samples/fml_2.jpg', label: 'Море 2', domain: 'камера у воды, Адриатика', survey: 'water_camera', truth: 3, license: 'FML, CC BY 4.0' },
  { src: 'photo_samples/winans_1.jpg', label: 'Берег 2', domain: 'аэрофото, надир, берег, GSD 0,02 м', survey: 'aerial', gsd: 0.02, truth: 6, license: 'Winans 2023, CC BY 4.0' },
];
const API = '/api/v3/photo';
const f2 = (x: number | undefined | null, d = 2) => (x == null || !isFinite(x) ? '—' : x.toFixed(d).replace('.', ','));
const pct = (x: number | undefined | null) => (x == null ? '—' : `${Math.round(x * 100)} %`);
const ci = (c?: number[], d = 2, p = false) => (c && c.length === 2 ? ` [${p ? pct(c[0]) : f2(c[0], d)} – ${p ? pct(c[1]) : f2(c[1], d)}]` : '');
const numRu = (x: number) => x.toLocaleString('ru-RU', { maximumFractionDigits: 0 });
const BINS_CM = [0, 30, 60, 120, 1e9];

function countInterval(ci: CountInterval | undefined, n: number): [number, number] | null {
  if (!ci) return null;
  const row = ci.by_pred_count.find((r) => r.pred_from <= n && n <= r.pred_to);
  const [lo, hi] = row ? [row.q025, row.q975] : ci.overall_val;
  return [Math.max(0, n + lo), Math.max(0, n + hi)];
}

function corrected(boxes: Box[], gsd: number, factor: number[]): number {
  let s = 0;
  for (const b of boxes) {
    const side = Math.max(b.x2 - b.x1, b.y2 - b.y1) * gsd * 100;
    let k = 0;
    while (k < factor.length - 1 && side >= BINS_CM[k + 1]) k++;
    s += factor[k];
  }
  return s;
}

export default function PhotoApp() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [metaErr, setMetaErr] = useState<string | null>(null);
  const [survey, setSurvey] = useState<Survey>(() => (new URLSearchParams(location.search).get('survey') === 'aerial' ? 'aerial' : 'water_camera'));
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [blob, setBlob] = useState<Blob | null>(null);
  const [name, setName] = useState<string>('');
  const [res, setRes] = useState<CountResp | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [thr, setThr] = useState<number | null>(null);
  const [area, setArea] = useState<string>('');
  const [gsd, setGsd] = useState<string>('');
  const [sample, setSample] = useState<Sample | null>(null);
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetch(`${API}/meta`)
      .then((r) => (r.ok ? r.json() : r.json().then((j) => Promise.reject(j?.error?.message ?? `HTTP ${r.status}`))))
      .then(setMeta)
      .catch((e) => setMetaErr(typeof e === 'string' ? e : 'API /api/v3/photo не отвечает'));
  }, []);

  const send = useCallback(async (b: Blob, label: string, sv: Survey) => {
    setBusy(true);
    setErr(null);
    setRes(null);
    setName(label);
    setBlob(b);
    setImgUrl((old) => {
      if (old) URL.revokeObjectURL(old);
      return URL.createObjectURL(b);
    });
    try {
      const r = await fetch(`${API}/count?threshold=0.05${sv === 'aerial' ? '&survey=aerial' : ''}`, {
        method: 'POST',
        headers: { 'Content-Type': b.type || 'application/octet-stream' },
        body: b,
      });
      const j = await r.json().catch(() => null);
      if (!r.ok) throw new Error(j?.error?.message ?? `HTTP ${r.status}`);
      setRes(j as CountResp);
      setThr((j as CountResp).threshold_default);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  const switchSurvey = (sv: Survey) => {
    if (sv === survey) return;
    setSurvey(sv);
    setThr(null);
    if (blob) void send(blob, name, sv);
  };
  const onFiles = (fl: FileList | null) => {
    const f = fl?.[0];
    if (!f) return;
    if (!f.type.startsWith('image/')) {
      setErr('Нужен файл изображения (JPEG/PNG/WebP)');
      return;
    }
    setSample(null);
    void send(f, f.name, survey);
  };
  const loadSample = async (smp: Sample) => {
    try {
      const b = await fetch(smp.src).then((r) => (r.ok ? r.blob() : Promise.reject(new Error(`нет файла ${smp.src}`))));
      setSample(smp);
      setSurvey(smp.survey);
      setThr(null);
      setArea('');
      setGsd(smp.gsd ? String(smp.gsd).replace('.', ',') : '');
      void send(b, smp.label, smp.survey);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  // пример открыт сразу (INBOX §31 п.2 в)
  const booted = useRef(false);
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    const q = new URLSearchParams(location.search).get('sample');
    const smp = SAMPLES.find((x) => x.label === q) ?? (survey === 'aerial' ? SAMPLES[2] : SAMPLES[0]);
    void loadSample(smp);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sm = meta?.surveys?.[survey];
  const t = thr ?? res?.threshold_default ?? sm?.threshold ?? meta?.model?.threshold ?? 0.9;
  const shown = useMemo(() => (res ? res.boxes.filter((b) => b.score >= t) : []), [res, t]);
  const atDefault = !!res && Math.abs(t - res.threshold_default) < 1e-9;
  const num = (s: string) => Number(s.replace(',', '.'));
  const gsdM = num(gsd);
  const gsdOk = gsd !== '' && isFinite(gsdM) && gsdM > 0;
  const areaIn = num(area);
  const areaM2 = area !== '' && isFinite(areaIn) && areaIn > 0 ? areaIn : res && gsdOk ? res.image.width * res.image.height * gsdM * gsdM : NaN;
  const areaOk = !!res && isFinite(areaM2) && areaM2 > 0;
  const dens = areaOk ? shown.length / (areaM2 / 1e6) : null;
  const cInt = res && atDefault && !sample?.warn ? countInterval(sm?.count_interval, shown.length) : null;
  const corr = survey === 'aerial' && areaOk && gsdOk && atDefault && !sample?.warn && sm?.correction ? sm.correction : null;
  const cN = corr ? corrected(shown, gsdM, corr.factor) : null;
  const cLo = corr ? corrected(shown, gsdM, corr.factor_ci95.map((x) => x[0])) : null;
  const cHi = corr ? corrected(shown, gsdM, corr.factor_ci95.map((x) => x[1])) : null;
  const g0 = sm?.gsd_train_m ?? 0.02;
  const gsdWarn = survey === 'aerial' && gsdOk && (gsdM < g0 / 2 || gsdM > g0 * 2);
  const f3 = (x: number) => f2(x, x < 1 ? 3 : 2);
  const mo = meta?.model?.metrics?.test_official;
  const mg = meta?.model?.metrics?.test_grouped;
  const ma = meta?.surveys?.aerial?.metrics;
  const aerialOff = meta?.surveys && !meta.surveys.aerial.available;

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
          <span className="ph-sub">отдельный модуль · не спутник: Sentinel-2 (10 м) отдельные предметы так не считает</span>
        </div>
      </header>

      <main className="ph-main">
        <section className="ph-view">
          <div className="ph-actions">
            <div className="seg" role="tablist" aria-label="Тип съёмки" data-testid="photo-survey">
              <button className={survey === 'water_camera' ? 'on' : ''} onClick={() => switchSurvey('water_camera')} data-testid="survey-water">
                Камера у воды
              </button>
              <button className={survey === 'aerial' ? 'on' : ''} onClick={() => switchSurvey('aerial')} disabled={!!aerialOff} data-testid="survey-aerial" title={aerialOff ? 'нет весов аэро-модели' : ''}>
                Аэро / дрон (надир)
              </button>
            </div>
            <span className="faint ph-hint">{sm?.label ?? meta?.survey}</span>
          </div>
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
                <div className="ph-empty-s">
                  {survey === 'water_camera'
                    ? 'Похожие снимки: вода с камеры у поверхности (катер, надводный аппарат, берег).'
                    : 'Снимок сверху (дрон/самолёт, надир), берег; укажите GSD — размер пикселя на земле.'}{' '}
                  JPEG/PNG/WebP до 25 МБ.
                </div>
              </div>
            )}
          </div>
          <div className="ph-actions">
            <button className="btn primary" onClick={() => fileRef.current?.click()} disabled={busy} data-testid="photo-pick">
              Загрузить фото
            </button>
            <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => onFiles(e.target.files)} data-testid="photo-file" />
            <div className="seg ph-samples" role="tablist" aria-label="Примеры" data-testid="photo-samples">
              {SAMPLES.map((x) => (
                <button
                  key={x.src}
                  className={sample?.src === x.src ? 'on' : ''}
                  onClick={() => loadSample(x)}
                  disabled={busy || (x.survey === 'aerial' && !!aerialOff)}
                  title={x.survey === 'aerial' && aerialOff ? 'нет весов аэро-модели на этой установке' : x.domain}
                  data-testid="photo-sample"
                >
                  {x.label}
                </button>
              ))}
            </div>
            {!sample && name && <span className="ph-name faint">{name}</span>}
          </div>
          {sample && (
            <div className="ph-sample-info faint" data-testid="photo-sample-info">
              Пример: {sample.domain} · разметка: {sample.truth} предм. · {sample.license}
            </div>
          )}
          {sample?.warn && (
            <div className="ph-err" data-testid="photo-sample-warn">
              {sample.warn}
            </div>
          )}
          {err && (
            <div className="ph-err" role="alert" data-testid="photo-error">
              {err}
            </div>
          )}
        </section>

        <aside className="ph-side">
          <div className="ph-count" data-testid="photo-count">
            <div className="ph-n">{res ? shown.length : '—'}</div>
            <div className="ph-unit">
              предметов на кадр (найдено)
              <div className="ph-src" data-testid="photo-source">
                {sample?.warn ? 'исследовательская оценка — модель на этот домен не перенесена' : 'посчитано по детальному фото'}
              </div>
              {cInt && (
                <div className="faint ph-hint" data-testid="photo-count-interval">
                  истинное число, 95 %: {f2(cInt[0], 0)}–{f2(cInt[1], 0)} (по ошибкам на отложенных кадрах)
                </div>
              )}
            </div>
          </div>
          {res?.composition && (
            <div className="faint ph-hint" data-testid="photo-composition">
              Состав:{' '}
              {res.composition.status === 'by_class' && res.composition.box_materials
                ? Object.entries(
                    res.boxes.reduce<Record<string, number>>((acc, b, i) => {
                      if (b.score < t) return acc;
                      const m = res.composition!.box_materials![i] ?? 'состав не определён';
                      acc[m] = (acc[m] ?? 0) + 1;
                      return acc;
                    }, {}),
                  )
                    .map(([k, v]) => `${k} ${v}`)
                    .join(', ')
                : `${res.composition.text}${res.composition.reason ? ` — ${res.composition.reason}` : ''}`}
            </div>
          )}
          <label className="ph-row">
            <span>
              Порог уверенности <b>{f2(t)}</b>
              {res && !atDefault && (
                <button className="ph-link" onClick={() => setThr(res.threshold_default)}>
                  сбросить к {f2(res.threshold_default)}
                </button>
              )}
            </span>
            <input type="range" min={0.05} max={0.95} step={0.05} value={t} onChange={(e) => setThr(Number(e.target.value))} data-testid="photo-thr" />
            <span className="faint ph-hint">По умолчанию {f2(res?.threshold_default ?? sm?.threshold ?? meta?.model?.threshold)} — выбран на отложенных кадрах val по ошибке числа предметов.</span>
          </label>
          <div className="ph-row">
            <span>Площадь кадра — м² или GSD (размер пикселя на земле, м)</span>
            <div className="ph-inrow">
              <input className="ph-in" inputMode="decimal" placeholder="площадь, м²" value={area} onChange={(e) => setArea(e.target.value)} data-testid="photo-area" />
              <span className="faint">или</span>
              <input className="ph-in" inputMode="decimal" placeholder="GSD, м" value={gsd} onChange={(e) => setGsd(e.target.value)} data-testid="photo-gsd" />
            </div>
            {res && areaOk && area === '' && gsdOk && (
              <span className="faint ph-hint">
                {res.image.width}×{res.image.height} px × GSD² = {f2(areaM2, 1)} м²
              </span>
            )}
            {gsdWarn && <span className="ph-err">GSD вне проверенного диапазона ({f2(g0 / 2)}–{f2(g0 * 2)} м) — результат не проверен.</span>}
          </div>
          {dens != null ? (
            <div className="ph-dens" data-testid="photo-density">
              <div>
                <b>{f3(shown.length / areaM2)}</b> шт./м² · <b>{numRu(dens)}</b> шт./км² — на площади кадра, не спутник
                <div className="ph-src">
                  {sample?.warn ? 'исследовательская оценка — не измерение' : `посчитано по детальному фото; площадь ${area !== '' ? 'задана вручную' : 'из GSD'}`}
                </div>
              </div>
              {cInt && (
                <div data-testid="photo-density-interval">
                  интервал по ошибке числа: {f3(cInt[0] / areaM2)}–{f3(cInt[1] / areaM2)} шт./м² ({numRu(cInt[0] / (areaM2 / 1e6))}–{numRu(cInt[1] / (areaM2 / 1e6))} шт./км²)
                </div>
              )}
              {cN != null && cLo != null && cHi != null && (
                <div data-testid="photo-density-corrected">
                  с поправкой на пропуски p(размер): ≈ {numRu(cN / (areaM2 / 1e6))} шт./км² [{numRu(cLo / (areaM2 / 1e6))} – {numRu(cHi / (areaM2 / 1e6))}]
                </div>
              )}
              {!atDefault && <div className="faint ph-hint">Интервал и поправка — только при пороге по умолчанию.</div>}
              {sample?.warn && <div className="ph-err">Не измерение: модель на этот домен не перенесена.</div>}
            </div>
          ) : (
            <span className="faint ph-hint">Без площади кадра — только штуки на кадр (у FML площади кадра нет).</span>
          )}
          {res && (
            <div className="ph-kv faint" data-testid="photo-model">
              Модель {res.model_version} · {res.device === 'cuda' ? 'GPU' : 'CPU'} · {f2(res.elapsed_ms / 1000, 1)} с
            </div>
          )}

          <div className="ph-block">
            <div className="ph-h">Качество (отложенный тест, IoU 0,5)</div>
            {metaErr && <div className="ph-err">{metaErr}</div>}
            {survey === 'water_camera' && mg && (
              <div className="ph-m" data-testid="photo-metric-grouped">
                <b>Эта модель, отложенные сессии съёмки</b> ({mg.n_images} фото): mAP@0,5 {f2(mg.ap50)}
                {ci(mg.ap50_ci95)}; ошибка числа {f2(mg.count_mae)}
                {ci(mg.count_mae_ci95)} шт./кадр; точное число {pct(mg.count_exact)}
                {ci(mg.count_exact_ci95, 2, true)}
              </div>
            )}
            {survey === 'water_camera' && mo && (
              <div className="ph-m" data-testid="photo-metric-official">
                <b>Для сравнения: веса авторов FML, их test</b> ({mo.n_images} фото): mAP@0,5 {f2(mo.ap50)}
                {ci(mo.ap50_ci95)}; ошибка числа {f2(mo.count_mae)}
                {ci(mo.count_mae_ci95)}; точное число {pct(mo.count_exact)}
                {ci(mo.count_exact_ci95, 2, true)}
                <div className="faint ph-hint">Соседние кадры видео с обучением (~90 % в пределах 2 с) — оценка оптимистична; опора — строка выше.</div>
              </div>
            )}
            {survey === 'aerial' && ma && (
              <div className="ph-m" data-testid="photo-metric-aerial">
                <b>Отложенные участки берега</b> ({ma.n_frames} кадров по {f2(163.84, 1)} м², {ma.n_items} предметов): mAP@0,5 {f2(ma.ap50)}; ошибка числа{' '}
                {f2(ma.count_mae)}
                {ci(ma.count_mae_ci95)} шт./кадр (медиана без модели — {f2(ma.baseline_median_mae)}); ошибка плотности {numRu(ma.density_mae_km2)} шт./км² на кадр.
                <div>
                  По всей отложенной площади: истинно {numRu(ma.true_km2)} шт./км²; найдено {numRu(ma.raw_km2)}; с поправкой {numRu(ma.corrected_km2)}
                  {ma.corrected_km2_ci95 ? ` [${numRu(ma.corrected_km2_ci95[0])} – ${numRu(ma.corrected_km2_ci95[1])}]` : ''}.
                </div>
                {ma.note && <div className="faint ph-hint">{ma.note}</div>}
              </div>
            )}
            {survey === 'aerial' && meta?.surveys?.aerial?.baseline && (
              <div className="faint ph-hint" data-testid="photo-baseline">
                Бейзлайн {meta.surveys.aerial.baseline.name}: {meta.surveys.aerial.baseline.status}
              </div>
            )}
          </div>

          <div className="ph-block">
            <div className="ph-h">Ограничения</div>
            <ul className="ph-lim" data-testid="photo-limits">
              {(res?.limitations ?? sm?.limitations ?? meta?.limitations ?? []).map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
            <div className="faint ph-hint">
              {survey === 'water_camera'
                ? `Данные: FML (Lazzerini и др., SEANOE doi:10.17882/106148, CC BY 4.0). Веса: ${meta?.model?.version ?? '—'}.`
                : `Данные: Winans и др. 2023 (Zenodo 8381113, CC BY 4.0). Веса: ${sm?.version ?? '—'}.`}
            </div>
          </div>
        </aside>
      </main>
    </div>
  );
}
