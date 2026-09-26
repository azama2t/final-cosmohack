// §54 п.3: lanes Sentinel-2 / NASA / Дроны / Поле / Дрейф, each on/off (saved); many dates → groups by month / year
// with the count; the chosen snapshot / zone / NASA day are props — switching lanes never loses them.
// §48 (BRIEF §1) = §47 п.2 «список дат»: a time strip at the bottom of the map (~64 px, collapsible).
// Amber marks — Sentinel-2 snapshots of the current район/период (size / number = finds), blue ticks — field measurements,
// violet dashed — the drift forecast interval (experiment). The current snapshot is highlighted; a click = choose the
// snapshot (the map flies there); wheel / ± = time zoom, drag = pan. Same data as the left list (one set of filters).
import { useEffect, useMemo, useRef, useState } from 'react';

export interface TlScene {
  key: string;
  t: number;
  finds: number;
  b: number;
  label: string;
  drift: boolean;
  noeval?: boolean;
}

const DAY = 86400e3;
const MONTHS = ['янв', 'фев', 'мар', 'апр', 'май', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

function ticks(t0: number, t1: number, W: number): { t: number; l: string; major: boolean }[] {
  const span = t1 - t0;
  const out: { t: number; l: string; major: boolean }[] = [];
  const d0 = new Date(t0);
  if (span > 3 * 365 * DAY) {
    for (let y = d0.getUTCFullYear(); ; y++) {
      const t = Date.UTC(y, 0, 1);
      if (t > t1) break;
      if (t >= t0) out.push({ t, l: String(y), major: true });
    }
  } else if (span > 70 * DAY) {
    const step = span > 18 * 30 * DAY ? 3 : 1;
    for (let y = d0.getUTCFullYear(), m = d0.getUTCMonth(); ; m += step) {
      const t = Date.UTC(y, m, 1);
      if (t > t1) break;
      const d = new Date(t);
      if (t >= t0) out.push({ t, l: d.getUTCMonth() === 0 ? String(d.getUTCFullYear()) : MONTHS[d.getUTCMonth()], major: d.getUTCMonth() === 0 });
    }
  } else {
    const step = span > 20 * DAY ? 7 : span > 6 * DAY ? 1 : 1;
    const s0 = Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth(), d0.getUTCDate());
    for (let t = s0; t <= t1; t += step * DAY) {
      const d = new Date(t);
      if (t >= t0) out.push({ t, l: `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}`, major: d.getUTCDate() === 1 });
    }
  }
  // thin out labels that would overlap
  const minPx = 46;
  let last = -Infinity;
  return out.filter((k) => {
    const x = ((k.t - t0) / (t1 - t0)) * W;
    if (x - last < minPx) return false;
    last = x;
    return true;
  });
}

export type LaneId = 's2' | 'nasa' | 'drones' | 'field' | 'drift';
const LANES: { id: LaneId; label: string; h: number; title: string }[] = [
  { id: 's2', label: 'Sentinel-2', h: 24, title: 'снимки Sentinel-2 (цифра — находки / число снимков в группе)' },
  { id: 'nasa', label: 'NASA', h: 12, title: 'NASA · ежедневно — обзор 250–375 м, не обнаружение пластика; нажмите на день — слой NASA на эту дату' },
  { id: 'drones', label: 'Дроны', h: 12, title: 'кадры с дронов (детальные, не спутник)' },
  { id: 'field', label: 'Поле', h: 12, title: 'полевые измерения организаторов (судно)' },
  { id: 'drift', label: 'Дрейф', h: 12, title: 'прогноз дрейфа 72 ч от снимка — эксперимент' },
];
const LANE_KEY = 'mp.case.tl.lanes';
const LBL = 62; // px: lane labels column
const AXIS = 13;
const VIIRS_FROM = Date.UTC(2012, 0, 19);
const iso = (t: number) => new Date(t).toISOString().slice(0, 10);
const ru = (t: number) => iso(t).split('-').reverse().join('.');

