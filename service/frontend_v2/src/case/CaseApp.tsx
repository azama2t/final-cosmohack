// «Кейс» mode (default): field observations, scenes + quality masks, zones (detection status + separate
// concentration status), pair registry, metrics, filters, export and saved queries — all from /api/v3.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Info from '../components/Info';
import type { Basemap, Projection } from '../types';
import { loadPrefs, savePrefs } from '../lib/prefs';
import { ctl } from '../map/controller';
import { apiUrl, ApiErr, get, MOCK, send, type FC, type Feat, type Meta, type ObsProps, type Pair, type SavedQuery, type Scene, type ZoneDetail, type ZoneProps } from './api3';
import CaseMap, { ACCENT, CONC_BREAKS, CONC_COLORS, STRIP_NO, STRIP_OK, flyToBox, geomBounds, WORLD_CENTER, worldZoom, type HoverInfo, type Pick } from './CaseMap';
import { ObsCard, ZoneCard } from './Cards';
import PairsDrawer from './PairsDrawer';
import SceneZoneCard, { type SceneZoneDetail, type SceneZoneProps } from './SceneZoneCard';
import { SZ_COLORS, szKey } from './CaseMap';
import MetricsPanel from './MetricsPanel';
import GoList, { rankSites } from './GoList';
import { plural, color, dateRu, eventRu, label, missionShort, num, profileRu, scopeRu, sourceShort } from './fmt';
import {
  DEFAULT_QUERY,
  dateError,
  deleteLocalQuery,
  fromApiQuery,
  isDefault,
  localQueries,
  lossyForApi,
  obsParams,
  pairParams,
  readCaseUrl,
  saveLocalQuery,
  sceneParams,
  toApiQuery,
  writeCaseUrl,
  zoneParams,
  type Bbox,
  type CaseQuery,
} from './query';
import './case.css';

type Load<T> = { data: T | null; err: string | null; loading: boolean };
const L0 = { data: null, err: null, loading: false };
const DET_ORDER = ['detected', 'not_detected', 'insufficient_data'];

function useLoad<T>(fn: ((s: AbortSignal) => Promise<T>) | null, key: string): Load<T> & { reload: () => void } {
  const [s, setS] = useState<Load<T>>(L0);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!fn) return setS(L0);
    const ac = new AbortController();
    setS((x) => ({ ...x, loading: true, err: null }));
    fn(ac.signal).then(
      (d) => !ac.signal.aborted && setS({ data: d, err: null, loading: false }),
      (e) => {
        if (ac.signal.aborted || e?.name === 'AbortError') return;
        setS({ data: null, err: e instanceof ApiErr ? e.message : String(e), loading: false });
      },
    );
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, tick]);
  return { ...s, reload: () => setTick((x) => x + 1) };
}

function bboxOf(fs: Feat<any>[]): Bbox | null {
  let b: Bbox = [180, 90, -180, -90];
  for (const f of fs) {
    const g = geomBounds(f.geometry);
    if (g) b = [Math.min(b[0], g[0]), Math.min(b[1], g[1]), Math.max(b[2], g[2]), Math.max(b[3], g[3])];
  }
  return b[0] <= b[2] ? b : null;
}
const pad = (b: Bbox, d: number): Bbox => [b[0] - d, b[1] - d, b[2] + d, b[3] + d];

function useMenu() {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false);
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('mousedown', off);
    window.addEventListener('keydown', esc);
    return () => {
      window.removeEventListener('mousedown', off);
      window.removeEventListener('keydown', esc);
    };
  }, [open]);
  return { open, setOpen, box };
}

