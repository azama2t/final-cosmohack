// §33 «В студию»: work with one zone — scene image, quality mask and detections on the map + large crops.
// §50 P1-4/5/6 (L142, Egor's screenshots 08–09), moved here from CaseApp.tsx:
// - three previews «Снимок / Детекция / Качество снимка» — all of THE SAME AREA (zone bbox + 300 m, as the backend crop,
//   scripts/case/scene_zones.py write_crop); a click opens a viewer with zoom/pan and the switch оригинал / детекция / маска;
// - «Качество снимка» is cut from this scene's quality.png at the zone box; no mask for the scene or the box outside it →
//   an honest empty state, never the whole scene or another picture;
// - «маска качества» on the map: hatched unusable pixels + legend line; disabled with the reason when there is nothing to mask.
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { Feat } from './api3';
import { API_BASE } from './api3';
import { dateRu, num } from './fmt';
import { zoneTitle, type SceneZoneDetail, type SceneZoneProps } from './SceneZoneCard';
import { loadImg, pxBox, QualityToggle, Q_CLASSES, statsLine, useMaskStats } from './QualityMask';
import './studio_view.css';

type Mode = 'rgb' | 'det' | 'mask';
const MODES: { k: Mode; t: string }[] = [
  { k: 'rgb', t: 'Снимок' },
  { k: 'det', t: 'Детекция' },
  { k: 'mask', t: 'Качество снимка' },
];

/** lon/lat bbox of a geometry */
function bbox(g: any): number[] | null {
  let x0 = Infinity,
    y0 = Infinity,
    x1 = -Infinity,
    y1 = -Infinity;
  const walk = (c: any) => {
    if (typeof c?.[0] === 'number') {
      x0 = Math.min(x0, c[0]);
      x1 = Math.max(x1, c[0]);
      y0 = Math.min(y0, c[1]);
      y1 = Math.max(y1, c[1]);
    } else if (Array.isArray(c)) c.forEach(walk);
  };
  walk(g?.coordinates);
  return isFinite(x0) ? [x0, y0, x1, y1] : null;
}
/** + margin in metres (the backend crop: zone bbox + 30 px of 10 m) */
function grow(b: number[], m: number): number[] {
  const dy = m / 111320;
  const dx = m / (111320 * Math.cos((((b[1] + b[3]) / 2) * Math.PI) / 180));
  return [b[0] - dx, b[1] - dy, b[2] + dx, b[3] + dy];
}

/** the backend crop is «image | 4 px dark gap | image with detector pixels»: find the gap, return both halves */
async function splitCrop(url: string): Promise<{ rgb: string; det: string; w: number; h: number }> {
  const im = await loadImg(url);
  const W = im.naturalWidth,
    H = im.naturalHeight;
  const c = document.createElement('canvas');
  c.width = W;
  c.height = H;
  const g = c.getContext('2d', { willReadFrequently: true })!;
  g.drawImage(im, 0, 0);
  const d = g.getImageData(0, 0, W, H).data;
  // darkest, least varying column band around the middle
  let best = Math.floor(W / 2),
    bestS = Infinity;
  for (let x = Math.floor(W * 0.4); x < Math.ceil(W * 0.6); x++) {
    let s = 0;
    for (let y = 0; y < H; y += 2) {
      const i = (y * W + x) * 4;
      s += Math.abs(d[i] - 20) + Math.abs(d[i + 1] - 20) + Math.abs(d[i + 2] - 20);
    }
    if (s < bestS) {
      bestS = s;
      best = x;
    }
  }
  let gl = best,
    gr = best;
  const dark = (x: number) => {
    let s = 0;
    for (let y = 0; y < H; y += 3) {
      const i = (y * W + x) * 4;
      s += Math.abs(d[i] - 20) + Math.abs(d[i + 1] - 20) + Math.abs(d[i + 2] - 20);
    }
    return s / Math.ceil(H / 3) < 25;
  };
  while (gl > 0 && dark(gl - 1)) gl--;
  while (gr < W - 1 && dark(gr + 1)) gr++;
  const pw = Math.min(gl, W - gr - 1);
  const cut = (x: number) => {
    const o = document.createElement('canvas');
    o.width = pw;
    o.height = H;
    o.getContext('2d')!.drawImage(im, x, 0, pw, H, 0, 0, pw, H);
    return o.toDataURL('image/png');
  };
  return { rgb: cut(0), det: cut(gr + 1), w: pw, h: H };
}

