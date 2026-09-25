// Client-side crop of rgb.png around a point (fallback when /api/crop is absent): pixel-exact, detection
// contours in the accent colour, optional H3 cell outline, centre ticks and a scale bar.
import type { Bounds } from '../types';
import { ACCENT } from './style';

export interface CropOpts {
  img: HTMLImageElement;
  bounds: Bounds;
  lon: number;
  lat: number;
  sizeM: number;
  /** polygons (lon/lat rings) to outline */
  polys?: number[][][][];
  /** H3 cell boundary [[lon,lat],...] */
  cell?: [number, number][];
  cssPx: number;
}

export function drawCrop(cv: HTMLCanvasElement, o: CropOpts) {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  cv.width = o.cssPx * dpr;
  cv.height = o.cssPx * dpr;
  const ctx = cv.getContext('2d')!;
  const [w, s, e, n] = o.bounds;
  const W = o.img.naturalWidth,
    H = o.img.naturalHeight;
  const mLat = 111320;
  const mLon = 111320 * Math.cos((o.lat * Math.PI) / 180);
  const half = o.sizeM / 2;
  const x0 = ((o.lon - half / mLon - w) / (e - w)) * W;
  const x1 = ((o.lon + half / mLon - w) / (e - w)) * W;
  const y0 = ((n - (o.lat + half / mLat)) / (n - s)) * H;
  const y1 = ((n - (o.lat - half / mLat)) / (n - s)) * H;
  ctx.fillStyle = '#07111f';
  ctx.fillRect(0, 0, cv.width, cv.height);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(o.img, x0, y0, x1 - x0, y1 - y0, 0, 0, cv.width, cv.height);
  const toPx = (pt: number[]) => [
    ((pt[0] - (o.lon - half / mLon)) / ((2 * half) / mLon)) * cv.width,
    ((o.lat + half / mLat - pt[1]) / ((2 * half) / mLat)) * cv.height,
  ];
  const path = (ring: number[][]) => {
    ctx.beginPath();
    ring.forEach((pt, i) => {
      const [x, y] = toPx(pt);
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.closePath();
  };
  if (o.cell?.length) {
    ctx.setLineDash([5 * dpr, 4 * dpr]);
    ctx.lineWidth = 1.2 * dpr;
    ctx.strokeStyle = 'rgba(255,255,255,0.75)';
    path(o.cell);
    ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.lineWidth = 2 * dpr;
  ctx.strokeStyle = ACCENT;
  ctx.fillStyle = 'rgba(255,107,74,0.18)';
  ctx.shadowColor = 'rgba(0,0,0,0.6)';
  ctx.shadowBlur = 4 * dpr;
  for (const poly of o.polys ?? [])
    for (const ring of poly) {
      path(ring);
      ctx.fill();
      ctx.stroke();
    }
  ctx.shadowBlur = 0;
  // centre ticks
  const cx = cv.width / 2,
    cy = cv.height / 2;
  ctx.strokeStyle = 'rgba(255,255,255,0.8)';
  ctx.lineWidth = 1 * dpr;
  for (const [a, b, c, d] of [
    [-14, 0, -6, 0],
    [6, 0, 14, 0],
    [0, -14, 0, -6],
    [0, 6, 0, 14],
  ]) {
    ctx.beginPath();
    ctx.moveTo(cx + a * dpr, cy + b * dpr);
    ctx.lineTo(cx + c * dpr, cy + d * dpr);
    ctx.stroke();
  }
  // scale bar
  const barM = o.sizeM > 3000 ? 1000 : o.sizeM > 1200 ? 250 : 100;
  const barPx = (barM / o.sizeM) * cv.width;
  const bx = 14 * dpr,
    by = cv.height - 16 * dpr;
  ctx.fillStyle = 'rgba(7,17,31,0.7)';
  ctx.fillRect(bx - 6 * dpr, by - 18 * dpr, barPx + 12 * dpr, 26 * dpr);
  ctx.fillStyle = '#fff';
  ctx.fillRect(bx, by, barPx, 3 * dpr);
  ctx.font = `${11 * dpr}px Inter, sans-serif`;
  ctx.fillText(barM >= 1000 ? `${barM / 1000} км` : `${barM} м`, bx, by - 5 * dpr);
}

/** Polygons of a GeoJSON geometry as [poly][ring][pt]. */
export function geomPolys(g: { type: string; coordinates: any }): number[][][][] {
  if (g.type === 'Polygon') return [g.coordinates];
  if (g.type === 'MultiPolygon') return g.coordinates;
  return [];
}
