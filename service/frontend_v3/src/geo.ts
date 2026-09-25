// Small geometry + formatting helpers (no dependencies).
import type { Geometry } from './api';

export type BBox = [number, number, number, number];

export function geomBounds(g: Geometry | null | undefined): BBox | null {
  if (!g) return null;
  let b: BBox = [180, 90, -180, -90];
  const walk = (c: any) => {
    if (typeof c[0] === 'number') b = [Math.min(b[0], c[0]), Math.min(b[1], c[1]), Math.max(b[2], c[0]), Math.max(b[3], c[1])];
    else c.forEach(walk);
  };
  walk(g.coordinates);
  return b[0] <= b[2] ? b : null;
}

export const bboxCenter = (b: BBox): [number, number] => [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2];

/** grow a bbox by `km` on every side */
export function padBBox(b: BBox, km: number): BBox {
  const dLat = km / 111.32;
  const dLon = km / (111.32 * Math.max(0.2, Math.cos((((b[1] + b[3]) / 2) * Math.PI) / 180)));
  return [b[0] - dLon, b[1] - dLat, b[2] + dLon, b[3] + dLat];
}

export const bboxHit = (a: BBox, b: BBox) => a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
export const inBBox = (p: number[], b: BBox) => p[0] >= b[0] && p[0] <= b[2] && p[1] >= b[1] && p[1] <= b[3];

/** ray casting, ring as [x,y][] */
export function inRing(x: number, y: number, ring: number[][]): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export function ringBBox(ring: number[][]): BBox {
  let b: BBox = [180, 90, -180, -90];
  for (const [x, y] of ring) b = [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)];
  return b;
}

// ------------------------------------------------------------------ formatting (ru)
export function fmtDate(s: string | null | undefined): string {
  if (!s) return '—';
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : s;
}
export function fmtTime(s: string | null | undefined): string {
  const m = s ? /T(\d{2}):(\d{2})/.exec(s) : null;
  return m ? `${m[1]}:${m[2]} UTC` : '';
}
export function fmtNum(v: number | null | undefined, digits?: number): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—';
  const d = digits ?? (Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : 2);
  return v.toLocaleString('ru-RU', { maximumFractionDigits: d, minimumFractionDigits: 0 });
}
export function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return `${fmtNum(v <= 1 ? v * 100 : v, 0)} %`;
}
export const dayKey = (s: string | null | undefined) => (s ? s.slice(0, 10) : '');
export function plural(n: number, one: string, few: string, many: string) {
  const a = Math.abs(n) % 100,
    b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

/** «>2 cm (macro)» → «> 2 см (макро)» (size classes come from the organisers' CSV as is) */
export function sizeRu(s: string | null | undefined): string {
  if (!s) return '—';
  return s
    .replace(/([<>≥≤])\s*(\d)/g, '$1 $2')
    .replace(/(\d)\s*cm\b/g, '$1 см')
    .replace(/(\d)\s*mm\b/g, '$1 мм')
    .replace(/\bmacro\b/g, 'макро')
    .replace(/\bmicro\b/g, 'микро')
    .replace(/\bmeso\b/g, 'мезо');
}
/** «дд.мм.гггг» (or ISO) → «YYYY-MM-DD»; null for an empty or invalid entry */
export function parseRuDate(s: string): string | null | undefined {
  const t = s.trim();
  if (!t) return null;
  let m = /^(\d{1,2})[.\/-](\d{1,2})[.\/-](\d{4})$/.exec(t);
  let y: number, mo: number, d: number;
  if (m) [d, mo, y] = [Number(m[1]), Number(m[2]), Number(m[3])];
  else if ((m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(t))) [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  else return undefined;
  const dt = new Date(Date.UTC(y, mo - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return undefined;
  return dt.toISOString().slice(0, 10);
}