export default function CaseApp() {
  const url = useMemo(readCaseUrl, []);
  const prefs = useMemo(loadPrefs, []);
  const [metaS, setMetaS] = useState<{ meta: Meta | null; err: ApiErr | null }>({ meta: null, err: null });
  const [metaTick, setMetaTick] = useState(0);
  const [q, setQ] = useState<CaseQuery>(url.q ?? DEFAULT_QUERY);
  const [sel, setSel] = useState<Pick | null>(() => {
    const m = url.sel?.match(/^(zone|obs):(.+)$/);
    return m ? { kind: m[1] as 'zone' | 'obs', id: m[2] } : null;
  });
  const [activePair, setActivePair] = useState<string | null>(url.pair);
  const [pairHl, setPairHl] = useState<{ geom: any; scene: Scene | null } | null>(null);
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const [basemap, setBasemap] = useState<Basemap>(prefs.basemap ?? 'satellite');
  const [projection, setProjection] = useState<Projection>(prefs.projection ?? 'globe');
  const [offline, setOffline] = useState(false);
  const [mapReady, setMapReady] = useState(false);
  const [drawer, setDrawer] = useState(url.pairs);
  const [pairStatus, setPairStatus] = useState('all');
  const [leftTab, setLeftTab] = useState<'zones' | 'obs' | 'go' | 'metrics'>(url.tab === 'metrics' ? 'metrics' : url.tab === 'obs' ? 'obs' : url.tab === 'go' ? 'go' : 'zones');
  const [toast, setToast] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedQuery[]>([]);
  const [qName, setQName] = useState('');
  const cam = useRef(url.cam);
  const exportMenu = useMenu();
  const queryMenu = useMenu();
  const layerMenu = useMenu();

  const showToast = useCallback((t: string) => {
    setToast(t);
    setTimeout(() => setToast((x) => (x === t ? null : x)), 3200);
  }, []);

  // ---- meta (if this fails: «Сервис недоступен», nothing invented) ----
  useEffect(() => {
    let alive = true;
    setMetaS({ meta: null, err: null });
    get<Meta>('/api/v3/meta').then(
      (m) => alive && setMetaS({ meta: m, err: null }),
      (e) => alive && setMetaS({ meta: null, err: e instanceof ApiErr ? e : new ApiErr('UNAVAILABLE', 'Сервис недоступен') }),
    );
    return () => {
      alive = false;
    };
  }, [metaTick]);
  const meta = metaS.meta;

  // all observations once: акватория extents + other records of the same transect
  const allObs = useLoad<FC<ObsProps>>(meta ? (s) => get('/api/v3/observations', { limit: 5000 }, s) : null, meta ? 'all' : '');
  const srcBox = useMemo(() => {
    const m = new Map<string, Bbox>();
    const by = new Map<string, Feat<ObsProps>[]>();
    for (const f of allObs.data?.features ?? []) {
      const k = f.properties.source_id;
      if (!by.has(k)) by.set(k, []);
      by.get(k)!.push(f);
    }
    for (const [k, fs] of by) {
      const b = bboxOf(fs);
      if (b) m.set(k, pad(b, 0.3));
    }
    return m;
  }, [allObs.data]);
  const box = q.source ? srcBox.get(q.source) ?? null : null;
  const dErr = dateError(q);
  const ready = !!meta && !!allObs.data && !dErr;
  const fkey = JSON.stringify({ ...q, layers: undefined });

  const oP = obsParams(q);
  const zP = zoneParams(q);
  const sP = sceneParams(q, box);
  const obs = useLoad<FC<ObsProps>>(ready ? (s) => get('/api/v3/observations', { ...oP, geometry: 'line', limit: 5000 }, s) : null, ready ? 'o' + fkey : '');
  const zones = useLoad<FC<ZoneProps>>(ready ? (s) => get('/api/v3/zones', zP, s) : null, ready ? 'z' + fkey : '');
  const scenes = useLoad<{ count: number; scenes: Scene[]; empty_reason: string | null }>(ready ? (s) => get('/api/v3/scenes', sP, s) : null, ready ? 's' + fkey : '');
  const pP = pairParams(q, pairStatus);
  const pairs = useLoad<{ count: number; pairs: Pair[]; empty_reason: string | null }>(
    ready && drawer ? (s) => get('/api/v3/pairs', pP, s) : null,
    ready && drawer ? 'p' + fkey + pairStatus : '',
  );
  // 3.10 satellite scene zones: no field source/profile/scope of their own → hidden when such a filter is on
  const szOn = !q.source && !q.profile && !q.scope;
  const szP = { date_from: q.from, date_to: q.to, detection_status: q.det, concentration_status: q.conc };
  const szones = useLoad<FC<SceneZoneProps>>(ready && szOn ? (s) => get('/api/v3/scene_zones', szP, s) : null, ready && szOn ? 'sz' + fkey : '');
  const szScenes = useLoad<{ scenes: Scene[] }>(meta ? (s) => get('/api/v3/scene_zones/scenes', {}, s) : null, meta ? 'szs' : '');
  const selZoneId = sel?.kind === 'zone' && !sel.id.startsWith('SZ-') ? sel.id : null;
  const selSzId = sel?.kind === 'zone' && sel.id.startsWith('SZ-') ? sel.id : null;
  const zoneDetail = useLoad<ZoneDetail>(selZoneId ? (sg) => get(`/api/v3/zones/${encodeURIComponent(selZoneId)}`, {}, sg) : null, selZoneId ? 'zd' + selZoneId : '');
  const szDetail = useLoad<SceneZoneDetail>(selSzId ? (sg) => get(`/api/v3/scene_zones/${encodeURIComponent(selSzId)}`, {}, sg) : null, selSzId ? 'szd' + selSzId : '');
  // detector objects of the selected strip; a quality-rejected scene (glint / clouds) → grey, «вероятно ложные»
  const detFC = useMemo(() => {
    if (selSzId) return szDetail.data?.detections ?? null;
    const fc = zoneDetail.data?.detections;
    if (!fc) return null;
    const qr = !!zoneDetail.data?.properties?.suspicious_pixels?.quality_rejected;
    return { ...fc, features: fc.features.map((f) => ({ ...f, properties: { ...f.properties, qr } })) };
  }, [zoneDetail.data, szDetail.data, selSzId]);
  const metrics = useLoad<any>(meta && leftTab === 'metrics' ? (s) => get('/api/v3/metrics', {}, s) : null, meta && leftTab === 'metrics' ? 'm' : '');

  const szList = useMemo(() => (szOn ? szones.data?.features ?? [] : []), [szOn, szones.data]);
  const selSz = selSzId ? szList.find((f) => f.id === selSzId) ?? null : null;
  // scene overlays of the layer: the held-out demo scene always, others only while one of their zones is open
  const sceneList = useMemo(() => {
    const extra = (szScenes.data?.scenes ?? []).filter((s: any) => s.evaluable && (s.scene_kind === 'demo' || s.scene_key === selSz?.properties.scene_key));
    return [...(scenes.data?.scenes ?? []), ...(szOn ? extra : [])];
  }, [scenes.data, szScenes.data, selSz, szOn]);
  const sites = useMemo(() => rankSites(allObs.data, obs.data, zones.data), [allObs.data, obs.data, zones.data]);
  const obsById = useMemo(() => {
    const m = new Map<string, Feat<ObsProps>>();
    for (const f of allObs.data?.features ?? []) m.set(f.id, f);
    return m;
  }, [allObs.data]);
  const zoneList = useMemo(() => {
    const fs = [...(zones.data?.features ?? [])];
    fs.sort((a, b) => DET_ORDER.indexOf(a.properties.detection_status) - DET_ORDER.indexOf(b.properties.detection_status) || (a.properties.datetime ?? '').localeCompare(b.properties.datetime ?? ''));
    return fs;
  }, [zones.data]);

  // ---- saved queries ----
  const loadSaved = useCallback(() => {
    get<{ queries: SavedQuery[] }>('/api/v3/queries').then(
      (r) => setSaved([...(r.queries ?? []), ...localQueries()]),
      () => setSaved(localQueries()),
    );
  }, []);
  useEffect(() => {
    if (meta) loadSaved();
  }, [meta, loadSaved]);

  // ---- URL ----
  useEffect(() => {
    writeCaseUrl({
      q: isDefault(q) && q.layers.obs && q.layers.zones && q.layers.scenes && q.layers.quality ? null : q,
      sel: sel ? `${sel.kind}:${sel.id}` : null,
      pair: activePair,
      cam: cam.current,
      pairs: drawer,
      tab: leftTab === 'zones' ? null : leftTab,
    });
  }, [q, sel, activePair, drawer, leftTab]);

  // ---- camera: акватория chosen → fly there ----
  const lastSource = useRef<string | null | undefined>(url.cam ? q.source : undefined);
  /** a selection from the URL flies to itself once the data are there (not to the акватория) */
  const pendingFly = useRef(!!url.sel && !url.cam);
  useEffect(() => {
    if (!allObs.data || !mapReady) return;
    if (lastSource.current === q.source) return;
    const first = lastSource.current === undefined;
    lastSource.current = q.source;
    if (first && pendingFly.current) return;
    if (q.source && srcBox.get(q.source)) flyToBox(srcBox.get(q.source)!, { duration: 1800, maxZoom: 9 });
    else if (!q.source && ctl.map) ctl.map.flyTo({ center: WORLD_CENTER, zoom: worldZoom(ctl.map.getContainer().clientHeight), duration: 1800, essential: true });
  }, [q.source, srcBox, allObs.data, mapReady]);
  // ---- actions ----
  const setFilter = (patch: Partial<CaseQuery>) => {
    setQ((x) => ({ ...x, ...patch }));
    setActivePair(null);
    setPairHl(null);
  };
  const setLayer = (k: keyof CaseQuery['layers']) => setQ((x) => ({ ...x, layers: { ...x.layers, [k]: !x.layers[k] } }));
  const resetFilters = () => {
    setQ((x) => ({ ...DEFAULT_QUERY, layers: x.layers }));
    setSel(null);
    setActivePair(null);
    setPairHl(null);
  };

  const closeCard = () => {
    setSel(null);
    setActivePair(null);
    setPairHl(null);
  };

  const flyToFeat = (f: Feat<any> | undefined | null, maxZoom = 12) => {
    const b = f ? geomBounds(f.geometry) : null;
    if (b) flyToBox(b, { maxZoom, duration: 1400 });
  };

  const openZone = (id: string, fly = true) => {
    setSel({ kind: 'zone', id });
    setActivePair(null);
    setPairHl(null);
    if (fly) flyToFeat(zones.data?.features.find((f) => f.id === id) ?? szList.find((f) => f.id === id), 12.5);
  };
  const openObs = (id: string, fly = true) => {
    setSel({ kind: 'obs', id });
    setActivePair(null);
    setPairHl(null);
    if (fly) flyToFeat(obs.data?.features.find((f) => f.id === id) ?? obsById.get(id), 10);
  };

  const showPair = useCallback(
    async (p: Pair, openCard = true) => {
      setActivePair(p.pair_id);
      if (openCard && sel?.kind !== 'zone') setSel({ kind: 'obs', id: p.sample_id });
      let sc: Scene | null = sceneList.find((s) => s.scene_id === p.scene_id) ?? null;
      if (!sc && p.scene_id) sc = await get<Scene>(`/api/v3/scenes/${encodeURIComponent(p.scene_id)}`).catch(() => null);
      setPairHl({ geom: p.geometry, scene: sc });
      const b1 = geomBounds(p.geometry);
      const b2 = sc?.bounds ?? null;
      const b = b1 && b2 ? ([Math.min(b1[0], b2[0]), Math.min(b1[1], b2[1]), Math.max(b1[2], b2[2]), Math.max(b1[3], b2[3])] as Bbox) : b1 ?? b2;
      if (b) flyToBox(b, { maxZoom: 11, duration: 1400 });
    },
    [sceneList, sel],
  );
  const pairFromTable = (p: Pair) => {
    setSel({ kind: 'obs', id: p.sample_id });
    showPair(p, false);
  };

  const saveQuery = async () => {
    const name = qName.trim() || autoName(meta!, q);
    const aq = toApiQuery(q);
    try {
      await send('POST', '/api/v3/queries', { name, query: aq });
      showToast(`Запрос «${name}» сохранён${lossyForApi(q) ? ' (без фильтра «измерение рядом»)' : ''}`);
    } catch (e: any) {
      saveLocalQuery(name, aq);
      showToast(`Сохранено в браузере: ${e?.message ?? 'сервис не принял запрос'}`);
    }
    setQName('');
    loadSaved();
  };
  const runQuery = async (s: SavedQuery) => {
    queryMenu.setOpen(false);
    let rq = s.query;
    let summary: any = null;
    if (!s.local) {
      try {
        const r = await get<any>(`/api/v3/queries/${encodeURIComponent(s.query_id)}/run`);
        rq = r.query ?? rq;
        summary = r.summary;
      } catch (e: any) {
        showToast(e?.message ?? 'Не удалось запустить запрос');
        return;
      }
    }
    const nq = fromApiQuery(rq);
    setSel(null);
    setActivePair(null);
    setPairHl(null);
    lastSource.current = '__run__';
    setQ(nq);
    showToast(summary ? `«${s.name}»: ${plural(summary.n_obs, 'наблюдение', 'наблюдения', 'наблюдений')}, ${plural(summary.n_zones, 'полоса', 'полосы', 'полос')}, ${plural(summary.n_scene_zones ?? 0, 'спутниковая зона', 'спутниковые зоны', 'спутниковых зон')}` : `«${s.name}» запущен`);
  };
  const deleteQuery = async (s: SavedQuery) => {
    if (s.local) deleteLocalQuery(s.query_id);
    else
      try {
        await send('DELETE', `/api/v3/queries/${encodeURIComponent(s.query_id)}`);
      } catch (e: any) {
        showToast(e?.message ?? 'Не удалось удалить');
      }
    loadSaved();
  };
  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(location.href);
      showToast('Ссылка скопирована — откроет тот же запрос');
    } catch {
      showToast('Ссылка — в адресной строке');
    }
  };

  // Esc: card → drawer
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      const tg = e.target as HTMLElement;
      if (tg && /INPUT|SELECT|TEXTAREA/.test(tg.tagName)) return;
      // an open «i» popover or menu closes first (its own Esc handler)
      if (document.querySelector('.info-pop, .menu')) return;
      if (sel) closeCard();
      else if (drawer) setDrawer(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [sel, drawer]);

  // restore selection from URL / keep it valid
  const selZone = sel?.kind === 'zone' ? zones.data?.features.find((f) => f.id === sel.id) ?? null : null;
  useEffect(() => {
    if (!pendingFly.current || !sel || !mapReady) return;
    const f = sel.kind === 'zone' ? zones.data?.features.find((x) => x.id === sel.id) ?? szList.find((x) => x.id === sel.id) : obsById.get(sel.id);
    if (!f) return;
    pendingFly.current = false;
    flyToFeat(f, sel.kind === 'zone' ? 11.5 : 10);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [zones.data, szList, obsById, sel, mapReady]);

  // ---- test hooks ----
  const mapReadyRef = useRef(false);
  useEffect(() => {
    const w = window as any;
    w.__app = {
      version: 'v2-case',
      mode: 'case',
      mock: MOCK,
      ready: !!meta && !!obs.data && !!zones.data && !!scenes.data && !obs.loading && !zones.loading && !scenes.loading && mapReady,
      metaError: metaS.err?.message ?? null,
      counts: { obs: obs.data?.count ?? null, zones: zones.data?.count ?? null, scenes: scenes.data?.count ?? null, pairs: pairs.data?.count ?? null, szones: szOn ? szones.data?.count ?? null : 0 },
      szIds: () => szList.map((f) => f.id),
      szReady: !szOn || !!szones.data,
      szDetailReady: !!szDetail.data,
      errors: [obs.err, zones.err, scenes.err, pairs.err].filter(Boolean),
      dateError: dErr,
      q,
      sel,
      activePair,
      basemap,
      projection,
      offline,
      drawer,
      pairsReady: !!pairs.data,
      metricsReady: !!metrics.data,
      isMoving: () => !!ctl.map?.isMoving(),
      tiles: () => ({ loaded: !!ctl.map?.areTilesLoaded(), zoom: ctl.map?.getZoom() }),
      exportUrl: (layer: string, fmt: string) => exportHref(layer, fmt),
      selectZone: (id: string) => openZone(id),
      selectObs: (id: string) => openObs(id),
      zoneIds: () => zoneList.map((f) => f.id),
      prefs: () => loadPrefs(),
    };
    mapReadyRef.current = !!window.__mapReady;
  });
  useEffect(() => {
    let frames = 0;
    let last = performance.now();
    let raf = 0;
    const loop = (t: number) => {
      frames++;
      if (t - last >= 1000) {
        window.__fps = Math.round((frames * 1000) / (t - last));
        frames = 0;
        last = t;
      }
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  const exportHref = (layer: string, fmt: string) =>
    apiUrl('/api/v3/export', {
      layer,
      format: fmt,
      ...(layer === 'zones' ? zP : layer === 'pairs' ? pP : layer === 'scene_zones' ? szP : { ...oP, geometry: 'line' }),
    });

  // ---------------------------------------------------------------- render
  if (metaS.err)
    return (
      <div className="c-fatal" data-testid="service-down">
        <div className="c-fatal-box">
          <div className="c-fatal-t">Сервис недоступен</div>
          <div className="c-fatal-s">{metaS.err.code === 'UNAVAILABLE' ? 'API /api/v3 не отвечает. Данные не показываются.' : metaS.err.message}</div>
          <div className="c-fatal-a">
            <button className="btn primary" onClick={() => setMetaTick((x) => x + 1)} data-testid="retry">
              Повторить
            </button>
            <a className="btn ghost" href="?mode=live">
              Живые снимки
            </a>
          </div>
        </div>
      </div>
    );
  if (!meta) return <div className="boot">Загрузка…</div>;

  const layerErr = obs.err || zones.err || scenes.err || allObs.err;
  const isEmpty = !!obs.data && !!zones.data && obs.data.count === 0 && zones.data.count === 0;
  const dateMin = [meta.date_range?.min, meta.scene_date_range?.min].filter(Boolean).sort()[0] as string | undefined;
  const dateMax = [meta.date_range?.max, meta.scene_date_range?.max].filter(Boolean).sort().reverse()[0] as string | undefined;
  const profilesForSource = new Set((allObs.data?.features ?? []).filter((f) => !q.source || f.properties.source_id === q.source).map((f) => f.properties.measurement_profile));
  const rightOpen = !!sel;
  const selObs = sel?.kind === 'obs' ? sel.id : null;
  const sameEvent = selObs && obsById.get(selObs) ? (allObs.data?.features ?? []).filter((f) => f.properties.event_id === obsById.get(selObs)!.properties.event_id) : [];
  const hasQuality = sceneList.some((s) => s.quality_url && s.bounds);
  const nScenesImg = sceneList.filter((s) => s.bounds && (s.preview_url || s.quality_url)).length;

  return (
    <div className={`app case ${rightOpen ? 'right-open' : ''}`} data-testid="case-app">
      {/* ------------------------------------------------ left: mode, filters, zones / metrics */}
      <aside className="left" data-panel="left">
        <div className="modebar">
          <div className="seg" role="tablist" aria-label="Режим">
            <button className="on" aria-selected data-testid="mode-case">
              Кейс
            </button>
            <button onClick={() => (location.href = '?mode=live')} data-testid="mode-live">
              Живые снимки
            </button>
            <button onClick={() => (location.href = '?mode=photo')} data-testid="mode-photo" title="Счётчик предметов по фото (камера у воды; отдельный модуль, не спутник)">
              Фото
            </button>
          </div>
        </div>
        <div className="brand">
          <div className="brand-name">
            <span className="brand-dot" />
            Плавающий мусор: поле и снимки
            <Info label="О карте" testid="info-about">
              Точки — полевые измерения организаторов (шт./км²; совокупность — в карточке: пластик или весь мусор). Пунктирные зоны — полосы наблюдений,
              проверенные снимком: статус обнаружения и отдельно статус концентрации. Детектор видит любой плавающий мусор, пластик не выделяет.
              Концентрация по снимку не выдаётся, пока перенос «снимок → шт./км²» не подтверждён.
            </Info>
          </div>
        </div>
        {MOCK && (
          <div className="c-mock" data-testid="mock-banner">
            Демо-данные (?mock=1) — не настоящие
          </div>
        )}

        <div className="c-filters" data-testid="filters">
          <label className="c-f">
            <span>Акватория</span>
            <select value={q.source ?? ''} onChange={(e) => setFilter({ source: e.target.value || null, profile: null })} data-testid="f-source">
              <option value="">Все ({num(allObs.data?.count ?? null, 0)})</option>
              {meta.sources.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                  {s.n ? ` (${s.n})` : ''}
                </option>
              ))}
            </select>
          </label>
          <div className="c-f">
            <span>
              Даты
              <Info label="Даты" testid="f-dates-info">
                Один диапазон для даты измерения и даты снимка. Поле: {dateRu(meta.date_range?.min)}–{dateRu(meta.date_range?.max)}; снимки:{' '}
                {meta.scene_date_range ? `${dateRu(meta.scene_date_range.min)}–${dateRu(meta.scene_date_range.max)}` : 'нет'}.
              </Info>
            </span>
            <div className="c-dates">
              <input type="date" value={q.from ?? ''} min={dateMin} max={dateMax} onChange={(e) => setFilter({ from: e.target.value || null })} data-testid="f-from" aria-label="Дата с" />
              <span className="faint">–</span>
              <input type="date" value={q.to ?? ''} min={dateMin} max={dateMax} onChange={(e) => setFilter({ to: e.target.value || null })} data-testid="f-to" aria-label="Дата по" />
            </div>
            {dErr && (
              <div className="c-ferr" role="alert" data-testid="date-error">
                {dErr}
              </div>
            )}
          </div>
          <div className="c-row2">
          <label className="c-f">
            <span>Профиль</span>
            <select value={q.profile ?? ''} onChange={(e) => setFilter({ profile: e.target.value || null })} data-testid="f-profile">
              <option value="">Все профили</option>
              {meta.measurement_profiles
                .filter((p) => profilesForSource.has(p.id))
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                    {p.id.startsWith('S1') ? '' : ` · ${p.id.slice(0, 2)}`}
                  </option>
                ))}
            </select>
          </label>
          <label className="c-f">
            <span>Совокупность</span>
            <select value={q.scope ?? ''} onChange={(e) => setFilter({ scope: e.target.value || null })} data-testid="f-scope">
              <option value="">Любая</option>
              {meta.target_scopes.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          </div>
          <div className="c-f">
            <span>
              Статус полосы
              <Info label="Статус полосы">
                Полоса обследования — участок снимка-кандидата вокруг полевой трансекты. «Обнаружено» возможно только при подтверждённой паре снимок ↔ поле.
              </Info>
            </span>
            <div className="c-chips">
              {[...meta.detection_statuses].sort((a, b) => DET_FILTER_ORDER.indexOf(a.id) - DET_FILTER_ORDER.indexOf(b.id)).map((s) => (
                <button
                  key={s.id}
                  className={`c-fchip ${q.det.includes(s.id) ? 'on' : ''}`}
                  onClick={() => setFilter({ det: q.det.includes(s.id) ? q.det.filter((x) => x !== s.id) : [...q.det, s.id] })}
                  aria-pressed={q.det.includes(s.id)}
                  data-testid={`f-det-${s.id}`}
                >
                  <i style={{ background: color(meta.detection_statuses, s.id) }} />
                  {DET_FILTER_RU[s.id] ?? s.label}
                </button>
              ))}
            </div>
          </div>
          <div className="c-f">
            <span>Концентрация по снимку</span>
            <div className="c-chips">
              {meta.concentration_statuses.map((s) => (
                <button
                  key={s.id}
                  className={`c-fchip ${q.conc.includes(s.id) ? 'on' : ''}`}
                  onClick={() => setFilter({ conc: q.conc.includes(s.id) ? q.conc.filter((x) => x !== s.id) : [...q.conc, s.id] })}
                  aria-pressed={q.conc.includes(s.id)}
                  data-testid={`f-conc-${s.id}`}
                >
                  <i className="hollow" style={{ borderColor: color(meta.concentration_statuses, s.id) }} />
                  {s.label}
                </button>
              ))}
            </div>
          </div>
          <div className="c-counts" data-testid="counts">
            <span>
              <b>{num(obs.data?.count ?? null, 0)}</b> наблюдений
            </span>
            <span>
              <b>{num(zones.data?.count ?? null, 0)}</b> полос
            </span>
            <span data-testid="count-szones">
              <b>{num(szOn ? szones.data?.count ?? null : 0, 0)}</b> спутн. зон
            </span>
            <span>
              <b>{num(scenes.data?.count ?? null, 0)}</b> снимков
            </span>
            {!isDefault(q) && (
              <button className="link" onClick={resetFilters} data-testid="f-reset">
                сбросить
              </button>
            )}
          </div>
        </div>

        <div className="tabs">
          <button className={`tab ${leftTab === 'zones' ? 'on' : ''}`} onClick={() => setLeftTab('zones')} data-testid="tab-zones">
            Зоны
          </button>
          <button className={`tab ${leftTab === 'obs' ? 'on' : ''}`} onClick={() => setLeftTab('obs')} data-testid="tab-obs">
            Измерения
          </button>
          <button className={`tab ${leftTab === 'go' ? 'on' : ''}`} onClick={() => setLeftTab('go')} data-testid="tab-go">
            Куда идти
          </button>
          <button className={`tab ${leftTab === 'metrics' ? 'on' : ''}`} onClick={() => setLeftTab('metrics')} data-testid="tab-metrics">
            Метрики
          </button>
        </div>
        <div className="left-body">
          {leftTab === 'metrics' ? (
            <MetricsPanel m={metrics.data} err={metrics.err} />
          ) : leftTab === 'go' ? (
            <GoList meta={meta} sites={sites} sel={selObs} onPick={(s) => openObs(s.best.id)} />
          ) : leftTab === 'obs' ? (
            <ObsList meta={meta} fc={obs.data} sel={selObs} onPick={(id) => openObs(id)} />
          ) : (
            <div data-testid="zone-list">
              <SzList szOn={szOn} fc={szones.data} err={szones.err} list={szList} sel={selSzId} onPick={(id) => openZone(id)} />
              <div className="c-list-note c-lg-sep">Полосы обследования на снимках-кандидатах · {num(zones.data?.count ?? null, 0)}</div>
              {zones.data && !zoneList.length && <div className="empty">{zones.data.empty_reason ?? 'Нет зон под выбранные фильтры'}</div>}
              {zoneList.map((f) => {
                const p = f.properties;
                return (
                  <button key={f.id} className={`c-zi ${sel?.kind === 'zone' && sel.id === f.id ? 'on' : ''}`} onClick={() => openZone(f.id)} data-testid="zone-item">
                    <i className="c-zi-dot" style={{ borderColor: p.pair_status === 'accepted' ? STRIP_OK : STRIP_NO }} />
                    <span className="c-zi-main">
                      <span className="c-zi-t">{eventRu(p.event_id ?? f.id)}</span>
                      <span className="c-zi-s">
                        {p.pair_status === 'accepted' ? 'связь подтверждена' : 'связь не подтверждена'} · {missionShort(p.mission)} {dateRu(p.datetime)}
                      </span>
                    </span>
                    <span className="c-zi-v">
                      {!p.suspicious_pixels?.quality_rejected && (p.suspicious_pixels?.n_objects ?? p.detector?.n_objects ?? 0) > 0 ? (
                        <span className="c-px-n" title="подозрительные пиксели детектора в полосе">
                          <i style={{ background: ACCENT }} />
                          {p.suspicious_pixels?.n_objects ?? p.detector?.n_objects}
                        </span>
                      ) : null}
                      {num(p.area_km2, 2)} км²
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </aside>

      {/* ------------------------------------------------ map */}
      <main className="main" data-testid="main">
        <CaseMap
          meta={meta}
          basemap={basemap}
          offline={offline}
          onOffline={(on) => {
            setOffline(on);
            if (on) showToast('Нет сети — офлайн-подложка');
          }}
          projection={projection}
          obs={obs.data}
          zones={zones.data}
          szones={szOn ? szones.data : null}
          scenes={sceneList}
          layers={q.layers}
          selected={sel}
          pairHl={pairHl}
          initialCamera={url.cam}
          onPick={(pk) => {
            if (!pk) return;
            if (pk.kind === 'zone') openZone(pk.id, false);
            else openObs(pk.id, false);
          }}
          onHover={setHover}
          onReady={() => setMapReady(true)}
          detections={detFC}
          onCamera={(c) => {
            cam.current = c;
            writeCaseUrl({
              q: isDefault(q) && q.layers.obs && q.layers.zones && q.layers.scenes && q.layers.quality ? null : q,
              sel: sel ? `${sel.kind}:${sel.id}` : null,
              pair: activePair,
              cam: c,
              pairs: drawer,
              tab: leftTab === 'zones' ? null : leftTab,
            });
          }}
        />
        {hover && <HoverTip meta={meta} h={hover} obs={obs.data} zones={zones.data} szones={szList} />}

        <div className="actions c-actions" data-testid="actions">
          <button className={drawer ? 'on' : ''} onClick={() => setDrawer((v) => !v)} data-testid="act-pairs">
            Реестр пар
          </button>
          <div className="c-menu-wrap" ref={exportMenu.box}>
            <button className={exportMenu.open ? 'on' : ''} onClick={() => exportMenu.setOpen((v) => !v)} data-testid="act-export" aria-expanded={exportMenu.open}>
              Выгрузка
            </button>
            {exportMenu.open && (
              <div className="menu c-menu" data-testid="export-menu">
                <div className="menu-group">То, что сейчас отфильтровано</div>
                {(
                  [
                    ['scene_zones', 'Спутниковые зоны', szOn ? szones.data?.count : 0],
                    ['zones', 'Полосы', zones.data?.count],
                    ['observations', 'Наблюдения', obs.data?.count],
                    ['pairs', 'Пары', pairs.data?.count],
                  ] as [string, string, number | undefined][]
                ).map(([k, l, n]) => (
                  <div className="c-exp-row" key={k}>
                    <span>
                      {l}
                      {n !== undefined ? <span className="faint"> · {num(n, 0)}</span> : null}
                    </span>
                    <a className="btn sm ghost" href={exportHref(k, 'geojson')} download data-testid={`export-${k}-geojson`}>
                      GeoJSON
                    </a>
                    <a className="btn sm ghost" href={exportHref(k, 'csv')} download data-testid={`export-${k}-csv`}>
                      CSV
                    </a>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="c-menu-wrap" ref={queryMenu.box}>
            <button className={queryMenu.open ? 'on' : ''} onClick={() => queryMenu.setOpen((v) => !v)} data-testid="act-queries" aria-expanded={queryMenu.open}>
              Запросы
            </button>
            {queryMenu.open && (
              <div className="menu c-menu" data-testid="query-menu">
                <div className="menu-group">Сохранить текущий</div>
                <div className="c-save">
                  <input value={qName} placeholder={autoName(meta, q)} onChange={(e) => setQName(e.target.value)} data-testid="q-name" aria-label="Название запроса" maxLength={200} />
                  <button className="btn sm" onClick={saveQuery} data-testid="q-save" disabled={!!dErr}>
                    Сохранить
                  </button>
                </div>
                <div className="menu-sep" />
                <div className="menu-group">Сохранённые</div>
                {!saved.length && <div className="note c-pad-s">пока нет</div>}
                {saved.map((s) => (
                  <div className="c-q-row" key={s.query_id} data-testid="q-item">
                    <button className="c-q-run" onClick={() => runQuery(s)} data-testid="q-run" title="Запустить">
                      <span className="c-q-n">{s.name}</span>
                      <span className="faint tiny">
                        {dateRu(s.created_at)}
                        {s.local ? ' · в браузере' : ''}
                      </span>
                    </button>
                    <button className="icon-btn" onClick={() => deleteQuery(s)} aria-label="Удалить" data-testid="q-del">
                      ✕
                    </button>
                  </div>
                ))}
                <div className="menu-sep" />
                <button className="menu-row" onClick={copyLink} data-testid="q-link">
                  Скопировать ссылку на этот вид
                </button>
              </div>
            )}
          </div>
        </div>

        <div className="toolbar" ref={layerMenu.box} data-testid="toolbar">
          <button className={`btn ${layerMenu.open ? 'on' : ''}`} onClick={() => layerMenu.setOpen((v) => !v)} data-testid="layers-menu" aria-expanded={layerMenu.open}>
            Слои
          </button>
          {layerMenu.open && (
            <div className="menu" data-testid="layers-panel">
              <div className="menu-group">Слои</div>
              {(
                [
                  ['obs', 'Полевые наблюдения'],
                  ['zones', 'Снимки-кандидаты: полосы обследования'],
                  ['scenes', `Снимки (${num(sceneList.filter((s) => s.preview_url && s.bounds).length, 0)} вырезок)`],
                  ['quality', 'Маска качества'],
                ] as [keyof CaseQuery['layers'], string][]
              ).map(([k, l]) => (
                <button key={k} className="menu-row" onClick={() => setLayer(k)} data-testid={`layer-${k}`} aria-pressed={q.layers[k]}>
                  <span className={`check ${q.layers[k] ? 'on' : ''}`} aria-hidden />
                  <span>{l}</span>
                </button>
              ))}
              <div className="menu-sep" />
              <div className="menu-group">Подложка{offline ? ' · сейчас офлайн' : ''}</div>
              {(
                [
                  ['satellite', 'Спутник (Esri)'],
                  ['dark', 'Тёмная (CARTO)'],
                  ['none', 'Без подложки'],
                ] as [Basemap, string][]
              ).map(([b, l]) => (
                <button
                  key={b}
                  className="menu-row"
                  onClick={() => {
                    setBasemap(b);
                    setOffline(false);
                    savePrefs({ basemap: b });
                  }}
                  data-testid={`basemap-${b}`}
                >
                  <span className={`radio ${basemap === b ? 'on' : ''}`} aria-hidden />
                  <span>{l}</span>
                </button>
              ))}
              <div className="menu-sep" />
              <div className="menu-group">Проекция</div>
              {(
                [
                  ['globe', 'Глобус'],
                  ['mercator', 'Плоская карта'],
                ] as [Projection, string][]
              ).map(([pr, l]) => (
                <button
                  key={pr}
                  className="menu-row"
                  onClick={() => {
                    setProjection(pr);
                    savePrefs({ projection: pr });
                  }}
                  data-testid={`proj-${pr}`}
                >
                  <span className={`radio ${projection === pr ? 'on' : ''}`} aria-hidden />
                  <span>{l}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        <CaseLegend meta={meta} layers={q.layers} hasQuality={hasQuality && q.layers.quality} nScenes={nScenesImg} zones={zones.data} hasDet={!!zoneDetail.data?.detections?.features?.length} />

        {layerErr && (
          <div className="c-banner" role="alert" data-testid="layer-error">
            {layerErr}
            <button
              className="btn sm"
              onClick={() => {
                obs.reload();
                zones.reload();
                scenes.reload();
                allObs.reload();
              }}
            >
              Повторить
            </button>
          </div>
        )}
        {!layerErr && isEmpty && (
          <div className="c-empty" data-testid="empty-result">
            <div className="c-empty-t">Нет данных под выбранные фильтры</div>
            <div className="c-empty-s">{obs.data?.empty_reason ?? zones.data?.empty_reason}</div>
            <button className="btn sm" onClick={resetFilters} data-testid="empty-reset">
              Сбросить фильтры
            </button>
          </div>
        )}
        {dErr && (
          <div className="c-empty" data-testid="date-error-map">
            <div className="c-empty-t">{dErr}</div>
            <div className="c-empty-s">Запрос не отправлен — исправьте даты</div>
          </div>
        )}

        {drawer && (
          <PairsDrawer
            meta={meta}
            pairs={pairs.data}
            loading={pairs.loading}
            err={pairs.err}
            status={pairStatus}
            onStatus={setPairStatus}
            active={activePair}
            onPair={pairFromTable}
            onClose={() => setDrawer(false)}
            exportHref={(fmt) => exportHref('pairs', fmt)}
            showSource={!q.source}
          />
        )}

        <div className="attrib" data-testid="attribution">
          {!offline && basemap === 'dark' && '© CARTO, © OpenStreetMap contributors'}
          {!offline && basemap === 'satellite' && 'Tiles © Esri — Esri, Maxar, Earthstar Geographics'}
          {(offline || basemap === 'none') && 'Natural Earth'}
          {' · Снимки: Copernicus Sentinel-2, USGS Landsat · Поле: данные организаторов (CSV)'}
        </div>
        {toast && (
          <div className="toast" role="status" data-testid="toast">
            {toast}
          </div>
        )}
      </main>

      <aside className="right" data-testid="right-panel">
        {sel?.kind === 'zone' && selSz && <SceneZoneCard meta={meta} zone={selSz} detail={szDetail.data} onClose={closeCard} onZone={(id) => openZone(id)} />}
        {sel?.kind === 'zone' && selZone && (
          <ZoneCard
            meta={meta}
            zone={selZone}
            scenes={sceneList}
            activePair={activePair}
            detector={zones.data?.model?.detector ?? null}
            detail={zoneDetail.data}
            onClose={closeCard}
            onObs={(id) => openObs(id)}
            onPair={(p) => showPair(p, false)}
          />
        )}
        {sel?.kind === 'zone' && !selZone && !selSz && zones.data && (!selSzId || szones.data) && (
          <div className="right-inner">
            <div className="sec note">Зона {sel.id} не входит в текущий фильтр</div>
          </div>
        )}
        {sel?.kind === 'obs' && (
          <ObsCard meta={meta} id={sel.id} sameEvent={sameEvent} activePair={activePair} onClose={closeCard} onObs={(id) => openObs(id)} onPair={(p) => showPair(p, false)} />
        )}
      </aside>
    </div>
  );
}

/** density records of the current filter, highest first: compare within one size profile */
function ObsList({ meta, fc, sel, onPick }: { meta: Meta; fc: FC<ObsProps> | null; sel: string | null; onPick: (id: string) => void }) {
  const [n, setN] = useState(100);
  const rows = useMemo(
    () =>
      (fc?.features ?? [])
        .filter((f) => f.properties.concentration_items_km2 !== null)
        .sort((a, b) => (b.properties.concentration_items_km2 ?? 0) - (a.properties.concentration_items_km2 ?? 0)),
    [fc],
  );
  if (!fc) return <div className="note c-pad">Загрузка…</div>;
  const nNo = fc.features.length - rows.length;
  const profs = new Set(rows.map((f) => f.properties.measurement_profile));
  return (
    <div data-testid="obs-list">
      <div className="c-list-note">
        {rows.length} с плотностью{nNo ? ` · ${nNo} без` : ''}
        {profs.size === 1 ? ` · ${profileRu(meta, [...profs][0])}` : ''}
        {profs.size > 1 && (
          <Info label="Сравнение">
            В списке {profs.size} размерных профиля. Концентрации разных профилей несравнимы — выберите один профиль в фильтре.
          </Info>
        )}
      </div>
      {!rows.length && <div className="empty">{fc.empty_reason ?? 'Нет измерений плотности под выбранные фильтры'}</div>}
      {rows.slice(0, n).map((f) => {
        const p = f.properties;
        return (
          <button key={f.id} className={`c-zi ${sel === f.id ? 'on' : ''}`} onClick={() => onPick(f.id)} data-testid="obs-item">
            <i className="c-kind obs" />
            <span className="c-zi-main">
              <span className="c-zi-t">{eventRu(p.event_id)}</span>
              <span className="c-zi-s">
                {dateRu(p.date_utc)} · {scopeRu(meta, p.target_scope)}
              </span>
            </span>
            <span className="c-zi-v">
              <b>{num(p.concentration_items_km2)}</b> шт./км²
            </span>
          </button>
        );
      })}
      {rows.length > n && (
        <div className="c-pad">
          <button className="btn sm" onClick={() => setN((x) => x + 300)}>
            Показать ещё ({rows.length - n})
          </button>
        </div>
      )}
    </div>
  );
}

function autoName(meta: Meta, q: CaseQuery): string {
  const parts = [q.source ? sourceShort(meta, q.source) : 'Все акватории'];
  if (q.from || q.to) parts.push(`${q.from ? dateRu(q.from) : '…'}–${q.to ? dateRu(q.to) : '…'}`);
  if (q.profile) parts.push(profileRu(meta, q.profile));
  if (q.det.length) parts.push(q.det.map((d) => label(meta.detection_statuses, d)).join(', '));
  return parts.join(' · ').slice(0, 120);
}

function HoverTip({ meta, h, obs, zones, szones }: { meta: Meta; h: HoverInfo; obs: FC<ObsProps> | null; zones: FC<ZoneProps> | null; szones: Feat<SceneZoneProps>[] }) {
  let t = '';
  let s = '';
  if (h.kind === 'obs') {
    const f = obs?.features.find((x) => x.id === h.id);
    if (!f) return null;
    const p = f.properties;
    const v = p.concentration_items_km2;
    t = `Измерение · ${v === null ? 'без плотности' : `${num(v)} шт./км²`}`;
    s = `${profileRu(meta, p.measurement_profile)} · ${dateRu(p.date_utc)} · ${scopeRu(meta, p.target_scope)}`;
  } else if (h.id.startsWith('SZ-')) {
    const f = szones.find((x) => x.id === h.id);
    if (!f) return null;
    const p = f.properties;
    t = `Спутниковая зона · ${p.detection_label}`;
    s = `${p.title} · ${dateRu(p.datetime)} · подозрительные пиксели ${num(p.measured.suspicious_area_m2, 0)} м²`;
  } else {
    const f = zones?.features.find((x) => x.id === h.id);
    if (!f) return null;
    const p = f.properties;
    t = `Снимок-кандидат · полоса обследования`;
    s = `${p.pair_status === 'accepted' ? 'связь с полем подтверждена' : 'связь с полем не подтверждена'} · ${label(meta.concentration_statuses, p.concentration_status).toLowerCase()}`;
  }
  return (
    <div className="tip" style={{ left: Math.min(h.x + 14, 9999), top: h.y + 14 }} data-testid="hover-tip">
      <div className="tt">{t}</div>
      <div className="ts">{s}</div>
    </div>
  );
}

const DET_FILTER_ORDER = ['insufficient_data', 'not_detected', 'detected'];
const DET_FILTER_RU: Record<string, string> = {
  insufficient_data: 'связь не подтверждена',
  not_detected: 'пикселей не найдено',
  detected: 'обнаружено',
};

const SZ_FLAG_RU: Record<string, string> = { foam: 'пена', glint: 'блик', ship: 'судно', seam: 'шов', coast: 'берег', shallow: 'мелководье' };
const SZ_ORDER = ['detected', 'unverified', 'insufficient_data', 'not_detected'];

/** satellite scene zones of the current filter: the held-out Cózar scene first, then by status and pixel area */
function SzList({ szOn, fc, err, list, sel, onPick }: { szOn: boolean; fc: FC<SceneZoneProps> | null; err: string | null; list: Feat<SceneZoneProps>[]; sel: string | null; onPick: (id: string) => void }) {
  const [n, setN] = useState(40);
  const rows = useMemo(
    () =>
      [...list].sort(
        (a, b) =>
          (a.properties.scene_kind === 'demo' ? 0 : 1) - (b.properties.scene_kind === 'demo' ? 0 : 1) ||
          SZ_ORDER.indexOf(szKey(a.properties)) - SZ_ORDER.indexOf(szKey(b.properties)) ||
          (b.properties.measured.suspicious_area_m2 ?? 0) - (a.properties.measured.suspicious_area_m2 ?? 0),
      ),
    [list],
  );
  return (
    <div data-testid="sz-list">
      <div className="c-list-note">
        Спутниковые зоны детектора · {err ? '—' : list.length}
        <Info label="Спутниковые зоны">
          Зоны, где текущий детектор (weights/lgbm, порог 0,63) нашёл подозрительные пиксели на реальных снимках: отложенная сцена Cózar 2024 и снимки районов.
          «Обнаружено» — только если контур совпадает с нитью каталога Cózar 2024 (независимая разметка людьми, уровень B); остальное — «срабатывание, не
          проверено». Признаки пены, блика, судна, берега или мелководья → «недостаточно данных». Снимки, где детектор не оценивается (низкое солнце), зон не дают.
        </Info>
      </div>
      {szOn && err && (
        <div className="c-err" role="alert" data-testid="sz-error">
          данные зон недоступны (ошибка API: {err})
        </div>
      )}
      {!szOn && <div className="empty">скрыты: у спутниковых зон нет акватории, профиля и совокупности поля — сбросьте эти фильтры</div>}
      {szOn && fc && !list.length && <div className="empty">{fc.empty_reason ?? 'Нет спутниковых зон под выбранные фильтры'}</div>}
      {rows.slice(0, n).map((f) => {
        const p = f.properties;
        return (
          <button key={f.id} className={`c-zi ${sel === f.id ? 'on' : ''}`} onClick={() => onPick(f.id)} data-testid="sz-item">
            <i className="c-zi-dot sz" style={{ borderColor: SZ_COLORS[szKey(p)] ?? '#9aa0a8' }} />
            <span className="c-zi-main">
              <span className="c-zi-t">{p.title}</span>
              <span className="c-zi-s">
                {p.detection_label.split(' (')[0]}
                {p.flags.length ? ` · ${p.flags.map((x) => SZ_FLAG_RU[x] ?? x).join(', ')}` : ''} · {dateRu(p.datetime)}
              </span>
            </span>
            <span className="c-zi-v">{num(p.measured.suspicious_area_m2, 0)} м²</span>
          </button>
        );
      })}
      {rows.length > n && (
        <div className="c-pad">
          <button className="btn sm" onClick={() => setN((x) => x + 100)}>
            Показать ещё ({rows.length - n})
          </button>
        </div>
      )}
    </div>
  );
}

/** one line for the whole view: /meta.summary.text, else counted from the zones of the current filter */
function summaryText(meta: Meta, zones: FC<ZoneProps> | null): string | null {
  const t = (meta as any).summary?.text;
  if (typeof t === 'string' && t) return t;
  if (!zones) return null;
  const n = zones.features.length;
  const ok = zones.features.filter((f) => f.properties.pair_status === 'accepted').length;
  const plastic = zones.features.some((f) => ['total_plastic', 'plastic_category'].includes(f.properties.support?.field_target_scope ?? ''));
  return `${plural(n, 'обследованный участок', 'обследованных участка', 'обследованных участков')} со снимками-кандидатами; ${plural(ok, 'подтверждённая пара', 'подтверждённые пары', 'подтверждённых пар')}${plastic ? '' : '; для пластика снимков нет'}`;
}

function CaseLegend({
  meta,
  layers,
  hasQuality,
  nScenes,
  zones,
  hasDet,
}: {
  meta: Meta;
  layers: CaseQuery['layers'];
  hasQuality: boolean;
  nScenes: number;
  zones: FC<ZoneProps> | null;
  hasDet: boolean;
}) {
  // 1366×768 and similar: compact by default (summary + one row of symbols), expands on click
  const [open, setOpen] = useState(() => window.innerHeight >= 900);
  const qc = meta.quality_classes.filter((c) => c.present !== false && c.id !== 'valid');
  const absent = meta.quality_classes.filter((c) => c.present === false);
  return (
    <div className={`legend c-legend ${open ? '' : 'min'}`} data-testid="legend">
      <div className="c-lg-head">
        <span className="lg-title">Легенда</span>
        <Info label="Как читать" testid="legend-info">
          Сплошной символ — полевое измерение. Пунктир — полоса обследования на снимке-кандидате (не контур пятна). Жёлтые контуры — подозрительные пиксели детектора
          выбранной полосы. Цвет точки — концентрация, шт./км²: сравнима только внутри одного размерного профиля.
          {absent.length ? ` ${absent.map((c) => `${c.label}: ${c.note ?? 'в маске не выделяется'}`).join('. ')}.` : ''}
        </Info>
        <button className="icon-btn c-lg-min" onClick={() => setOpen((v) => !v)} aria-label={open ? 'Свернуть легенду' : 'Развернуть легенду'} data-testid="legend-toggle">
          {open ? '–' : '+'}
        </button>
      </div>
      {!open && (
        <button className="c-lg-compact" onClick={() => setOpen(true)} data-testid="legend-compact" title="Развернуть легенду">
          {summaryText(meta, zones) && <span className="c-lg-summary clamp">{summaryText(meta, zones)}</span>}
          <span className="c-lg-keys">
            <span className="c-sw-dot" /> измерение
            <span className="c-sw-strip-i" /> полоса
            <span className="c-sw-px" /> пиксели
          </span>
        </button>
      )}
      {open && (
        <>
          {summaryText(meta, zones) && (
            <div className="c-lg-summary" data-testid="legend-summary">
              {summaryText(meta, zones)}
            </div>
          )}
          {layers.obs && (
            <>
              <div className="lg-row">
                <span className="c-sw-dot" />
                Полевые измерения, шт./км²
              </div>
              <div className="c-ramp" aria-hidden>
                {CONC_COLORS.map((c) => (
                  <span key={c} style={{ background: c }} />
                ))}
              </div>
              <div className="c-ramp-t">
                <span>&lt;{CONC_BREAKS[0]}</span>
                {CONC_BREAKS.slice(1).map((b) => (
                  <span key={b}>{num(b, 0)}</span>
                ))}
              </div>
              <div className="lg-row">
                <span className="c-sw-ring" />
                измеренный ноль
                <span className="c-sw-dia" />
                без плотности
              </div>
            </>
          )}
          {layers.zones && (
            <>
              <div className="lg-row c-lg-sep">Снимки-кандидаты: полосы обследования</div>
              <div className="c-lg-status">
                <span className="c-chip">
                  <i className="c-sw-strip" style={{ borderColor: STRIP_NO }} />
                  связь не подтверждена
                </span>
                <span className="c-chip">
                  <i className="c-sw-strip" style={{ borderColor: STRIP_OK }} />
                  подтверждена
                </span>
              </div>
              <div className="lg-row c-lg-sep2">
                <span className="c-sw-px" />
                Подозрительные пиксели детектора{hasDet ? '' : ' (у выбранной полосы)'}
              </div>
              <div className="lg-row c-lg-sep">Спутниковые зоны детектора</div>
              <div className="c-lg-status" data-testid="legend-szones">
                <span className="c-chip">
                  <i className="sq" style={{ background: SZ_COLORS.detected }} />
                  обнаружено, совпадает с нитью Cózar
                </span>
                <span className="c-chip">
                  <i className="sq" style={{ background: SZ_COLORS.unverified }} />
                  срабатывание, не проверено
                </span>
                <span className="c-chip">
                  <i className="sq" style={{ background: SZ_COLORS.insufficient_data }} />
                  пена / блик / судно / берег
                </span>
                <span className="c-chip">
                  <i className="sq" style={{ background: SZ_COLORS.not_detected }} />
                  не обнаружено
                </span>
              </div>
              <div className="c-lg-note">класс MARIDA Marine Debris = любой плавающий мусор, не только пластик; без полевого подтверждения</div>
              {(meta as any).detector?.version?.sha256_short && (
                <div className="c-lg-note" data-testid="legend-model">
                  модель: {(meta as any).detector.version.weights} · sha256 {(meta as any).detector.version.sha256_short} · {dateRu((meta as any).detector.version.trained_at)} · порог{' '}
                  {num((meta as any).detector.version.threshold, 2)}
                </div>
              )}
            </>
          )}
          {hasQuality && (
            <>
              <div className="lg-row c-lg-sep">Маска качества{nScenes ? ` · ${nScenes} снимков` : ''}</div>
              <div className="c-lg-status" data-testid="legend-quality">
                {qc.map((c) => (
                  <span className="c-chip" key={c.id}>
                    <i className="sq" style={{ background: c.color ?? 'transparent' }} />
                    {c.label}
                  </span>
                ))}
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
