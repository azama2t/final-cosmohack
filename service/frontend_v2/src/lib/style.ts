// v2 palette (L48): dark neutral surfaces, ONE accent for findings, one separate sequential scale
// for the index / survey priority. Keep in sync with styles.css (:root variables).
export const ACCENT = '#ffc53d'; // the ONE signal colour: findings (signs of floating material) only
export const ACCENT_RGB: [number, number, number] = [255, 197, 61];

// Sequential «ice» ramp for the H3 index and survey priority: lightness grows with value; no hue of the accent.
export const SEQ: [number, number, number][] = [
  [30, 52, 70],
  [36, 72, 96],
  [46, 94, 122],
  [62, 118, 146],
  [88, 144, 168],
  [126, 174, 192],
  [172, 206, 216],
  [226, 238, 238],
];
export const seqCss = (i: number, a = 1) => {
  const c = SEQ[Math.max(0, Math.min(SEQ.length - 1, i))];
  return `rgba(${c[0]},${c[1]},${c[2]},${a})`;
};
/** Colour of a zone by rank (rank 1 = lightest step of the sequential scale). */
export function rankColor(rank: number, n = 10): [number, number, number] {
  const k = Math.max(0, Math.min(1, (rank - 1) / Math.max(1, n - 1)));
  return SEQ[Math.round(SEQ.length - 1 - k * 4)];
}

// Fixed log breaks for share_permille (1 px of 10 m in an H3 res-8 cell ≈ 0.14 ‰) — comparable across regions.
export const H3_BREAKS = [0.14, 0.5, 1, 2, 5, 10, 20];
export interface H3Scale {
  breaks: number[];
  colors: [number, number, number][];
}
export const DEFAULT_SCALE: H3Scale = { breaks: H3_BREAKS, colors: SEQ };

const NICE = [1, 2, 5];
function nice(v: number): number {
  const e = Math.floor(Math.log10(v));
  let best = v,
    bd = Infinity;
  for (let k = e - 1; k <= e + 1; k++)
    for (const n of NICE) {
      const c = n * 10 ** k;
      const d = Math.abs(Math.log(c / v));
      if (d < bd) {
        bd = d;
        best = c;
      }
    }
  return Number(best.toPrecision(2));
}

/** Log-spaced breaks up to the 98th percentile (fixed default when data are within 0.14…30 ‰). */
export function makeScale(values: (number | null | undefined)[]): H3Scale {
  const nz = values.filter((v): v is number => typeof v === 'number' && v > 0).sort((a, b) => a - b);
  if (nz.length < 5) return DEFAULT_SCALE;
  const hi = nz[Math.min(nz.length - 1, Math.floor(nz.length * 0.98))];
  if (hi <= 30) return DEFAULT_SCALE;
  const lo = 0.14;
  const br: number[] = [];
  for (let i = 0; i < 7; i++) {
    const v = nice(lo * (hi / lo) ** (i / 6));
    if (!br.length || v > br[br.length - 1]) br.push(v);
  }
  const colors = Array.from({ length: br.length + 1 }, (_, i) => SEQ[Math.round((i * (SEQ.length - 1)) / br.length)]);
  return { breaks: br, colors };
}

export function h3Color(v: number | null | undefined, scale: H3Scale = DEFAULT_SCALE): [number, number, number, number] {
  if (v === null || v === undefined || Number.isNaN(v)) return [0, 0, 0, 0];
  if (v <= 0) return [120, 140, 160, 10];
  let i = 0;
  while (i < scale.breaks.length && v >= scale.breaks[i]) i++;
  const c = scale.colors[i];
  return [c[0], c[1], c[2], i <= 1 ? 150 : 215];
}
export function h3LineColor(v: number | null | undefined): [number, number, number, number] {
  if (v === null || v === undefined || Number.isNaN(v)) return [160, 168, 178, 26];
  if (v <= 0) return [160, 176, 192, 44];
  return [226, 238, 238, 110];
}
export const H3_MIN_H = 450;
export const H3_MAX_H = 3200;
export function h3Elevation(v: number | null | undefined, vmax: number): number {
  if (!v || v <= 0) return 0;
  const lv = Math.log1p(v / 0.14);
  const lm = Math.max(Math.log1p(Math.max(vmax, v) / 0.14), 1e-6);
  return H3_MIN_H + (H3_MAX_H - H3_MIN_H) * Math.min(1, lv / lm);
}
export const rgbStr = (c: number[], a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

// ---- formatting ----
const nf = (d: number) => new Intl.NumberFormat('ru-RU', { maximumFractionDigits: d, minimumFractionDigits: 0 });

export function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return nf(digits).format(v);
}

export function fmtPermille(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  if (v === 0) return '0';
  if (Math.abs(v) < 0.01) return `< ${nf(2).format(0.01)}`;
  const d = Math.abs(v) >= 10 ? 1 : Math.abs(v) >= 1 ? 2 : 3;
  return nf(d).format(v);
}

/** Area: m² below 10 000, else hectares. Returns [value, unit]. */
export function fmtArea(m2: number | null | undefined): [string, string] {
  if (m2 === null || m2 === undefined || Number.isNaN(m2)) return ['—', ''];
  if (m2 < 10000) return [nf(0).format(m2), 'м²'];
  return [nf(m2 >= 1e6 ? 0 : 2).format(m2 / 10000), 'га'];
}

export const fmtPct = (f: number | null | undefined, digits = 0) =>
  f === null || f === undefined || Number.isNaN(f) ? '—' : `${nf(digits).format(f * 100)} %`;

