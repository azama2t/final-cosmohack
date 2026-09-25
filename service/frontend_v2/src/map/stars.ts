// Calm space behind the globe: a pre-rendered, seeded star tile (static, no twinkle) and a soft atmosphere halo
// at the limb of the Earth. Both are moved only with compositor-friendly properties (transform / opacity) on
// camera moves — no repaint of the full-screen layer while panning.
import { ctl } from './controller';

const TILE = 1024;

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
  // many faint stars, a few brighter ones; warm / cool whites only
  for (let i = 0; i < 1100; i++) {
    const x = r() * TILE,
      y = r() * TILE;
    const m = r();
    const size = m > 0.992 ? 2 : m > 0.96 ? 1.45 : m > 0.8 ? 1.05 : 0.8;
    const a = m > 0.992 ? 1 : m > 0.96 ? 0.85 : m > 0.8 ? 0.62 : 0.3 + r() * 0.3;
    const tint = r();
    const c = tint > 0.82 ? '255,238,220' : tint < 0.22 ? '214,228,255' : '240,242,246';
    ctx.fillStyle = `rgba(${c},${a})`;
    ctx.beginPath();
    ctx.arc(x * dpr, y * dpr, size * dpr * 0.5 + 0.25, 0, Math.PI * 2);
    ctx.fill();
    if (m > 0.992) {
      // soft glow around the brightest stars (static)
      const rg = ctx.createRadialGradient(x * dpr, y * dpr, 0, x * dpr, y * dpr, 5 * dpr);
      rg.addColorStop(0, `rgba(${c},0.22)`);
      rg.addColorStop(1, `rgba(${c},0)`);
      ctx.fillStyle = rg;
      ctx.fillRect(x * dpr - 5 * dpr, y * dpr - 5 * dpr, 10 * dpr, 10 * dpr);
    }
  }
  tileUrl = cv.toDataURL('image/png');
  return tileUrl;
}

/** Globe radius on screen, px (MapLibre globe: world size 512·2^z wraps the sphere). */
export function globeRadiusPx(zoom: number, lat = 0, height = 1000) {
  // MapLibre v5 keeps the scale at the centre equal to Mercator: nominal radius = worldSize / (2π·cos φ).
  // The camera is a perspective one (fov ≈ 36.87°, distance to the centre point = 1.5·height), so the visible limb
  // (silhouette) is smaller: Rs = f·R / sqrt(d² − R²), f = 1.5·H, d = 1.5·H + R.
  const R = (512 * Math.pow(2, zoom)) / (2 * Math.PI * Math.max(0.2, Math.cos((lat * Math.PI) / 180)));
  const f = 1.5 * height;
  const d = f + R;
  return (f * R) / Math.sqrt(d * d - R * R);
}

/**
 * Attaches the star background (oversized layer, moved by translate3d) and the atmosphere halo
 * (a radial gradient centred on the globe, scaled with the zoom) to the map camera.
 */
export function attachStars(el: HTMLElement, halo: HTMLElement): () => void {
  el.style.backgroundImage = `url(${starTile()})`;
  el.style.backgroundSize = `${TILE}px ${TILE}px`;
  const HALO = 1000; // halo element size, px (scaled with transform)
  halo.style.width = halo.style.height = `${HALO}px`;
  let lastOp = -1;
  let lastHalo = -1;
  const update = () => {
    const map = ctl.map;
    if (!map) return;
    const c = map.getCenter();
    const z = map.getZoom();
    const globe = ctl.projection === 'globe';
    // parallax: a full turn of the globe moves the sky by ~1 tile; latitude by a little
    const x = ((((-c.lng / 360) * TILE) % TILE) + TILE) % TILE;
    const y = ((((c.lat / 180) * TILE * 0.5) % TILE) + TILE) % TILE;
    el.style.transform = `translate3d(${(x - TILE).toFixed(1)}px, ${(y - TILE).toFixed(1)}px, 0)`;
    const op = globe ? Math.max(0, Math.min(1, (5.2 - z) / 2)) : 0;
    if (op !== lastOp) {
      el.style.opacity = String(op);
      lastOp = op;
    }
    // atmosphere: ring just outside the limb; the globe canvas covers the inner part
    const R = globeRadiusPx(z, c.lat, map.getContainer().clientHeight || 1000);
    const hop = globe ? Math.max(0, Math.min(1, (4.2 - z) / 1.5)) : 0;
    if (hop !== lastHalo) {
      halo.style.opacity = String(hop);
      lastHalo = hop;
    }
    if (hop > 0) {
      const k = (R * 2 * 1.12) / HALO;
      halo.style.transform = `translate(-50%, -50%) scale(${k.toFixed(4)})`;
    }
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
      ctl.map.on('resize', onMove);
      update();
    }
  }, 100);
  return () => {
    clearInterval(t);
    ctl.map?.off('move', onMove);
    ctl.map?.off('resize', onMove);
  };
}
