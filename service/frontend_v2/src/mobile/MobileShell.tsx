// L140 (§45/§45а): mobile layer for the «Кейс» v2 UI. It does NOT re-implement CaseApp / SceneZoneCard:
// on ≤ 820 px the existing panels are re-laid out by mobile.css (map full screen, the left column becomes a
// bottom sheet, the right card becomes a full-screen sheet), and this component only adds the chrome around
// them — the sheet handle («N находок · район · дата»), the drag/tap logic, «Назад» over the card, the
// «Фильтры» sheet header, the (i) legend toggle and the «lite globe» switch. It reads what CaseApp renders
// (data-testid hooks) and drives it through the same buttons a user would press, so L132 can change the card
// freely.
import { useCallback, useEffect, useRef, useState } from 'react';
import { openDynamics } from '../case/DynamicsHost';
import './mobile.css';
import './photo-mobile.css'; // «Фото» mode has no shell, only these rules

export const MOBILE_MQ = '(max-width: 820px)';
// §48 timeline: collapsed by default on a phone (the user's own choice, once made, is kept by Timeline itself)
try {
  if (typeof matchMedia !== 'undefined' && matchMedia(MOBILE_MQ).matches && localStorage.getItem('mp.case.timeline') === null) {
    localStorage.setItem('mp.case.timeline', '0');
  }
} catch {
  /* no storage */
}
type SheetState = 'peek' | 'half' | 'full';
const PEEK = 64; // px, handle only

function useMedia(q: string) {
  const [on, setOn] = useState(() => typeof matchMedia !== 'undefined' && matchMedia(q).matches);
  useEffect(() => {
    const m = matchMedia(q);
    const f = () => setOn(m.matches);
    m.addEventListener('change', f);
    return () => m.removeEventListener('change', f);
  }, [q]);
  return on;
}

const $ = (sel: string) => document.querySelector<HTMLElement>(sel);
const txt = (sel: string) => ($(sel)?.textContent ?? '').replace(/\s+/g, ' ').trim();

/** What CaseApp currently shows — read from its DOM (cheap, a MutationObserver batches it). */
interface Snap {
  ready: boolean;
  rightOpen: boolean;
  scene: boolean;
  filtersOpen: boolean;
  modalOpen: boolean;
  menuOpen: boolean;
  demo: boolean;
  title: string;
  sub: string;
  filtersSet: boolean;
}

function plural(n: number, one: string, few: string, many: string) {
  const a = n % 100;
  const b = n % 10;
  if (a > 10 && a < 20) return many;
  if (b === 1) return one;
  if (b >= 2 && b <= 4) return few;
  return many;
}

function readSnap(): Snap {
  const app = $('[data-testid="case-app"]');
  const scene = !!$('[data-testid="scene-title"]');
  let title = '';
  let sub = '';
  if (scene) {
    const n = document.querySelectorAll('[data-testid="sz-item"]').length;
    const name = txt('[data-testid="scene-title"]');
    const d = (document.querySelector('.c-scene-s')?.textContent ?? '').match(/\d{2}\.\d{2}\.\d{4}/)?.[0] ?? '';
    title = [n ? `${n} ${plural(n, 'зона', 'зоны', 'зон')}` : '', name].filter(Boolean).join(' · ');
    sub = d ? `снимок ${d} · Sentinel-2` : 'снимок Sentinel-2';
  } else {
    const counts = txt('[data-testid="counts"]');
    const finds = counts.match(/\d[\d\s]*\s*наход[а-яё]*/)?.[0] ?? '';
    const scenes = counts.match(/\d[\d\s]*\s*сним[а-яё]*/)?.[0] ?? '';
    const years = [...document.querySelectorAll('[data-testid="scene-item"]')]
      .map((e) => Number((e.textContent ?? '').match(/\d{2}\.\d{2}\.(\d{4})/)?.[1]))
      .filter((y) => y > 1900);
    const yr = years.length ? (Math.min(...years) === Math.max(...years) ? `${years[0]}` : `${Math.min(...years)}–${Math.max(...years)}`) : '';
    const regions = new Set(
      [...document.querySelectorAll('[data-testid="scene-item"] .ri-name')].map((e) => (e.textContent ?? '').split('·')[0].trim()).filter(Boolean),
    ).size;
    title = [finds || 'Находки', scenes].filter(Boolean).join(' · ');
    sub = [regions ? `${regions} ${plural(regions, 'район', 'района', 'районов')}` : 'все районы', yr].filter(Boolean).join(' · ');
  }
  return {
    ready: !!app,
    rightOpen: !!app?.classList.contains('right-open'),
    scene,
    filtersOpen: !!$('.c-filters-body'),
    modalOpen: !!$('.c-modal-bg'),
    menuOpen: !!$('.c-menu, [data-testid="layers-panel"]'),
    demo: !!$('[data-testid="demo-tour-card"]'),
    title,
    sub,
    filtersSet: /заданы/.test(txt('[data-testid="filters-toggle"]')),
  };
}

