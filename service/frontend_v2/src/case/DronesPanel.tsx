// L151 · §54 п.2 «Дроны»: detailed frames (drone / aircraft / vessel) — NOT satellite.
// Sets -> frame grid -> frame with the set's own boxes/polygons, class legend, items per frame, units, source.
// Data: GET /api/v3/drones, /api/v3/drones/{set}/frames (service/routes_drones.py, data/case/drones/index.json).
// Honesty: шт./м² and шт./км² only when the frame area is known; our counter's boxes only where its error was checked.
import { useEffect, useMemo, useState } from 'react';
import { get } from './api3';
import './drones.css';

export type Group = 'plastic' | 'algae' | 'wood' | 'other';
export interface DroneObj {
  cls: string;
  group: Group;
  bbox: [number, number, number, number];
  poly?: [number, number][];
}
/** §56: our photo counter on this frame (scripts/case/drones_pred.py -> data/case/drones/pred.json) */
export interface DroneModelFrame {
  n: number;
  boxes: [number, number, number, number][];
  scores: number[];
  conf_mean: number | null;
  conf_min: number | null;
  tp?: number;
  fp?: number;
  fn?: number;
  n_labelled?: number;
  match?: number[];
}
export interface DroneModelSet {
  survey: string;
  model: string;
  threshold: number;
  iou: number;
  trained_on_this_set: boolean;
  training_note: string;
  checked_metric: { count_mae_per_frame: number; n_test_frames: number; source: string } | null;
  summary: { frames: number; n_pred: number; found: number | null; labelled: number | null; false: number | null; note?: string };
}
export interface DroneFrame {
  id: string;
  image: string;
  source_file: string;
  width: number;
  height: number;
  orig_width: number;
  orig_height: number;
  objects: DroneObj[] | null;
  n_objects: number | null;
  n_by_group: Partial<Record<Group, number>> | null;
  frame_area_m2: number | null;
  density_m2: number | null;
  density_km2: number | null;
  area_note: string | null;
  lat?: number;
  lon?: number;
  date?: string;
  station?: number;
  subregion?: string;
  published?: { density_m2: number; density_km2: number; scope: string; per_frame_expected: number; per_frame_note: string } | null;
  model?: DroneModelFrame | null;
  sensor?: string;
  date_note?: string;
}
export interface DroneSet {
  id: string;
  name: string;
  sensor: 'drone' | 'aircraft' | 'vessel' | 'shore';
  sensor_label: string;
  view: string;
  region: string;
  center: [number, number] | null;
  license: string;
  link: string;
  catalog_row: string;
  dataset_size: string;
  classes_src: string[];
  class_groups: Record<string, Group>;
  gsd_m: number | null;
  frame_area_m2: number | null;
  note: string;
  n_frames: number;
  groups_labelled: Group[];
  algae_note: string;
  model?: DroneModelSet | null;
  cover: string | null;
}
interface SetsResp {
  banner: string;
  groups: Record<Group, string>;
  not_included: { what: string; why: string }[];
  sets: DroneSet[];
}
interface FramesResp {
  set: DroneSet;
  banner: string;
  frames: DroneFrame[];
}

export const BANNER = 'Дрон, не спутник — со спутника считаются зоны, не отдельные предметы';
export const GROUP_LABEL: Record<Group, string> = { plastic: 'пластик', algae: 'водоросли', wood: 'дерево', other: 'прочее' };
const GROUP_ORDER: Group[] = ['plastic', 'algae', 'wood', 'other'];
const SENSOR_SHORT: Record<DroneSet['sensor'], string> = { drone: 'дрон', aircraft: 'самолёт', vessel: 'судно', shore: 'берег' };

const nf = (v: number, d = 0) => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: d, minimumFractionDigits: 0 }).format(v);
const dens = (v: number) => (v >= 1 ? nf(v, 2) : nf(v, 3));

