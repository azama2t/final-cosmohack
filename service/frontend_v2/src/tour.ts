import type { MutableRefObject } from 'react';
import type { Basemap, DateEntry, DetProps, Feature, FC, LayerKey, Layers, Manifest, Region, Zone, ZonesFile } from './types';
import type { FeedEvent } from './lib/feed';
import type { CheckPair } from './App';
import { bestRegion, shortName, summaryDate } from './lib/data';
import { anim, ctl, turnGlobeTo } from './map/controller';
import { verdictZone } from './lib/priority';

export interface TourApi {
  get: () => {
    detections: FC<DetProps> | null;
    zones: ZonesFile | null;
    region: Region | null;
    dateEntry: DateEntry | null;
    manifest: Manifest | null | undefined;
    feed: { items: FeedEvent[] } | null;
    checkSummary: any;
    paths: Set<string>;
  };
  selectRegion: (id: string | null, o?: { date?: string; model?: string; fly?: boolean }) => void;
  setView: (v: 'findings' | 'zones' | 'history' | 'drift') => void;
  setLayers: (l: Partial<Layers>) => void;
  toggleLayer: (k: LayerKey, on?: boolean) => void;
  openDetection: (f: Feature<DetProps>) => void;
  closeDetection: () => void;
  openCompare: () => void;
  closeCompare: () => void;
  caption: (step: number, total: number, text: string | null) => void;
  openZone: (z: Zone) => void;
  closeZone: () => void;
  openPlace: (h3: string) => void;
  closePlace: () => void;
  waitIdle: (ms?: number) => Promise<void>;
  ensureGlobe: () => void;
  setLeftTab: (t: 'feed' | 'regions') => void;
  highlightFeed: (key: string | null) => void;
  openCheck: (on: boolean) => void;
  selectCheckPair: (p: CheckPair) => void;
  setTab: (t: 'map' | 'review') => void;
  setBasemap: (b: Basemap) => void;
  showToast: (t: string) => void;
}

class Aborted extends Error {}