const same = (a: Snap, b: Snap) => (Object.keys(a) as (keyof Snap)[]).every((k) => a[k] === b[k]);

function useCaseSnap(active: boolean): Snap {
  const [s, setS] = useState<Snap>(() => readSnap());
  useEffect(() => {
    if (!active) return;
    let raf = 0;
    const upd = () => {
      if (raf) return;
      raf = requestAnimationFrame(() => {
        raf = 0;
        const n = readSnap();
        setS((o) => (same(o, n) ? o : n));
      });
    };
    const mo = new MutationObserver(upd);
    mo.observe(document.getElementById('root')!, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'], characterData: true });
    upd();
    return () => {
      mo.disconnect();
      cancelAnimationFrame(raf);
    };
  }, [active]);
  return s;
}

/** Simplified globe (no stars / halo, no fly animation) on prefers-reduced-motion or a slow device (FPS). */
function useLiteGlobe() {
  useEffect(() => {
    const html = document.documentElement;
    const rm = matchMedia('(prefers-reduced-motion: reduce)');
    const apply = (why: string | null) => {
      if (why) html.dataset.mlite = why;
      else delete html.dataset.mlite;
    };
    if (rm.matches) apply('reduced-motion');
    const onRm = () => apply(rm.matches ? 'reduced-motion' : html.dataset.mlite === 'fps' ? 'fps' : null);
    rm.addEventListener('change', onRm);
    // FPS probe: 90 frames starting 2 s after load (after the first map paint); median frame > 45 ms (< ~22 fps) → lite
    let raf = 0;
    const times: number[] = [];
    let last = 0;
    const tick = (t: number) => {
      if (last) times.push(t - last);
      last = t;
      if (times.length < 90) raf = requestAnimationFrame(tick);
      else {
        const med = [...times].sort((a, b) => a - b)[times.length >> 1];
        html.dataset.mfps = String(Math.round(1000 / med));
        // only phones/tablets: a slow desktop (e.g. headless demo recording) keeps the full globe
        if (med > 45 && !rm.matches && matchMedia(`${MOBILE_MQ}, (pointer: coarse)`).matches) apply('fps');
      }
    };
    const t0 = setTimeout(() => {
      if (!document.hidden) raf = requestAnimationFrame(tick);
    }, 2000);
    return () => {
      clearTimeout(t0);
      cancelAnimationFrame(raf);
      rm.removeEventListener('change', onRm);
    };
  }, []);
}

function click(sel: string) {
  const el = $(sel);
  if (el) el.click();
  return !!el;
}

export default function MobileShell() {
  const mobile = useMedia(MOBILE_MQ);
  useLiteGlobe();
  const snap = useCaseSnap(mobile);
  const [sheet, setSheet] = useState<SheetState>('peek');
  const [legend, setLegend] = useState(false);
  const drag = useRef<{ y0: number; h0: number; t0: number; moved: boolean } | null>(null);
  const [dragH, setDragH] = useState<number | null>(null);

  // html attributes consumed by mobile.css
  useEffect(() => {
    const h = document.documentElement;
    if (!mobile) {
      delete h.dataset.m;
      delete h.dataset.msheet;
      delete h.dataset.mlegend;
      return;
    }
    h.dataset.m = '1';
    h.dataset.msheet = sheet;
    if (legend) h.dataset.mlegend = '1';
    else delete h.dataset.mlegend;
  }, [mobile, sheet, legend]);
  useEffect(() => {
    const h = document.documentElement;
    if (dragH == null) h.style.removeProperty('--m-sheet-drag');
    else h.style.setProperty('--m-sheet-drag', `${dragH}px`);
  }, [dragH]);

  // scene opened → show its zones; card closed → back to the list at half height
  const prev = useRef(snap);
  useEffect(() => {
    const p = prev.current;
    if (snap.scene && !p.scene) setSheet((s) => (s === 'peek' ? 'half' : s));
    if (!snap.scene && p.scene && !snap.rightOpen) setSheet((s) => (s === 'full' ? 'half' : s));
    if (!snap.rightOpen && p.rightOpen) setSheet((s) => (s === 'peek' ? 'half' : s));
    if (snap.rightOpen && !p.rightOpen) setLegend(false);
    // «Демо ▶» clicks rows of the list: let them be seen
    if (snap.demo && !p.demo) setSheet((s) => (s === 'peek' ? 'half' : s));
    // the tour highlights rows of the list — the «Фильтры» sheet (step 1) must not cover them
    if (snap.demo && snap.filtersOpen) click('[data-testid="filters-toggle"]');
    prev.current = snap;
  }, [snap]);

  const sheetPx = useCallback((s: SheetState) => {
    const vh = window.innerHeight;
    return s === 'peek' ? PEEK : s === 'half' ? Math.round(vh * 0.5) : vh - 60;
  }, []);

  const onDown = (e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest('button:not(.m-handle-main)')) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { y0: e.clientY, h0: sheetPx(sheet), t0: performance.now(), moved: false };
  };
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dy = d.y0 - e.clientY;
    if (Math.abs(dy) > 6) d.moved = true;
    if (d.moved) setDragH(Math.max(PEEK, Math.min(window.innerHeight - 60, d.h0 + dy)));
  };
  const onUp = (e: React.PointerEvent) => {
    const d = drag.current;
    drag.current = null;
    setDragH(null);
    if (!d) return;
    if (!d.moved) {
      setSheet((s) => (s === 'peek' ? 'half' : s === 'half' ? 'full' : 'peek'));
      return;
    }
    const dy = d.y0 - e.clientY;
    const v = dy / Math.max(1, performance.now() - d.t0); // px/ms, + = up
    const h = d.h0 + dy;
    const order: SheetState[] = ['peek', 'half', 'full'];
    let target: SheetState;
    if (Math.abs(v) > 0.6) {
      const i = order.indexOf(sheet) + (v > 0 ? 1 : -1);
      target = order[Math.max(0, Math.min(2, i))];
    } else {
      target = order.reduce((best, s) => (Math.abs(sheetPx(s) - h) < Math.abs(sheetPx(best) - h) ? s : best), 'peek' as SheetState);
    }
    setSheet(target);
  };

  // phone «back»: since §47 п.3 CaseApp keeps its own history (one entry per scene/zone step, popstate restores it),
  // so the shell does not touch history — an extra entry here re-opened the card on «Назад» (18:10).

  if (!mobile || !snap.ready) return null;

  const cardBack = () => {
    // same buttons as on the desktop card, most specific first
    if (click('[data-testid="sz-back"]')) return;
    if (click('[data-testid="studio-back"]')) return;
    if (click('[data-testid="card-close"]')) return;
    click('.right .icon-btn[aria-label="Закрыть"]');
  };

  return (
    <>
      {/* sheet handle: sits on top of the left column (which mobile.css turned into a bottom sheet) */}
      {!snap.rightOpen && !snap.filtersOpen && !snap.modalOpen && (
        <div
          className="m-handle"
          data-testid="m-sheet-handle"
          role="button"
          aria-label={sheet === 'peek' ? 'Открыть список' : 'Свернуть или развернуть список'}
          aria-expanded={sheet !== 'peek'}
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerCancel={() => {
            drag.current = null;
            setDragH(null);
          }}
        >
          <span className="m-grip" aria-hidden />
          <div className="m-handle-row">
            <div className="m-handle-txt">
              <div className="m-handle-t" data-testid="m-sheet-title">
                {snap.title}
              </div>
              <div className="m-handle-s">{snap.sub}</div>
            </div>
            {snap.scene && (
              <button
                className="m-hbtn"
                onClick={(e) => {
                  e.stopPropagation();
                  openDynamics({ scene: new URLSearchParams(location.search).get('scene') });
                }}
                data-testid="m-dynamics"
                title="Динамика района по датам снимков"
              >
                Динамика
              </button>
            )}
            <button
              className={`m-hbtn ${snap.filtersSet ? 'on' : ''}`}
              onClick={(e) => {
                e.stopPropagation();
                click('[data-testid="filters-toggle"]');
              }}
              data-testid="m-filters"
            >
              Фильтры{snap.filtersSet ? ' ·' : ''}
            </button>
          </div>
        </div>
      )}

      {/* «Фильтры» — its own full-screen sheet (the filter form itself is CaseApp's) */}
      {snap.filtersOpen && (
        <div className="m-sheet-head" data-testid="m-filters-head">
          <span className="m-sheet-head-t">Фильтры</span>
          <button className="m-hbtn primary" onClick={() => click('[data-testid="filters-toggle"]')} data-testid="m-sheet-close">
            Готово
          </button>
        </div>
      )}

      {/* card: full-screen sheet with «Назад» */}
      {snap.rightOpen && (
        <div className="m-card-bar" data-testid="m-card-bar">
          <button className="m-back" onClick={cardBack} data-testid="m-card-back">
            ← Назад
          </button>
          <span className="m-card-bar-t">{snap.scene ? 'к зонам снимка' : 'к карте'}</span>
        </div>
      )}

      {/* map-side round buttons: legend (i) */}
      {!snap.rightOpen && !snap.filtersOpen && !snap.modalOpen && (
        <button
          className={`m-fab m-fab-legend ${legend ? 'on' : ''}`}
          onClick={() => setLegend((v) => !v)}
          aria-pressed={legend}
          aria-label="Легенда"
          data-testid="m-legend"
        >
          i
        </button>
      )}
    </>
  );
}