function Legend({ f, set }: { f: DroneFrame; set: DroneSet }) {
  const by = f.n_by_group ?? {};
  return (
    <div className="dr-legend" data-testid="drones-legend">
      {GROUP_ORDER.map((g) => {
        const labelled = set.groups_labelled.includes(g);
        const n = by[g] ?? 0;
        return (
          <span key={g} className={`dr-lg ${labelled ? '' : 'off'}`} title={labelled ? `${GROUP_LABEL[g]}: размечено в наборе` : `${GROUP_LABEL[g]}: в этом наборе не размечено`}>
            <i className={`dr-sw g-${g}`} aria-hidden />
            {GROUP_LABEL[g]} {labelled ? <b>{n}</b> : <em>не размечено</em>}
          </span>
        );
      })}
    </div>
  );
}

function srcClasses(f: DroneFrame): string {
  const m = new Map<string, number>();
  for (const o of f.objects ?? []) m.set(o.cls, (m.get(o.cls) ?? 0) + 1);
  return [...m.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([c, n]) => `${c} ${n}`)
    .join(' · ');
}

export function modelSummaryText(m: DroneModelSet): string {
  const s = m.summary;
  if (s.found == null) return `модель нашла ${s.n_pred} предм. на ${s.frames} кадрах (рамок в наборе нет — сверить нельзя)`;
  return `модель нашла ${s.found} из ${s.labelled} размеченных, ложных ${s.false}`;
}
const rule = (m: DroneModelSet) => `совпадение — IoU ≥ ${nf(m.iou, 2)}, порог уверенности ${nf(m.threshold, 2)}`;