/** group unit by the visible span: years (> 4 y), months (> 8 mo), else none (pixel merge only) */
function unitOf(span: number): 'year' | 'month' | null {
  return span > 4 * 365 * DAY ? 'year' : span > 240 * DAY ? 'month' : null;
}
function bucketOf(t: number, u: 'year' | 'month'): { k: string; a: number; b: number; l: string } {
  const d = new Date(t);
  const y = d.getUTCFullYear();
  if (u === 'year') return { k: String(y), a: Date.UTC(y, 0, 1), b: Date.UTC(y + 1, 0, 1), l: String(y) };
  const m = d.getUTCMonth();
  return { k: `${y}-${m}`, a: Date.UTC(y, m, 1), b: Date.UTC(y, m + 1, 1), l: `${MONTHS[m]} ${y}` };
}

type Group = { items: TlScene[]; l?: string; a?: number; b?: number };

export default function Timeline({
  scenes,
  obs,
  cur,
  onPick,
  period,
  onDynamics,
  nasa,
  onNasaDate,
  drones,
  fresh,
  freshCur,
  onFresh,
}: {
  /** §55/§57: «Реальное время» — fresh Sentinel-2 processed by our model (shown in the Sentinel-2 lane, red ring) */
  fresh?: { key: string; t: number; label: string; finds: number }[];
  freshCur?: string | null;
  onFresh?: (key: string) => void;
  /** §51 п.3: open the «Динамика района» panel (L140) */
  onDynamics?: (() => void) | null;
  scenes: TlScene[];
  /** field measurement dates (ms) */
  obs: number[];
  cur: string | null;
  onPick: (key: string) => void;
  /** the period of step 1 (dates from the filter), if set — the default window */
  period: [number | null, number | null];
  /** §54 п.3: NASA daily lane — on/off state of the NASA layer, its day and the latest day */
  nasa?: { on: boolean; date: string; latest: string } | null;
  onNasaDate?: (d: string) => void;
  /** drone frame dates (ms) with a label, if the drone data give dates */
  drones?: { t: number; label: string }[];
}) {
  const [open, setOpen] = useState(() => {
    try {
      return localStorage.getItem('mp.case.timeline') !== '0';
    } catch {
      return true;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem('mp.case.timeline', open ? '1' : '0');
    } catch {
      /* no storage */
    }
  }, [open]);
  const [lanes, setLanes] = useState<Record<LaneId, boolean>>(() => {
    const d = { s2: true, nasa: true, drones: true, field: true, drift: true };
    try {
      return { ...d, ...JSON.parse(localStorage.getItem(LANE_KEY) ?? '{}') };
    } catch {
      return d;
    }
  });
  const toggleLane = (id: LaneId) =>
    setLanes((x) => {
      const n = { ...x, [id]: !x[id] };
      try {
        localStorage.setItem(LANE_KEY, JSON.stringify(n));
      } catch {
        /* no storage */
      }
      return n;
    });
  // §57 п.2: no drone lane without real shooting dates (none are invented)
  const hasDrones = (drones ?? []).some((d) => Number.isFinite(d.t));
  const avail = LANES.filter((l) => l.id !== 'drones' || hasDrones);
  const shown = avail.filter((l) => lanes[l.id]);
  let yy = 0;
  const laneY: Partial<Record<LaneId, [number, number]>> = {};
  for (const l of shown) {
    laneY[l.id] = [yy, yy + l.h];
    yy += l.h;
  }
  const H = Math.max(yy, 12) + AXIS;
  // the legend / drift player above the strip follow its height
  useEffect(() => {
    document.documentElement.style.setProperty('--c-tl-h', `${open ? H + 26 : 22}px`);
  }, [open, H]);

  const box = useRef<HTMLDivElement>(null);
  const [BW, setW] = useState(600);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(200, el.clientWidth)));
    ro.observe(el);
    return () => ro.disconnect();
  }, [open]);
  const W = Math.max(120, BW - LBL);

  const nasaT = nasa ? Date.parse(nasa.date + 'T12:00:00Z') : NaN;
  const droneTs = useMemo(() => (drones ?? []).map((d) => d.t).filter((t) => Number.isFinite(t)), [drones]);
  const full = useMemo<[number, number]>(() => {
    const ts = [...scenes.map((s) => s.t), ...obs, ...droneTs, ...(fresh ?? []).map((f) => f.t)].filter((t) => Number.isFinite(t));
    let a = period[0] ?? (ts.length ? Math.min(...ts) : Date.UTC(2018, 0, 1));
    let b = period[1] ?? (ts.length ? Math.max(...ts) : Date.now());
    if (b - a < 20 * DAY) {
      const c = (a + b) / 2;
      a = c - 10 * DAY;
      b = c + 10 * DAY;
    }
    const pad = (b - a) * 0.04;
    return [a - pad, b + pad];
  }, [scenes, obs, droneTs, fresh, period[0], period[1]]); // eslint-disable-line react-hooks/exhaustive-deps
  const [win, setWin] = useState<[number, number]>(full);
  useEffect(() => setWin(full), [full]);
  const [t0, t1] = win;
  const x = (t: number) => LBL + ((t - t0) / (t1 - t0)) * W;

  const zoom = (f: number, at = W / 2) => {
    const tc = t0 + (at / W) * (t1 - t0);
    const span = Math.min(Math.max(full[1] - full[0], Date.now() - VIIRS_FROM) + 400 * DAY, Math.max(5 * DAY, (t1 - t0) * f));
    const a = tc - (at / W) * span;
    setWin([a, a + span]);
  };
  const drag = useRef<{ x: number; w: [number, number]; moved: boolean } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoom(e.deltaY > 0 ? 1.25 : 0.8, Math.max(0, e.clientX - r.left - LBL));
    };
    el.addEventListener('wheel', wheel, { passive: false });
    return () => el.removeEventListener('wheel', wheel);
  });

  const inWin = scenes.filter((s) => s.t >= t0 - DAY && s.t <= t1 + DAY);
  const curS = scenes.find((s) => s.key === cur) ?? null;
  const unit = unitOf(t1 - t0);
  // §54 п.3: groups by month / year (with the count) when their marks would collide; then a pixel merge of the rest
  const groups = useMemo(() => {
    const srt = [...inWin].sort((a, b) => a.t - b.t);
    const pre: Group[] = [];
    if (unit) {
      const by = new Map<string, Group>();
      for (const s of srt) {
        const k = bucketOf(s.t, unit);
        if (!by.has(k.k)) {
          const g: Group = { items: [], l: k.l, a: k.a, b: k.b };
          by.set(k.k, g);
          pre.push(g);
        }
        by.get(k.k)!.items.push(s);
      }
    } else for (const s of srt) pre.push({ items: [s] });
    // a bucket whose marks fit side by side stays as single marks
    const flat: Group[] = [];
    for (const g of pre) {
      const px = g.items.length > 1 ? ((g.items[g.items.length - 1].t - g.items[0].t) / (t1 - t0)) * W : Infinity;
      if (g.items.length > 1 && px < 26 * (g.items.length - 1)) flat.push(g);
      else for (const s of g.items) flat.push({ items: [s] });
    }
    const out: Group[] = [];
    let x0 = -Infinity;
    for (const g of flat) {
      const t = g.items.length > 1 && g.a !== undefined ? (g.a + g.b!) / 2 : g.items[0].t;
      const px = ((t - t0) / (t1 - t0)) * W;
      const last = out[out.length - 1];
      if (last && px - x0 < 26) {
        last.items.push(...g.items);
        if (last.l && g.l && !last.l.endsWith(g.l)) last.l = `${last.l.split(' – ')[0]} – ${g.l}`;
        if (g.b !== undefined) last.b = Math.max(last.b ?? g.b, g.b);
      } else {
        out.push({ ...g, items: [...g.items] });
        x0 = px;
      }
    }
    return out;
  }, [inWin, t0, t1, W, unit]); // eslint-disable-line react-hooks/exhaustive-deps
  const bins = (ts: number[]) => {
    const m = new Map<number, number>();
    for (const t of ts) {
      if (t < t0 || t > t1) continue;
      const px = Math.round(x(t) / 3) * 3;
      m.set(px, (m.get(px) ?? 0) + 1);
    }
    return [...m.entries()];
  };
  const obsBins = useMemo(() => bins(obs), [obs, t0, t1, W]); // eslint-disable-line react-hooks/exhaustive-deps
  const droneBins = useMemo(() => bins(droneTs), [droneTs, t0, t1, W]); // eslint-disable-line react-hooks/exhaustive-deps
  // fresh snapshots: 14 px bins (a month of dates at a year scale → one mark with the count)
  const freshBins = useMemo(() => {
    const m = new Map<number, { key: string; t: number; label: string; finds: number }[]>();
    for (const f of fresh ?? []) {
      if (f.t < t0 || f.t > t1) continue;
      const px = Math.round(x(f.t) / 22) * 22;
      if (!m.has(px)) m.set(px, []);
      m.get(px)!.push(f);
    }
    return [...m.entries()];
  }, [fresh, t0, t1, W]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!open)
    return (
      <button className="c-tl-open" onClick={() => setOpen(true)} data-testid="timeline-open" title="Показать шкалу времени">
        Шкала времени ▴
      </button>
    );
  const ly = (id: LaneId) => laneY[id] ?? [0, 0];
  const mid = (id: LaneId) => {
    const r = ly(id);
    return (r[0] + r[1]) / 2;
  };
  const nasaLatestT = nasa ? Date.parse(nasa.latest + 'T23:59:59Z') : Date.now();
  const nasaPick = (clientX: number) => {
    const r = svgRef.current?.getBoundingClientRect();
    if (!r || !onNasaDate) return;
    let t = t0 + ((clientX - r.left - LBL) / W) * (t1 - t0);
    t = Math.max(VIIRS_FROM, Math.min(nasaLatestT, t));
    onNasaDate(iso(t));
  };
  const moved = () => !!drag.current?.moved;
  const nx0 = Math.max(LBL, x(VIIRS_FROM));
  const nx1 = Math.min(LBL + W, x(nasaLatestT));
  return (
    <div className="c-tl c-tl-lanes" data-testid="timeline" style={{ height: H + 26 }}>
      <div className="c-tl-side">
        <button className="icon-btn" onClick={() => zoom(0.6)} title="Крупнее по времени" aria-label="Крупнее по времени" data-testid="timeline-zoom-in">
          +
        </button>
        <button className="icon-btn" onClick={() => zoom(1.6)} title="Мельче по времени" aria-label="Мельче по времени" data-testid="timeline-zoom-out">
          −
        </button>
        <button className="icon-btn" onClick={() => setWin(full)} title="Весь период" aria-label="Весь период" data-testid="timeline-fit">
          ↔
        </button>
      </div>
      <div className="c-tl-main" ref={box}>
        <div className="c-tl-key">
          <span className="faint">дорожки:</span>
          {avail.map((l) => (
            <button
              key={l.id}
              className={`c-tl-chip ${l.id} ${lanes[l.id] ? 'on' : ''}`}
              onClick={() => toggleLane(l.id)}
              aria-pressed={lanes[l.id]}
              data-testid={`timeline-lane-${l.id}`}
              title={`${l.title} — показать / скрыть дорожку`}
            >
              {l.label}
            </button>
          ))}
          {unit && <span className="faint">· группы по {unit === 'year' ? 'годам' : 'месяцам'}</span>}
          {curS && <span className="c-tl-cur">выбран: {curS.label}</span>}
          {onDynamics && (
            <button className="link c-tl-dyn" onClick={onDynamics} data-testid="dynamics-open-tl" title="Сравнение дат района">
              Динамика ↗
            </button>
          )}
        </div>
        <svg
          ref={svgRef}
          width={W + LBL}
          height={H}
          className="c-tl-svg"
          onMouseDown={(e) => (drag.current = { x: e.clientX, w: win, moved: false })}
          onMouseMove={(e) => {
            const d = drag.current;
            if (!d || e.buttons !== 1) return;
            const dt = ((e.clientX - d.x) / W) * (d.w[1] - d.w[0]);
            if (Math.abs(e.clientX - d.x) > 2) {
              d.moved = true;
              setWin([d.w[0] - dt, d.w[1] - dt]);
            }
          }}
          onMouseUp={() => setTimeout(() => (drag.current = null), 0)}
          onMouseLeave={() => (drag.current = null)}
        >
          {shown.map((l, i) => (
            <g key={'lane' + l.id} data-testid={`timeline-row-${l.id}`}>
              {i % 2 === 0 && <rect x={0} y={ly(l.id)[0]} width={W + LBL} height={l.h} className="c-tl-band" />}
              <text x={4} y={mid(l.id) + 3.5} className={`c-tl-lbl ${l.id}`}>
                {l.label}
                <title>{l.title}</title>
              </text>
            </g>
          ))}
          {ticks(t0, t1, W).map((k) => (
            <g key={k.t}>
              <line x1={x(k.t)} x2={x(k.t)} y1={0} y2={H} className={k.major ? 'c-tl-gl major' : 'c-tl-gl'} />
              <text x={x(k.t) + 3} y={H - 2} className="c-tl-tx">
                {k.l}
              </text>
            </g>
          ))}
          {lanes.field &&
            obsBins.map(([px, n]) => (
              <rect key={'o' + px} x={px - 1} y={mid('field') - Math.min(5, 1.5 + n / 2)} width={2} height={Math.min(10, 3 + n)} className="c-tl-obs">
                <title>полевые измерения (судно): {n}</title>
              </rect>
            ))}
          {lanes.field && !obsBins.length && (
            <text x={LBL + 6} y={mid('field') + 3.5} className="c-tl-empty">
              {obs.length ? 'нет измерений в этом окне' : 'слой «Полевые измерения» выключен или нет дат'}
            </text>
          )}
          {lanes.drones && hasDrones &&
            droneBins.map(([px, n]) => (
              <rect key={'dr' + px} x={px - 2} y={mid('drones') - 4} width={4} height={8} rx={1} className="c-tl-drone">
                <title>кадры с дронов (не спутник): {n}</title>
              </rect>
            ))}
          {lanes.drones && hasDrones && !droneBins.length && (
            <text x={LBL + 6} y={mid('drones') + 3.5} className="c-tl-empty">
              {droneTs.length ? 'нет кадров в этом окне' : 'у кадров с дронов нет дат съёмки в данных'}
            </text>
          )}
          {lanes.drift &&
            inWin
              .filter((s) => s.drift)
              .map((s) => (
                <line key={'d' + s.key} x1={x(s.t)} x2={Math.max(x(s.t) + 8, x(s.t + 3 * DAY))} y1={mid('drift')} y2={mid('drift')} className="c-tl-drift">
                  <title>прогноз дрейфа 24–72 ч (эксперимент) от снимка {s.label}</title>
                </line>
              ))}
          {lanes.nasa && (
            <g className={`c-tl-nasa ${nasa?.on ? 'on' : ''}`} data-testid="timeline-nasa" onClick={(e) => !moved() && nasaPick(e.clientX)} style={{ cursor: 'pointer' }}>
              <rect x={nx0} y={ly('nasa')[0] + 3} width={Math.max(0, nx1 - nx0)} height={Math.max(2, ly('nasa')[1] - ly('nasa')[0] - 6)} className="c-tl-nasa-band" />
              <title>NASA · ежедневно (VIIRS с 19.01.2012) — снимок на каждый день; нажмите — слой NASA на этот день. Обзор, не обнаружение пластика</title>
              {nasa?.on && Number.isFinite(nasaT) && nasaT >= t0 && nasaT <= t1 && (
                <g data-testid="timeline-nasa-day">
                  <line x1={x(nasaT)} x2={x(nasaT)} y1={ly('nasa')[0]} y2={ly('nasa')[1]} className="c-tl-nasa-day" />
                  <text x={Math.min(LBL + W - 58, x(nasaT) + 4)} y={ly('nasa')[1] - 2} className="c-tl-nasa-tx">
                    {ru(nasaT)}
                  </text>
                </g>
              )}
            </g>
          )}
          {lanes.s2 &&
            freshBins.map(([px, fs]) => {
              const cy = mid('s2');
              const on = fs.some((f) => f.key === freshCur);
              if (fs.length === 1)
                return (
                  <g key={'f' + fs[0].key} className={`c-tl-fresh ${on ? 'on' : ''}`} onClick={() => !moved() && onFresh?.(fs[0].key)} data-testid="timeline-fresh" data-scene={fs[0].key} style={{ cursor: 'pointer' }}>
                    <circle cx={px} cy={cy} r={on ? 6 : 4.5} />
                    <title>Свежий Sentinel-2 (авто) · {fs[0].label} · {fs[0].finds ? `${fs[0].finds} наход.` : '0 находок'} — автоматически, не проверено человеком</title>
                  </g>
                );
              const a = Math.min(...fs.map((f) => f.t));
              const b = Math.max(...fs.map((f) => f.t));
              return (
                <g
                  key={'fg' + px}
                  className={`c-tl-fresh c-tl-g ${on ? 'on' : ''}`}
                  onClick={() => {
                    if (moved()) return;
                    const pad = Math.max(2 * DAY, (b - a) * 0.3);
                    setWin([a - pad, b + pad]);
                  }}
                  data-testid="timeline-fresh-group"
                  style={{ cursor: 'zoom-in' }}
                >
                  <rect x={px - (5 + String(fs.length).length * 3.5)} y={cy - 7} width={10 + String(fs.length).length * 7} height={14} rx={7} />
                  <text x={px} y={cy + 3.5} className="c-tl-n fresh">
                    {fs.length}
                  </text>
                  <title>Свежие Sentinel-2 (авто): {fs.length} снимков Sentinel-2 ({ru(a)} – {ru(b)}) — нажмите, чтобы приблизить</title>
                </g>
              );
            })}
          {curS && curS.t >= t0 && curS.t <= t1 && (
            <g className="c-tl-curmark" data-testid="timeline-current">
              <line x1={x(curS.t)} x2={x(curS.t)} y1={0} y2={H - AXIS} />
              <text x={Math.min(LBL + W - 60, x(curS.t) + 12)} y={9}>
                {ru(curS.t)}
              </text>
            </g>
          )}
          {lanes.s2 &&
            groups.map((g) => {
              const cy = mid('s2');
              if (g.items.length === 1) {
                const s = g.items[0];
                const r = s.finds ? Math.min(10, 5 + Math.sqrt(s.finds) * 1.3) : 3.2;
                const on = s.key === cur;
                return (
                  <g
                    key={s.key}
                    className={`c-tl-s ${on ? 'on' : ''} ${s.finds ? '' : 'nf'}`}
                    onClick={() => !moved() && onPick(s.key)}
                    data-testid="timeline-scene"
                    data-scene={s.key}
                    style={{ cursor: 'pointer' }}
                  >
                    <circle cx={x(s.t)} cy={cy} r={r + (on ? 2 : 0)} className="c-tl-sc" style={{ fill: s.finds ? (s.b ? '#ff8c42' : '#d9b870') : 'transparent' }} />
                    {s.finds > 0 && r >= 7 && (
                      <text x={x(s.t)} y={cy + 3.5} className="c-tl-n">
                        {s.finds}
                      </text>
                    )}
                    <title>
                      {s.label}: {s.finds ? `${s.finds} наход.` : s.noeval ? 'детектор не оценивается' : 'находок нет'}
                      {s.drift ? ' · есть прогноз дрейфа (эксперимент)' : ''} — нажмите, чтобы открыть снимок
                    </title>
                  </g>
                );
              }
              // a month / year group (or marks closer than a mark): the count; a click zooms into it
              const a = g.items[0].t;
              const b = g.items[g.items.length - 1].t;
              const nf = g.items.reduce((q, s) => q + s.finds, 0);
              const hasCur = g.items.some((s) => s.key === cur);
              const cx = x(g.l && g.a !== undefined ? Math.max(t0, Math.min(t1, (Math.max(g.a, a) + Math.min(g.b!, b)) / 2)) : (a + b) / 2);
              const txt = String(g.items.length);
              const w = 10 + txt.length * 6;
              return (
                <g
                  key={'g' + g.items[0].key}
                  className={`c-tl-s c-tl-g ${hasCur ? 'on' : ''}`}
                  onClick={() => {
                    if (moved()) return;
                    const pad = Math.max(3 * DAY, (b - a) * 0.3);
                    setWin([a - pad, b + pad]);
                  }}
                  data-testid="timeline-cluster"
                  data-group={g.l ?? ''}
                  style={{ cursor: 'zoom-in' }}
                >
                  <rect x={cx - w / 2} y={cy - 8} width={w} height={16} rx={8} className="c-tl-sc" style={{ fill: nf ? '#d9b870' : 'transparent' }} />
                  <text x={cx} y={cy + 3.5} className="c-tl-n">
                    {txt}
                  </text>
                  <title>
                    {g.l ? `${g.l}: ` : ''}
                    {g.items.length} снимков Sentinel-2 ({nf} наход.){hasCur ? ' · среди них выбранный' : ''} — нажмите, чтобы приблизить
                  </title>
                </g>
              );
            })}
        </svg>
      </div>
      <button className="icon-btn c-tl-close" onClick={() => setOpen(false)} title="Свернуть шкалу времени" aria-label="Свернуть шкалу времени" data-testid="timeline-close">
        ▾
      </button>
    </div>
  );
}
