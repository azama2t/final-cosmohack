import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/**
 * «i»: long explanations, formulas and technical details live here, not in the main view.
 * Click (or Enter) opens a small popover; a click outside or Esc closes it.
 *
 * §48 (L142): on desktop the popover is rendered in <body> (portal, position: fixed) and placed
 * collision-aware — below the button, flipped above if there is no room, shifted horizontally to
 * stay inside the window with an 8 px margin, max-height = free space (scrolls inside). No parent
 * overflow / stacking context can clip it. On phones (html[data-m] / ≤ 820 px) the old inline
 * element is kept: mobile.css turns it into a fixed bottom sheet.
 */
const PAD = 8;
const GAP = 6;
const W_MAX = 320;

function isMobile(): boolean {
  if (typeof window === 'undefined') return false;
  return !!document.documentElement.dataset.m || window.matchMedia('(max-width: 820px)').matches;
}

export default function Info({ children, label = 'Пояснение', testid, align = 'left' }: { children: ReactNode; label?: string; testid?: string; align?: 'left' | 'right' }) {
  const [open, setOpen] = useState(false);
  const [float, setFloat] = useState(false);
  const [pos, setPos] = useState<CSSProperties>({ left: -9999, top: -9999, visibility: 'hidden' });
  const box = useRef<HTMLSpanElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLSpanElement>(null);

  const place = useCallback(() => {
    const b = btn.current?.getBoundingClientRect();
    const p = pop.current;
    if (!b || !p) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const w = Math.min(W_MAX, vw - 2 * PAD);
    p.style.width = `${w}px`;
    p.style.maxHeight = '';
    const h = p.scrollHeight;
    // horizontal: prefer the requested side, then shift into the window
    let left = align === 'right' ? b.right + 8 - w : b.left - 8;
    left = Math.max(PAD, Math.min(left, vw - PAD - w));
    // vertical: below if it fits, else above if more room there; cap by free space
    const below = vh - b.bottom - GAP - PAD;
    const above = b.top - GAP - PAD;
    let top: number;
    let maxH: number;
    if (h <= below || below >= above) {
      top = b.bottom + GAP;
      maxH = below;
    } else {
      maxH = above;
      top = Math.max(PAD, b.top - GAP - Math.min(h, above));
    }
    setPos({ left, top, width: w, maxHeight: Math.max(80, maxH), visibility: 'visible' });
  }, [align]);

  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => {
      const t = e.target as Node;
      if (box.current?.contains(t) || pop.current?.contains(t)) return;
      setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('mousedown', off);
    window.addEventListener('keydown', esc);
    return () => {
      window.removeEventListener('mousedown', off);
      window.removeEventListener('keydown', esc);
    };
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !float) return;
    place();
    const re = () => place();
    window.addEventListener('resize', re);
    window.addEventListener('scroll', re, true);
    return () => {
      window.removeEventListener('resize', re);
      window.removeEventListener('scroll', re, true);
    };
  }, [open, float, place]);

  const popEl = (
    <span
      ref={pop}
      className={float ? 'info-pop info-pop-float' : `info-pop ${align === 'right' ? 'r' : ''}`}
      style={float ? pos : undefined}
      role="note"
      data-testid={testid ? `${testid}-pop` : undefined}
      onMouseDown={(e) => e.stopPropagation()}
    >
      {children}
    </span>
  );

  return (
    <span className="info" ref={box}>
      <button
        ref={btn}
        type="button"
        className={`info-btn ${open ? 'on' : ''}`}
        aria-label={label}
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          if (!open) {
            setFloat(!isMobile());
            setPos({ left: -9999, top: -9999, visibility: 'hidden' });
          }
          setOpen((v) => !v);
        }}
        data-testid={testid}
      >
        i
      </button>
      {open && (float ? createPortal(popEl, document.body) : popEl)}
    </span>
  );
}
