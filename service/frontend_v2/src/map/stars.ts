// Calm star field behind the globe: one pre-rendered tile (seeded, static) used as a CSS background;
// it shifts very slowly with the globe rotation (parallax) and fades out when zooming into a region.
import { ctl } from './controller';

const TILE = 768;

function rng(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

let tileUrl: string | null = null;
export function starTile(): string {
  if (tileUrl) return tileUrl;
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const cv = document.createElement('canvas');
  cv.width = cv.height = TILE * dpr;
  const ctx = cv.getContext('2d')!;
  const r = rng(20260925);
  // many faint stars, few brighter ones; warm/cool whites only
  for (let i = 0; i < 260; i++) {
    const x = r() * TILE,
      y = r() * TILE;
    const m = r();
    const size = m > 0.985 ? 1.3 : m > 0.9 ? 0.9 : 0.6;
    const a = m > 0.985 ? 0.75 : m > 0.9 ? 0.45 : 0.18 + r() * 0.2;
    const tint = r();
    const c = tint > 0.8 ? '255,240,225' : tint < 0.2 ? '220,232,255' : '240,242,245';
    ctx.fillStyle = `rgba(${c},${a})`;
    ctx.beginPath();
    ctx.arc(x * dpr, y * dpr, size * dpr * 0.5 + 0.25, 0, Math.PI * 2);
    ctx.fill();
  }
  tileUrl = cv.toDataURL('image/png');
  return tileUrl;
}

/** Attaches the star background to an element and keeps it in sync with the map camera. */
export function attachStars(el: HTMLElement): () => void {
  el.style.backgroundImage = `url(${starTile()})`;
  el.style.backgroundSize = `${TILE}px ${TILE}px`;
  const update = () => {
    const map = ctl.map;
    if (!map) return;
    const c = map.getCenter();
    const z = map.getZoom();
    // parallax: a full turn of the globe moves the sky by ~1 tile; latitude by a little
    el.style.backgroundPosition = `${(-c.lng / 360) * TILE}px ${(c.lat / 180) * TILE * 0.5}px`;
    const op = ctl.projection === 'globe' ? Math.max(0, Math.min(1, (5.2 - z) / 2)) : 0;
    el.style.opacity = String(op);
  };
  let raf = 0;
  const onMove = () => {
    if (raf) return;
    raf = requestAnimationFrame(() => {
      raf = 0;
      update();
    });
  };
  const t = setInterval(() => {
    if (ctl.map) {
      clearInterval(t);
      ctl.map.on('move', onMove);
      update();
    }
  }, 100);
  return () => {
    clearInterval(t);
    ctl.map?.off('move', onMove);
  };
}
