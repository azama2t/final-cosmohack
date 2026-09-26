// «Кейс» mode (default): field observations, scenes + quality masks, zones (detection status + separate
// concentration status), pair registry, metrics, filters, export and saved queries — all from /api/v3.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Info from '../components/Info';
import type { Basemap, Projection } from '../types';
import { loadPrefs, savePrefs } from '../lib/prefs';
import { ctl } from '../map/controller';
import { API_BASE, apiUrl, ApiErr, get, MOCK, send, type FC, type Feat, type Meta, type ObsProps, type Pair, type SavedQuery, type Scene, type ZoneDetail, type ZoneProps } from './api3';
import CaseMap, { ACCENT, CONC_BREAKS, CONC_COLORS, STRIP_NO, STRIP_OK, flyToBox, geomBounds, WORLD_CENTER, worldZoom, type HoverInfo, type Pick } from './CaseMap';
import { ObsCard, ZoneCard } from './Cards';
import PairsDrawer from './PairsDrawer';
import SceneZoneCard, { zoneTitle, type CardTab, type SceneZoneDetail, type SceneZoneProps } from './SceneZoneCard';
import { SZ_COLORS, szKey, isFind, geomCenter } from './CaseMap';
import { NasaFolder, NasaMapPanel, useNasaInfo } from './Nasa';
import { DronesOptions, openDrones, openDronesFromValue } from './DronesHost';
import { PhotoRolesLine } from './PhotoRoles';
import ZoneStudio from './Studio'; // §50 P1-4/5/6 L142
import { QualityToggle } from './QualityMask'; // §50 P1-6 L142
import { ALERT_RU, AlertBadge, AlertFilter, byAlert, byOrganic, OrganicLegend, useAlertFilter, useOrganicFilter } from './Alerts'; // §51 п.7 L142
import QcPanel, { HELP_EVENT } from './QcPanel'; // §50 P2 L142
import Timeline, { type TlScene } from './Timeline';
import { openDynamics } from './DynamicsHost';
import { SceneIntegral } from './DynamicsPanel';
import { plural, dateRu, eventRu, label, missionShort, num, profileRu, scopeRu, sourceShort } from './fmt';
import { estLine, estTxt, RES_CAPTION, RES_CAPTION_LIST, RES_CONTEXT, RES_NOTE, researchEst } from './estimate';
import { shortName } from '../lib/data';
import { statusLabel, classShort, confirmation } from './zoneinfo';
import {
  DEFAULT_QUERY,
  dateError,
  deleteLocalQuery,
  fromApiQuery,
  isDefault,
  layersDefault,
  localQueries,
  lossyForApi,
  obsParams,
  pairParams,
  readCaseUrl,
  saveLocalQuery,
  sceneParams,
  szParams,
  toApiQuery,
  writeCaseUrl,
  zoneParams,
  type Bbox,
  type CaseQuery,
} from './query';
import './case.css';
import DriftPlayer from '../components/DriftPlayer';
import type { DriftFile } from '../types';
import { checkLine, closeDrift, driftBounds, driftCaption, driftKey, driftPaths, openDrift, renderDrift } from './drift';
import { DRIFT_CORRIDOR_LABEL } from './DriftLayer';
import DemoTour, { type DemoTourHandle } from './DemoTour';
import DriftMapButton from './DriftMapButton'; // §54 п.4 (L141): «Дрейф» visible on the map next to the selected find

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
  const [studio, setStudio] = useState(false);
  /** §34 п.3: ONE block at a time — «Фильтры» (left), «Цифры» or «Проверка качества» (modal over the map) */
  const [panel, setPanelRaw] = useState<null | 'filters' | 'nums' | 'qc'>(() => {
    if (url.tab === 'metrics') return 'qc';
    try {
      if (localStorage.getItem('mp.case.filtersOpen') === '1') return 'filters';
    } catch {
      /* no storage */
    }
    return null;
  });
  const setPanel = (v: null | 'filters' | 'nums' | 'qc') => {
    setPanelRaw(v);
    try {
      localStorage.setItem('mp.case.filtersOpen', v === 'filters' ? '1' : '0');
    } catch {
      /* no storage */
    }
  };
  const togglePanel = (v: 'filters' | 'nums' | 'qc') => setPanel(panel === v ? null : v);
  // §50 P2-9 L142: «Справка / Методика» can be opened from the zone card
  useEffect(() => {
    const on = () => setPanel('qc');
    window.addEventListener(HELP_EVENT, on);
    return () => window.removeEventListener(HELP_EVENT, on);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  /** §34 п.3: the snapshot opened in the left list (scene_key) — its image on the map + its numbered zones */
  const [scene, setScene] = useState<string | null>(url.scene ?? null);
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
  const [obsListOpen, setObsListOpen] = useState(url.tab === 'obs');
  const [toast, setToast] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedQuery[]>([]);
  const [qName, setQName] = useState('');
  const cam = useRef(url.cam);
  const exportMenu = useMenu();
  const queryMenu = useMenu();
  const layerMenu = useMenu();
  const moreMenu = useMenu();
  // §54 п.1: NASA «ежедневно» — one «NASA» button shows / hides the folder, the layer and its captions; off by default,
  // not part of the query / URL
  const [nasaOn, setNasaOn] = useState(false);
  const [nasaLayerId, setNasaLayerId] = useState<string | null>(null);
  const [nasaDay, setNasaDay] = useState<string | null>(null);
  const nasaInfo = useNasaInfo(nasaOn);
  // «Запросы» live inside «Ещё ▾»: closing «Ещё» closes them too (no stale open state behind a closed menu)
  useEffect(() => {
    if (!moreMenu.open) queryMenu.setOpen(false);
  }, [moreMenu.open]); // eslint-disable-line react-hooks/exhaustive-deps

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
  const box = q.bbox;
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
  // 3.10 satellite scene zones — §34 п.3: the main list; акватория (frame), dates, zone status; field-layer filters
  // (profile / совокупность) do not touch them
  const szOn = true;
  const szP = szParams(q);
  const szones = useLoad<FC<SceneZoneProps>>(ready ? (s) => get('/api/v3/scene_zones', szP, s) : null, ready ? 'sz' + fkey : '');
  const szScenes = useLoad<{ scenes: SzScene[] }>(meta ? (s) => get('/api/v3/scene_zones/scenes', {}, s) : null, meta ? 'szs' : '');
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
  const photoMeta = useLoad<any>(meta ? (s) => get('/api/v3/photo/meta', {}, s) : null, meta ? 'pm' : '');
  const metrics = useLoad<any>(meta && panel === 'qc' ? (s) => get('/api/v3/metrics', {}, s) : null, meta && panel === 'qc' ? 'm' : '');

  /** §33 / jury 08:51: the first-screen count is finds (detector «detected»), the rest is secondary */
  const nFinds = useMemo(() => {
    const fs = szones.data?.features ?? [];
    return { n: fs.filter((f) => isFind(f.properties)).length, b: fs.filter((f) => (f.properties as any).verification === 'level_B_cozar').length };
  }, [szones.data]);
  const szList = useMemo(() => szones.data?.features ?? [], [szones.data]);
  const selSz = selSzId ? szList.find((f) => f.id === selSzId) ?? null : null;
  // §47 п.4: the card tab; a new zone opens on «Главное»
  const [cardTab, setCardTab] = useState<CardTab>('main');
  useEffect(() => setCardTab('main'), [selSzId]);
  // §44 п.3: drift forecast of the open zone's snapshot (published OpenDrift run), experiment, own style
  const demoRef = useRef<DemoTourHandle>(null);
  const [dPaths, setDPaths] = useState<Map<string, string> | null>(null);
  const [drift, setDrift] = useState<DriftFile | null>(null);
  const [driftKeyOn, setDriftKeyOn] = useState<string | null>(null);
  const [driftSum, setDriftSum] = useState<any>(null);
  useEffect(() => {
    driftPaths().then(setDPaths);
  }, []);
  const selDriftPath = selSz && dPaths ? dPaths.get(driftKey(selSz.properties as any)) ?? null : null;
  /** jury_s44 п.2–3: which snapshots of the list have a published drift forecast */
  const nDriftScenes = useMemo(() => (dPaths ? (szScenes.data?.scenes ?? []).filter((x) => dPaths.has(driftKey(x as any))).length : 0), [dPaths, szScenes.data]);
  const hasDrift = useCallback((x: { region?: string; datetime?: string | null } | null | undefined) => !!(x && dPaths?.has(driftKey(x))), [dPaths]);
  const stopDrift = useCallback(() => {
    closeDrift();
    setDrift(null);
    setDriftKeyOn(null);
  }, []);
  useEffect(() => {
    // another snapshot (or no zone) → the forecast of the previous one goes away
    if (driftKeyOn && (!selSz || driftKey(selSz.properties as any) !== driftKeyOn)) stopDrift();
  }, [selSz, driftKeyOn, stopDrift]);
  useEffect(() => {
    if (!drift) return;
    const t = window.setTimeout(renderDrift, 900); // the style may have been reloaded (basemap/projection)
    return () => window.clearTimeout(t);
  }, [drift, basemap, projection]);
  const toggleDrift = useCallback(async () => {
    if (drift || !selSz || !selDriftPath) {
      stopDrift();
      return;
    }
    const d = await openDrift(selDriftPath);
    if (!d) return;
    setDrift(d);
    setDriftKeyOn(driftKey(selSz.properties as any));
    if (!driftSum)
      fetch(API_BASE + '/api/drift_check')
        .then((r) => (r.ok ? r.json() : null))
        .then(setDriftSum)
        .catch(() => undefined);
    const b = driftBounds(d);
    if (b && ctl.map) ctl.map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 80, duration: 1200, maxZoom: 12 });
  }, [drift, selSz, selDriftPath, driftSum, stopDrift]);
  /** the snapshot shown: the one opened in the list, else the scene of the open zone */
  const curScene = scene ?? selSz?.properties.scene_key ?? null;
  const szScene = useMemo(() => (szScenes.data?.scenes ?? []).find((s) => s.scene_key === curScene) ?? null, [szScenes.data, curScene]);
  /** §54 п.1: the NASA day — chosen by the user (arrows / timeline), else the latest full day from the API */
  const nasaDate = nasaDay ?? nasaInfo?.latestFull ?? new Date(Date.now() - 864e5).toISOString().slice(0, 10);
  const nasaLayer = nasaInfo ? nasaInfo.layers.find((x) => x.id === (nasaLayerId ?? nasaInfo.defaultLayer)) ?? nasaInfo.layers[0] : null;
  const nasa = useMemo(
    () => (nasaOn && nasaLayer ? { url: nasaLayer.tile_url.replace('{date}', nasaDate), maxzoom: nasaLayer.max_native_zoom ?? 9 } : null),
    [nasaOn, nasaLayer, nasaDate],
  );
  // image overlays: the chosen snapshot; candidate scenes of the field pairs only with the «Полевые измерения» layer
  const sceneList = useMemo(() => {
    const extra = szScene && szScene.evaluable !== false ? [szScene as Scene] : [];
    return [...(q.layers.obs ? scenes.data?.scenes ?? [] : []), ...extra];
  }, [scenes.data, szScene, q.layers.obs]);
  /** §34 п.3: «район · дата · N находок · облачность» — only scenes with zones under the current filter (= map = export) */
  const sceneRows = useMemo(() => buildSceneRows(szScenes.data?.scenes ?? [], szList, q), [szScenes.data, szList, q]);
  // §48 timeline: the same snapshots as the left list (район/период/статус of step 1) + field dates
  const tlScenes = useMemo<TlScene[]>(
    () =>
      sceneRows.all
        .map((r) => ({
          key: r.s.scene_key,
          t: Date.parse(r.s.datetime ?? ''),
          finds: r.finds,
          b: r.b,
          label: `${sceneName(r.s)} · ${dateRu(r.s.datetime)}`,
          drift: !!dPaths?.has(driftKey(r.s as any)),
          noeval: r.group === 'noeval',
        }))
        .filter((x) => Number.isFinite(x.t)),
    [sceneRows, dPaths],
  );
  const tlObs = useMemo(() => (obs.data?.features ?? []).map((f) => Date.parse(f.properties.date_utc ?? '')).filter((t) => Number.isFinite(t)), [obs.data]);
  /** numbered zones of the shown snapshot: the same numbers in the list, on the map and in the card */
  const sceneZones = useMemo(() => (curScene ? numberZones(szList.filter((f) => f.properties.scene_key === curScene)) : []), [szList, curScene]);
  const numbered = useMemo(
    () =>
      sceneZones
        .filter((z) => z.n > 0)
        .map((z) => ({ id: z.f.id, n: z.n, at: geomCenter(z.f.geometry as any) as [number, number], ds: szKey(z.f.properties) }))
        .filter((z) => !!z.at),
    [sceneZones],
  );
  const selNum = selSzId ? sceneZones.find((z) => z.f.id === selSzId)?.n : undefined;
  /** on the map: every zone on the Earth overview; only the zones of the open snapshot once one is open (other dates of
   *  the same place would overlap its numbered zones) */
  const szMap = useMemo(
    () => (curScene && szones.data ? { ...szones.data, features: szones.data.features.filter((f) => f.properties.scene_key === curScene) } : szones.data),
    [szones.data, curScene],
  );
  /** акватории: районы of the satellite snapshots (frame = union of their crops) + field sources of the organisers */
  const areas = useMemo(() => {
    const reg = new Map<string, { id: string; label: string; box: Bbox; n: number }>();
    for (const s of szScenes.data?.scenes ?? []) {
      if (!s.bounds || !s.region) continue;
      const k = 'r:' + s.region;
      const r = reg.get(k);
      const b = s.bounds as Bbox;
      if (!r) reg.set(k, { id: k, label: s.scene_kind === 'demo' ? 'Альборан (отложенная сцена Cózar)' : shortName(s.region_name ?? s.region), box: [...b] as Bbox, n: 1 });
      else {
        r.box = [Math.min(r.box[0], b[0]), Math.min(r.box[1], b[1]), Math.max(r.box[2], b[2]), Math.max(r.box[3], b[3])];
        r.n++;
      }
    }
    const regions = [...reg.values()].map((r) => ({ ...r, box: pad(r.box, 0.02) })).sort((a, b) => a.label.localeCompare(b.label, 'ru'));
    const fields = (meta?.sources ?? []).map((s) => ({ id: s.id, label: s.label, box: srcBox.get(s.id) ?? null, n: s.n ?? 0 }));
    return { regions, fields };
  }, [szScenes.data, meta, srcBox]);
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
  const urlState = (c: typeof cam.current) => ({
    q: isDefault(q) && layersDefault(q.layers) ? null : q,
    sel: sel ? `${sel.kind}:${sel.id}` : null,
    pair: activePair,
    cam: c,
    pairs: drawer,
    tab: panel === 'qc' ? 'metrics' : obsListOpen && q.layers.obs ? 'obs' : null,
    scene,
  });
  const navKey = `${scene ?? ''}|${sel ? `${sel.kind}:${sel.id}` : ''}`;
  const lastNav = useRef(navKey);
  const popping = useRef(false);
  useEffect(() => {
    // §47 п.3: «назад» of the browser = «← к снимку» / «← к списку снимков»
    const push = navKey !== lastNav.current && !popping.current;
    lastNav.current = navKey;
    popping.current = false;
    writeCaseUrl(urlState(cam.current), push);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, sel, activePair, drawer, panel, scene, obsListOpen]);
  useEffect(() => {
    const onPop = () => {
      const u = readCaseUrl();
      popping.current = true;
      const m = u.sel?.match(/^(zone|obs):(.+)$/);
      setStudio(false);
      setSel(m ? { kind: m[1] as 'zone' | 'obs', id: m[2] } : null);
      setScene(u.scene ?? null);
      if (!u.scene && !m && ctl.map) ctl.map.flyTo({ center: WORLD_CENTER, zoom: worldZoom(ctl.map.getContainer().clientHeight), duration: 1200, essential: true });
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  // ---- camera: акватория chosen → fly there ----
  const lastSource = useRef<string | null | undefined>(url.cam || url.scene ? q.area : undefined);
  /** a selection from the URL flies to itself once the data are there (not to the акватория) */
  const pendingFly = useRef(!!url.sel && !url.cam);
  /** the URL selection keeps the camera: the first акватория/world fly must not override it even if the selection
   *  flew before all observations loaded (bug: ?sel=zone:SZ-… opened on the globe) */
  const urlSelFly = useRef(!!url.sel && !url.cam);
  useEffect(() => {
    if (!allObs.data || !mapReady) return;
    if (lastSource.current === q.area) return;
    const first = lastSource.current === undefined;
    lastSource.current = q.area;
    if (first && urlSelFly.current) return;
    if (q.bbox) flyToBox(q.bbox, { duration: 1800, maxZoom: 10 });
    else if (!q.bbox && ctl.map && !first) ctl.map.flyTo({ center: WORLD_CENTER, zoom: worldZoom(ctl.map.getContainer().clientHeight), duration: 1800, essential: true });
  }, [q.area, q.bbox, allObs.data, mapReady]);
  // a snapshot from the URL (no camera, no selection): fly to it once the scene list is there
  const pendingSceneFly = useRef(!!url.scene && !url.cam && !url.sel);
  useEffect(() => {
    if (!pendingSceneFly.current || !mapReady || !szScene?.bounds) return;
    pendingSceneFly.current = false;
    flyToBox(szScene.bounds as Bbox, { maxZoom: 12, duration: 1200 });
  }, [szScene, mapReady]);
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
    setScene(null);
    setActivePair(null);
    setPairHl(null);
  };
  /** §34 п.3: акватория = район of the snapshots or a field source; its frame filters the zones, the map and the export */
  const setArea = (id: string) => {
    const a = [...areas.regions, ...areas.fields].find((x) => x.id === id) ?? null;
    const field = !!a && !a.id.startsWith('r:');
    // a field акватория has no satellite zones (API: source → 0 zones) — its measurements are what there is: layer on
    setFilter({ area: a ? a.id : null, bbox: a?.box ? (a.box.map((v) => +v.toFixed(4)) as Bbox) : null, source: field ? a!.id : null, profile: null, ...(field ? { layers: { ...q.layers, obs: true } } : {}) });
    setSel(null);
    setStudio(false);
    setScene(null);
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

  /** §33: camera (and the open snapshot) before a find was opened — «Назад» returns there to pick the next find */
  const backCam = useRef<{ center: [number, number]; zoom: number; scene: string | null } | null>(null);
  const camNow = () => {
    const m = ctl.map;
    return m ? { center: [m.getCenter().lng, m.getCenter().lat] as [number, number], zoom: m.getZoom(), scene } : null;
  };
  const rememberCam = () => {
    if (!sel) backCam.current = camNow();
  };
  const goBack = () => {
    const b = backCam.current;
    setStudio(false);
    closeCard();
    if (b) setScene(b.scene);
    if (b && ctl.map) ctl.map.flyTo({ center: b.center, zoom: b.zoom, duration: 1200, essential: true });
    backCam.current = null;
  };
  const toEarth = () => {
    setStudio(false);
    closeCard();
    setScene(null);
    backCam.current = null;
    sceneBack.current = null;
    if (ctl.map) ctl.map.flyTo({ center: WORLD_CENTER, zoom: worldZoom(ctl.map.getContainer().clientHeight), duration: 1400, essential: true });
  };
  /** §34 п.3: a snapshot of the list → its image on the map + its numbered zones; «← все снимки» returns the camera */
  const sceneBack = useRef<{ center: [number, number]; zoom: number } | null>(null);
  const openScene = (key: string) => {
    const c = camNow();
    if (!scene && !sel && c) sceneBack.current = { center: c.center, zoom: c.zoom };
    setStudio(false);
    closeCard();
    backCam.current = null;
    setScene(key);
    const s = (szScenes.data?.scenes ?? []).find((x) => x.scene_key === key);
    if (s?.bounds) flyToBox(s.bounds as Bbox, { maxZoom: 12, duration: 1400 });
  };
  const closeScene = () => {
    const b = sceneBack.current;
    setStudio(false);
    closeCard();
    setScene(null);
    backCam.current = null;
    sceneBack.current = null;
    if (ctl.map) {
      if (b) ctl.map.flyTo({ center: b.center, zoom: b.zoom, duration: 1200, essential: true });
      else if (q.bbox) flyToBox(q.bbox, { duration: 1200, maxZoom: 10 });
      else ctl.map.flyTo({ center: WORLD_CENTER, zoom: worldZoom(ctl.map.getContainer().clientHeight), duration: 1200, essential: true });
    }
  };
  const openZone = (id: string, fly = true) => {
    if (id.startsWith('SZ-')) {
      rememberCam();
      const f = szList.find((x) => x.id === id);
      if (f) {
        if (!scene && !sel) {
          const c = camNow();
          if (c) sceneBack.current = { center: c.center, zoom: c.zoom };
        }
        setScene(f.properties.scene_key);
      }
    }
    if (fly) {
      urlSelFly.current = true; // the first акватория/world fly must not override this selection
      if (!mapReady || !ctl.map) pendingFly.current = true; // fly when the map is ready
    }
    setStudio(false);
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
    // the frame of a saved query → the акватория it was saved from (same frame), else «рамка запроса»
    if (nq.bbox) {
      const a = [...areas.regions, ...areas.fields].find((x) => x.box && x.box.every((v, i) => Math.abs(+v.toFixed(4) - nq.bbox![i]) < 1e-3));
      if (a) {
        nq.area = a.id;
        nq.source = a.id.startsWith('r:') ? null : a.id;
      }
    }
    if (nq.source && !nq.bbox) nq.bbox = areas.fields.find((x) => x.id === nq.source)?.box ?? null;
    nq.layers = { ...nq.layers, obs: q.layers.obs };
    setSel(null);
    setScene(null);
    setStudio(false);
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
      if (panel === 'nums' || panel === 'qc') setPanel(null);
      else if (sel) closeCard();
      else if (drawer) setDrawer(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sel, drawer, panel]);

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
      /** §34 п.3 test hooks: snapshot rows of the left list, the open snapshot and its numbered zones */
      sceneRows: () => sceneRows.all.map((r) => ({ key: r.s.scene_key, finds: r.finds, zones: r.zones.length, group: r.group })),
      scene: curScene,
      sceneZones: () => sceneZones.map((z) => ({ id: z.f.id, n: z.n, est: researchEst(z.f.properties) })),
      openScene: (k: string) => openScene(k),
      panel,
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
      /** §33 test hook: rendered find points / clusters on the map (screen px, map-relative) */
      findPoints: () => {
        const m = ctl.map;
        if (!m || !m.getLayer('c-sz-pts')) return [];
        return m.queryRenderedFeatures(undefined as any, { layers: ['c-sz-pts', 'c-sz-clu'] }).map((f: any) => {
          const pt = m.project(f.geometry.coordinates);
          return { x: pt.x, y: pt.y, id: f.properties.id ?? null, cluster: f.properties.point_count ?? 0, ds: f.properties.ds ?? null };
        });
      },
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

  const alertTop = useAlertFilter(); // §51 п.7 L142: the level filter goes into the zone export too
  const orgTop = useOrganicFilter(); // §51 п.9 L142: and the organic filter
  const exportHref = (layer: string, fmt: string) =>
    apiUrl('/api/v3/export', {
      layer,
      format: fmt,
      ...(layer === 'zones' ? zP : layer === 'pairs' ? pP : layer === 'scene_zones' ? { ...szP, alert_level: alertTop === 'all' ? null : ALERT_RU[alertTop], organic: orgTop === 'all' ? null : orgTop === 'yes' ? 'true' : 'false' } : { ...oP, geometry: 'line' }),
    });
  const exportRows: [string, string, number | undefined][] = [
    ['scene_zones', alertTop !== 'all' || orgTop !== 'all' ? `Спутниковые зоны: только${alertTop !== 'all' ? ` алерт «${ALERT_RU[alertTop]}»` : ''}${orgTop !== 'all' ? (orgTop === 'yes' ? ' «вероятно органика»' : ' без флага органики') : ''}` : `Спутниковые зоны (все статусы; из них находок ${szones.data ? nFinds.n : '—'})`, szones.err ? undefined : szones.data?.count],
    ['observations', 'Полевые измерения', obs.data?.count],
    ['zones', 'Полосы обследования (поле ↔ снимок)', zones.data?.count],
    ['pairs', q.bbox && !q.source ? 'Пары снимок ↔ поле (все: рамка района к парам не применяется)' : 'Пары снимок ↔ поле', pairs.data?.count],
  ];

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
          </div>
        </div>
      </div>
    );
  if (!meta) return <div className="boot">Загрузка…</div>;

  const layerErr = obs.err || zones.err || scenes.err || allObs.err;
  const dateMin = [meta.date_range?.min, meta.scene_date_range?.min].filter(Boolean).sort()[0] as string | undefined;
  const dateMax = [meta.date_range?.max, meta.scene_date_range?.max].filter(Boolean).sort().reverse()[0] as string | undefined;
  const profilesForSource = new Set((allObs.data?.features ?? []).filter((f) => !q.source || f.properties.source_id === q.source).map((f) => f.properties.measurement_profile));
  const rightOpen = !!sel;
  // §47 п.1: the current step of the expert path
  const step = exportMenu.open ? 5 : panel === 'qc' ? 4 : selSz ? 3 : curScene ? 2 : 1;
  const nextHint =
    step === 1
      ? 'выберите район и даты слева, затем снимок в списке (точки на Земле — находки детектора)'
      : step === 2
        ? 'выберите зону — номер на карте или строку в списке слева'
        : step === 3
          ? 'посмотрите качество и статус (вкладки карточки), затем «5 Выгрузка»'
          : step === 4
            ? 'закройте окно и выгрузите результат — «5 Выгрузка»'
            : 'CSV или GeoJSON — то, что сейчас отфильтровано';
  const goStep1 = () => {
    setPanel('filters');
    if (curScene) closeScene();
    window.setTimeout(() => (document.querySelector('[data-testid=f-source]') as HTMLElement | null)?.focus(), 50);
  };
  const goStep2 = () => {
    if (sel) closeCard();
  };
  const goStep3 = () => {
    if (panel === 'qc') setPanel(null);
    setCardTab('main');
  };
  const selObs = sel?.kind === 'obs' ? sel.id : null;
  const sameEvent = selObs && obsById.get(selObs) ? (allObs.data?.features ?? []).filter((f) => f.properties.event_id === obsById.get(selObs)!.properties.event_id) : [];
  const hasQuality = sceneList.some((s) => s.quality_url && s.bounds);
  const nScenesImg = sceneList.filter((s) => s.bounds && (s.preview_url || s.quality_url)).length;
  const szStatuses: { id: string; label: string }[] = SZ_FILTER.map((id) => ({ id, label: SZ_FILTER_RU[id] }));
  // §51 п.3 (L140 panel): the район of step 1, else of the open snapshot
  const dynRegion = q.area && q.area.startsWith('r:') ? q.area.slice(2) : szScene?.region ?? null;
  const areaLabel = q.area ? [...areas.regions, ...areas.fields].find((a) => a.id === q.area)?.label ?? (q.area === 'bbox' ? 'рамка запроса' : q.area) : null;

  return (
    <div className={`app case ${rightOpen ? 'right-open' : ''}`} data-testid="case-app">
      {/* §47 п.7 / §50 п.8 «Демо ▶» (L141): trigger is a normal button in .toolbar (below, next to «Слои») so the
          two share one top-right action group with fixed gaps — a standalone fixed button used to sit at the exact
          same corner and cover «Слои» once the card panel was closed (Егор, скрин 11). */}
      <DemoTour ref={demoRef} />
      {/* §54 п.4: «Дрейф» кнопка на карте у выбранной находки, не только во вкладке карточки */}
      <DriftMapButton zone={selSz} path={selDriftPath} on={!!drift} onToggle={toggleDrift} />
      {/* ------------------------------------------------ left: mode, filters, snapshots → zones */}
      <aside className="left" data-panel="left">
        <div className="modebar">
          <div className="seg" role="tablist" aria-label="Режим">
            <button className="on" aria-selected data-testid="mode-case">
              Снимки и зоны
            </button>
            <button onClick={() => (location.href = '?mode=photo')} data-testid="mode-photo" title="Счётчик предметов по фото (камера у воды; отдельный модуль, не спутник)">
              Счёт по фото
            </button>
          </div>
        </div>
        <div className="brand">
          <div className="brand-name">
            <span className="brand-dot" />
            Плавающий мусор: снимки и поле
            <Info label="О карте" testid="info-about">
              Точки на Земле — находки детектора на обработанных снимках Sentinel-2 (после фильтров судов, пены, блика, облаков и ветра). Путь — полоса шагов сверху:
              район и даты → снимок → зона → качество и статус → выгрузка. Количество предметов по снимку не определяется (принятых пар 0); оценка шт./км² — по
              полевым данным (профиль акватории), исследовательский сценарий PLP — во вкладке «Подробно» карточки. Полевые измерения организаторов — отдельный слой
              («Ещё ▾ → Полевые измерения»).
            </Info>
          </div>
        </div>
        {MOCK && (
          <div className="c-mock" data-testid="mock-banner">
            Демо-данные (?mock=1) — не настоящие
          </div>
        )}

        <div className="c-filters" data-testid="filters">
          {/* §47 п.2: step 1 «Район и даты» — open in the left panel (the snapshot list below = the dates of the район) */}
          {(!curScene || panel === 'filters') && (
            <div className={`c-step1 ${panel === 'filters' ? 'open' : ''}`} data-testid="step1">
              <div className="c-step1-h">
                <span className="c-stn">1</span> Район и даты
              </div>
              <label className="c-f">
                  <span>Район</span>
                  <select value={q.area && q.area !== 'bbox' ? q.area : q.area === 'bbox' ? 'bbox' : ''} onChange={(e) => {
                      if (openDronesFromValue(e.target.value)) return;
                      setArea(e.target.value);
                    }}
                    data-testid="f-source"
                  >
                    <option value="">Все</option>
                    {q.area === 'bbox' && <option value="bbox">Рамка сохранённого запроса</option>}
                    <optgroup label="Районы снимков Sentinel-2">
                      {areas.regions.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.label} ({plural(a.n, 'снимок', 'снимка', 'снимков')})
                        </option>
                      ))}
                    </optgroup>
                    <optgroup label="Полевые данные организаторов">
                      {areas.fields.map((a) => (
                        <option key={a.id} value={a.id}>
                          {a.label}
                          {a.n ? ` (${a.n})` : ''}
                        </option>
                      ))}
                    </optgroup>
                    <DronesOptions />
                  </select>
                </label>
                <div className="c-f">
                  <span>
                    Даты снимка
                    <Info label="Даты" testid="f-dates-info">
                      Один диапазон для даты снимка и даты полевого измерения. Снимки: {meta.scene_date_range ? `${dateRu(meta.scene_date_range.min)}–${dateRu(meta.scene_date_range.max)}` : 'нет'};
                      поле: {dateRu(meta.date_range?.min)}–{dateRu(meta.date_range?.max)}.
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
              <button
                className="btn sm ghost c-dyn-btn"
                disabled={!dynRegion && !curScene}
                onClick={() => openDynamics({ region: dynRegion, scene: curScene })}
                data-testid="dynamics-open"
                title={dynRegion || curScene ? 'Сравнение дат района: находки, площадь скоплений, поле' : 'Сначала выберите район'}
              >
                Динамика района по датам
              </button>
            </div>
          )}
        <div className="c-lbar" data-testid="left-bar">
            <button className={`c-lbtn ${panel === 'filters' ? 'on' : ''}`} onClick={() => togglePanel('filters')} aria-expanded={panel === 'filters'} data-testid="filters-toggle">
              Статус зоны{q.det.length ? ' · задан' : ''} {panel === 'filters' ? '▾' : '▸'}
            </button>
            <button className={`c-lbtn ${panel === 'nums' ? 'on' : ''}`} onClick={() => togglePanel('nums')} data-testid="headline-open" title="Концентрация по полевым данным, счётчик по фото, спутник">
              Цифры
            </button>
          </div>
          {panel === 'filters' && (
            <div className="c-filters-body">
              <div className="c-f">
                <span>
                  Статус зоны
                  <Info label="Статус зоны">
                    «Находка» — детектор отметил пиксели, и признаков судна/кильватера, пены, блика, облаков, берега и мелководья нет. «Недостаточно данных» — такие
                    признаки есть или ветер {'>'} 5 м/с. «Не обнаружено» — снимок оценивается, объектов нет. Фильтр действует одинаково на список, карту и выгрузку.
                  </Info>
                </span>
                <div className="c-chips">
                  {szStatuses.map((s) => (
                    <button
                      key={s.id}
                      className={`c-fchip ${q.det.includes(s.id) ? 'on' : ''}`}
                      onClick={() => setFilter({ det: q.det.includes(s.id) ? q.det.filter((x) => x !== s.id) : [...q.det, s.id] })}
                      aria-pressed={q.det.includes(s.id)}
                      data-testid={`f-det-${s.id}`}
                    >
                      <i style={{ background: SZ_COLORS[s.id === 'detected' ? 'unverified' : s.id] }} />
                      {s.label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}
          {/* §44 п.1: with a snapshot open the list goes first — the totals stay on the overview */}
          <div className={`c-counts ${curScene ? 'c-hide' : ''}`} data-testid="counts">
            {szones.err ? (
              <span className="c-err-inline" title={szones.err} data-testid="count-szones">
                спутниковые зоны: данные недоступны
              </span>
            ) : (
              <>
                <span data-testid="count-scenes">
                  <b>{num(szones.data ? sceneRows.withZones : null, 0)}</b> {pluralW(sceneRows.withZones, 'снимок', 'снимка', 'снимков')}
                </span>
                <span data-testid="count-szones" title={`всего спутниковых зон ${num(szones.data?.count ?? null, 0)}: вместе с «недостаточно данных» и «не обнаружено»`}>
                  <b>{num(szones.data ? nFinds.n : null, 0)}</b> {pluralW(nFinds.n, 'находка', 'находки', 'находок')} ({num(szones.data ? nFinds.b : null, 0)} · Cózar B)
                </span>
              </>
            )}
            {areaLabel && <span className="faint">· {areaLabel}</span>}
            {!isDefault(q) && (
              <button className="link" onClick={resetFilters} data-testid="f-reset">
                сбросить
              </button>
            )}
          </div>
        </div>

        <div className="left-body" data-testid="zone-list">
          {nasaOn && !curScene && <NasaFolder info={nasaInfo} onRegion={(r) => flyToBox(r.bbox, { maxZoom: Math.min(r.zoom ?? 9, 9), duration: 1200 })} />}
          {!curScene ? (
            <SceneList rows={sceneRows} loading={!szones.data && !szones.err} err={szones.err} onPick={openScene} filtered={!isDefault(q)} fieldArea={!!q.source}
              drift={hasDrift}
              area={q.area && q.area.startsWith('r:') ? areaLabel : null}
              onField={() => setQ((x) => ({ ...x, layers: { ...x.layers, obs: true } }))}
            />
          ) : (
            <SceneZones
              s={szScene}
              sceneKey={curScene}
              zones={sceneZones}
              sel={selSzId}
              onPick={(id) => {
                if (sel?.id !== id) openZone(id);
              }}
              onClose={closeScene}
              layers={q.layers}
              onLayer={setLayer}
              drift={hasDrift(szScene ?? (selSz?.properties as any))}
            />
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
          szones={szMap}
          scenes={sceneList}
          nasa={nasa}
          layers={q.layers}
          selected={sel}
          pairHl={pairHl}
          initialCamera={url.cam}
          numbered={q.layers.zones ? numbered : undefined}
          onPick={(pk) => {
            setHover(null);
            if (!pk) return;
            // §47 п.3: one click = one result. A find of another snapshot (or on the Earth) → that snapshot and its
            // zone list (no card); a zone of the open snapshot → only its card; the same zone again → nothing
            if (pk.kind === 'zone' && pk.id.startsWith('SZ-')) {
              const f = szList.find((x) => x.id === pk.id);
              const sk = f?.properties.scene_key ?? null;
              if (sk && sk !== curScene) {
                openScene(sk);
                return;
              }
              if (sel?.id === pk.id) return;
              openZone(pk.id, false);
            } else if (pk.kind === 'zone') openZone(pk.id, false);
            else openObs(pk.id, false);
          }}
          onHover={setHover}
          onReady={() => setMapReady(true)}
          detections={detFC}
          onCamera={(c) => {
            cam.current = c;
            writeCaseUrl(urlState(c));
          }}
        />
        {/* §33б: with a card open the find tooltip would repeat the card — not shown */}
        {hover && !(sel && hover.id.startsWith('SZ-')) && <HoverTip meta={meta} h={hover} obs={obs.data} zones={zones.data} szones={szList} />}

        {/* §47 п.1: the step bar «1 Район и даты → 2 Снимок → 3 Зона → 4 Качество и статус → 5 Выгрузка»; the rest — «Ещё ▾» */}
        <div className="actions c-actions c-steps" data-testid="actions" data-step={step}>
          <button className={`c-step ${step === 1 ? 'cur' : ''}`} onClick={goStep1} data-testid="step-1" title="Выбрать район и период, затем снимок в списке слева">
            <span className="c-stn">1</span> <span className="c-al">Район и даты</span>
            <span className="c-as">Район</span>
          </button>
          <button
            className={`c-step ${step === 2 ? 'cur' : ''}`}
            onClick={goStep2}
            disabled={!curScene}
            data-testid="step-2"
            title={curScene ? 'Снимок и список его зон' : 'Сначала выберите снимок в списке слева или точку на Земле'}
          >
            <span className="c-stn">2</span> Снимок
          </button>
          <button
            className={`c-step ${step === 3 ? 'cur' : ''}`}
            onClick={goStep3}
            disabled={!selSz}
            data-testid="step-3"
            title={selSz ? 'Карточка выбранной зоны' : 'Сначала выберите зону — номер на карте или строку в списке'}
          >
            <span className="c-stn">3</span> Зона
          </button>
          <button className={`c-step ${panel === 'qc' ? 'on cur' : ''}`} onClick={() => togglePanel('qc')} data-testid="act-qc" title="Проверка качества: метрики детектора и концентрации (F1, интервалы, базовые линии)">
            <span className="c-stn">4</span> <span className="c-al">Качество и статус</span>
            <span className="c-as">Качество</span>
          </button>
          <div className="c-menu-wrap" ref={exportMenu.box}>
            <button className={`c-step ${exportMenu.open ? 'on cur' : ''}`} onClick={() => exportMenu.setOpen((v) => !v)} data-testid="act-export" aria-expanded={exportMenu.open}>
              <span className="c-stn">5</span> Выгрузка
            </button>
            {exportMenu.open && (
              <div className="menu c-menu" data-testid="export-menu">
                <div className="menu-group">То, что сейчас отфильтровано</div>
                {exportRows.map(([k, l, n]) => (
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
          <div className="c-menu-wrap c-more" ref={moreMenu.box}>
            <button className={moreMenu.open ? 'on' : ''} onClick={() => moreMenu.setOpen((v) => !v)} data-testid="act-more" aria-expanded={moreMenu.open}>
              Ещё ▾
            </button>
            {moreMenu.open && (
              <div className="menu c-menu c-more-menu" data-testid="more-menu">
          <button onClick={() => { moreMenu.setOpen(false); toEarth(); }} className="menu-row" data-testid="act-earth" title="Вернуться к обзору Земли со всеми находками">
            ⊕ <span className="c-al">Обзор Земли</span>
            <span className="c-as">Земля</span>
          </button>
          <button
            className={`menu-row ${q.layers.obs ? 'on' : ''}`}
            onClick={() => {
              if (q.layers.obs) {
                if (sel?.kind === 'obs') closeCard();
                setDrawer(false);
              }
              setLayer('obs');
            }}
            aria-pressed={q.layers.obs}
            data-testid="act-field"
            title="Слой «Полевые измерения» организаторов (шт./км² по трансектам) — включить / выключить"
          >
            <span className={`check ${q.layers.obs ? 'on' : ''}`} aria-hidden /> <span className="c-al">Полевые измерения</span>
            <span className="c-as">Поле</span>
          </button>
                <button
                  className="menu-row"
                  onClick={() => {
                    moreMenu.setOpen(false);
                    layerMenu.setOpen(true);
                  }}
                  data-testid="more-layers"
                >
                  Слои карты
                </button>
                <button
                  className={`menu-row ${nasaOn ? 'on' : ''}`}
                  onClick={() => setNasaOn((v) => !v)}
                  aria-pressed={nasaOn}
                  data-testid="more-nasa"
                  title="Ежедневный обзорный снимок NASA GIBS — фон для облачности / цветения / пятен, не обнаружение пластика"
                >
                  <span className={`check ${nasaOn ? 'on' : ''}`} aria-hidden /> NASA: обзор (MODIS/VIIRS, ~250 м)
                </button>
                <button
                  className="menu-row"
                  onClick={() => {
                    moreMenu.setOpen(false);
                    openDrones();
                  }}
                  data-testid="more-drones"
                  title="Детальные кадры с дронов: предметы по классам — не спутник"
                >
                  Дроны: кадры с предметами (не спутник)
                </button>
                <a className="menu-row" href="?mode=photo" data-testid="more-photo">
                  Посчитать предметы по фото
                </a>
                <div className="menu-sep" />
            <div className="c-menu-wrap c-q-inline" ref={queryMenu.box}>
              <button className={queryMenu.open ? 'on' : ''} onClick={() => queryMenu.setOpen((v) => !v)} data-testid="act-queries" aria-expanded={queryMenu.open}>
                Запросы
              </button>
              {queryMenu.open && (
                <div className="menu c-menu" data-testid="query-menu">
                  <div className="menu-group">Сохранить текущий (акватория, даты, статус зоны)</div>
                  <div className="c-save">
                    <input value={qName} placeholder={autoName(meta, q, areaLabel)} onChange={(e) => setQName(e.target.value)} data-testid="q-name" aria-label="Название запроса" maxLength={200} />
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
            )}
          </div>
        </div>
        <div className="c-next" data-testid="next-hint">
          <b>Что дальше:</b> {nextHint}
        </div>

        {q.layers.obs && (
          <FieldPanel
            meta={meta}
            fc={obs.data}
            nStrips={zones.data?.count ?? null}
            q={q}
            profiles={profilesForSource}
            onFilter={setFilter}
            listOpen={obsListOpen}
            onList={() => setObsListOpen((v) => !v)}
            sel={selObs}
            onPick={(id) => openObs(id)}
            drawer={drawer}
            onDrawer={() => setDrawer((v) => !v)}
            onClose={() => {
              if (sel?.kind === 'obs') closeCard();
              setDrawer(false);
              setLayer('obs');
            }}
          />
        )}

        <div className="toolbar" ref={layerMenu.box} data-testid="toolbar">
          {/* §50 п.8: same group as «Слои», fixed gap (flex, .toolbar) — not a separate fixed button anymore */}
          <button
            className={`btn c-nasa-btn ${nasaOn ? 'on' : ''}`}
            onClick={() => setNasaOn((v) => !v)}
            aria-pressed={nasaOn}
            data-testid="nasa-toggle"
            title="NASA · ежедневно: показать / скрыть папку, слой и подписи (обзор, не обнаружение пластика)"
          >
            NASA
          </button>
          <button className="btn" onClick={() => demoRef.current?.open()} data-testid="demo-tour-btn">
            Демо ▶
          </button>
          <button className={`btn ${layerMenu.open ? 'on' : ''}`} onClick={() => layerMenu.setOpen((v) => !v)} data-testid="layers-menu" aria-expanded={layerMenu.open}>
            Слои
          </button>
          {layerMenu.open && (
            <div className="menu" data-testid="layers-panel">
              <div className="menu-group">Слои</div>
              {(
                [
                  ['zones', 'Спутниковые зоны (находки)'],
                  ['scenes', 'Снимок Sentinel-2 выбранного района'],
                  ['quality', 'Маска качества снимка'],
                  ['obs', 'Полевые измерения (+ полосы обследования)'],
                ] as [keyof CaseQuery['layers'], string][]
              ).map(([k, l]) => (
                <button key={k} className="menu-row" onClick={() => setLayer(k)} data-testid={`layer-${k}`} aria-pressed={q.layers[k]}>
                  <span className={`check ${q.layers[k] ? 'on' : ''}`} aria-hidden />
                  <span>{l}</span>
                </button>
              ))}
              <button className="menu-row" onClick={() => setNasaOn((v) => !v)} data-testid="layer-nasa" aria-pressed={nasaOn}>
                <span className={`check ${nasaOn ? 'on' : ''}`} aria-hidden />
                <span>NASA: обзор (MODIS/VIIRS, ~250 м) · не обнаружение</span>
              </button>
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
        {!layerErr && !!szones.data && szones.data.count === 0 && (!q.layers.obs || obs.data?.count === 0) && (
          <div className="c-empty" data-testid="empty-result">
            <div className="c-empty-t">Нет спутниковых зон под выбранные фильтры</div>
            <div className="c-empty-s">{szones.data.empty_reason ?? 'в этой акватории и в эти даты обработанных снимков с зонами нет'}</div>
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

        {drawer && q.layers.obs && (
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
          {nasa && ' · Обзор: NASA EOSDIS GIBS'}
        </div>
        {nasaOn && (
          <NasaMapPanel
            info={nasaInfo}
            layerId={nasaLayer?.id ?? ''}
            date={nasaDate}
            sceneDate={szScene?.datetime?.slice(0, 10) ?? null}
            onLayer={setNasaLayerId}
            onDate={setNasaDay}
            onClose={() => setNasaOn(false)}
          />
        )}
        {toast && (
          <div className="toast" role="status" data-testid="toast">
            {toast}
          </div>
        )}

        {/* §34 п.3: «Цифры» and «Проверка качества» — a modal over the map (never on top of «Фильтры» or of each other) */}
        {(panel === 'nums' || panel === 'qc') && (
          <div className="c-modal-bg" onMouseDown={(e) => e.target === e.currentTarget && setPanel(null)} data-testid="modal-bg">
            <div className={`c-modal ${panel === 'qc' ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={panel === 'nums' ? 'Цифры' : 'Проверка качества'} data-testid={panel === 'nums' ? 'headline' : 'qc-panel'}>
              <div className="c-modal-h">
                <span className="c-modal-t">{panel === 'nums' ? 'Цифры: поле, фото, спутник' : 'Проверка качества'}</span>
                <button className="icon-btn" onClick={() => setPanel(null)} aria-label="Закрыть" data-testid={panel === 'nums' ? 'headline-close' : 'qc-close'}>
                  ✕
                </button>
              </div>
              <div className="c-modal-b">
                {panel === 'nums' ? (
                  <Headline
                    meta={meta}
                    photo={photoMeta.data}
                    hasEst={szList.some((f) => !!researchEst(f.properties))}
                    onField={(r) => {
                      setPanel(null);
                      setFilter({ area: r.source, bbox: null, source: r.source, profile: r.profile, layers: { ...q.layers, obs: true } });
                      setObsListOpen(true);
                    }}
                    onZone={(id) => {
                      setPanel(null);
                      openZone(id);
                    }}
                  />
                ) : (
                  <div data-testid="metrics-wrap">
                    {/* §50 P2-9/10 L142: short summary + «Справка / Методика» (examples, metrics) behind «Подробнее» */}
                    <QcPanel
                      m={metrics.data}
                      err={metrics.err}
                      onOpen={(e) => {
                        setPanel(null);
                        if (e.zone_id) openZone(e.zone_id);
                        else openScene(e.scene_key);
                      }}
                    />
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
        <Timeline
          scenes={tlScenes}
          obs={tlObs}
          cur={curScene}
          onPick={(k) => {
            if (k !== curScene) openScene(k);
          }}
          period={[q.from ? Date.parse(q.from) : null, q.to ? Date.parse(q.to) + 86400e3 - 1 : null]}
          onDynamics={dynRegion || curScene ? () => openDynamics({ region: dynRegion, scene: curScene }) : null}
          nasa={{ on: nasaOn, date: nasaDate, latest: nasaInfo?.latest ?? nasaDate }}
          onNasaDate={(d) => {
            setNasaDay(d);
            setNasaOn(true);
          }}
        />
        {drift && (
          <div className="c-drift-wrap" data-testid="drift-wrap">
            <div className="c-drift-tag">
              {/* §48/§51 п.8: текст даёт driftCaption()/DRIFT_CORRIDOR_LABEL (DriftLayer.ts) — L141, одна строка на карте и в панели */}
              {driftCaption(drift.forcing)} Линия — медианная траектория. {DRIFT_CORRIDOR_LABEL} ·{' '}
              <span data-testid="drift-horizon">
                горизонт ≤ {Math.min(72, drift.hours?.length ? drift.hours[drift.hours.length - 1] : 72)} ч, шаг расчёта {(drift.forcing as any)?.output_step_h ?? 1} ч
              </span>
            </div>
            <DriftPlayer drift={drift} onRender={renderDrift} flow={[]} autoplay />
          </div>
        )}
      </main>

      <aside className="right" data-testid="right-panel">
        {sel?.kind === 'zone' && selSz && !studio && (
          <SceneZoneCard
            meta={meta}
            zone={selSz}
            detail={szDetail.data}
            num={selNum}
            onClose={goBack}
            onZone={(id) => openZone(id)}
            onBack={goBack}
            onField={(sid) => {
              setQ((qq) => ({ ...qq, layers: { ...qq.layers, obs: true } }));
              openObs(sid);
            }}
            onDrift={selDriftPath ? toggleDrift : null}
            driftOn={!!drift}
            driftCheck={checkLine(driftSum)}
            driftScenes={nDriftScenes}
            nPairs={(meta as any).summary?.n_confirmed_pairs ?? null}
            tab={cardTab}
            onTab={setCardTab}
            onStudio={() => {
              setStudio(true);
              setQ((x) => ({ ...x, layers: { ...x.layers, scenes: true } }));
              flyToFeat(selSz, 14);
            }}
          />
        )}
        {sel?.kind === 'zone' && selSz && studio && (
          <ZoneStudio
            zone={selSz}
            num={selNum}
            detail={szDetail.data}
            quality={q.layers.quality}
            scenesOn={q.layers.scenes}
            onLayer={(k) => setLayer(k)}
            onCard={() => setStudio(false)}
            onBack={goBack}
          />
        )}
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

// ---------------------------------------------------------------- §34 п.3: snapshots → numbered zones

/** scene record of /api/v3/scene_zones/scenes (form of /scenes + the layer fields) */
export interface SzScene extends Scene {
  scene_key: string;
  scene_kind?: string;
  scene_kind_label?: string;
  region?: string;
  region_name?: string;
  evaluable?: boolean;
  not_evaluated_reason?: string | null;
  wind10m_ms?: number | null;
  sun_zenith_deg?: number | null;
  tile_cloud_pct?: number | null;
  region_short?: string;
  crop_cloud_pct?: number | null;
  by_status?: Record<string, number>;
  n_large?: number | null;
  large_area_km2?: number | null;
}

interface SceneRow {
  s: SzScene;
  zones: Feat<SceneZoneProps>[];
  finds: number;
  b: number;
  group: 'finds' | 'nofinds' | 'noeval';
}
interface SceneRows {
  finds: SceneRow[];
  nofinds: SceneRow[];
  noeval: SceneRow[];
  all: SceneRow[];
  withZones: number;
}

function buildSceneRows(scenes: SzScene[], zones: Feat<SceneZoneProps>[], q: CaseQuery): SceneRows {
  const by = new Map<string, Feat<SceneZoneProps>[]>();
  for (const f of zones) {
    const k = f.properties.scene_key;
    if (!by.has(k)) by.set(k, []);
    by.get(k)!.push(f);
  }
  const inDates = (d: string | null | undefined) => {
    const x = (d ?? '').slice(0, 10);
    return (!q.from || x >= q.from) && (!q.to || x <= q.to);
  };
  const inBox = (b: number[] | null | undefined) => !q.bbox || (!!b && b[0] <= q.bbox[2] && b[2] >= q.bbox[0] && b[1] <= q.bbox[3] && b[3] >= q.bbox[1]);
  const rows: SceneRow[] = [];
  for (const s of scenes) {
    // L146 21:03: dates / frame also on the client — the list follows the filter at once (no stale rows while the
    // filtered zones load); the API filters the zones by the same params
    if (!inDates(s.datetime) || !inBox(s.bounds)) continue;
    const zs = by.get(s.scene_key) ?? [];
    const finds = zs.filter((f) => isFind(f.properties)).length;
    const b = zs.filter((f) => f.properties.verification === 'level_B_cozar').length;
    if (zs.length) rows.push({ s, zones: zs, finds, b, group: finds ? 'finds' : 'nofinds' });
    // a snapshot where the detector is not evaluated (low sun / weak signal) has no zones: shown folded, only without a
    // zone-status filter (it has no status to match)
    else if (s.evaluable === false && !q.det.length && !q.conc.length && inDates(s.datetime) && inBox(s.bounds)) rows.push({ s, zones: [], finds: 0, b: 0, group: 'noeval' });
  }
  const byDate = (a: SceneRow, c: SceneRow) => (c.s.datetime ?? '').localeCompare(a.s.datetime ?? '');
  const finds = rows.filter((r) => r.group === 'finds').sort((a, c) => c.finds - a.finds || c.b - a.b || byDate(a, c));
  const nofinds = rows.filter((r) => r.group === 'nofinds').sort(byDate);
  const noeval = rows.filter((r) => r.group === 'noeval').sort(byDate);
  return { finds, nofinds, noeval, all: [...finds, ...nofinds, ...noeval], withZones: finds.length + nofinds.length };
}

/** §-review 19:1x: area of the zone contour — ≥ 0.1 км² in км², else in м² */
export const areaTxt = (km2: number | null | undefined) =>
  km2 === null || km2 === undefined ? '—' : km2 >= 0.1 ? `${num(km2, 2)} км²` : `${num(km2 * 1e6, 0)} м²`;

/** the word only (the number is printed separately) */
const pluralW = (n: number, one: string, few: string, many: string) => plural(n, one, few, many).replace(/^\S+\s/, '');

/** zones of one snapshot, numbered by the API (zone_id «SZ-<снимок>-NNN» = «зона N» of the title, the CSV and the
 *  documents); the whole-crop zone (-000: «не обнаружено» / wind) gets no number — it is the status of the snapshot */
export const zoneNo = (zoneId: string) => {
  const m = zoneId.match(/-(\d{3})$/);
  return m ? Number(m[1]) : 0;
};
export function numberZones(fs: Feat<SceneZoneProps>[]): { f: Feat<SceneZoneProps>; n: number }[] {
  return fs.map((f) => ({ f, n: zoneNo(f.properties.zone_id) })).sort((a, b) => (a.n || 1e9) - (b.n || 1e9));
}

const sceneName = (s: SzScene) => (s.scene_kind === 'demo' ? 'Альборан · 30SXE' : s.region_short ?? shortName(s.region_name ?? s.region ?? s.scene_key));
/** cloudiness of the snapshot: of the crop (SCL) if the API gives it, else of the tile */
function cloudTxt(s: SzScene): string {
  const c = s.crop_cloud_pct ?? s.cloud_pct;
  if (c !== null && c !== undefined) return `облачность ${num(c, c < 1 ? 1 : 0)} %`;
  if (s.tile_cloud_pct !== null && s.tile_cloud_pct !== undefined) return `облачность тайла ${num(s.tile_cloud_pct, 0)} %`;
  return 'облачность —';
}

function SceneRowBtn({ r, onPick, drift }: { r: SceneRow; onPick: (k: string) => void; drift?: boolean }) {
  const s = r.s;
  const nIns = r.zones.filter((f) => szKey(f.properties) === 'insufficient_data').length;
  return (
    <button
      className={`reg-item c-scene-row ${r.group === 'finds' ? '' : 'bad'}`}
      onClick={() => onPick(s.scene_key)}
      data-testid="scene-item"
      data-scene={s.scene_key}
      title={`${s.region_name ?? ''} · Sentinel-2 ${dateRu(s.datetime)}${s.wind10m_ms !== null && s.wind10m_ms !== undefined ? ` · ветер ${num(s.wind10m_ms, 1)} м/с (ERA5)` : ''}`}
    >
      <span className="ri-name">
        {sceneName(s)}
        {drift && (
          <span className="c-drift-badge" data-testid="scene-drift-badge" title="Для этого снимка есть прогноз дрейфа 24–72 ч (эксперимент)">
            дрейф
          </span>
        )}
      </span>
      <span className={`ri-val ${r.finds ? 'c-ri-finds' : 'faint'}`}>
        {r.group === 'noeval'
          ? 'не оценивается'
          : r.finds
            ? `${plural(r.finds, 'находка', 'находки', 'находок')}${r.b ? ` · ${r.b} Cózar` : ''}`
            : nIns
              ? `находок нет · ${nIns} недост. данных`
              : 'находок нет'}
      </span>
      <span className="ri-sub">
        {dateRu(s.datetime)} · {r.group === 'noeval' ? s.not_evaluated_reason ?? 'низкое солнце / слабый сигнал' : cloudTxt(s)}
      </span>
    </button>
  );
}

function SceneList({
  rows,
  loading,
  err,
  onPick,
  filtered,
  fieldArea,
  drift,
  area,
  onField,
}: {
  rows: SceneRows;
  loading: boolean;
  err: string | null;
  onPick: (k: string) => void;
  filtered: boolean;
  fieldArea?: boolean;
  drift?: (s: SzScene) => boolean;
  /** §47 п.2: the район of step 1 (for the empty-state text) */
  area?: string | null;
  onField?: () => void;
}) {
  const [openNo0, setOpenNo] = useState(false);
  // §47 п.2: snapshots but no finds → the dates are listed open, with what was rejected
  const openNo = openNo0 || (rows.finds.length === 0 && rows.nofinds.length > 0);
  const rejected = useMemo(() => {
    const c = new Map<string, number>();
    let nd = 0;
    for (const r of rows.nofinds)
      for (const f of r.zones) {
        const p: any = f.properties;
        if ((p.status ?? p.detection_status) === 'not_detected') nd++;
        for (const k of p.flags ?? []) c.set(k, (c.get(k) ?? 0) + 1);
      }
    const parts = [...c.entries()].sort((a, b) => b[1] - a[1]).map(([k, n]) => `${SZ_FLAG_RU[k] ?? k} — ${n}`);
    if (nd) parts.push(`«не обнаружено» — ${nd}`);
    return parts.join(', ');
  }, [rows.nofinds]);
  const [openBad, setOpenBad] = useState(false);
  if (err)
    return (
      <div className="c-err c-pad" role="alert" data-testid="sz-error">
        данные спутниковых зон недоступны (ошибка API: {err})
      </div>
    );
  if (loading) return <div className="note c-pad">Загрузка снимков…</div>;
  return (
    <div data-testid="scene-list">
      <div className="c-list-note">
        Снимки Sentinel-2 · район · дата · находки
        <Info label="Снимки">
          Обработанные снимки архива (не съёмка всей Земли сегодня). «Находка» — зона детектора после фильтров судов/кильватера, пены, блика, облаков, берега,
          мелководья и ветра {'>'} 5 м/с; «Cózar» — совпала с нитью каталога Cózar 2024 (разметка людьми). Клик — снимок на карте и его зоны по номерам.
        </Info>
      </div>
      {!rows.all.length && (
        <div className="empty" data-testid="scene-list-empty">
          {fieldArea ? (
            <>
              Это акватория полевых данных — снимков нет, смотрите «Поле».{' '}
              {onField && (
                <button className="link" onClick={onField} data-testid="empty-to-field">
                  Открыть полевые измерения
                </button>
              )}
            </>
          ) : area ? (
            `В районе «${area}» за выбранный период нет снимков Sentinel-2 в нашем наборе`
          ) : filtered ? (
            'Нет снимков с зонами под выбранные фильтры'
          ) : (
            'Слой спутниковых зон не построен'
          )}
        </div>
      )}
      {rows.all.length > 0 && rows.finds.length === 0 && rows.nofinds.length > 0 && (
        <div className="empty c-empty-nf" data-testid="scene-list-nofinds">
          Снимки есть, находок 0{rejected ? ` (что отбраковано: ${rejected})` : ''}.
        </div>
      )}
      {rows.finds.map((r) => (
        <SceneRowBtn key={r.s.scene_key} r={r} onPick={onPick} drift={!!drift?.(r.s)} />
      ))}
      {rows.nofinds.length > 0 && (
        <button className="reg-fold" onClick={() => setOpenNo((v) => !v)} aria-expanded={openNo} data-testid="fold-nofind">
          <span>{openNo ? '▾' : '▸'}</span> Без находок ({rows.nofinds.length})
        </button>
      )}
      {openNo && rows.nofinds.map((r) => <SceneRowBtn key={r.s.scene_key} r={r} onPick={onPick} drift={!!drift?.(r.s)} />)}
      {rows.noeval.length > 0 && (
        <button className="reg-fold" onClick={() => setOpenBad((v) => !v)} aria-expanded={openBad} data-testid="fold-noeval" title="Низкое солнце или слабый сигнал: детектор на таких снимках не оценивается, зон нет">
          <span>{openBad ? '▾' : '▸'}</span> Детектор не оценивается ({rows.noeval.length})
        </button>
      )}
      {openBad && rows.noeval.map((r) => <SceneRowBtn key={r.s.scene_key} r={r} onPick={onPick} drift={!!drift?.(r.s)} />)}
    </div>
  );
}

function SceneZones({
  s,
  sceneKey,
  zones,
  sel,
  onPick,
  onClose,
  layers,
  onLayer,
  drift,
}: {
  /** jury_s44 п.2: the snapshot has a published drift forecast */
  drift?: boolean;
  s: SzScene | null;
  sceneKey: string;
  zones: { f: Feat<SceneZoneProps>; n: number }[];
  sel: string | null;
  onPick: (id: string) => void;
  onClose: () => void;
  layers: CaseQuery['layers'];
  onLayer: (k: keyof CaseQuery['layers']) => void;
}) {
  const numbered = zones.filter((z) => z.n > 0);
  const whole = zones.find((z) => z.n === 0)?.f ?? null;
  const finds = numbered.filter((z) => isFind(z.f.properties)).length;
  const est0 = numbered.map((z) => researchEst(z.f.properties)).find((e) => !!e) ?? null;
  const anyEst = !!est0;
  // INBOX §46 п.1: by default only the finds (status «обнаружено»); the rest behind «показать все N зон»
  const isDet = (p: any) => (p.status ?? p.detection_status) === 'detected';
  const nDet = numbered.filter((z) => isDet(z.f.properties)).length;
  const [showAll, setShowAll] = useState(false);
  useEffect(() => setShowAll(false), [sceneKey]);
  const selHidden = !!sel && numbered.some((z) => z.f.id === sel && !isDet(z.f.properties));
  const all = showAll || nDet === 0 || selHidden;
  const shown0 = all ? numbered : numbered.filter((z) => isDet(z.f.properties));
  // §51 п.4: sorted by area (largest first); the number stays the zone's number on the map / in the CSV
  const areaOf = (z: { f: Feat<SceneZoneProps> }) => z.f.properties.measured?.zone_area_km2 ?? z.f.properties.area_km2 ?? 0;
  const alertF = useAlertFilter(); // §51 п.7 L142
  const orgF = useOrganicFilter(); // §51 п.9 L142
  const shown = byOrganic(byAlert([...shown0], (z) => z.f.properties, alertF), (z) => z.f.properties, orgF).sort((a, b) => areaOf(b) - areaOf(a));
  const nLarge = s?.n_large ?? numbered.filter((z) => (z.f.properties as any).is_large).length;
  const largeKm2 = s?.large_area_km2 ?? null;
  return (
    <div data-testid="scene-zones">
      <div className="c-scene-head">
        <button className="link c-scene-back" onClick={onClose} data-testid="scene-back">
          ← к списку снимков
        </button>
        <div className="c-scene-t" data-testid="scene-title">
          {s ? sceneName(s) : sceneKey}
        </div>
        <div className="c-scene-s">
          Sentinel-2 · {dateRu(s?.datetime)}
          {s ? ` · ${cloudTxt(s)}` : ''}
          {s?.wind10m_ms !== null && s?.wind10m_ms !== undefined ? ` · ветер ${num(s.wind10m_ms, 1)} м/с` : ''}
        </div>
        <div className="c-scene-s">
          {plural(finds, 'находка', 'находки', 'находок')} · {plural(numbered.length, 'зона', 'зоны', 'зон')} детектора
        </div>
        {s && <SceneIntegral s={s as any} />}
        {drift && (
          <div className="c-scene-s" data-testid="scene-drift">
            <span className="c-drift-badge">дрейф</span> прогноз 24–72 ч (эксперимент) — кнопка в карточке зоны
          </div>
        )}
        {whole && (
          <div className="c-scene-s faint" data-testid="scene-whole">
            вся вырезка: {whole.properties.detection_label}
          </div>
        )}
        <div className="c-scene-tg">
          <label>
            <input type="checkbox" checked={layers.scenes} onChange={() => onLayer('scenes')} data-testid="scene-rgb" /> снимок
          </label>
          <QualityToggle scene={s as any} checked={layers.quality} onChange={() => onLayer('quality')} testid="scene-quality" />
        </div>
      </div>
      {!numbered.length && <div className="empty">{whole ? 'Зон детектора на этом снимке нет' : 'Нет зон под выбранные фильтры'}</div>}
      {numbered.length > 0 && nDet < numbered.length && (
        <div className="c-zshow" data-testid="sz-show">
          <span className="faint" data-testid="sz-shown">
            {all
              ? `${pluralW(numbered.length, 'показана', 'показаны', 'показаны')} все ${plural(numbered.length, 'зона', 'зоны', 'зон')}`
              : `${pluralW(nDet, 'показана', 'показаны', 'показано')} ${plural(nDet, 'находка', 'находки', 'находок')} из ${plural(numbered.length, 'зоны', 'зон', 'зон')}`}
          </span>
          {nDet > 0 && !selHidden && (
            <button className="link" onClick={() => setShowAll((v) => !v)} data-testid="sz-show-all" aria-pressed={showAll}>
              {showAll ? `только находки (${nDet})` : `показать все ${plural(numbered.length, 'зону', 'зоны', 'зон')} (в т.ч. недостаточно данных)`}
            </button>
          )}
        </div>
      )}
      <AlertFilter zones={numbered.map((z) => z.f.properties)} />
      {shown.map(({ f, n }) => {
        const p = f.properties;
        const est = researchEst(p);
        return (
          <button
            key={f.id}
            className={`c-zi c-zrow ${sel === f.id ? 'on' : ''}`}
            onClick={() => onPick(f.id)}
            data-testid="sz-item"
            data-zone={f.id}
            title={`${zoneTitle(p)} · ${classShort(p)} · ${statusLabel(p)}${confirmation(p) ? ` · подтверждение: ${confirmation(p)}` : ''} · площадь контура ${areaTxt(p.measured.zone_area_km2)}, пикселей детектора ${num(p.measured.suspicious_area_m2, 0)} м²`}
          >
            <span className={`c-znum-i ${szKey(p)}`} aria-hidden>
              {n}
            </span>
            <span className="c-zi-main">
              <span className="c-zi-t">
                <span title="площадь контура зоны">{areaTxt(p.measured.zone_area_km2)}</span>
                <span className="c-zi-st"> · {statusLabel(p)}</span>
              </span>
              {(p as any).is_large && (
                <span className="c-large c-large-row" data-testid="sz-large" title={`Крупное скопление: ${(p as any).large_reason ?? 'маска ≥ 0,1 км² или длина ≥ 500 м'}`}>
                  крупное скопление
                </span>
              )}
              <span className="c-zi-cls" data-testid="sz-item-class">
                {classShort(p)} <AlertBadge p={p} compact />
              </span>
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** §34 п.3: the «Полевые измерения» layer — its filters (профиль, совокупность), the list and the pair registry */
function FieldPanel({
  meta,
  fc,
  nStrips,
  q,
  profiles,
  onFilter,
  listOpen,
  onList,
  sel,
  onPick,
  drawer,
  onDrawer,
  onClose,
}: {
  meta: Meta;
  fc: FC<ObsProps> | null;
  nStrips: number | null;
  q: CaseQuery;
  profiles: Set<string | null>;
  onFilter: (p: Partial<CaseQuery>) => void;
  listOpen: boolean;
  onList: () => void;
  sel: string | null;
  onPick: (id: string) => void;
  drawer: boolean;
  onDrawer: () => void;
  onClose: () => void;
}) {
  return (
    <div className="c-fieldp" data-testid="field-panel">
      <div className="c-fieldp-h">
        <span className="c-fieldp-t">
          Полевые измерения · {num(fc?.count ?? null, 0)}
          <Info label="Полевые измерения" align="left">
            Измерения организаторов (CSV): шт./км² по трансектам, со своим интервалом. Это другое место и время, чем спутниковые зоны: плотность соседнего
            измерения не переносится на зону. Концентрации разных размерных профилей несравнимы — выберите один профиль. Пунктир — полоса обследования на
            снимке-кандидате вокруг трансекты ({num(nStrips, 0)}).
          </Info>
        </span>
        <button className="icon-btn" onClick={onClose} aria-label="Выключить слой" data-testid="field-close">
          ✕
        </button>
      </div>
      <div className="c-row2">
        <label className="c-f">
          <span>Профиль</span>
          <select value={q.profile ?? ''} onChange={(e) => onFilter({ profile: e.target.value || null })} data-testid="f-profile">
            <option value="">Все профили</option>
            {meta.measurement_profiles
              .filter((p) => profiles.has(p.id))
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
          <select value={q.scope ?? ''} onChange={(e) => onFilter({ scope: e.target.value || null })} data-testid="f-scope">
            <option value="">Любая</option>
            {meta.target_scopes.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="c-fieldp-a">
        <button className={`btn sm ${listOpen ? 'on' : ''}`} onClick={onList} data-testid="tab-obs" aria-expanded={listOpen}>
          Список {listOpen ? '▾' : '▸'}
        </button>
        <button className={`btn sm ghost ${drawer ? 'on' : ''}`} onClick={onDrawer} data-testid="act-pairs">
          Реестр пар снимок ↔ поле
        </button>
      </div>
      {listOpen && (
        <div className="c-fieldp-list">
          <ObsList meta={meta} fc={fc} sel={sel} onPick={onPick} />
        </div>
      )}
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

function autoName(meta: Meta, q: CaseQuery, areaLabel?: string | null): string {
  const parts = [areaLabel ?? (q.source ? sourceShort(meta, q.source) : 'Все акватории')];
  if (q.from || q.to) parts.push(`${q.from ? dateRu(q.from) : '…'}–${q.to ? dateRu(q.to) : '…'}`);
  if (q.profile) parts.push(profileRu(meta, q.profile));
  if (q.det.length) parts.push(q.det.map((d) => SZ_FILTER_RU[d] ?? label(meta.detection_statuses, d)).join(', '));
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
    const lo = (p as any).ci95_lo, hi = (p as any).ci95_hi;
    t = `Измерение · ${v === null ? 'без плотности' : `${num(v)} шт./км²${lo !== null && lo !== undefined && hi !== null && hi !== undefined ? ` [${num(lo)}–${num(hi)}]` : ''}`}`;
    s = `${profileRu(meta, p.measurement_profile)} · ${dateRu(p.date_utc)} · ${scopeRu(meta, p.target_scope)}`;
  } else if (h.id.startsWith('CL-')) {
    const [, , n0, nb0, d0, d1] = h.id.split('-');
    const leaves: string[] = (h as any).leaves ?? [];
    const scenes = new Map<string, string>();
    for (const id of leaves) {
      const f = szones.find((x) => x.id === id);
      if (f) scenes.set(f.properties.scene_key, `${zoneTitle(f.properties).replace(/ · (зона \d+|вся вырезка)$/, '')} · ${dateRu(f.properties.datetime)}`);
    }
    const n = Number(n0), nb = Number(nb0);
    const dd = (v: string) => (v && v !== '0' ? `${v.slice(6, 8)}.${v.slice(4, 6)}.${v.slice(0, 4)}` : '—');
    t = `${plural(n, 'находка', 'находки', 'находок')} детектора рядом${nb ? `, из них ${nb} совпали с Cózar` : ' — независимой разметки нет'}`;
    s = scenes.size
      ? `${scenes.size === 1 ? 'сцена' : `сцен: ${scenes.size}`} — ${[...scenes.values()].slice(0, 3).join('; ')}${scenes.size > 3 ? '…' : ''} · нажмите — ${scenes.size === 1 ? 'к сцене' : 'приблизить'}`
      : `снимки Sentinel-2 ${d0 === d1 ? dd(d0) : `${dd(d0)} – ${dd(d1)}`} · нажмите — приблизить и раскрыть`;
  } else if (h.id.startsWith('SZ-')) {
    const f = szones.find((x) => x.id === h.id);
    if (!f) return null;
    const p = f.properties;
    const est = researchEst(p);
    t = `${classShort(p)} · ${statusLabel(p)}`;
    s = `снимок Sentinel-2 ${dateRu(p.datetime)} · ${zoneTitle(p)} · площадь пикселей ${num(p.measured.suspicious_area_m2, 0)} м²`;
  } else {
    const f = zones?.features.find((x) => x.id === h.id);
    if (!f) return null;
    const p = f.properties;
    t = `Снимок-кандидат · полоса обследования`;
    s = `${p.pair_status === 'accepted' ? 'связь с полем подтверждена' : 'связь с полем не подтверждена'} · ${label(meta.concentration_statuses, p.concentration_status).toLowerCase()}`;
  }
  return (
    <div
      className="tip c-tip-wide"
      style={h.x > ((document.querySelector('[data-testid=main]') as HTMLElement | null)?.clientWidth ?? 1200) - 360 ? { right: ((document.querySelector('[data-testid=main]') as HTMLElement | null)?.clientWidth ?? 1200) - h.x + 14, top: h.y + 14 } : { left: h.x + 14, top: h.y + 14 }}
      data-testid="hover-tip"
    >
      <div className="tt">{t}</div>
      <div className="ts">{s}</div>
    </div>
  );
}

/** §34 п.3: zone status filter (detection_status of /scene_zones: the same for the list, the map and the export) */
const SZ_FILTER = ['detected', 'insufficient_data', 'not_detected'];
const SZ_FILTER_RU: Record<string, string> = {
  detected: 'обнаружено',
  insufficient_data: 'недостаточно данных',
  not_detected: 'не обнаружено',
};
const SZ_STATUS_SHORT: Record<string, string> = {
  detected: 'обнаружено · Cózar (B)',
  unverified: 'обнаружено',
  insufficient_data: 'недостаточно данных',
  not_detected: 'не обнаружено',
};

const SZ_FLAG_RU: Record<string, string> = { foam: 'пена', glint: 'блик', ship: 'судно', seam: 'шов', coast: 'берег', shallow: 'мелководье', cloud: 'облака', wind: 'ветер > 5 м/с' };
const SZ_ORDER = ['detected', 'unverified', 'insufficient_data', 'not_detected'];

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
  const [open, setOpen] = useState(false); // jury 08:51: folded by default at every size
  const qc = meta.quality_classes.filter((c) => c.present !== false && c.id !== 'valid');
  const absent = meta.quality_classes.filter((c) => c.present === false);
  return (
    <div className={`legend c-legend ${open ? '' : 'min'}`} data-testid="legend">
      <div className="c-lg-head">
        <span className="lg-title">Легенда</span>
        <Info label="Как читать" testid="legend-info">
          Цвет зоны = класс, так же в списке, в карточке и в выгрузке (поле class): плавающий материал = класс MARIDA Marine Debris (любой плавающий мусор; пластик не
          подтверждён; состав по снимку не определяется); «Cózar B» — совпадает с разметкой Cózar 2024; «не определено» — сработал признак судна/кильватера, пены,
          блика, облаков, берега, мелководья или ветер {'>'} 5 м/с (правило Cózar 2024); цифра на карте — номер зоны в списке снимка.
          {(meta as any).detector?.version?.sha256_short
            ? ` Модель: ${(meta as any).detector.version.weights} · sha256 ${(meta as any).detector.version.sha256_short} · порог ${num((meta as any).detector.version.threshold, 2)}.`
            : ''}{' '}
          Сплошной символ — полевое измерение. Пунктир — полоса обследования на снимке-кандидате (не контур пятна). Жёлтые контуры — подозрительные пиксели детектора
          выбранной полосы. Цвет точки — концентрация, шт./км²: сравнима только внутри одного размерного профиля.
          {absent.length ? ` ${absent.map((c) => `${c.label}: ${c.note ?? 'в маске не выделяется'}`).join('. ')}.` : ''}
        </Info>
        <button className="icon-btn c-lg-min" onClick={() => setOpen((v) => !v)} aria-label={open ? 'Свернуть легенду' : 'Развернуть легенду'} title={open ? 'Свернуть легенду' : 'Развернуть легенду'} data-testid="legend-toggle">
          {open ? 'свернуть' : 'развернуть'}
        </button>
      </div>
      {!open && (
        <button className="c-lg-compact" onClick={() => setOpen(true)} data-testid="legend-compact" title="Развернуть легенду">
          <span className="c-lg-finds" data-testid="legend-finds">
            <i className="c-sw-find" style={{ background: SZ_COLORS.detected }} /> плавающий материал · Cózar B
            <i className="c-sw-find" style={{ background: SZ_COLORS.unverified }} /> плавающий материал · без разметки
          </span>
          {layers.obs ? (
            <>
              <span className="c-lg-conc-t">Концентрация, шт./км² (полевые измерения)</span>
              <span className="c-ramp c-ramp-mini" aria-hidden>
                {CONC_COLORS.map((c) => (
                  <span key={c} style={{ background: c }} />
                ))}
              </span>
              <span className="c-lg-keys">
                <span className="c-sw-dot" /> измерение
                <span className="c-sw-strip-i" /> полоса
                <span className="c-sw-px" /> пиксели
              </span>
            </>
          ) : null}
        </button>
      )}
      {open && (
        <>
          <div className="c-lg-classes" data-testid="legend-finds">
            <div data-testid="legend-szones">
              <span className="c-lg-cl"><i className="c-sw-find" style={{ background: SZ_COLORS.detected }} /> плавающий материал · Cózar B</span>
              <span className="c-lg-cl"><i className="c-sw-find" style={{ background: SZ_COLORS.unverified }} /> плавающий материал · без разметки</span>
              <span className="c-lg-cl" data-testid="legend-sz-wind"><i className="c-sw-find" style={{ background: SZ_COLORS.insufficient_data }} /> не определено: судно, пена, блик, облака, берег, ветер</span>
              <span className="c-lg-cl"><i className="c-sw-find" style={{ background: SZ_COLORS.not_detected }} /> объектов нет</span>
              <OrganicLegend />
            </div>
          </div>
          {layers.obs && (
            <>
              <div className="lg-row c-lg-conc-t" data-testid="legend-conc-title">
                <span className="c-sw-dot" />
                Концентрация, шт./км² (полевые измерения)
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
              {summaryText(meta, zones) && (
                <div className="c-lg-summary c-lg-second" data-testid="legend-summary">
                  {summaryText(meta, zones)}
                </div>
              )}
              <div className="lg-row">
                <span className="c-sw-ring" />
                измеренный ноль
                <span className="c-sw-dia" />
                без плотности
              </div>
            </>
          )}
          {layers.obs && (
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


/** §31 п.2 а: «Главное» — numbers visible at once (field concentration per profile = measurement; photo counter; satellite) */
function Headline({ meta, photo, hasEst, onField, onZone }: { meta: Meta; photo: any; hasEst: boolean; onField: (r: any) => void; onZone: (id: string) => void }) {
  const h = (meta as any).headline;
  if (!h) return <div className="note">нет данных /meta.headline</div>;
  const tg = photo?.model?.metrics?.test_grouped;
  const sat = h.satellite;
  return (
    <div className="c-head c-head-modal">
      <div className="c-head-t">
        {h.field_label}
        <Info label="Как читать" testid="headline-info">
          Интервалы разного рода (бутстреп по дням рейса, Пуассон — только ошибка счёта, межквартильный размах): строки между собой не сравнивать. S3 и S4 — весь
          мусор, не только пластик; ADIS — все объекты крупнее 10 см. {h.field_note}. Источник: {h.source}.
        </Info>
      </div>
      {[
        ['Пластик', h.field.filter((r: any) => String(r.material).startsWith('пластик'))],
        ['Весь мусор (справочно)', h.field.filter((r: any) => !String(r.material).startsWith('пластик'))],
      ].map(([g, rs]: any) => (
        <div key={g} className="c-head-g" data-testid={`headline-group-${g === 'Пластик' ? 'plastic' : 'all'}`}>
          <div className="c-head-gt">{g}</div>
          {rs.map((r: any) => (
        <button
          key={r.key}
          className={`c-head-r ${r.source ? '' : 'static'}`}
          onClick={() => r.source && onField(r)}
          disabled={!r.source}
          data-testid={`headline-${r.key}`}
          title={`${r.source_label ?? ''} · ${r.stat}, ${r.interval_label}${r.n ? ` · n = ${num(r.n, 0)}` : ''}`}
        >
          <span className="c-head-k">{r.short ?? r.key}</span>
          <span className="c-head-s">{r.stat}</span>
          <span className="c-head-v">
            <b>{num(r.value)}</b>
            <small>
              {' '}
              [{num(r.lo)}–{num(r.hi)}]
            </small>
          </span>
          <span className="c-head-d">
            {r.size_class} · {r.material} · измерение · интервал: {r.interval_label}
            {r.stat === 'медиана' ? ' (нет N — только медиана)' : ''}
          </span>
        </button>
          ))}
        </div>
      ))}
      <PhotoRolesLine />
      {tg?.count_mae !== undefined && (
        <a className="c-head-r c-head-link" href="?mode=photo" data-testid="headline-photo">
          <span className="c-head-d">
            Счётчик предметов по фото: MAE <b>{num(tg.count_mae, 2)}</b> шт./кадр на независимом тесте{tg.count_mae_ci95 ? ` [${num(tg.count_mae_ci95[0], 2)}–${num(tg.count_mae_ci95[1], 2)}]` : ''} · открыть →
          </span>
        </a>
      )}
      {sat && (
        <div className="c-head-r c-head-sat">
          <button className="c-head-link" onClick={() => sat.open_zone_id && onZone(sat.open_zone_id)} data-testid="headline-sat">
            Спутник: {num(sat.n_finds ?? null, 0)} находок из {num(sat.n_zones, 0)} зон ({num(sat.n_level_b, 0)} совпали с Cózar);{' '}
            шт./км² по снимку не определены (исследовательский сценарий по мишеням PLP — свёрнут в карточке зоны)
          </button>
          <Info label="почему →" testid="headline-why" align="left">
            {sat.why}. {(meta as any).quantity_levels?.calibration_pairs?.note ?? ''} {sat.finds_note ? `Находки: ${sat.finds_note}.` : ''}
          </Info>
        </div>
      )}
    </div>
  );
}


// §33 «В студию» — ZoneStudio moved to ./Studio.tsx (§50 P1-4/5/6, L142)