const scrollTo = (testid: string) =>
  document.querySelector(`[data-testid="${testid}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });

/**
 * ≈ 90 s scripted demo (L48): globe → feed → region & scene → finding card → H3 2D/3D → zones «why first» →
 * drift + particles → forecast check → compare → calendar → PDF → review tab → offline basemap.
 * Steps without data are skipped; numbering follows the actual plan.
 */
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
    const st0 = api().get();
    const m = st0.manifest;
    if (!m) return;
    const demo = m.demo?.region ? m.regions.find((r) => r.id === m.demo!.region) : null;
    const best = demo ?? bestRegion(m);
    if (!best) return;
    const demoDate = (m.demo?.region === best.id && m.demo?.date) || summaryDate(best)?.date;
    const de = best.dates.find((d) => d.date === demoDate) ?? summaryDate(best);
    const paths = st0.paths;
    const hasDrift = !!de?.drift;
    const hasCheck = paths.has('/api/drift_check');
    const hasReview = paths.has('/api/review/queue');
    const hasPdf = paths.has('/api/place_report.pdf');
    const TOTAL = 10 + (hasDrift ? 1 : 0) + (hasCheck ? 1 : 0) + (hasReview ? 1 : 0) + (hasPdf ? 1 : 0) - 1;
    let step = 0;
    const cap = (t: string) => api().caption(++step, TOTAL, t);
    const name = shortName(best.name);

    // 1. globe and space
    api().setTab('map');
    api().closeCompare();
    api().openCheck(false);
    api().setBasemap('dark');
    api().setLayers({ rgb: true, detections: true, prob: false, h3: false, h3_3d: false, zones: false, drift: false, currents: false, wind: false, osm: false, sources: false, artifacts: false });
    api().ensureGlobe();
    api().setLeftTab('regions');
    api().selectRegion(null);
    await wait(300);
    cap(`${m.regions.length} районов наблюдения. Число у метки — индекс по последнему надёжному снимку: доля наблюдаемой воды с признаками плавающего материала, ‰.`);
    await wait(5000);

    // 2. feed of findings (with the image date)
    api().setLeftTab('feed');
    const items = api().get().feed?.items ?? [];
    const ev = items.find((e) => e.region === best.id && e.date === de?.date && e.kind === 'new') ?? items.find((e) => e.region === best.id) ?? null;
    cap('Лента находок: у каждой записи — дата снимка, что отмечено и статус проверки. «Подтверждено» появляется только после решения человека.');
    if (ev) api().highlightFeed(ev.key);
    await wait(5000);

    // 3. region and scene
    api().setLeftTab('regions');
    const turn = turnGlobeTo(best.center[0], best.center[1], 1800);
    if (turn) await wait(turn + 200);
    cap(`${name}: снимок Sentinel-2 от ${de?.date.split('-').reverse().join('.')}. Справа — где и когда снимок, что модель отметила и куда нажать дальше.`);
    api().selectRegion(best.id, { date: de?.date, model: 'mdd' });
    await wait(700);
    await api().waitIdle(6000);
    await until(() => !!api().get().detections);
    await wait(2200);

    // 4. finding → evidence card
    const det = api().get().detections;
    if (det?.features.length) {
      const f = [...det.features].sort((a, b) => b.properties.area_m2 - a.properties.area_m2)[0];
      cap('Карточка доказательств: исходная вырезка снимка, дата и время съёмки, координаты, обе модели, качество снимка, возможные альтернативы и кнопка «Отправить на проверку».');
      api().openDetection(f);
      await wait(7500);
      api().closeDetection();
      await wait(300);
    }

    // 5. H3 2D → 3D (zones view)
    api().setView('zones');
    cap('Индекс по сетке H3 (~0,7 км²): доля наблюдаемой воды с признаками материала. Затем — в объёме.');
    api().toggleLayer('h3', true);
    await wait(3000);
    api().toggleLayer('h3_3d', true);
    await wait(4500);
    api().toggleLayer('h3', false);
    await wait(900);

    // 6. zones and «why first»
    const zones = api().get().zones?.zones ?? [];
    const vz = verdictZone(zones);
    if (vz) {
      cap('Зоны обследования и «почему это место первое»: пиксели × уверенность × повторяемость × согласие моделей. Порядок проверки, не измеренная опасность.');
      api().openZone(vz.zone);
      await wait(1200);
      scrollTo('zone-why');
      await wait(6000);
      api().closeZone();
    }

    // 7. drift + particles
    if (hasDrift) {
      cap('Дрейф 0–72 ч и частицы течений — демонстрационный прогноз, не валидирован. Серое облако — разброс при другом ветровом коэффициенте.');
      anim.spread = true;
      api().setView('drift');
      await until(() => !!(window as any).__app?.driftReady && !!(window as any).__driftPlay, 5000);
      await wait(1800);
      anim.hour = 0;
      anim.speed = 1;
      (window as any).__driftPlay?.(true);
      await wait(8500);
      (window as any).__driftPlay?.(false);
    }

    // 8. forecast check (experiment)
    if (hasCheck) {
      cap('Эксперимент: прогноз против следующего снимка того же района и против базовой линии «материал остался на месте». Это не оценка точности.');
      api().openCheck(true);
      await until(() => !!api().get().checkSummary, 4000);
      const pairs: CheckPair[] = api().get().checkSummary?.pairs ?? [];
      const pick = pairs.filter((x) => x.pair_hit).sort((a, b) => (b.hits ?? 0) - (a.hits ?? 0))[0] ?? pairs[0];
      if (pick) api().selectCheckPair(pick);
      await wait(7500);
      api().openCheck(false);
    }

    // 9. compare
    api().selectRegion(best.id, { date: de?.date, model: 'mdd', fly: true });
    cap('Сравнение районов: снимки рядом и таблица различий.');
    await wait(900);
    api().openCompare();
    await wait(5500);
    api().closeCompare();

    // 10. calendar (history)
    api().setView('history');
    cap('История: календарь реальных снимков — только даты съёмки, без интерполяции; ненадёжные даты помечены.');
    await wait(600);
    scrollTo('obs-calendar');
    await wait(5000);

    // 11. PDF
    if (hasPdf && vz) {
      cap('Справка PDF по месту: координаты, вырезки, история дат, формула и ограничения — для выезда на проверку.');
      api().openZone(vz.zone);
      await wait(900);
      document.querySelector('[data-testid="zone-pdf"]')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      (document.querySelector('[data-testid="zone-pdf"]') as HTMLElement | null)?.classList.add('on');
      await wait(4500);
      api().closeZone();
    }

    // 12. review tab
    if (hasReview) {
      cap('«Проверка»: очередь сомнительных находок, метки клавишами 1–6, инциденты со статусами и журналом «кто и когда».');
      api().setTab('review');
      await wait(6000);
      api().setTab('map');
    }

    // 13. offline basemap: our Sentinel-2 crops + a free coastline
    api().setView('findings');
    cap('Без сети карта работает на наших снимках Sentinel-2 и свободной береговой линии Natural Earth. Онлайн-подложки — CARTO и Esri, атрибуция внизу справа.');
    api().setBasemap('none');
    await wait(4500);
    api().setBasemap('dark');
    api().caption(TOTAL, TOTAL, null);
  } catch (e) {
    if (!(e instanceof Aborted)) console.warn('tour', e);
  }
}
