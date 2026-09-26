// §47 п.7 «Демо ▶»: a real, guided 5-step walkthrough of one real example — Альборан, тайл 30SXE, 11.03.2021,
// зона 1 (Cózar B, отложенная сцена, data/case/scene_zones/demo-cozar-2021-03-11). Никаких выдуманных значений: the
// tour only clicks the real scene/zone list rows already rendered from the API (data-scene / data-zone attributes,
// CaseApp.tsx) and points at real DOM blocks (sz-measured / sz-status-chip / export menu) — every number the user
// sees is whatever the API returned at that moment, never text authored here.
// §50 п.8 (Егор, скрин 11): the trigger used to be its own position:fixed button and covered «Слои» (same top-right
// corner once the right card panel is closed — .toolbar is absolute inside <main>, which then spans to the
// viewport edge). Fix: no more standalone button — CaseApp renders the actual «Демо ▶» button as a normal flex
// sibling of «Слои» inside .toolbar (one group, fixed gaps, same responsive rule), and calls this component's
// imperative `open()` via a ref. DemoTour itself only renders the walkthrough overlay (ring + tooltip card),
// portalled into document.body so it still works above the mobile bottom sheet at 390px.
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import './DemoTour.css';

export interface DemoTourHandle {
  open: () => void;
}

const DEMO_SCENE_KEY = 'demo-cozar-2021-03-11';
const DEMO_ZONE_ID = 'SZ-demo-cozar-2021-03-11-001';
const SEL = {
  step1: '[data-testid="step-1"]',
  step2: '[data-testid="step-2"]',
  scene: `[data-testid="scene-item"][data-scene="${DEMO_SCENE_KEY}"]`,
  zone: `[data-testid="sz-item"][data-zone="${DEMO_ZONE_ID}"]`,
  anySzItem: '[data-testid="sz-item"]',
  // sz-measured/sz-status-chip live inside the collapsible «Подробнее» block and can sit far down the scrollable
  // card — sz-thumb (снимок с контуром, always in the main flow) and sz-qtop (headline: что это/статус/подтверждение)
  // are the equivalent content that's reliably on-screen right after the card opens.
  quality: '[data-testid="sz-thumb"]',
  status: '[data-testid="sz-qtop"]',
  exportBtn: '[data-testid="act-export"]',
  exportMenu: '[data-testid="export-menu"]',
};

interface Step {
  title: string;
  what: string;
  where: string;
  target: string; // to highlight/scroll to once this step's state is reached
}

const STEPS: Step[] = [
  {
    title: '1/5 · Сцена',
    what: 'Снимок Sentinel-2 над Альбораном (тайл 30SXE, 11.03.2021) — отложенная сцена Cózar, не участвовавшая в обучении детектора.',
    where: 'Нажмите «Далее» — откроется список зон этого снимка (то же самое, что клик по строке слева).',
    target: SEL.scene,
  },
  {
    title: '2/5 · Зона',
    what: 'Зона 1 этого снимка — засчитанная защитой находка (верификация Cózar B).',
    where: 'Нажмите «Далее» — откроется карточка зоны с её показателями по снимку.',
    target: SEL.zone,
  },
  {
    title: '3/5 · Качество',
    what: 'Снимок зоны с контуром — то же изображение и та же маска качества, по которым детектор дал результат. Ниже в карточке (вкладка/раздел «Подробнее») — доля воды/облаков/блика и площадь пикселей: метрики по изображению, не число предметов.',
    where: 'Дальше — «Далее».',
    target: SEL.quality,
  },
  {
    title: '4/5 · Итоговый статус',
    what: 'Итоговый статус зоны и подтверждение — то же значение, что в списке зон и в выгрузке.',
    where: 'Нажмите «Далее» — перейдём к выгрузке результата.',
    target: SEL.status,
  },
  {
    title: '5/5 · Выгрузка',
    what: 'Выгрузка того, что сейчас отфильтровано — CSV или GeoJSON, из тех же данных API.',
    where: 'Меню выгрузки открыто — можно закрыть тур и повторить на своих данных.',
    target: SEL.exportMenu,
  },
];

function click(sel: string) {
  (document.querySelector(sel) as HTMLElement | null)?.click();
}

function waitFor(sel: string, timeoutMs = 4000): Promise<HTMLElement | null> {
  return new Promise((resolve) => {
    const t0 = performance.now();
    const tick = () => {
      const el = document.querySelector(sel) as HTMLElement | null;
      if (el) return resolve(el);
      if (performance.now() - t0 > timeoutMs) return resolve(null);
      requestAnimationFrame(tick);
    };
    tick();
  });
}

/** 0 = scene list (nothing open), 1 = scene open (zone list), 2 = zone card open */
function curLevel(): 0 | 1 | 2 {
  if (document.querySelector(SEL.status)) return 2;
  if (document.querySelector(SEL.anySzItem)) return 1;
  return 0;
}

/** Drive the real app (clicks on the real scene/zone rows, same handlers a person would use) to the state step `n`
 *  needs, from whatever state it is currently in — forward, backward or a direct jump all go through this, so
 *  «Назад» is exactly as reliable as «Далее». Idempotent: calling it twice in a row is a no-op. */
