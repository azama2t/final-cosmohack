// §50 P1-5/P1-6 (L142, Egor's screenshots 08–09): the quality mask must be visible and honest.
// - maskStats(): per-class shares of the scene quality.png (EPSG:4326 over scene.bounds, scripts/case/scene_zones.py),
//   for the whole cut-out and for a lon/lat box (the zone + margin) — counted from the pixels, nothing assumed.
// - hatchedMask(): the same mask re-drawn for the map: unusable pixels get a denser tint + diagonal hatching, usable water
//   stays transparent (so «маска качества» is clearly visible wherever there is something to mask).
// - QualityToggle: the checkbox; when the scene has no mask, or the mask has no unusable pixels, it is disabled and says why.
import { useEffect, useState } from 'react';
import { API_BASE } from './api3';

export interface QClass {
  id: string;
  label: string;
  color: string;
}
/** colours of quality.png (GET /api/v3/meta quality_classes; the same list is the fallback) */
export const Q_CLASSES: QClass[] = [
  { id: 'cloud', label: 'облака', color: '#ffffff99' },
  { id: 'glint', label: 'блик', color: '#ffd43b99' },
  { id: 'land', label: 'суша', color: '#495057cc' },
  { id: 'nodata', label: 'нет данных', color: '#00000066' },
];
const hex = (c: string) => [1, 3, 5, 7].map((i) => parseInt(c.slice(i, i + 2) || 'ff', 16));
const REF = Q_CLASSES.map((c) => ({ id: c.id, rgba: hex(c.color) }));

export interface MaskStats {
  /** shares 0..1 of all pixels of the box, per class id + 'valid' */
  share: Record<string, number>;
  bad: number;
  n: number;
}
export interface MaskInfo {
  w: number;
  h: number;
  whole: MaskStats;
  box: MaskStats | null;
}

const imgCache = new Map<string, Promise<HTMLImageElement>>();
export function loadImg(url: string): Promise<HTMLImageElement> {
  let p = imgCache.get(url);
  if (!p) {
    p = new Promise((res, rej) => {
      const im = new Image();
      im.crossOrigin = 'anonymous';
      im.onload = () => res(im);
      im.onerror = () => rej(new Error('image'));
      im.src = url;
    });
    imgCache.set(url, p);
    p.catch(() => imgCache.delete(url));
  }
  return p;
}

const pxCache = new Map<string, Promise<ImageData>>();
function pixels(url: string): Promise<ImageData> {
  let p = pxCache.get(url);
  if (!p) {
    p = loadImg(url).then((im) => {
      const c = document.createElement('canvas');
      c.width = im.naturalWidth;
      c.height = im.naturalHeight;
      const g = c.getContext('2d', { willReadFrequently: true })!;
      g.drawImage(im, 0, 0);
      return g.getImageData(0, 0, c.width, c.height);
    });
    pxCache.set(url, p);
    p.catch(() => pxCache.delete(url));
  }
  return p;
}

function classOf(r: number, g: number, b: number, a: number): string {
  if (a < 8) return 'valid';
  let best = 'nodata';
  let d0 = Infinity;
  for (const c of REF) {
    const d = (r - c.rgba[0]) ** 2 + (g - c.rgba[1]) ** 2 + (b - c.rgba[2]) ** 2 + ((a - c.rgba[3]) * 0.5) ** 2;
    if (d < d0) {
      d0 = d;
      best = c.id;
    }
  }
  return best;
}

function count(d: ImageData, x0: number, y0: number, x1: number, y1: number): MaskStats {
  const cnt: Record<string, number> = { valid: 0 };
  let n = 0;
  for (let y = y0; y < y1; y++)
    for (let x = x0; x < x1; x++) {
      const i = (y * d.width + x) * 4;
      const k = classOf(d.data[i], d.data[i + 1], d.data[i + 2], d.data[i + 3]);
      cnt[k] = (cnt[k] ?? 0) + 1;
      n++;
    }
  const share: Record<string, number> = {};
  for (const [k, v] of Object.entries(cnt)) share[k] = n ? v / n : 0;
  return { share, bad: n ? 1 - (cnt.valid ?? 0) / n : 0, n };
}

/** pixel box of a lon/lat box inside scene bounds (EPSG:4326, linear); null if it does not overlap */
export function pxBox(bounds: number[], box: number[], w: number, h: number): [number, number, number, number] | null {
  const [X0, Y0, X1, Y1] = bounds;
  const x0 = Math.max(0, Math.floor(((box[0] - X0) / (X1 - X0)) * w));
  const x1 = Math.min(w, Math.ceil(((box[2] - X0) / (X1 - X0)) * w));
  const y0 = Math.max(0, Math.floor(((Y1 - box[3]) / (Y1 - Y0)) * h));
  const y1 = Math.min(h, Math.ceil(((Y1 - box[1]) / (Y1 - Y0)) * h));
  return x1 - x0 >= 2 && y1 - y0 >= 2 ? [x0, y0, x1, y1] : null;
}