function FrameView({ set, frames, idx, onIdx, onBack }: { set: DroneSet; frames: DroneFrame[]; idx: number; onIdx: (i: number) => void; onBack: () => void }) {
  const f = frames[idx];
  const [boxes, setBoxes] = useState<'model' | 'labels' | 'both'>(f.model ? 'model' : 'labels');
  const showLab = boxes !== 'model';
  const showPred = boxes !== 'labels';
  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if (e.key === 'ArrowRight') onIdx(Math.min(frames.length - 1, idx + 1));
      if (e.key === 'ArrowLeft') onIdx(Math.max(0, idx - 1));
    };
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, [idx, frames.length, onIdx]);
  const p = f.model ?? null;
  const ms = set.model ?? null;
  return (
    <div className="dr-frame" data-testid="drones-frame">
      <div className="dr-frame-nav">
        <button className="btn sm ghost" onClick={onBack} data-testid="drones-back-grid">
          ← Кадры набора
        </button>
        <span className="faint">
          кадр {idx + 1} из {frames.length}
        </span>
        <span className="dr-grow" />
        <button className="btn sm ghost" disabled={idx === 0} onClick={() => onIdx(idx - 1)} aria-label="Предыдущий кадр">
          ‹
        </button>
        <button className="btn sm ghost" disabled={idx === frames.length - 1} onClick={() => onIdx(idx + 1)} aria-label="Следующий кадр" data-testid="drones-next">
          ›
        </button>
      </div>
      <div className="dr-frame-main">
        <div className="dr-imgwrap" style={{ aspectRatio: `${f.width} / ${f.height}`, ['--ar' as any]: f.width / f.height }}>
          <img src={f.image} alt={`${set.name}: ${f.source_file}`} />
          <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="dr-ov" aria-hidden>
            {showLab &&
              (f.objects ?? []).map((o, i) =>
                o.poly ? (
                  <polygon key={i} className={`dr-shape g-${o.group}`} points={o.poly.map(([x, y]) => `${x},${y}`).join(' ')} vectorEffect="non-scaling-stroke" />
                ) : (
                  <rect key={i} className={`dr-shape g-${o.group}`} x={o.bbox[0]} y={o.bbox[1]} width={o.bbox[2]} height={o.bbox[3]} vectorEffect="non-scaling-stroke" />
                ),
              )}
            {showPred &&
              p &&
              p.boxes.map((b, i) => (
                <rect key={`p${i}`} className={`dr-shape pred ${p.match && !p.match[i] ? 'fp' : ''}`} x={b[0]} y={b[1]} width={b[2]} height={b[3]} vectorEffect="non-scaling-stroke" />
              ))}
          </svg>
          <span className="dr-img-tag">{set.sensor_label.split(',')[0]} · не спутник</span>
          <div className="dr-boxsw" role="tablist" aria-label="Рамки" data-testid="drones-boxes">
            {(['model', 'labels', 'both'] as const).map((k) => (
              <button key={k} className={boxes === k ? 'on' : ''} disabled={k !== 'labels' && !p} onClick={() => setBoxes(k)} data-testid={`drones-boxes-${k}`}>
                {k === 'model' ? 'модель' : k === 'labels' ? 'разметка' : 'обе'}
              </button>
            ))}
          </div>
        </div>
        <div className="dr-side">
          <div className="dr-sec">
            <div className="dr-h">Предметы на кадре</div>
            <div className="dr-line model" data-testid="drones-model-line">
              <i className="dr-sw pred" aria-hidden /> наша модель:{' '}
              {p ? (
                <>
                  <b>{p.n}</b> предм.{' '}
                  <span className="faint">
                    (порог {nf(ms?.threshold ?? 0, 2)}
                    {p.conf_mean != null ? `, уверенность ср. ${nf(p.conf_mean, 2)}, мин. ${nf(p.conf_min ?? 0, 2)}` : ''})
                  </span>
                </>
              ) : (
                <span className="faint">нет прогона</span>
              )}
            </div>
            <div className="dr-line" data-testid="drones-count">
              <i className="dr-sw g-other" aria-hidden /> разметка набора: {f.n_objects != null ? <b>{f.n_objects}</b> : <span className="faint">рамок в наборе нет</span>}
            </div>
            {p && p.tp != null && (
              <div className="faint" data-testid="drones-match">
                совпало {p.tp}, ложных {p.fp} (оранжевые), пропущено {p.fn} · IoU ≥ {nf(ms?.iou ?? 0.5, 2)}
              </div>
            )}
            {f.objects && <div className="dr-h">Классы разметки набора (модель классы не выдаёт)</div>}
            {f.objects && <Legend f={f} set={set} />}
            {f.objects && f.objects.length > 0 && <div className="dr-src-cls faint">классы набора: {srcClasses(f)}</div>}
            {!set.groups_labelled.includes('algae') && <div className="dr-note faint">Водоросли в этом наборе не размечены — класс не показывается.</div>}
          </div>
          <div className="dr-sec" data-testid="drones-density">
            <div className="dr-h">Плотность</div>
            {f.density_m2 != null && f.density_km2 != null ? (
              <>
                <div>
                  <b>{dens(f.density_m2)}</b> шт./м² · <b>{nf(f.density_km2)}</b> шт./км²
                </div>
                <div className="faint">на площади кадра {nf(f.frame_area_m2 ?? 0, 2)} м²{set.gsd_m ? `, GSD ${nf(set.gsd_m * 100, 2)} см` : ''} — не пересчёт на район</div>
              </>
            ) : f.published ? (
              <>
                <div>
                  <b>{dens(f.published.density_m2)}</b> шт./м² · <b>{nf(f.published.density_km2)}</b> шт./км²
                </div>
                <div className="faint">{f.published.scope}</div>
                <div className="faint">
                  ≈ {nf(f.published.per_frame_expected, 1)} шт. на кадр {nf(f.frame_area_m2 ?? 0, 1)} м² — {f.published.per_frame_note.replace(/^ожидание на кадр [\d.,]+ м² по плотности пляжа, /, '')}
                </div>
              </>
            ) : (
              <div className="dr-na">{f.area_note ?? 'площадь кадра неизвестна'} — шт./м² и шт./км² не считаются</div>
            )}
          </div>
          <div className="dr-sec" data-testid="drones-counter">
            <div className="dr-h">Наш счётчик по фото (тот же, что «Счёт по фото», без дообучения)</div>
            {ms ? (
              <>
                <div>
                  по набору: <b>{modelSummaryText(ms)}</b>
                </div>
                <div className="faint">{rule(ms)}</div>
                <div className={ms.trained_on_this_set ? 'faint' : 'dr-warn'}>{ms.training_note}</div>
                {ms.checked_metric && (
                  <div className="faint">
                    проверенная ошибка {nf(ms.checked_metric.count_mae_per_frame, 2)} шт./кадр на {nf(ms.checked_metric.n_test_frames)} отложенных кадрах
                  </div>
                )}
                <div className="faint">Одна категория «предмет»: материал и водоросли модель не определяет.</div>
              </>
            ) : (
              <div className="faint">прогона модели по этому набору нет</div>
            )}
          </div>
          <div className="dr-sec dr-meta">
            <div className="dr-h">Источник</div>
            <div>
              <span className={`dr-sensor s-${set.sensor}`}>{set.sensor_label}</span> · {set.view}
            </div>
            <div>
              {set.name}
              {f.subregion ? ` · ${f.subregion}` : ''}
              {f.station != null ? ` · станция ${f.station}` : ''}
            </div>
            <div className="faint" data-testid="drones-date">
              {f.date ? `дата ${f.date}` : 'дата не указана'}
              {f.lat != null && f.lon != null ? ` · ${nf(f.lat, 4)}, ${nf(f.lon, 4)}` : ''}
            </div>
            <div>
              Лицензия: <b>{set.license}</b>
            </div>
            <div className="faint dr-file">файл: {f.source_file}</div>
            <a href={set.link} target="_blank" rel="noreferrer">
              {set.link.replace(/^https?:\/\//, '')}
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}

function SetView({ set, onBack, initialFrame }: { set: DroneSet; onBack: () => void; initialFrame?: number }) {
  const [data, setData] = useState<FramesResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [idx, setIdx] = useState<number | null>(initialFrame ?? null);
  useEffect(() => {
    setData(null);
    setErr(null);
    get<FramesResp>(`/api/v3/drones/${set.id}/frames`).then(setData, (e) => setErr(String(e?.message ?? e)));
  }, [set.id]);
  if (err) return <div className="dr-err">Не удалось загрузить кадры: {err}</div>;
  if (!data) return <div className="dr-load">Загрузка кадров…</div>;
  if (idx != null && data.frames[idx]) return <FrameView set={data.set} frames={data.frames} idx={idx} onIdx={setIdx} onBack={() => setIdx(null)} />;
  return (
    <div className="dr-setview" data-testid="drones-set">
      <div className="dr-frame-nav">
        <button className="btn sm ghost" onClick={onBack} data-testid="drones-back-sets">
          ← Наборы
        </button>
        <span className="dr-grow" />
      </div>
      <div className="dr-set-h">
        <div className="dr-title2">{set.name}</div>
        <div className="faint">
          <span className={`dr-sensor s-${set.sensor}`}>{set.sensor_label}</span> · {set.region} · {set.license} · в наборе {set.dataset_size}
        </div>
        {set.model && (
          <div className="dr-setsum" data-testid="drones-set-summary">
            <b>{modelSummaryText(set.model)}</b> <span className="faint">· {rule(set.model)}</span>
            <div className={set.model.trained_on_this_set ? 'faint' : 'dr-warn'}>{set.model.training_note}</div>
          </div>
        )}
        <div className="dr-note">{set.note}</div>
      </div>
      <div className="dr-grid" data-testid="drones-grid">
        {data.frames.map((f, i) => (
          <button key={f.id} className="dr-thumb" onClick={() => setIdx(i)} data-testid="drones-thumb" title={f.source_file}>
            <img src={f.image} alt="" loading="lazy" />
            <span className="dr-thumb-n">разметка {f.n_objects != null ? f.n_objects : '—'}</span>
            {f.model && <span className="dr-thumb-p">модель {f.model.n}</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function DronesPanel({ initialSet, onClose }: { initialSet?: string | null; onClose: () => void }) {
  const [data, setData] = useState<SetsResp | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cur, setCur] = useState<string | null>(initialSet ?? null);
  useEffect(() => {
    get<SetsResp>('/api/v3/drones').then(setData, (e) => setErr(String(e?.message ?? e)));
  }, []);
  useEffect(() => setCur(initialSet ?? null), [initialSet]);
  useEffect(() => {
    const on = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, [onClose]);
  const set = useMemo(() => data?.sets.find((s) => s.id === cur) ?? null, [data, cur]);
  const drones = data?.sets.filter((s) => s.sensor === 'drone') ?? [];
  const others = data?.sets.filter((s) => s.sensor !== 'drone') ?? [];
  const card = (s: DroneSet) => (
    <button key={s.id} className="dr-card" onClick={() => setCur(s.id)} data-testid="drones-setcard" data-set={s.id}>
      {s.cover && <img src={s.cover} alt="" loading="lazy" />}
      <span className="dr-card-b">
        <span className="dr-card-t">{s.name}</span>
        <span className="faint">
          <span className={`dr-sensor s-${s.sensor}`}>{SENSOR_SHORT[s.sensor]}</span> · {s.region}
        </span>
        <span className="faint">
          {s.n_frames} кадр. · {s.groups_labelled.length ? s.groups_labelled.map((g) => GROUP_LABEL[g]).join(', ') : 'классы не размечены'}
          {s.frame_area_m2 ? ` · кадр ${nf(s.frame_area_m2, 1)} м²` : ' · площадь кадра неизвестна'}
        </span>
        <span className="faint">
          {s.license}
        </span>
        <span className="faint">
          {s.model ? `${modelSummaryText(s.model)} · ${s.model.trained_on_this_set ? 'обучался на этом наборе' : 'не обучался на этом наборе'}` : ''}
        </span>
      </span>
    </button>
  );
  return (
    <div className="dr-panel" role="dialog" aria-label="Дроны: детальные кадры" data-testid="drones-panel">
      <div className="dr-head">
        <div>
          <div className="dr-kicker">Дроны · детальные кадры с известной разметкой</div>
          <div className="dr-title">{set ? set.name : 'Наборы кадров'}</div>
        </div>
        <button className="btn sm ghost" onClick={onClose} aria-label="Закрыть" data-testid="drones-close">
          ✕
        </button>
      </div>
      <div className="dr-banner" data-testid="drones-banner">
        <b>{BANNER}</b>
      </div>
      <div className="dr-body">
        {err && <div className="dr-err">Не удалось загрузить наборы: {err}</div>}
        {!data && !err && <div className="dr-load">Загрузка…</div>}
        {data && !set && (
          <div className="dr-sets" data-testid="drones-sets">
            <div className="dr-grp">Дроны</div>
            <div className="dr-cards">{drones.map(card)}</div>
            {others.length > 0 && (
              <>
                <div className="dr-grp">Другие детальные кадры (не дрон) — на них обучался и проверен наш счётчик</div>
                <div className="dr-cards">{others.map(card)}</div>
              </>
            )}
            {data.not_included.length > 0 && (
              <div className="dr-note faint" data-testid="drones-not-included">
                Не показано: {data.not_included.map((n) => `${n.what} — ${n.why}`).join('; ')}.
              </div>
            )}
          </div>
        )}
        {data && set && <SetView key={set.id} set={set} onBack={() => setCur(null)} />}
      </div>
    </div>
  );
}
