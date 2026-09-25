import { useEffect, useRef, useState } from 'react';
import type { DriftFile } from '../types';
import type { FlowField } from '../map/flow';
import { anim } from '../map/controller';

const SPEEDS = [1, 2, 4];
const HOURS_PER_SEC = 6; // 72 h in 12 s at 1×

export default function DriftPlayer({ drift, onRender, flow, autoplay }: { drift: DriftFile; onRender: () => void; flow: FlowField[]; autoplay?: boolean }) {
  const maxH = drift.hours?.length ? drift.hours[drift.hours.length - 1] : 72;
  const [hour, setHour] = useState(anim.hour);
  // the drift view starts playing by itself: «+0 ч» with nothing moving looked like an empty layer
  const [playing, setPlaying] = useState(anim.playing || !!autoplay);
  const [speed, setSpeed] = useState(anim.speed);
  const [spread, setSpread] = useState(anim.spread);
  const render = useRef(onRender);
  render.current = onRender;
  const ens = (drift.ensemble ?? []).filter((m) => m?.particles?.length);
  const wdfs = ens.map((m) => m.wind_drift_factor).sort((a, b) => a - b);

  useEffect(() => {
    anim.maxHour = maxH;
    const l = (h: number) => setHour(h);
    anim.listeners.add(l);
    return () => {
      anim.listeners.delete(l);
      anim.playing = false;
    };
  }, [maxH]);

  useEffect(() => {
    anim.playing = playing;
    anim.speed = speed;
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    let lastUi = 0;
    const tick = (t: number) => {
      const dt = Math.min(0.1, (t - last) / 1000);
      last = t;
      anim.hour += dt * HOURS_PER_SEC * anim.speed;
      if (anim.hour > maxH) anim.hour = 0;
      render.current();
      if (t - lastUi > 90) {
        lastUi = t;
        setHour(anim.hour);
        anim.listeners.forEach((l) => l(anim.hour));
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, maxH]);

  const setH = (h: number) => {
    anim.hour = h;
    setHour(h);
    anim.listeners.forEach((l) => l(h));
    render.current();
  };
  (window as any).__driftPlay = (on: boolean) => setPlaying(on);
  const f = drift.forcing ?? {};
  const start = Date.parse(drift.start_time);
  const cur = Number.isFinite(start) ? new Date(start + hour * 3600e3).toISOString() : null;

  return (
    <div className="bottom-bar" data-testid="drift-player">
      <div className="dp-row">
        <button className="play" onClick={() => setPlaying((v) => !v)} data-testid="drift-play" aria-label={playing ? 'Пауза' : 'Старт'}>
          {playing ? (
            <svg viewBox="0 0 24 24" width="14" height="14">
              <rect x="6" y="5" width="4" height="14" fill="currentColor" />
              <rect x="14" y="5" width="4" height="14" fill="currentColor" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="14" height="14">
              <path d="M8 5v14l11-7z" fill="currentColor" />
            </svg>
          )}
        </button>
        <div className="dp-hour" data-testid="drift-hour">
          +{Math.floor(hour)}
          <small> ч</small>
        </div>
        <input
          className="slider"
          type="range"
          min={0}
          max={maxH}
          step={0.5}
          value={hour}
          onChange={(e) => setH(Number(e.target.value))}
          data-testid="drift-slider"
          style={{ ['--p' as any]: `${(hour / maxH) * 100}%` }}
        />
        <div className="seg">
          {SPEEDS.map((s) => (
            <button key={s} className={speed === s ? 'on' : ''} onClick={() => setSpeed(s)} data-testid={`drift-speed-${s}`}>
              {s}×
            </button>
          ))}
        </div>
        {ens.length > 0 && (
          <button
            className="menu-row"
            style={{ width: 'auto', padding: 0 }}
            onClick={() => {
              anim.spread = !spread;
              setSpread(anim.spread);
              render.current();
            }}
            data-testid="drift-spread"
            title={`Где были бы частицы при ветровом коэффициенте ${wdfs.join(' и ')}`}
          >
            <span className={`check ${spread ? 'on' : ''}`} aria-hidden />
            <span className="small">неопределённость</span>
          </button>
        )}
      </div>
      <div className="dp-cap">
        <b>Демонстрационный прогноз, не валидирован.</b> {f.model ?? 'OpenDrift'} · течения {shortSrc(f.currents)} · ветер {shortSrc(f.wind)} · коэф.
        ветра {f.wind_drift_factor ?? '—'}
        {ens.length > 0 ? ` · облако: ${wdfs.join('–')}` : ''}
        {cur ? ` · ${cur.slice(8, 10)}.${cur.slice(5, 7)} ${cur.slice(11, 16)} UTC` : ''}
        {flow.length ? ` · частицы: ${flow.map((x) => (x.kind === 'wind' ? 'ветер' : 'течения')).join(' и ')}, поле на +${Math.floor(hour / 6) * 6} ч` : ''}
      </div>
    </div>
  );
}

function shortSrc(s?: string) {
  if (!s) return '—';
  return s.split(/,|\(| global| hourly| 10 m/)[0].trim() || s;
}