export async function maskStats(url: string, bounds?: number[] | null, box?: number[] | null): Promise<MaskInfo> {
  const d = await pixels(url);
  const whole = count(d, 0, 0, d.width, d.height);
  const pb = bounds && box ? pxBox(bounds, box, d.width, d.height) : null;
  return { w: d.width, h: d.height, whole, box: pb ? count(d, ...pb) : null };
}

/** hatched copy of the mask for the map (object URL, cached per mask url) */
const hatchCache = new Map<string, Promise<string>>();
export function hatchedMask(url: string): Promise<string> {
  let p = hatchCache.get(url);
  if (!p) {
    p = pixels(url).then(
      (d) =>
        new Promise<string>((res, rej) => {
          const out = new ImageData(d.width, d.height);
          for (let y = 0; y < d.height; y++)
            for (let x = 0; x < d.width; x++) {
              const i = (y * d.width + x) * 4;
              const a = d.data[i + 3];
              if (a < 8) continue; // usable water: transparent
              const stripe = (x + y) % 8 < 3;
              out.data[i] = stripe ? 255 : d.data[i];
              out.data[i + 1] = stripe ? 80 : d.data[i + 1];
              out.data[i + 2] = stripe ? 120 : d.data[i + 2];
              out.data[i + 3] = stripe ? 235 : Math.max(a, 190);
            }
          const c = document.createElement('canvas');
          c.width = d.width;
          c.height = d.height;
          c.getContext('2d')!.putImageData(out, 0, 0);
          c.toBlob((b) => (b ? res(URL.createObjectURL(b)) : rej(new Error('blob'))), 'image/png');
        }),
    );
    hatchCache.set(url, p);
    p.catch(() => hatchCache.delete(url));
  }
  return p;
}

export const pctRu = (v: number) => {
  const x = v * 100;
  if (x === 0) return '0 %';
  if (x < 0.1) return '< 0,1 %';
  return `${x.toLocaleString('ru-RU', { maximumFractionDigits: x < 10 ? 1 : 0 })} %`;
};

/** «облака 3 % · суша 12 % · годная вода 85 %» for the classes that are present */
export function statsLine(s: MaskStats): string {
  const parts = Q_CLASSES.filter((c) => (s.share[c.id] ?? 0) > 0).map((c) => `${c.label} ${pctRu(s.share[c.id])}`);
  parts.push(`годная вода ${pctRu(s.share.valid ?? 0)}`);
  return parts.join(' · ');
}

export function useMaskStats(scene: { quality_url?: string | null; bounds?: number[] | null } | null | undefined, box?: number[] | null) {
  const url = scene?.quality_url ? API_BASE + scene.quality_url : null;
  const [st, setSt] = useState<{ url: string | null; info: MaskInfo | null; err: boolean }>({ url: null, info: null, err: false });
  const bk = box ? box.join(',') : '';
  useEffect(() => {
    if (!url) return;
    let alive = true;
    maskStats(url, scene?.bounds ?? null, box ?? null).then(
      (info) => alive && setSt({ url, info, err: false }),
      () => alive && setSt({ url, info: null, err: true }),
    );
    return () => {
      alive = false;
    };
  }, [url, bk]); // eslint-disable-line react-hooks/exhaustive-deps
  return { url, info: st.url === url ? st.info : null, err: st.url === url && st.err };
}

/**
 * «маска качества» checkbox with an honest state:
 * no mask for the scene → disabled + reason; mask without unusable pixels → disabled + «затенять нечего» (with the %);
 * otherwise normal, and when on — a legend line with the classes and their shares (hatched on the map).
 */
export function QualityToggle({
  scene,
  checked,
  onChange,
  testid,
  label = 'маска качества',
  box,
}: {
  scene: { quality_url?: string | null; bounds?: number[] | null } | null | undefined;
  checked: boolean;
  onChange: () => void;
  testid?: string;
  label?: string;
  box?: number[] | null;
}) {
  const { url, info, err } = useMaskStats(scene, box);
  const s = info ? (info.box ?? info.whole) : null;
  const nothing = !!s && s.bad === 0;
  const reason = !url
    ? 'у этого снимка маски качества нет'
    : err
      ? 'маска качества не загрузилась'
      : nothing
        ? `в ${info?.box ? 'области зоны' : 'вырезке снимка'} облаков, бликов и суши нет — вся вода годная, затенять нечего`
        : null;
  const disabled = !!reason;
  return (
    <span className="c-qtoggle" data-testid={testid ? `${testid}-wrap` : undefined}>
      <label className={disabled ? 'off' : ''} title={reason ?? 'Затенить и заштриховать непригодные пиксели (облака, блик, суша)'}>
        <input type="checkbox" checked={checked && !disabled} disabled={disabled} onChange={onChange} data-testid={testid} /> {label}
      </label>
      {reason && (
        <span className="c-qtoggle-why faint" data-testid={testid ? `${testid}-why` : undefined}>
          {reason}
        </span>
      )}
      {!reason && checked && s && (
        <span className="c-qlegend" data-testid={testid ? `${testid}-legend` : undefined}>
          <i className="c-qhatch" /> заштриховано — непригодно: {statsLine(s)}
        </span>
      )}
    </span>
  );
}
