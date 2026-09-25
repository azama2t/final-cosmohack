import type { MutableRefObject } from 'react';
import type { DateEntry, DetProps, Feature, FC, LayerKey, Layers, Manifest, Region, Zone, ZonesFile } from './types';
import { bestRegion } from './lib/data';
import { anim } from './map/controller';

export interface TourApi {
  get: () => {
    detections: FC<DetProps> | null;
    zones: ZonesFile | null;
    region: Region | null;
    dateEntry: DateEntry | null;
    manifest: Manifest | null | undefined;
  };
  selectRegion: (id: string | null) => void;
  setLayers: (l: Partial<Layers>) => void;
  toggleLayer: (k: LayerKey, on?: boolean) => void;
  openDetection: (f: Feature<DetProps>) => void;
  closeDetection: () => void;
  openCompare: () => void;
  closeCompare: () => void;
  caption: (step: number, total: number, text: string | null) => void;
  showZone: (z: Zone) => void;
  closeZone: () => void;
  waitIdle: (ms?: number) => Promise<void>;
}

class Aborted extends Error {}

/** ~60 s scripted demo over the best region: overview → flyTo → spots → card → H3 3D → zones → drift → compare. */
export async function runTour(apiRef: MutableRefObject<TourApi>, signal: AbortSignal) {
  const api = () => apiRef.current;
  const wait = (ms: number) =>
    new Promise<void>((res, rej) => {
      if (signal.aborted) return rej(new Aborted());
      const t = setTimeout(res, ms);
      signal.addEventListener('abort', () => {
        clearTimeout(t);
        rej(new Aborted());
      });
    });
  const until = async (cond: () => boolean, max = 5000) => {
    const t0 = performance.now();
    while (!cond() && performance.now() - t0 < max) await wait(100);
  };
  try {
    const m = api().get().manifest;
    if (!m) return;
    const best = bestRegion(m);
    if (!best) return;
    const latest = best.dates.find((d) => d.date === best.summary?.latest_date) ?? best.dates[best.dates.length - 1];
    const hasDet = (best.summary?.n_detections ?? 0) > 0;
    const hasDrift = !!latest?.drift;
    // steps without data are skipped entirely (no empty pauses); numbering follows the actual plan
    const TOTAL = 5 + (hasDet ? 2 : 0) + (hasDrift ? 1 : 0);
    let step = 0;
    const cap = (t: string) => api().caption(++step, TOTAL, t);

    api().closeCompare();
    api().closeDetection();
    api().setLayers({ rgb: true, detections: true, prob: false, h3: false, h3_3d: false, zones: false, drift: false });
    cap('Все районы наблюдения. Число у метки — индекс: доля наблюдаемой воды с признаками мусора, ‰.');
    api().selectRegion(null);
    await wait(5000);

    cap(
      hasDet
        ? `Больше всего находок на свежем снимке — ${best.name}. Летим к снимку Sentinel-2.`
        : `${best.name}: летим к свежему снимку Sentinel-2.`,
    );
    api().selectRegion(best.id);
    await wait(600);
    await api().waitIdle(6000);
    await until(() => !!api().get().detections);
    await wait(1400);

    cap(
      hasDet
        ? 'Коралловые кольца — находки: здесь модель видит признаки плавающего мусора. Размер кольца растёт с площадью пятна.'
        : 'На этом снимке модель не нашла признаков мусора — так тоже бывает.',
    );
    await wait(5000);

    const det = api().get().detections;
    if (hasDet && det?.features.length) {
      const f = [...det.features].sort((a, b) => b.properties.area_m2 - a.properties.area_m2)[0];
      cap('Карточка находки: вырезка снимка, площадь помеченной области, уверенность, качество наблюдения.');
      api().openDetection(f);
      await wait(7000);
      api().closeDetection();
    }

    cap('Индекс по сетке H3 (~0.7 км²) в 3D: цвет и высота (лог) — доля наблюдаемой воды с признаками мусора.');
    api().toggleLayer('h3_3d', true);
    await wait(8000);

    if (hasDet) {
      cap('Приоритет обследования: топ ячеек по помеченной воде с учётом повторяемости по датам и уверенности.');
      api().toggleLayer('h3', false);
      api().toggleLayer('zones', true);
      await wait(400);
      const z = api().get().zones?.zones?.[0];
      if (z) api().showZone(z);
      await wait(z ? 7000 : 3000);
      api().closeZone();
    } else {
      api().toggleLayer('h3', false);
    }

    const de = api().get().dateEntry;
    if (hasDrift && de?.drift) {
      cap('Дрейф 0→72 ч — демонстрационный прогноз, без валидации: куда может сместиться мусор.');
      api().toggleLayer('zones', false);
      api().toggleLayer('drift', true);
      await until(() => !!(window as any).__driftPlay, 4000);
      await wait(500);
      anim.hour = 0;
      anim.speed = 1;
      (window as any).__driftPlay?.(true);
      await wait(10000);
      (window as any).__driftPlay?.(false);
      api().toggleLayer('drift', false);
    }

    cap('Сравнение двух районов или дат: снимки рядом и таблица различий.');
    api().toggleLayer('zones', false);
    api().openCompare();
    await wait(8000);
    api().closeCompare();
    api().caption(TOTAL, TOTAL, null);
  } catch (e) {
    if (!(e instanceof Aborted)) console.warn('tour', e);
  }
}