/** quality.png of this scene cut to the zone box, on a dark «usable water» background, upscaled to `w` px */
async function cutMask(url: string, bounds: number[], box: number[], w: number, h: number): Promise<string | null> {
  const im = await loadImg(url);
  const pb = pxBox(bounds, box, im.naturalWidth, im.naturalHeight);
  if (!pb) return null;
  const [x0, y0, x1, y1] = pb;
  const o = document.createElement('canvas');
  o.width = w;
  o.height = h;
  const g = o.getContext('2d')!;
  g.fillStyle = '#10263a'; // usable water (transparent in the mask) — dark blue
  g.fillRect(0, 0, w, h);
  g.imageSmoothingEnabled = false;
  g.drawImage(im, x0, y0, x1 - x0, y1 - y0, 0, 0, w, h);
  return o.toDataURL('image/png');
}

interface Media {
  rgb: string | null;
  det: string | null;
  mask: string | null;
  maskWhy: string | null;
  w: number;
  h: number;
}

function useMedia(zone: Feat<SceneZoneProps>, sc: any): Media | null {
  const p = zone.properties;
  const [m, setM] = useState<Media | null>(null);
  useEffect(() => {
    let alive = true;
    setM(null);
    (async () => {
      let rgb: string | null = null,
        det: string | null = null,
        w = 320,
        h = 320;
      if (p.crop_url) {
        try {
          const s = await splitCrop(API_BASE + p.crop_url);
          rgb = s.rgb;
          det = s.det;
          w = s.w;
          h = s.h;
        } catch {
          /* no crop */
        }
      }
      let mask: string | null = null;
      let maskWhy: string | null = null;
      const b = bbox(zone.geometry);
      if (!sc?.quality_url) maskWhy = 'У этого снимка маски качества нет — показать нечего.';
      else if (!sc?.bounds || !b) maskWhy = 'Нет границ снимка или зоны — вырезать маску той же области нельзя.';
      else {
        try {
          // scale up to at least ~320 px, keep the crop's aspect
          const k = Math.max(1, Math.ceil(320 / Math.max(w, 1)));
          mask = await cutMask(API_BASE + sc.quality_url, sc.bounds, grow(b, 300), w * k, h * k);
          if (!mask) maskWhy = 'Зона вне вырезки снимка с маской — маски для этой области нет.';
        } catch {
          maskWhy = 'Маска качества не загрузилась.';
        }
      }
      if (alive) setM({ rgb, det, mask, maskWhy, w, h });
    })();
    return () => {
      alive = false;
    };
  }, [p.crop_url, sc?.quality_url, sc?.bounds?.join(','), zone.id]); // eslint-disable-line react-hooks/exhaustive-deps
  return m;
}

const MASK_KEY = (
  <span className="c-lb-key" data-testid="studio-mask-key">
    <span>
      <i style={{ background: '#10263a' }} /> годная вода
    </span>
    {Q_CLASSES.map((c) => (
      <span key={c.id}>
        <i style={{ background: c.color }} /> {c.label}
      </span>
    ))}
  </span>
);

