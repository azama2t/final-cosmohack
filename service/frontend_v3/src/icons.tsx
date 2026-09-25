// Inline SVG icons (stroke, 18 px) — no icon font, no extra requests.
const P: Record<string, string> = {
  pin: 'M12 21s-6-5.5-6-10a6 6 0 1 1 12 0c0 4.5-6 10-6 10z M12 11.5a1.8 1.8 0 1 0 0-.01',
  film: 'M4 5h16v14H4z M4 9h16 M4 15h16 M8 5v4 M16 5v4 M8 15v4 M16 15v4',
  layers: 'M12 4l8 4-8 4-8-4z M4 12l8 4 8-4 M4 16l8 4 8-4',
  download: 'M12 4v11 M7 10l5 5 5-5 M5 20h14',
  collapse: 'M15 6l-6 6 6 6',
  expand: 'M9 6l6 6-6 6',
  x: 'M6 6l12 12 M18 6L6 18',
  back: 'M11 6l-6 6 6 6 M5 12h14',
  lasso: 'M12 5c4.5 0 8 2 8 4.5S16.5 14 12 14 4 12 4 9.5 7.5 5 12 5z M7 13.5c-1 1.5-1 3.5 1 4.5 M8 18l-1 3',
  up: 'M6 15l6-6 6 6',
  down: 'M6 9l6 6 6-6',
};

export function Icon({ name }: { name: string }) {
  return (
    <svg className="ic" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={P[name] ?? ''} />
    </svg>
  );
}
