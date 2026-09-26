import { useEffect, useRef, useState } from 'react';
import type { DriftFile } from '../types';
import type { FlowField } from '../map/flow';
import Info from './Info';
import { anim } from '../map/controller';
import { driftCaption, DRIFT_CORRIDOR_LABEL } from '../case/DriftLayer';

const SPEEDS = [1, 2, 4];
const HOURS_PER_SEC = 6; // 72 h in 12 s at 1×

export default function DriftPlayer({ drift, onRender, flow, autoplay }: { drift: DriftFile; onRender: () => void; flow: FlowField[]; autoplay?: boolean }) {
  // §51 п.8: горизонт максимум 72 ч — hard cap, even if a published run were longer
  const maxH = Math.min(72, drift.hours?.length ? drift.hours[drift.hours.length - 1] : 72);
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
  // §60 В (Фёдор, 23:08, «дрейф без захламления»): ONE compact block — time control + 2–3 headline numbers, taken
  // only from what the published run actually computed (drift.json.stats); no invented «median distance» when the
  // file only has a mean. Everything else (model, sources, wind coefficient, diffusivity, Stokes drift, corridor
  // definition) moves behind the (i) expander below — collapsed by default, was previously always-on text
  // duplicated both here and in CaseApp.tsx's outer `.c-drift-tag` (removed there, see drift tasklog).
  const st = drift.stats;
  const nums: string[] = [`горизонт ≤ ${maxH} ч`];
  if (typeof st?.mean_displacement_km === 'number') nums.push(`смещение ≈ ${st.mean_displacement_km.toFixed(1)} км`);
  if (typeof st?.stranded_pct === 'number') nums.push(`на берегу ${st.stranded_pct.toFixed(0)} % (72 ч)`);

  return (
    <div className="bottom-bar dp-compact" data-testid="drift-player">
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
      </div>
      <div className="dp-cap dp-cap-compact" data-testid="drift-caption">
        <b>Модельный сценарий</b> по погоде/течениям — не наблюдавшееся перемещение. {nums.join(' · ')}
        {cur ? ` · ≈ ${cur.slice(8, 10)}.${cur.slice(5, 7)} ${cur.slice(11, 16)} UTC` : ''}
        <Info label="Допущения и источники" testid="info-drift-src">
          {driftCaption(f)} {DRIFT_CORRIDOR_LABEL}
          <br />
          {f.model ?? 'OpenDrift'} · течения {shortSrc(f.currents)} · ветер {shortSrc(f.wind)} · коэф. ветра {f.wind_drift_factor ?? '—'} · диффузия{' '}
          {f.horizontal_diffusivity_m2s ?? '—'} м²/с · стоксов дрейф {f.stokes_drift ?? 'не учтён'}
          {ens.length > 0 ? ` · облако неопределённости: коэф. ${wdfs.join('–')}` : ''}
          {flow.length ? ` · частицы: ${flow.map((x) => (x.kind === 'wind' ? 'ветер' : 'течения')).join(' и ')}, поле на +${Math.floor(hour / 6) * 6} ч` : ''}
          {ens.length > 0 && (
            <>
              <br />
              <button
                className="menu-row"
                style={{ width: 'auto', padding: '4px 0 0' }}
                onClick={() => {
                  anim.spread = !spread;
                  setSpread(anim.spread);
                  render.current();
                }}
                data-testid="drift-spread"
                title={`Где были бы частицы при ветровом коэффициенте ${wdfs.join(' и ')}`}
              >
                <span className={`check ${spread ? 'on' : ''}`} aria-hidden />
                <span className="small">показывать разброс ансамбля на карте</span>
              </button>
            </>
          )}
        </Info>
      </div>
    </div>
  );
}

function shortSrc(s?: string) {
  if (!s) return '—';
  return s.split(/,|\(| global| hourly| 10 m/)[0].trim() || s;
}