/** viewer: zoom (wheel, + / −), pan (drag), switch оригинал / детекция / маска, ← → between them, Esc */
function Lightbox({ media, mode: mode0, title, onClose, stats }: { media: Media; mode: Mode; title: string; onClose: () => void; stats: string | null }) {
  const [mode, setMode] = useState<Mode>(mode0);
  const [z, setZ] = useState(1);
  const [off, setOff] = useState<[number, number]>([0, 0]);
  const drag = useRef<{ x: number; y: number; o: [number, number] } | null>(null);
  const src = media[mode];
  const reset = () => {
    setZ(1);
    setOff([0, 0]);
  };
  const step = useCallback(
    (d: number) => {
      const i = MODES.findIndex((m) => m.k === mode);
      setMode(MODES[(i + d + MODES.length) % MODES.length].k);
    },
    [mode],
  );
  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
      else if (e.key === 'ArrowRight') step(1);
      else if (e.key === 'ArrowLeft') step(-1);
      else if (e.key === '+' || e.key === '=') setZ((v) => Math.min(12, v * 1.25));
      else if (e.key === '-') setZ((v) => Math.max(1, v / 1.25));
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [onClose, step]);
  return (
    <div className="c-lb-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()} data-testid="studio-viewer">
      <div className="c-lb" role="dialog" aria-modal="true" aria-label={`Просмотр: ${title}`}>
        <div className="c-lb-h">
          <b>{title}</b>
          <div className="seg c-lb-seg" role="tablist">
            {MODES.map((m) => (
              <button key={m.k} className={mode === m.k ? 'on' : ''} onClick={() => setMode(m.k)} data-testid={`viewer-${m.k}`} aria-selected={mode === m.k}>
                {m.k === 'rgb' ? 'оригинал' : m.k === 'det' ? 'детекция' : 'маска качества'}
              </button>
            ))}
          </div>
          <span className="c-lb-zoom">
            <button className="icon-btn" onClick={() => setZ((v) => Math.max(1, v / 1.25))} aria-label="Уменьшить">
              −
            </button>
            <span data-testid="viewer-zoom">{Math.round(z * 100)} %</span>
            <button className="icon-btn" onClick={() => setZ((v) => Math.min(12, v * 1.25))} aria-label="Увеличить">
              +
            </button>
            <button className="btn sm ghost" onClick={reset}>
              вписать
            </button>
          </span>
          {src && (
            <a className="btn sm ghost" href={src} download={`${title.replace(/[^\wА-Яа-яЁё-]+/g, '_')}_${mode}.png`} data-testid="viewer-save">
              сохранить
            </a>
          )}
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть" data-testid="viewer-close">
            ✕
          </button>
        </div>
        <div
          className="c-lb-stage"
          onWheel={(e) => {
            const f = e.deltaY < 0 ? 1.2 : 1 / 1.2;
            setZ((v) => Math.min(12, Math.max(1, v * f)));
          }}
          onMouseDown={(e) => {
            drag.current = { x: e.clientX, y: e.clientY, o: off };
          }}
          onMouseMove={(e) => {
            const d = drag.current;
            if (d) setOff([d.o[0] + e.clientX - d.x, d.o[1] + e.clientY - d.y]);
          }}
          onMouseUp={() => (drag.current = null)}
          onMouseLeave={() => (drag.current = null)}
          onDoubleClick={reset}
        >
          {src ? (
            <img
              src={src}
              alt={MODES.find((m) => m.k === mode)!.t}
              draggable={false}
              style={{ transform: `translate(${off[0]}px, ${off[1]}px) scale(${z})`, aspectRatio: `${media.w} / ${media.h}` }}
              data-testid="viewer-img"
            />
          ) : (
            <div className="c-lb-empty" data-testid="viewer-empty">
              {mode === 'mask' ? media.maskWhy : 'Вырезки этой зоны нет.'}
            </div>
          )}
        </div>
        <div className="c-lb-f faint">
          {mode === 'rgb' && 'Исходный снимок Sentinel-2 (RGB, 10 м) — область зоны + 300 м.'}
          {mode === 'det' && (
            <>
              Та же область: <span style={{ color: '#ff2842' }}>красные</span> пиксели — детектор (плавающий материал); <span style={{ color: '#ffd43b' }}>жёлтые</span> —
              объекты, снятые фильтрами (судно, кильватер, шов).
            </>
          )}
          {mode === 'mask' && (
            <>
              Маска качества этой же сцены и области (SCL + блик). {MASK_KEY}
              {stats && <div data-testid="viewer-mask-stats">В области зоны: {stats}</div>}
            </>
          )}
          <span className="c-lb-hint"> · колесо — масштаб, перетаскивание — сдвиг, ← → — переключить, двойной клик — вписать</span>
        </div>
      </div>
    </div>
  );
}

