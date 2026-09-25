import { useEffect, useRef, useState } from 'react';
import type { DriftFile } from '../types';
import { anim } from '../map/controller';

const SPEEDS = [1, 2, 4];
const HOURS_PER_SEC = 6; // at 1×: 72 h in 12 s

export default function DriftPlayer({ drift, onRender }: { drift: DriftFile; onRender: () => void }) {
  const maxH = drift.hours?.length ? drift.hours[drift.hours.length - 1] : 72;
  const [hour, setHour] = useState(anim.hour);
  const [playing, setPlaying] = useState(anim.playing);
  const [speed, setSpeed] = useState(anim.speed);
  const render = useRef(onRender);
  render.current = onRender;

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
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, speed, maxH]);

  const setH = (h: number) => {
    anim.hour = h;
    setHour(h);
    render.current();
  };
  (window as any).__driftPlay = (on: boolean) => setPlaying(on);

  const start = Date.parse(drift.start_time);
  const cur = Number.isFinite(start) ? new Date(start + hour * 3600e3) : null;
  const f = drift.forcing ?? {};

  return (
    <div className="drift-player glass" data-testid="drift-player">
      <div className="dp-row">
        <button
          className={`play-btn ${playing ? 'on' : ''}`}
          onClick={() => setPlaying((v) => !v)}
          data-testid="drift-play"
          aria-label={playing ? 'Пауза' : 'Старт'}
        >
          {playing ? (
            <svg viewBox="0 0 24 24" width="18" height="18">
              <rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" />
              <rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24" width="18" height="18">
              <path d="M8 5v14l11-7z" fill="currentColor" />
            </svg>
          )}
        </button>
        <div className="dp-hour" data-testid="drift-hour">
          T+{Math.floor(hour)}
          <small> ч</small>
        </div>
        <input
          className="dp-slider"
          type="range"
          min={0}
          max={maxH}
          step={0.5}
          value={hour}
          onChange={(e) => setH(Number(e.target.value))}
          data-testid="drift-slider"
          style={{ ['--p' as any]: `${(hour / maxH) * 100}%` }}
        />
        <div className="segmented small">
          {SPEEDS.map((s) => (
            <button key={s} className={`seg ${speed === s ? 'on' : ''}`} onClick={() => setSpeed(s)} data-testid={`drift-speed-${s}`}>
              {s}×
            </button>
          ))}
        </div>
      </div>
      <div className="dp-caption">
        <b>Демонстрационный прогноз, без валидации</b> · модель течений: {f.currents ?? '—'}, ветер: {f.wind ?? '—'}, ветровой
        коэффициент {f.wind_drift_factor ?? '—'} · {f.model ?? ''}
        {cur && <span className="muted"> · {cur.toISOString().slice(0, 16).replace('T', ' ')} UTC</span>}
      </div>
    </div>
  );
}