async function reachStep(n: number) {
  const wantLevel = n === 0 ? 0 : n === 1 ? 1 : 2;
  if (curLevel() > wantLevel) {
    if (curLevel() === 2) {
      click(SEL.step2); // §47 п.3: one defined action — closes the card, keeps the scene's zone list
      await waitFor(SEL.anySzItem);
    }
    if (wantLevel === 0) {
      click(SEL.step1); // closes the scene, back to the scene list
      await waitFor(SEL.scene);
    }
  }
  if (curLevel() < wantLevel) {
    if (curLevel() === 0) {
      click(SEL.step1);
      await waitFor(SEL.scene);
      click(SEL.scene);
      await waitFor(SEL.anySzItem);
    }
    if (wantLevel === 2) {
      click(SEL.zone);
      await waitFor(SEL.status);
    }
  }
  if (n === 4 && !document.querySelector(SEL.exportMenu)) {
    click(SEL.exportBtn); // toggle — only click if the menu isn't already open
    await waitFor(SEL.exportMenu);
  }
  const el = await waitFor(STEPS[n].target);
  // the target may be lower in a scrollable card (e.g. sz-measured/sz-status-chip) — bring it into view first,
  // otherwise the ring/tooltip would be positioned off-screen (§48 скрин 02: no clipped popovers). The card can
  // reset its own scroll position right after opening, so scroll again a beat later.
  el?.scrollIntoView({ block: 'center', behavior: 'instant' as ScrollBehavior });
  await new Promise((r) => setTimeout(r, 220));
  el?.scrollIntoView({ block: 'center', behavior: 'instant' as ScrollBehavior });
}

/** clamp a tooltip card near `rect` inside the viewport (§48 скрин 02: no clipped/overlapping popovers) */
function place(rect: DOMRect, cardW: number, cardH: number) {
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const margin = 10;
  let top = rect.bottom + margin;
  if (top + cardH > vh - margin) top = Math.max(margin, rect.top - cardH - margin);
  let left = rect.left;
  if (left + cardW > vw - margin) left = vw - margin - cardW;
  if (left < margin) left = margin;
  return { top, left };
}

export default forwardRef<DemoTourHandle>(function DemoTour(_props, ref) {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [busy, setBusy] = useState(false);
  const cardRef = useRef<HTMLDivElement | null>(null);

  // keep the highlight ring glued to its target while scrolling/resizing/animating
  useEffect(() => {
    if (!open) return;
    let raf = 0;
    const tick = () => {
      const el = document.querySelector(STEPS[step].target) as HTMLElement | null;
      if (el) {
        const r = el.getBoundingClientRect();
        setRect((prev) => (prev && prev.top === r.top && prev.left === r.left && prev.width === r.width ? prev : r));
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [open, step]);

  const gotoStep = async (n: number) => {
    setBusy(true);
    await reachStep(n);
    setStep(n);
    setBusy(false);
  };

  const start = async () => {
    setOpen(true);
    await gotoStep(0);
  };
  useImperativeHandle(ref, () => ({ open: start }));

  const next = async () => {
    if (step >= STEPS.length - 1) return finish();
    await gotoStep(step + 1);
  };
  const back = async () => {
    if (step === 0) return;
    await gotoStep(step - 1);
  };
  const exit = () => {
    setOpen(false);
    setStep(0);
  };
  const finish = () => {
    // «Начать свой поиск» → шаг 1 с фокусом на «Район» — the app's own goStep1() already does exactly this
    click(SEL.step1);
    exit();
  };

  const s = STEPS[step];
  const cardW = 320;
  const cardH = 180;
  const pos = rect ? place(rect, cardW, cardH) : { top: 80, left: Math.max(10, window.innerWidth - cardW - 20) };

  return (
    <>
      {open &&
        createPortal(
          <>
            {rect && (
              <div
                className="dt-ring"
                data-testid="demo-tour-ring"
                style={{ top: rect.top - 4, left: rect.left - 4, width: rect.width + 8, height: rect.height + 8 }}
              />
            )}
            <div className="dt-card" ref={cardRef} data-testid="demo-tour-card" style={{ top: pos.top, left: pos.left }}>
              <div className="dt-step-n">Демо · шаг {s.title}</div>
              <div className="dt-what">
                <b>Что показано:</b> {s.what}
              </div>
              <div className="dt-where">
                <b>Куда нажать:</b> {s.where}
              </div>
              <div className="dt-dots">
                {STEPS.map((_, i) => (
                  <span key={i} className={`dt-dot ${i === step ? 'on' : ''}`} />
                ))}
              </div>
              <div className="dt-row">
                <button className="dt-btn2 ghost" onClick={exit} data-testid="demo-tour-exit">
                  Выйти
                </button>
                <span className="sp" />
                <button className="dt-btn2" onClick={back} disabled={step === 0 || busy} data-testid="demo-tour-back">
                  ← Назад
                </button>
                {step < STEPS.length - 1 ? (
                  <button className="dt-btn2 primary" onClick={next} disabled={busy} data-testid="demo-tour-next">
                    {busy ? 'Секунду…' : 'Далее →'}
                  </button>
                ) : (
                  <button className="dt-btn2 primary" onClick={finish} data-testid="demo-tour-start-search">
                    Начать свой поиск
                  </button>
                )}
              </div>
            </div>
          </>,
          document.body,
        )}
    </>
  );
});