export default function ZoneStudio({
  zone,
  num: zoneNum,
  detail,
  quality,
  scenesOn,
  onLayer,
  onCard,
  onBack,
}: {
  zone: Feat<SceneZoneProps>;
  num?: number;
  detail: SceneZoneDetail | null;
  quality: boolean;
  scenesOn: boolean;
  onLayer: (k: 'scenes' | 'quality') => void;
  onCard: () => void;
  onBack: () => void;
}) {
  const p = zone.properties;
  const sc: any = (detail as any)?.scene;
  const dets: any[] = (detail as any)?.detections?.features ?? [];
  const media = useMedia(zone, sc);
  const b = bbox(zone.geometry);
  const box = b ? grow(b, 300) : null;
  const ms = useMaskStats(sc, box);
  const stats = ms.info?.box ? statsLine(ms.info.box) : null;
  const [open, setOpen] = useState<Mode | null>(null);
  const [view, setView] = useState<Mode>('rgb');
  const [side, setSide] = useState(false);
  useEffect(() => {
    setSide(false);
  }, [zone.id]);
  const title = `${zoneTitle(p)} · ${dateRu(p.datetime)}`;
  return (
    <div className="right-inner" data-testid="zone-studio">
      <div className="rp-head">
        <div className="rp-titles">
          <div className="rp-kicker">Студия · работа с зоной{zoneNum ? ` ${zoneNum}` : ''}</div>
          <div className="rp-title">{zoneTitle(p)}</div>
          <div className="rp-sub">Sentinel-2 · {dateRu(p.datetime)}</div>
        </div>
      </div>
      <div className="rp-body">
        <div className="sec c-studio-bar">
          <button className="btn sm" onClick={onCard} data-testid="studio-card">
            ← к карточке
          </button>
          <button className="btn sm ghost" onClick={onBack} data-testid="studio-back">
            Назад к карте
          </button>
        </div>
        <div className="sec">
          <div className="sec-h">
            <h3>На карте</h3>
          </div>
          <label className="c-studio-t">
            <input type="checkbox" checked={scenesOn} onChange={() => onLayer('scenes')} data-testid="studio-rgb" /> снимок (Sentinel-2, RGB)
          </label>
          <div className="c-studio-t">
            <QualityToggle scene={sc} checked={quality} onChange={() => onLayer('quality')} testid="studio-quality" label="маска качества (облака, блики, суша)" />
          </div>
          <div className="c-line faint">контуры объектов детектора — поверх снимка ({num(dets.length, 0)} объект., порог 0,63)</div>
        </div>
        <div className="sec" data-testid="studio-media">
          <div className="sec-h">
            <h3>Снимок · детекция · качество</h3>
            <span className="aside">одна область: зона + 300 м</span>
          </div>
          {!media && <div className="note">Загрузка вырезок…</div>}
          {media && (
            <div className="sv-bar">
              <div className="seg sv-seg" role="tablist" aria-label="Что показать">
                {MODES.map((m) => (
                  <button key={m.k} role="tab" className={view === m.k ? 'on' : ''} aria-selected={view === m.k} onClick={() => setView(m.k)} data-testid={`studio-view-${m.k}`}>
                    {m.k === 'mask' ? 'Качество' : m.t}
                  </button>
                ))}
              </div>
              <button className={`btn sm ${side ? '' : 'ghost'} sv-cmp`} onClick={() => setSide((v) => !v)} aria-pressed={side} data-testid="studio-compare">
                {side ? 'Один просмотр' : 'Сравнить рядом'}
              </button>
            </div>
          )}
          {media && !side && (
            <div className="sv-main" data-testid="studio-main">
              {media[view] ? (
                <button className="sv-main-i" onClick={() => setOpen(view)} title="Открыть крупно: масштаб, сдвиг" data-testid={`studio-main-${view}`}>
                  <img src={media[view]!} alt={MODES.find((m) => m.k === view)!.t} style={{ aspectRatio: `${media.w} / ${media.h}` }} />
                  {view === 'mask' && ms.info?.box && ms.info.box.bad === 0 && <span className="c-studio-th-over">вся вода годная — непригодных пикселей 0</span>}
                  <span className="sv-main-z">⤢ крупно</span>
                </button>
              ) : (
                <div className="sv-main-e" data-testid="studio-main-empty">
                  {view === 'mask' ? media.maskWhy || 'маски для этой области нет' : 'Вырезки этой зоны нет.'}
                </div>
              )}
              <div className="sv-cap faint">
                {view === 'rgb' && 'Исходный снимок Sentinel-2 (RGB, 10 м) — область зоны + 300 м.'}
                {view === 'det' && (
                  <>
                    Та же область: <span style={{ color: '#ff2842' }}>красные</span> пиксели — детектор; <span style={{ color: '#ffd43b' }}>жёлтые</span> — снятые фильтрами.
                  </>
                )}
                {view === 'mask' && <>Маска качества этой сцены и области (SCL + блик). {MASK_KEY}</>}
              </div>
            </div>
          )}
          {media && side && (
            <div className="c-studio-tri">
              {MODES.map((m) => {
                const src = media[m.k];
                return (
                  <button key={m.k} className={`c-studio-th ${src ? '' : 'empty'}`} onClick={() => setOpen(m.k)} data-testid={`studio-th-${m.k}`} title={src ? 'Открыть крупно: масштаб, сдвиг, оригинал / детекция / маска' : ''}>
                    {src ? (
                      <span className="c-studio-th-i">
                        <img src={src} alt={m.t} />
                        {m.k === 'mask' && ms.info?.box && ms.info.box.bad === 0 && <span className="c-studio-th-over">вся вода годная — непригодных пикселей 0</span>}
                      </span>
                    ) : <span className="c-studio-th-e">{m.k === 'mask' ? 'маски для этой области нет' : 'вырезки нет'}</span>}
                    <span className="c-studio-th-t">
                      {m.t} {src && <span className="c-studio-th-z">⤢</span>}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
          {media && !media.mask && media.maskWhy && (
            <div className="c-line faint" data-testid="studio-mask-why">
              {media.maskWhy}
            </div>
          )}
          {media?.mask && stats && (
            <div className="c-line faint" data-testid="studio-mask-stats">
              Качество в области зоны: {stats}
            </div>
          )}
        </div>
        <div className="sec">
          <div className="sec-h">
            <h3>Объекты детектора</h3>
          </div>
          <table className="c-ptable">
            <thead>
              <tr>
                <th>объект</th>
                <th className="r">пикс.</th>
                <th className="r">м²</th>
                <th className="r">вер. макс.</th>
              </tr>
            </thead>
            <tbody>
              {dets.slice(0, 12).map((d: any) => (
                <tr key={d.properties.det_id}>
                  <td>{d.properties.det_id}</td>
                  <td className="r">{num(d.properties.n_pixels, 0)}</td>
                  <td className="r">{num(d.properties.area_m2, 0)}</td>
                  <td className="r">{num(d.properties.prob_max, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {dets.length > 12 && <div className="c-line faint">ещё {num(dets.length - 12, 0)}</div>}
        </div>
      </div>
      {open && media && createPortal(<Lightbox media={media} mode={open} title={title} onClose={() => setOpen(null)} stats={stats} />, document.body)}
    </div>
  );
}