// L52: organiser chips without a date get a placeholder date (org_to_map --unknown-date, default 1900-01-01),
// flagged in the manifest as dates[].date_unknown — shown as «дата неизвестна», never as «1 янв 1900».
export const UNKNOWN_DATE_TEXT = 'дата неизвестна';
const UNKNOWN_DATES = new Set<string>(['1900-01-01']);

export function registerUnknownDates(m: { regions?: { dates?: { date: string; date_unknown?: boolean }[] }[] } | null | undefined): void {
  for (const r of m?.regions ?? []) for (const d of r.dates ?? []) if (d.date_unknown) UNKNOWN_DATES.add(d.date);
}

export const isUnknownDate = (iso: string | null | undefined): boolean => !!iso && UNKNOWN_DATES.has(iso.slice(0, 10));

export function fmtDate(iso: string): string {
  if (isUnknownDate(iso)) return UNKNOWN_DATE_TEXT;
  const [y, m, d] = iso.split('-').map(Number);
  const months = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  if (!y || !m || !d) return iso;
  return `${d} ${months[m - 1]} ${y}`;
}

/** ISO timestamp → «YYYY-MM-DD HH:MM»; a placeholder (unknown) date → «дата неизвестна». */
export function fmtTs(ts: string | null | undefined): string {
  const s = String(ts ?? '');
  return isUnknownDate(s) ? UNKNOWN_DATE_TEXT : s.slice(0, 16).replace('T', ' ');
}

export function fmtDateShort(iso: string): string {
  if (isUnknownDate(iso)) return '??.??.??';
  const [y, m, d] = iso.split('-');
  return `${d}.${m}.${y?.slice(2)}`;
}

export const modelLabel = (id: string, name?: string) =>
  id === 'mdd' ? 'MDD' : id === 'lgbm' ? 'LGBM' : name ? name.split(' ')[0] : id.toUpperCase();

/** Honest model title for the evidence card and the models menu. The threshold always comes from manifest.models.
 *  On live Sentinel-2 L2A data (manifest.kind ≠ «organizer») the layer «lgbm» is the L2A variant of our model
 *  (weights/lgbm_live), not the final model trained on MARIDA — the card and the menu say so. */
export const LGBM_VARIANT_HINT =
  'Итоговая модель обучена на снимках MARIDA в коррекции ACOLITE, а живые снимки — уровня L2A (сдвиг домена), поэтому на карте работает её вариант, обученный с поправкой на L2A.';
export function modelTitle(
  id: string,
  manifest: { kind?: string; models?: Record<string, { name?: string; threshold?: number; note?: string }> },
): { label: string; hint: string; variant: boolean } {
  const m = manifest.models?.[id];
  const thr = `порог ${fmtThr(m?.threshold)}`;
  if (id === 'lgbm') {
    if (manifest.kind !== 'organizer') return { label: `наша модель (вариант для снимков L2A, ${thr})`, hint: LGBM_VARIANT_HINT, variant: true };
    return { label: `наша модель (${thr})`, hint: 'Модель LightGBM; порог — из файла модели, выбранной при сборке карты.', variant: false };
  }
  if (id === 'mdd') return { label: `MDD (${thr})`, hint: 'marinedebrisdetector (Rußwurm et al., 2023, MIT) — открытая модель для сравнения.', variant: false };
  return { label: `${modelLabel(id, m?.name)} (${thr})`, hint: m?.note ?? '', variant: false };
}

/** Threshold: never rounded up to «1» (L52: 0.999 → «0.999», 0.995 → «0.995»); 0.63 → «0.63», 0.5 → «0.5»,
 *  0.06387 → «0.0639». Up to 3 decimals (truncated, not rounded, near 1), trailing zeros dropped. */
export function fmtThr(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const x = Number(v);
  if (x >= 1) return '1';
  if (x < 0.1) return String(Number(x.toPrecision(3)));
  let s = x.toFixed(3);
  if (Number(s) >= 1) s = (Math.floor(x * 1000) / 1000).toFixed(3);
  return String(Number(s));
}

/** Probability / confidence: 2 decimals; from 0.99 to < 1 — 3 decimals truncated (0.9964 → «0.996», not «1.00»). */
export function fmtProb(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '—';
  const x = Number(v);
  if (x >= 0.99 && x < 1) return (Math.floor(x * 1000) / 1000).toFixed(3);
  return x.toFixed(2);
}

// prob.png palette (docs/CONTRACTS.md, «Дополнения 03:35»): P < 0.05 transparent; 0.05…thr ramp
// #2b6cb0 (α 40) → #b794f4 → #f6ad55 (α 170); P ≥ thr accent #ff6b4a (α 230).
export function probLegendGradient(thr: number): string {
  const t = Math.min(0.98, Math.max(0.08, thr));
  const pos = (p: number) => `${(((p - 0.05) / 0.95) * 100).toFixed(1)}%`;
  const mid = (0.05 + t) / 2;
  return `linear-gradient(90deg, rgba(43,108,176,${(40 / 255).toFixed(2)}) 0%, rgba(183,148,244,0.45) ${pos(mid)}, rgba(246,173,85,${(170 / 255).toFixed(2)}) ${pos(t)}, rgba(255,107,74,${(230 / 255).toFixed(2)}) ${pos(t)}, rgba(255,107,74,${(230 / 255).toFixed(2)}) 100%)`;
}
