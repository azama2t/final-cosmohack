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
  const TOTAL = 8;
  const cap = (i: number, t: string) => api().caption(i, TOTAL, t);

  try {
    const m = api().get().manifest;
    if (!m) return;
    const best = bestRegion(m);
    if (!best) return;

    api().closeCompare();
    api().closeDetection();
    api().setLayers({ rgb: true, detections: true, prob: false, h3: false, h3_3d: false, zones: false, drift: false });
    cap(1, 'Все районы наблюдения. Число у метки — индекс: доля наблюдаемой воды с признаками мусора, ‰.');
    api().selectRegion(null);
    await wait(5000);

    cap(2, `Лучший по индексу район — ${best.name}. Летим к свежему снимку Sentinel-2.`);
    api().selectRegion(best.id);
    await wait(600);
    await api().waitIdle(6000);
    await until(() => !!api().get().detections);
    await wait(1400);

    cap(3, 'Коралловым подсвечены пятна: здесь модель видит признаки плавающего мусора.');
    await wait(5000);

    const det = api().get().detections;
    if (det?.features.length) {
      const f = [...det.features].sort((a, b) => b.properties.area_m2 - a.properties.area_m2)[0];
      cap(4, 'Карточка находки: вырезка снимка, площадь помеченной области, уверенность, качество наблюдения.');
      api().openDetection(f);
      await wait(7000);
      api().closeDetection();
    }

    cap(5, 'Индекс по сетке H3 (~0.7 км²) в 3D: высота и цвет — доля воды с признаками мусора. Серые — нет данных.');
    api().toggleLayer('h3_3d', true);
    await wait(8500);

    cap(6, 'Приоритет обследования: топ ячеек по помеченной воде с учётом повторяемости по датам и уверенности.');
    api().toggleLayer('h3', false);
    api().toggleLayer('zones', true);
    await wait(400);
    const z = api().get().zones?.zones?.[0];
    if (z) api().showZone(z);
    await wait(7000);
    api().closeZone();

    const de = api().get().dateEntry;
    if (de?.drift) {
      cap(7, 'Дрейф 0→72 ч — демонстрационный прогноз, без валидации: куда может сместиться мусор.');
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
    } else {
      cap(7, 'Вероятность модели — сырой выход до порога.');
      api().toggleLayer('prob', true);
      await wait(5000);
      api().toggleLayer('prob', false);
    }

    cap(8, 'Сравнение двух районов или дат: снимки рядом и таблица различий.');
    api().openCompare();
    await wait(8000);
    api().closeCompare();
    api().caption(TOTAL, TOTAL, null);
  } catch (e) {
    if (!(e instanceof Aborted)) console.warn('tour', e);
  }
}
