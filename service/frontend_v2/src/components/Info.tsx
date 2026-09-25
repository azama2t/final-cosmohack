import { useEffect, useRef, useState, type ReactNode } from 'react';

/**
 * «i»: long explanations, formulas and technical details live here, not in the main view.
 * Click (or Enter) opens a small popover; a click outside or Esc closes it.
 */
export default function Info({ children, label = 'Пояснение', testid, align = 'left' }: { children: ReactNode; label?: string; testid?: string; align?: 'left' | 'right' }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('mousedown', off);
    window.addEventListener('keydown', esc);
    return () => {
      window.removeEventListener('mousedown', off);
      window.removeEventListener('keydown', esc);
    };
  }, [open]);
  return (
    <span className="info" ref={box}>
      <button
        type="button"
        className={`info-btn ${open ? 'on' : ''}`}
        aria-label={label}
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        data-testid={testid}
      >
        i
      </button>
      {open && (
        <span className={`info-pop ${align === 'right' ? 'r' : ''}`} role="note" data-testid={testid ? `${testid}-pop` : undefined}>
          {children}
        </span>
      )}
    </span>
  );
}
