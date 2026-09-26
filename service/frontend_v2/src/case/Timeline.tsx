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

export default function Timeline({
  scenes,
  obs,
  cur,
  onPick,
  period,
  onDynamics,
}: {
  /** §51 п.3: open the «Динамика района» panel (L140) */
  onDynamics?: (() => void) | null;
  scenes: TlScene[];
  /** field measurement dates (ms) */
  obs: number[];
  cur: string | null;
  onPick: (key: string) => void;
  /** the period of step 1 (dates from the filter), if set — the default window */
  period: [number | null, number | null];
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
  const box = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(600);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setW(Math.max(200, el.clientWidth)));
    ro.observe(el);
    return () => ro.disconnect();
  }, [open]);

  const full = useMemo<[number, number]>(() => {
    const ts = [...scenes.map((s) => s.t), ...obs].filter((t) => Number.isFinite(t));
    let a = period[0] ?? (ts.length ? Math.min(...ts) : Date.UTC(2018, 0, 1));
    let b = period[1] ?? (ts.length ? Math.max(...ts) : Date.now());
    if (b - a < 20 * DAY) {
      const c = (a + b) / 2;
      a = c - 10 * DAY;
      b = c + 10 * DAY;
    }
    const pad = (b - a) * 0.04;
    return [a - pad, b + pad];
  }, [scenes, obs, period[0], period[1]]); // eslint-disable-line react-hooks/exhaustive-deps
  const [win, setWin] = useState<[number, number]>(full);
  useEffect(() => setWin(full), [full]);
  const [t0, t1] = win;
  const x = (t: number) => ((t - t0) / (t1 - t0)) * W;

  const zoom = (f: number, at = W / 2) => {
    const tc = t0 + (at / W) * (t1 - t0);
    const span = Math.min(full[1] - full[0] + 400 * DAY, Math.max(5 * DAY, (t1 - t0) * f));
    const a = tc - (at / W) * span;
    setWin([a, a + span]);
  };
  const drag = useRef<{ x: number; w: [number, number] } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoom(e.deltaY > 0 ? 1.25 : 0.8, e.clientX - r.left);
    };
    el.addEventListener('wheel', wheel, { passive: false });
    return () => el.removeEventListener('wheel', wheel);
  });

  const inWin = scenes.filter((s) => s.t >= t0 - DAY && s.t <= t1 + DAY);
  const curS = scenes.find((s) => s.key === cur) ?? null;
  // §50 P1 (7): marks closer than ~18 px merge into a cluster (by years at a coarse scale)
  const groups = useMemo(() => {
    const srt = [...inWin].sort((a, b) => a.t - b.t);
    const out: { items: TlScene[] }[] = [];
    let x0 = -Infinity;
    for (const s of srt) {
      const px = ((s.t - t0) / (t1 - t0)) * W;
      if (out.length && px - x0 < 26) out[out.length - 1].items.push(s);
      else {
        out.push({ items: [s] });
        x0 = px;
      }
    }
    return out;
  }, [inWin, t0, t1, W]); // eslint-disable-line react-hooks/exhaustive-deps
  const obsBins = useMemo(() => {
    const m = new Map<number, number>();
    for (const t of obs) {
      if (t < t0 || t > t1) continue;
      const px = Math.round(x(t) / 2) * 2;
      m.set(px, (m.get(px) ?? 0) + 1);
    }
    return [...m.entries()];
  }, [obs, t0, t1, W]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!open)
    return (
      <button className="c-tl-open" onClick={() => setOpen(true)} data-testid="timeline-open" title="Показать шкалу времени">
        Шкала времени ▴
      </button>
    );
  return (
    <div className="c-tl" data-testid="timeline">
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
          <span>
            <i className="c-tl-k-s" /> снимки Sentinel-2 (цифра — находки)
          </span>
          <span>
            <i className="c-tl-k-o" /> полевые измерения
          </span>
          <span>
            <i className="c-tl-k-d" /> прогноз дрейфа 72 ч — эксперимент
          </span>
          {curS && <span className="c-tl-cur">выбран: {curS.label}</span>}
          {onDynamics && (
            <button className="link c-tl-dyn" onClick={onDynamics} data-testid="dynamics-open-tl" title="Сравнение дат района">
              Динамика ↗
            </button>
          )}
        </div>
        <svg
          ref={svgRef}
          width={W}
          height={46}
          className="c-tl-svg"
          onMouseDown={(e) => (drag.current = { x: e.clientX, w: win })}
          onMouseMove={(e) => {
            const d = drag.current;
            if (!d || e.buttons !== 1) return;
            const dt = ((e.clientX - d.x) / W) * (d.w[1] - d.w[0]);
            if (Math.abs(e.clientX - d.x) > 2) setWin([d.w[0] - dt, d.w[1] - dt]);
          }}
          onMouseUp={() => (drag.current = null)}
          onMouseLeave={() => (drag.current = null)}
        >
          {ticks(t0, t1, W).map((k) => (
            <g key={k.t}>
              <line x1={x(k.t)} x2={x(k.t)} y1={0} y2={46} className={k.major ? 'c-tl-gl major' : 'c-tl-gl'} />
              <text x={x(k.t) + 3} y={44} className="c-tl-tx">
                {k.l}
              </text>
            </g>
          ))}
          {obsBins.map(([px, n]) => (
            <rect key={'o' + px} x={px - 1} y={31} width={2} height={Math.min(6, 2 + n)} className="c-tl-obs">
              <title>полевые измерения: {n}</title>
            </rect>
          ))}
          {inWin
            .filter((s) => s.drift)
            .map((s) => (
              <line key={'d' + s.key} x1={x(s.t)} x2={Math.max(x(s.t) + 8, x(s.t + 3 * DAY))} y1={33} y2={33} className="c-tl-drift">
                <title>прогноз дрейфа 24–72 ч (эксперимент) от снимка {s.label}</title>
              </line>
            ))}
          {curS && curS.t >= t0 && curS.t <= t1 && (
            <g className="c-tl-curmark" data-testid="timeline-current">
              <line x1={x(curS.t)} x2={x(curS.t)} y1={0} y2={36} />
              <text x={Math.min(W - 60, x(curS.t) + 12)} y={9}>
                {new Date(curS.t).toISOString().slice(0, 10).split('-').reverse().join('.')}
              </text>
            </g>
          )}
          {groups.map((g) => {
            if (g.items.length === 1) {
              const s = g.items[0];
              const r = s.finds ? Math.min(11, 5 + Math.sqrt(s.finds) * 1.3) : 3.2;
              const on = s.key === cur;
              return (
                <g
                  key={s.key}
                  className={`c-tl-s ${on ? 'on' : ''} ${s.finds ? '' : 'nf'}`}
                  onClick={() => onPick(s.key)}
                  data-testid="timeline-scene"
                  data-scene={s.key}
                  style={{ cursor: 'pointer' }}
                >
                  <circle cx={x(s.t)} cy={20} r={r + (on ? 2.5 : 0)} className="c-tl-sc" style={{ fill: s.finds ? (s.b ? '#ff8c42' : '#d9b870') : 'transparent' }} />
                  {s.finds > 0 && r >= 7 && (
                    <text x={x(s.t)} y={23.5} className="c-tl-n">
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
            // several snapshots closer than a mark: one cluster mark, a click zooms into them
            const a = g.items[0].t;
            const b = g.items[g.items.length - 1].t;
            const nf = g.items.reduce((q, s) => q + s.finds, 0);
            const hasCur = g.items.some((s) => s.key === cur);
            const cx = x((a + b) / 2);
            return (
              <g
                key={'g' + g.items[0].key}
                className={`c-tl-s c-tl-g ${hasCur ? 'on' : ''}`}
                onClick={() => {
                  const pad = Math.max(3 * DAY, (b - a) * 0.3);
                  setWin([a - pad, b + pad]);
                }}
                data-testid="timeline-cluster"
                style={{ cursor: 'zoom-in' }}
              >
                <rect x={cx - 12} y={12} width={24} height={16} rx={8} className="c-tl-sc" style={{ fill: nf ? '#d9b870' : 'transparent' }} />
                <text x={cx} y={23.5} className="c-tl-n">
                  {g.items.length}
                </text>
                <title>
                  {g.items.length} снимков ({nf} наход.) — нажмите, чтобы приблизить
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
