// L140 · §54 п.5 → §55 п.2 в / §55а: PRIME MODE · демо-данные (ДЕМО-МАКЕТ по запросу жюри). Self-contained host (main.tsx).
// Off by default → no demo object on screen (no panel, no map layer). On → docked magenta panel whose header is the badge
// «ДЕМО: как сервис будет выглядеть на детальных снимках…» (visible for the whole mode; the panel can only be minimised);
// demo scenes at the CSV event points (own map layer prime-csv*, PrimeCsv.tsx, API of L154 /api/prime/csv_scenes),
// click → card of the future view (class, N шт., шт./км², boxes; numbers from the CSV row = «демо-значение»).
// The §54 6-scene variant is removed from the UI (it carried quality metrics; PRIME must not show any).
// Real map layers are muted while on; NASA ('c-nasa') is untouched → NASA and PRIME toggle independently.
// Inline toggle for a top bar: <PrimeToggle /> (then the floating one hides itself). Programmatic: setPrime(true|false).
import { Component, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { CsvCard, CsvExperiment, CsvList, DEMO_BADGE, flyToScene, useCsvIndex, usePrimeCsvLayer } from './PrimeCsv';
import './prime.css';

const EV = 'mp:prime';
let primeOn = new URLSearchParams(location.search).get('prime') === '1';
export function setPrime(on: boolean) {
  primeOn = on;
  window.dispatchEvent(new CustomEvent<boolean>(EV, { detail: on }));
}
function usePrime(): boolean {
  const [on, setOn] = useState(primeOn);
  useEffect(() => {
    const f = (e: Event) => setOn((e as CustomEvent<boolean>).detail);
    window.addEventListener(EV, f);
    return () => window.removeEventListener(EV, f);
  }, []);
  return on;
}

export function PrimeToggle({ inline = true }: { inline?: boolean }) {
  const on = usePrime();
  return (
    <button
      className={`prime-toggle ${on ? 'on' : ''} ${inline ? 'inline' : ''}`}
      aria-pressed={on}
      data-testid={inline ? 'prime-toggle-inline' : 'prime-toggle'}
      title="PRIME MODE: как сервис будет выглядеть на детальных снимках — демо-данные, не результат модели"
      onClick={() => setPrime(!on)}
    >
      <span className="prime-sw" aria-hidden />
      <span className="prime-lab">
        PRIME MODE <span className="prime-sub">· демо-данные</span>
      </span>
    </button>
  );
}

/** mute the real layers of the case map while PRIME is on: real find/zone vectors hidden, snapshot rasters at 35 %
 *  (the NASA raster 'c-nasa' and «Реальное время» are left untouched → independent of PRIME) */
function useDimRealLayers(on: boolean) {
  useEffect(() => {
    if (!on) return;
    const hidden = new Set<string>();
    const dimmed = new Map<string, any>();
    let map: any = null;
    const m0 = (window as any).__caseMap;
    // §60 А1: the full camera is restored on off. flyToScene / fitBounds of the demo points fly with a big
    // left/bottom padding, and MapLibre keeps flyTo padding on the camera → restoring only center+zoom
    // left the globe shifted off screen. So padding, bearing and pitch are saved and restored too.
    const cam = m0?.getCenter
      ? { center: m0.getCenter(), zoom: m0.getZoom(), bearing: m0.getBearing(), pitch: m0.getPitch(), padding: m0.getPadding?.() }
      : null;
    const apply = () => {
      map = (window as any).__caseMap;
      if (!map?.getStyle || !map.style) return;
      for (const l of map.getStyle()?.layers ?? []) {
        if (!l.id.startsWith('c-') || l.id.startsWith('c-nasa') || /live|rt-/.test(l.id)) continue;
        if (l.type === 'raster') {
          if (!dimmed.has(l.id)) {
            dimmed.set(l.id, map.getPaintProperty(l.id, 'raster-opacity') ?? 1);
            map.setPaintProperty(l.id, 'raster-opacity', 0.35);
          }
          continue;
        }
        if (map.getLayoutProperty(l.id, 'visibility') !== 'none') {
          map.setLayoutProperty(l.id, 'visibility', 'none');
          hidden.add(l.id);
        }
      }
    };
    const safe = () => {
      try {
        apply();
      } catch {
        /* style reloading */
      }
    };
    safe();
    const t = window.setInterval(safe, 800); // CaseMap may (re)add layers; the map may mount later
    document.body.classList.add('prime-on');
    return () => {
      window.clearInterval(t);
      document.body.classList.remove('prime-on');
      if (map?.getLayer && map.style) {
        for (const id of hidden) if (map.getLayer(id)) map.setLayoutProperty(id, 'visibility', 'visible');
        for (const [id, v] of dimmed) if (map.getLayer(id)) map.setPaintProperty(id, 'raster-opacity', v);
      }
      // camera: stop a demo fly still in progress, then put back the pre-PRIME view (or at least clear the demo padding)
      const mc = (window as any).__caseMap;
      try {
        if (mc?.jumpTo && mc.style) {
          mc.stop?.();
          const zero = { top: 0, bottom: 0, left: 0, right: 0 };
          if (cam && mc === m0) mc.jumpTo({ ...cam, padding: cam.padding ?? zero });
          else mc.jumpTo({ padding: zero });
        }
      } catch {
        /* map removed */
      }
    };
  }, [on]);
}

function useMainRect(active: boolean) {
  const [r, setR] = useState<DOMRect | null>(null);
  useLayoutEffect(() => {
    if (!active) return;
    let ro: ResizeObserver | null = null;
    let el: Element | null = null;
    const upd = () => {
      const m = document.querySelector('.app.case .main') ?? document.querySelector('.main');
      if (m && m !== el) {
        ro?.disconnect();
        el = m;
        ro = new ResizeObserver(upd);
        ro.observe(m);
      }
      setR(m ? m.getBoundingClientRect() : null);
    };
    upd();
    const t = window.setInterval(upd, 1000);
    window.addEventListener('resize', upd);
    return () => {
      ro?.disconnect();
      window.clearInterval(t);
      window.removeEventListener('resize', upd);
    };
  }, [active]);
  return r;
}

function PrimePanel({ onClose, mobile }: { onClose: () => void; mobile: boolean }) {
  const [sel, setSel] = useState<string | null>(null);
  const [mOpen, setMOpen] = useState(false);
  const [min, setMin] = useState(false);
  const { ix, err } = useCsvIndex(true);
  const scRef = useRef(ix?.scenes);
  scRef.current = ix?.scenes;
  const pick = useCallback((id: string) => {
    setSel(id);
    const sc = scRef.current?.find((x) => x.id === id);
    if (sc) flyToScene(sc);
    setMin(false);
  }, []);
  usePrimeCsvLayer(true, ix?.scenes, sel, pick);
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && (sel ? setSel(null) : onClose());
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [onClose, sel]);
  const cur = ix?.scenes.find((x) => x.id === sel) ?? null;
  const bodyRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bodyRef.current?.scrollTo(0, 0);
  }, [sel]);
  return (
    <section className={`prime-panel ${min ? 'min' : ''}`} data-testid="prime-panel" role="dialog" aria-label="PRIME MODE · демо-данные">
      <header className="prime-head">
        <span className="prime-badge" data-testid="prime-badge">
          {DEMO_BADGE}
        </span>
        <button className="prime-x" onClick={() => setMin(!min)} aria-label={min ? 'Развернуть' : 'Свернуть'} data-testid="prime-min" title={min ? 'Развернуть' : 'Свернуть (плашка останется)'}>
          {min ? (mobile ? '▴' : '▾') : mobile ? '▾' : '▴'}
        </button>
        <button className="prime-x" onClick={onClose} aria-label="Выключить PRIME MODE" data-testid="prime-close" title="Выключить PRIME MODE">
          ✕
        </button>
      </header>
      {!min && (
        <div className="prime-body" ref={bodyRef}>
          {(
            <>
              {err && <p className="prime-err">Демо-сцены по CSV: {err}</p>}
              {!ix && !err && <p className="prime-muted">Загрузка…</p>}
              {ix && (
                <>
                  {ix.stub && <p className="prime-err" data-testid="prime-stub">ЗАГЛУШКА вёрстки — числа не настоящие</p>}
                  {cur ? (
                    <CsvCard s={cur} onBack={() => setSel(null)} />
                  ) : (
                    <>
                      <p className="prime-about">
                        Так будет выглядеть сервис, когда появятся детальные снимки (дрон, самолёт, коммерческий спутник): для каждой строки CSV
                        организаторов — «снимок» скопления из реальной воды и вырезок реальных предметов из дрон-наборов, карточка с классом,
                        количеством и плотностью. Пурпурные точки на карте — события CSV ({ix.scenes.length}); нажмите точку или строку.
                      </p>
                      <CsvList
                        scenes={ix.scenes}
                        onPick={(s) => {
                          setSel(s.id);
                          flyToScene(s);
                        }}
                      />
                    </>
                  )}
                  <CsvExperiment m={ix.metrics} open={mOpen} setOpen={setMOpen} />
                  <p className="prime-muted pc-foot">
                    Демо-режим по запросу жюри: снимков такого разрешения в этих точках у нас нет, изображения и числа — демонстрационные, метрик
                    качества здесь нет. Реальные слои карты приглушены; NASA и «Реальное время» не затронуты. Генератор: <code>{ix.generator}</code>
                  </p>
                </>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

const OBST =
  'button, a[href], select, input, .c-nasa-note, .c-legend, .menu, [data-testid="nasa-note"], [data-testid="timeline"], .c-timeline, ' +
  '.c-modal-bg, .dy-panel, .app.case.right-open .right, [data-testid="scene-zone-card"]';
type Pos = { top: number; right: number };
/** first free spot for the floating toggle: never on top of another control / the NASA note / legend / timeline */
function placeToggle(R: DOMRect, mobile: boolean, tw: number, th: number): Pos | null {
  const W = window.innerWidth;
  const obs: DOMRect[] = [];
  for (const e of document.querySelectorAll(OBST)) {
    if (e.closest('.prime-float, .prime-wrap')) continue;
    const q = e.getBoundingClientRect();
    if (q.width > 0 && q.height > 0 && getComputedStyle(e).visibility !== 'hidden') obs.push(q);
  }
  const free = (p: Pos) => {
    const l = W - p.right - tw, t = p.top, r = W - p.right, b = p.top + th;
    if (l < Math.max(R.left, 0) + 4 || r > W - 2 || b > R.bottom - 4) return false;
    return !obs.some((q) => q.left < r + 4 && q.right > l - 4 && q.top < b + 4 && q.bottom > t - 4);
  };
  const cands: Pos[] = [];
  const demo = [...document.querySelectorAll('button')].find((b) => /^Демо/.test((b.textContent ?? '').trim()));
  if (demo && !mobile) {
    // left of the leftmost button of the «Демо» row (NASA / Демо / Слои …)
    const d = demo.getBoundingClientRect();
    let x = d.left;
    for (let moved = true; moved; ) {
      moved = false;
      for (const q of obs)
        if (Math.abs(q.top - d.top) < 8 && q.right <= x + 1 && q.right > x - 24 && q.left < x) {
          x = q.left;
          moved = true;
        }
    }
    cands.push({ top: d.top + (d.height - th) / 2, right: W - x + 8 });
  }
  const edge = W - R.right + (mobile ? 12 : 16);
  for (let y = R.top + (mobile ? 56 : 64); y < R.bottom - th - 120; y += 23)
    for (const dx of mobile ? [0, 48] : [0]) cands.push({ top: y, right: edge + dx });
  return cands.find(free) ?? null; // no free spot (e.g. a card sheet covers the phone screen) → hide the toggle
}

/** PRIME must never take the whole app down: any render/effect error inside stays inside this boundary */
class PrimeBoundary extends Component<{ children: ReactNode }, { err: string | null }> {
  state = { err: null as string | null };
  static getDerivedStateFromError(e: unknown) {
    return { err: String((e as Error)?.message ?? e) };
  }
  componentDidCatch(e: unknown) {
    console.warn('[PRIME] error contained:', e);
  }
  render() {
    if (this.state.err)
      return (
        <div className="prime-wrap prime-crashed" style={{ top: 120, left: 16, width: 320 }}>
          <section className="prime-panel" data-testid="prime-panel">
            <header className="prime-head">
              <span className="prime-badge" data-testid="prime-badge">
                {DEMO_BADGE}
              </span>
              <button className="prime-x" onClick={() => { this.setState({ err: null }); setPrime(false); }} aria-label="Выключить PRIME MODE" data-testid="prime-close">
                ✕
              </button>
            </header>
            <p className="prime-err" style={{ padding: '8px 14px' }}>Демо-режим не открылся ({this.state.err}). Остальной сервис работает.</p>
          </section>
        </div>
      );
    return this.props.children;
  }
}

export default function PrimeHost() {
  return (
    <PrimeBoundary>
      <PrimeHostInner />
    </PrimeBoundary>
  );
}

function PrimeHostInner() {
  const on = usePrime();
  const [inlinePresent, setInlinePresent] = useState(false);
  const [pos, setPos] = useState<Pos | null | undefined>(undefined);
  const rect = useMainRect(true);
  useDimRealLayers(on);
  useEffect(() => {
    const chk = () => {
      setInlinePresent(!!document.querySelector('[data-testid="prime-toggle-inline"]'));
      const m = document.querySelector('.app.case .main') ?? document.querySelector('.main');
      if (!m) return;
      const t = document.querySelector('.prime-float')?.getBoundingClientRect();
      const p = placeToggle(m.getBoundingClientRect(), window.innerWidth <= 820, t?.width || 240, t?.height || 44);
      setPos((o) => (o && p && Math.abs(o.top - p.top) < 1 && Math.abs(o.right - p.right) < 1 ? o : p));
    };
    const t0 = window.setTimeout(chk, 300);
    const t = window.setInterval(chk, 1000);
    window.addEventListener('resize', chk);
    return () => {
      window.clearTimeout(t0);
      window.clearInterval(t);
      window.removeEventListener('resize', chk);
    };
  }, []);
  if (!rect || rect.width < 10) return null;
  const mobile = window.innerWidth <= 820;
  const tgStyle = pos ?? { top: rect.top + 64, right: 16, visibility: 'hidden' as const };
  const panelStyle = mobile
    ? undefined
    : { top: rect.top + 108, left: rect.left + 16, maxHeight: Math.max(160, rect.bottom - rect.top - 124), width: Math.min(440, rect.width - 32) };
  return (
    <>
      {!inlinePresent && pos !== null && (
        <div className="prime-float" style={tgStyle}>
          <PrimeToggle inline={false} />
        </div>
      )}
      {on && (
        <div className={`prime-wrap ${mobile ? 'mobile' : ''}`} style={panelStyle}>
          <PrimePanel onClose={() => setPrime(false)} mobile={mobile} />
        </div>
      )}
    </>
  );
}
