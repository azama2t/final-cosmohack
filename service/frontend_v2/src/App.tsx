import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Basemap, Bounds, Camera, DetProps, Feature, LayerKey, Layers, Manifest, Projection, Region, SceneRef, Zone } from './types';
import {
  bestRegion,
  getImage,
  summaryDate,
  loadDetections,
  loadDrift,
  loadH3,
  loadManifest,
  loadTimeseries,
  loadZones,
  modelPath,
  rankRegions,
  scopeCheckSummary,
} from './lib/data';
import { useAsync } from './lib/hooks';
import { makeScale, registerUnknownDates } from './lib/style';
import { DEFAULT_LAYERS, readUrl, writeUrl } from './lib/url';
import { loadPrefs, PERSIST_LAYERS, savePrefs } from './lib/prefs';
import MapView from './map/MapView';
import { anim, ctl, fitOverview, flyToArea, flyToBounds, getCamera, waitIdle } from './map/controller';
import { featureBBox } from './map/layers';
import { planRoute } from './lib/route';
import { centroid, loadGeo, prepareDrift, type CheckOverlay, type HoverInfo } from './map/layers';
import { FlowLayer, parseFlow, type FlowField } from './map/flow';
import { apiGet, apiPaths, type ReviewItem } from './lib/api';
import { onlyConfirmed as filterConfirmed, hasConfirm, nConfirmed } from './lib/confirm';
import { splitArtifacts } from './lib/artifacts';
import { loadFeed, type FeedEvent } from './lib/feed';
import LeftColumn from './components/LeftColumn';
import Toolbar from './components/Toolbar';
import RightPanel, { type RightMode } from './components/RightPanel';
import Legend from './components/Legend';
import Tooltip from './components/Tooltip';
import DriftPlayer from './components/DriftPlayer';
import { runTour, type TourApi } from './tour';
import { lazyRetry } from './lib/reload';

const CompareView = lazy(lazyRetry(() => import('./components/CompareView')));
const ReviewView = lazy(lazyRetry(() => import('./components/ReviewView')));

export interface CheckPair {
  region: string;
  date1: string;
  date2: string;
  url: string;
  [k: string]: any;
}

/** What the right panel shows for a selected region (large action buttons over the map). */
export type RegionView = 'findings' | 'zones' | 'history' | 'drift';
const VIEWS: RegionView[] = ['findings', 'zones', 'history', 'drift'];

export interface IncidentLite {
  id: string;
  status: string;
  status_ru?: string;
  priority?: number | null;
  updated?: string;
  label?: string | null;
  verdict?: string | null;
}

/** spot centred in the map column; visible width = max(1,5 km, 3 × spot extent) */
function flyToFeature(f: Feature<DetProps>, duration = 1600) {
  const [lon, lat] = centroid(f);
  const b = featureBBox(f);
  const mLon = 111320 * Math.cos((lat * Math.PI) / 180);
  const ext = Math.max((b[2] - b[0]) * mLon, (b[3] - b[1]) * 111320);
  flyToArea(lon, lat, Math.min(4000, Math.max(1500, ext * 3)), duration);
}

/** the user's model if the date has it, else the first model of the date (the preference itself is kept) */
function pickModelFrom(want: string, models: string[]): string {
  return models.includes(want) ? want : models[0];
}

export default function App() {
  const url = useMemo(readUrl, []);
  // the user's own choices (localStorage); the URL of a shared link wins over them
  const prefs = useMemo(loadPrefs, []);
  const wantModel = useRef<string>(url.model ?? prefs.model ?? 'mdd');
  const [manifest, setManifest] = useState<Manifest | null | undefined>(undefined);
  const [paths, setPaths] = useState<Set<string>>(new Set());
  const [regionId, setRegionId] = useState<string | null>(null);
  const [date, setDate] = useState<string | null>(null);
  const [model, setModel] = useState<string>(wantModel.current);
  // heavy layers (drift, particles, H3 3D) are never restored: only by a button
  const [layers, setLayers] = useState<Layers>(() => ({
    ...DEFAULT_LAYERS,
    ...(prefs.layers ?? {}),
    ...(url.layers ?? {}),
    drift: false,
    currents: false,
    wind: false,
    h3_3d: false,
  }));
  // default: Esri satellite on the globe; changed only by the user (menu) — never by the app
  const [basemap, setBasemap] = useState<Basemap>(url.basemap ?? prefs.basemap ?? 'satellite');
  const [projection, setProjection] = useState<Projection>(url.projection ?? prefs.projection ?? 'globe');
  /** temporary offline fallback (online tiles do not load); does not touch the chosen basemap */
  const [offline, setOffline] = useState(false);
  const layersRef = useRef(layers);
  layersRef.current = layers;
  const [view, setView] = useState<RegionView>(VIEWS.includes(url.view as RegionView) ? (url.view as RegionView) : 'findings');
  const [routeOn, setRouteOn] = useState<boolean>(!!url.route);
  const [selected, setSelected] = useState<Feature<DetProps> | null>(null);
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const [compare, setCompare] = useState<{ a: SceneRef; b: SceneRef } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [tourCaption, setTourCaption] = useState<{ step: number; total: number; text: string } | null>(null);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [leftTab, setLeftTab] = useState<'regions' | 'feed'>('regions');
  const [geoReady, setGeoReady] = useState(false);
  const [zone, setZone] = useState<Zone | null>(null);
  const [onlyConf, setOnlyConf] = useState<boolean>(!!url.confirmed);
  const [place, setPlace] = useState<{ h3: string; fromZone: boolean } | null>(url.place ? { h3: url.place, fromZone: false } : null);
  const [tab, setTab] = useState<'map' | 'review'>(url.tab ?? 'map');
  const [feed, setFeed] = useState<{ items: FeedEvent[]; source: 'api' | 'client' } | null>(null);
  const [feedSel, setFeedSel] = useState<string | null>(null);
  const [checkMode, setCheckMode] = useState<boolean>(!!url.check);
  const [checkSummary, setCheckSummary] = useState<any | null | undefined>(undefined);
  const [checkPair, setCheckPair] = useState<CheckPair | null>(null);
  const [checkData, setCheckData] = useState<any | null>(null);
  const [flowList, setFlowList] = useState<{ region: string; date: string; kinds: string[] }[]>([]);
  const [flowInfo, setFlowInfo] = useState<FlowField[]>([]);
  const [incidents, setIncidents] = useState<Map<string, IncidentLite> | null>(null);
  const [incTick, setIncTick] = useState(0);
  const pickModel = (models: string[]) => pickModelFrom(wantModel.current, models);
  const pendingZone = useRef<number | undefined>(url.zone);
  const pendingDet = useRef<{ id?: string; lon?: number; lat?: number } | null>(url.det ? { id: url.det } : null);
  const camera = useRef<Camera | undefined>(url.camera);
  const tourAbort = useRef<AbortController | null>(null);
  const flowLayer = useRef<FlowLayer | null>(null);

  // ---- manifest, API paths; the first screen opens on the demo region (a real, checked finding) ----
  useEffect(() => {
    loadManifest().then((m) => {
      if (!m || !m.regions.length) return setManifest(null);
      registerUnknownDates(m);
      const br = bestRegion(m);
      if (br) ctl.overviewFocus = br.center as [number, number];
      setManifest(m);
      const demo = m.demo?.region ? m.regions.find((x) => x.id === m.demo!.region) : null;
      const r = url.region ? m.regions.find((x) => x.id === url.region) : url.world || url.tour ? undefined : demo ?? br ?? undefined;
      if (r) {
        setRegionId(r.id);
        const want = url.date ?? (r.id === m.demo?.region ? m.demo?.date : undefined);
        const d = want && r.dates.some((x) => x.date === want) ? want : summaryDate(r)?.date;
        setDate(d ?? null);
        const de = r.dates.find((x) => x.date === d);
        if (de) setModel(pickModel(de.models));
        // globe → region: one calm fly-in (the globe is the first frame, the region is where the work is)
        if (!url.camera) setTimeout(() => flyToBounds(de?.bounds ?? r.bounds, { duration: url.region ? 0 : 2800, rightPanel: true }), url.region ? 60 : 600);
      }
      if (url.compare) {
        const [a, b] = url.compare.split(',').map((s) => {
          const [region, date] = s.split(':');
          return { region, date };
        });
        if (a?.region && b?.region) setCompare({ a, b });
      }
    });
    apiPaths().then((p) => {
      setPaths(p);
      if (p.has('/api/flow/list')) apiGet<any[]>('/api/flow/list').then((l) => setFlowList(Array.isArray(l) ? l : []));
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const region: Region | null = useMemo(() => manifest?.regions.find((r) => r.id === regionId) ?? null, [manifest, regionId]);
  const dateEntry = useMemo(() => region?.dates.find((d) => d.date === date) ?? null, [region, date]);
  const modelOk = !!dateEntry && dateEntry.models.includes(model);
  const scene = region && dateEntry && modelOk ? { r: region.id, d: dateEntry.date, m: model } : null;
  const sk = scene ? `${scene.r}/${scene.d}/${scene.m}` : '';
  const boundsRef = useRef<Bounds | null>(null);
  boundsRef.current = dateEntry?.bounds ?? region?.bounds ?? null;

  // ---- scene data ----
  const rgbImg = useAsync(dateEntry && layers.rgb ? () => getImage(dateEntry.rgb) : null, [dateEntry?.rgb, layers.rgb]);
  const probImg = useAsync(scene && layers.prob ? () => getImage(modelPath(scene.r, scene.d, scene.m, 'prob.png')) : null, [sk, layers.prob]);
  const detectionsAll = useAsync(scene ? () => loadDetections(scene.r, scene.d, scene.m) : null, [sk]);
  // the other model's detections (evidence card: «обе модели и их согласие»)
  const otherModel = dateEntry?.models.find((m) => m !== model) ?? null;
  const otherDet = useAsync(scene && otherModel && selected ? () => loadDetections(scene.r, scene.d, otherModel) : null, [sk, otherModel, !!selected]);
  const artSplit = useMemo(() => splitArtifacts(detectionsAll), [detectionsAll]);
  const detections = artSplit.real;
  const nArts = artSplit.n;
  const zones = useAsync(scene ? () => loadZones(scene.r, scene.d, scene.m) : null, [sk]);
  const needH3 = layers.h3 || !!selected || !!zone;
  const h3 = useAsync(scene && needH3 ? () => loadH3(scene.r, scene.d, scene.m) : null, [sk, needH3]);
  const driftRaw = useAsync(dateEntry?.drift && layers.drift ? () => loadDrift(dateEntry.drift!) : null, [dateEntry?.drift, layers.drift]);
  const h3Scale = useMemo(() => makeScale(h3?.features.map((f) => f.properties.share_permille) ?? []), [h3]);
  const h3Max = useMemo(() => (h3?.features ?? []).reduce((a, f) => Math.max(a, f.properties.share_permille ?? 0), 0), [h3]);
  const drift = useMemo(() => (driftRaw ? prepareDrift(driftRaw) : null), [driftRaw]);
  const route = useMemo(
    () => (routeOn && region && zones?.zones.length ? planRoute(region.id, zones.zones.slice(0, 10)) : null),
    [routeOn, region, zones],
  );
  const confAvail = hasConfirm(dateEntry, detections);
  const nConf = scene ? nConfirmed(dateEntry, model, detections) : null;
  const confOn = onlyConf && confAvail;
  const shownDet = useMemo(() => (confOn ? filterConfirmed(detections) : detections), [confOn, detections]);
  const timeseries = useAsync(region ? () => loadTimeseries(region.id) : null, [region?.id]);
  // OSM context exists for our 18 regions (service/context/*.geojson); organiser data: only if the manifest says so
  // (a request for a missing file would answer 404 → a console error)
  const contextOk = !!region && paths.has('/api/context') && (manifest?.kind === 'real' || !!(region as any).context);
  const osm = useAsync<any>(
    region && layers.osm && contextOk ? () => apiGet<any>('/api/context', { region: region.id }) : null,
    [region?.id, layers.osm, paths],
  );
  const flowAvail = useMemo(
    () => (region && date ? flowList.find((f) => f.region === region.id && f.date === date)?.kinds ?? [] : []),
    [flowList, region, date],
  );

  // incident statuses of the scene (status of each finding: не проверено / на проверке / подтверждено …)
  useEffect(() => {
    if (!scene || !paths.has('/api/incidents')) return setIncidents(null);
    let alive = true;
    apiGet<any>('/api/incidents', { region: scene.r, date: scene.d, model: scene.m, kind: 'detection', limit: 2000 }).then((r) => {
      if (!alive) return;
      const m = new Map<string, IncidentLite>();
      for (const it of r?.items ?? []) m.set(it.id, it);
      setIncidents(m);
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sk, paths, incTick]);

  // ---- feed (findings with the image date; operator decisions) ----
  useEffect(() => {
    if (!manifest || !paths.size) return;
    let alive = true;
    // MDD when the data has it (live scenes), else the first model of the manifest (organiser data: lgbm only)
    const feedModel = manifest.models?.mdd ? 'mdd' : Object.keys(manifest.models ?? {})[0] ?? 'mdd';
    loadFeed(manifest, feedModel).then((f) => alive && setFeed(f));
    return () => {
      alive = false;
    };
  }, [manifest, paths, incTick]);

  // ---- region view → map layers (the large actions drive what the map shows) ----
  useEffect(() => {
    if (!region || checkMode) return;
    setLayers((l) => ({
      ...l,
      detections: true,
      zones: view === 'zones',
      drift: view === 'drift' && !!dateEntry?.drift,
      // particles are a separate, on-demand layer (checkbox in the drift panel), never switched on by the view
      currents: view === 'drift' ? l.currents && flowAvail.includes('currents') : false,
      wind: view === 'drift' ? l.wind && flowAvail.includes('wind') : false,
    }));
    if (view !== 'drift') {
      anim.playing = false;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, region?.id, checkMode, dateEntry?.drift, flowAvail.join(',')]);

  // frame the whole drift forecast when it is shown
  useEffect(() => {
    if (!layers.drift || !drift || !region || checkMode) return;
    let b = region.bounds;
    for (const t of [...drift.trips, ...drift.ensemble.flatMap((m) => m.trips)])
      for (const [x, y] of t.path) b = [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)];
    flyToBounds(b, { duration: 1600 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drift, layers.drift]);

  useEffect(() => {
    if ((layers.h3 || layers.drift) && !geoReady) loadGeo().then(() => setGeoReady(true));
  }, [layers.h3, layers.drift, geoReady]);

  // ---- flow particles (currents / wind) ----
  const [flowTick, setFlowTick] = useState(0);
  useEffect(() => {
    if (!layers.drift) return;
    const l = (h: number) => {
      const b = Math.max(0, Math.floor(h / 6) * 6); // the drift clock may start below 0; /api/flow wants t ≥ 0 (422 otherwise)
      setFlowTick((x) => (x === b ? x : b));
    };
    anim.listeners.add(l);
    return () => void anim.listeners.delete(l);
  }, [layers.drift]);
  useEffect(() => {
    const kinds = (['currents', 'wind'] as const).filter((k) => layers[k] && flowAvail.includes(k));
    if (!kinds.length || !region || !date || tab !== 'map') {
      flowLayer.current?.stop();
      setFlowInfo([]);
      return;
    }
    let alive = true;
    Promise.all(kinds.map((k) => apiGet<any>('/api/flow', { region: region.id, date, kind: k, t: layers.drift ? flowTick : 0 }).then((j) => parseFlow(j, k)))).then(
      (fs) => {
        if (!alive || !ctl.map) return;
        const ok = fs.filter((f): f is FlowField => !!f);
        if (!flowLayer.current) flowLayer.current = new FlowLayer(ctl.map.getContainer());
        flowLayer.current.setFields(ok);
        setFlowInfo(ok);
      },
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layers.currents, layers.wind, flowAvail, region?.id, date, flowTick, layers.drift, tab]);

  // ---- drift forecast check (experiment) ----
  useEffect(() => {
    if (!checkMode || checkSummary !== undefined) return;
    apiGet<any>('/api/drift_check').then((s) => setCheckSummary(scopeCheckSummary(s, manifest)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checkMode, checkSummary]);
  useEffect(() => {
    if (!checkPair) return setCheckData(null);
    let alive = true;
    fetch(checkPair.url)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => alive && setCheckData(d))
      .catch(() => alive && setCheckData(null));
    return () => {
      alive = false;
    };
  }, [checkPair]);
  const checkOverlay: CheckOverlay | null = useMemo(() => {
    if (!checkMode || !checkData) return null;
    return {
      fan: checkData.particles ?? [],
      contour: checkData.contour ?? null,
      targets: (checkData.detections?.features ?? []).map((f: any) => ({ lon: f.geometry.coordinates[0], lat: f.geometry.coordinates[1], hit: !!f.properties?.hit })),
    };
  }, [checkMode, checkData]);
  useEffect(() => {
    if (!checkData) return;
    const pts: number[][] = [...(checkData.particles ?? []), ...(checkData.detections?.features ?? []).map((f: any) => f.geometry.coordinates)];
    if (!pts.length) return;
    const xs = pts.map((p) => p[0]).sort((a, b) => a - b),
      ys = pts.map((p) => p[1]).sort((a, b) => a - b);
    const q = (a: number[], k: number) => a[Math.max(0, Math.min(a.length - 1, Math.round(k * (a.length - 1))))];
    flyToBounds([q(xs, 0.02), q(ys, 0.02), q(xs, 0.98), q(ys, 0.98)], { duration: 1600, extra: 24 });
  }, [checkData]);

  // ---- pending selections (URL / feed / review) once data are loaded ----
  useEffect(() => {
    if (!zones || pendingZone.current === undefined) return;
    const z = zones.zones.find((x) => x.rank === pendingZone.current);
    pendingZone.current = undefined;
    if (z) setZone(z);
  }, [zones]);
  useEffect(() => {
    const pd = pendingDet.current;
    if (!pd || !detectionsAll) return;
    pendingDet.current = null;
    const all = detectionsAll.features;
    let f = pd.id ? all.find((x) => x.properties.id === pd.id) : undefined;
    if (!f && pd.lon !== undefined && pd.lat !== undefined && all.length) {
      const d2 = (x: Feature<DetProps>) => {
        const [a, b] = centroid(x);
        return (a - pd.lon!) ** 2 + (b - pd.lat!) ** 2;
      };
      f = [...all].sort((a, b) => d2(a) - d2(b))[0];
    }
    if (f) {
      if (f.properties.artifact && !layers.artifacts) setLayers((l) => ({ ...l, artifacts: true }));
      setSelected(f);
      // ONE flight, after the evidence panel has widened the layout
      setTimeout(() => flyToFeature(f!, 2200), 80);
    } else if (pd.lon !== undefined && pd.lat !== undefined) setTimeout(() => flyToArea(pd.lon!, pd.lat!, 2000, 2200), 80);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detectionsAll]);

  // ---- URL ----
  const syncUrl = useCallback(() => {
    writeUrl({
      region: regionId ?? undefined,
      date: regionId ? date ?? undefined : undefined,
      model,
      layers,
      basemap: basemap !== 'satellite' ? basemap : undefined,
      projection: projection !== 'globe' ? projection : undefined,
      route: regionId && routeOn ? true : undefined,
      camera: camera.current,
      compare: compare ? `${compare.a.region}:${compare.a.date},${compare.b.region}:${compare.b.date}` : undefined,
      confirmed: onlyConf || undefined,
      tab: tab === 'review' ? 'review' : undefined,
      zone: regionId && zone ? zone.rank : undefined,
      place: regionId && place ? place.h3 : undefined,
      det: regionId && selected ? selected.properties.id : undefined,
      check: checkMode || undefined,
      view: regionId && view !== 'findings' ? view : undefined,
      world: !regionId || undefined,
    });
  }, [regionId, date, model, layers, basemap, projection, routeOn, compare, onlyConf, tab, zone, place, selected, checkMode, view]);
  useEffect(syncUrl, [syncUrl]);

  // ---- actions ----
  const showToast = useCallback((t: string) => {
    setToast(t);
    setTimeout(() => setToast((x) => (x === t ? null : x)), 2800);
  }, []);

  const clearSelection = () => {
    setSelected(null);
    setHover(null);
    setZone(null);
    setPlace(null);
  };

  const selectRegion = useCallback(
    (id: string | null, opts: { date?: string; model?: string; fly?: boolean; view?: RegionView } = {}) => {
      clearSelection();
      setRouteOn(false);
      if (!manifest) return;
      if (!id) {
        setRegionId(null);
        setCheckMode(false);
        setCheckPair(null);
        setLayers((l) => ({ ...l, drift: false, currents: false, wind: false, zones: false, h3: false, h3_3d: false }));
        ctl.map?.easeTo({ pitch: 0, bearing: 0, duration: 600 });
        fitOverview(manifest.regions.map((r) => r.bounds), 2400);
        return;
      }
      const r = manifest.regions.find((x) => x.id === id);
      if (!r) return;
      setRegionId(r.id);
      setView(opts.view ?? 'findings');
      // heavy layers are switched off when the region changes (they are on-demand only)
      setLayers((l) => ({ ...l, drift: false, currents: false, wind: false, h3_3d: false }));
      anim.playing = false;
      const de = (opts.date && r.dates.find((d) => d.date === opts.date)) || summaryDate(r);
      setDate(de?.date ?? null);
      if (de) setModel(opts.model && de.models.includes(opts.model) ? opts.model : pickModel(de.models));
      if (opts.fly !== false) flyToBounds(de?.bounds ?? r.bounds, { pitch: 0, bearing: 0, rightPanel: true, duration: 2600 });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [manifest],
  );

  const toggleLayer = useCallback((k: LayerKey, on?: boolean) => {
    setLayers((l) => {
      const v = on ?? !l[k];
      const n = { ...l, [k]: v };
      if (k === 'h3_3d') {
        if (v) n.h3 = true;
        if (v && boundsRef.current && (ctl.map?.getZoom() ?? 0) > 12.3) flyToBounds(boundsRef.current, { pitch: 52, bearing: -18, duration: 1800 });
        else ctl.map?.easeTo({ pitch: v ? 52 : 0, bearing: v ? -18 : 0, duration: 1200 });
      }
      if (k === 'h3' && !v && l.h3_3d) {
        n.h3_3d = false;
        ctl.map?.easeTo({ pitch: 0, bearing: 0, duration: 900 });
      }
      if (k === 'drift') {
        anim.playing = false;
        anim.hour = 0;
      }
      return n;
    });
  }, []);

  /** a layer switched by the user: light layers are remembered */
  const userToggleLayer = useCallback(
    (k: LayerKey, on?: boolean) => {
      const v = on ?? !layersRef.current[k];
      toggleLayer(k, v);
      if (PERSIST_LAYERS.includes(k)) savePrefs({ layers: { [k]: v } });
    },
    [toggleLayer],
  );

  const changeProjection = useCallback(
    (pr: Projection) => {
      setProjection(pr);
      ctl.projection = pr;
      setTimeout(() => {
        if (manifest && !regionId) fitOverview(manifest.regions.map((r) => r.bounds), 1400);
      }, 60);
    },
    [manifest, regionId],
  );

  const selectDate = useCallback(
    (d: string) => {
      setSelected(null);
      setZone(null);
      setDate(d);
      const de = region?.dates.find((x) => x.date === d);
      if (de) setModel(pickModel(de.models));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [region],
  );

  const openDetection = useCallback((f: Feature<DetProps>, fly = false) => {
    setSelected(f);
    setHover(null);
    setZone(null);
    setPlace(null);
    // after the panel has widened (the map column shrinks): centre the spot with ~1–2 km of context
    if (fly) setTimeout(() => flyToFeature(f, 1600), 240);
  }, []);

  const openZone = useCallback((z: Zone, fly = true) => {
    setSelected(null);
    setHover(null);
    setPlace(null);
    setZone(z);
    setView('zones');
    if (fly) setTimeout(() => flyToArea(z.lon, z.lat, 2600, 1800), 60);
  }, []);

  const openPlace = useCallback((h3id: string, fromZone = false) => {
    setSelected(null);
    setHover(null);
    setPlace({ h3: h3id, fromZone });
  }, []);

  const changeView = useCallback(
    (v: RegionView) => {
      setCheckMode(false);
      setCheckPair(null);
      clearSelection();
      setView(v);
      if (v !== 'drift' && boundsRef.current && (ctl.map?.getZoom() ?? 0) > 13.2) flyToBounds(boundsRef.current, { duration: 1400 });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const openFeed = useCallback(
    (e: FeedEvent) => {
      if (!manifest) return;
      setFeedSel(e.key);
      setTab('map');
      setCompare(null);
      setCheckMode(false);
      const r = manifest.regions.find((x) => x.id === e.region);
      if (!r) return;
      const sameScene = regionId === r.id && date === e.date && (!e.model || e.model === model);
      pendingDet.current = { id: e.incidentKind === 'detection' ? e.incidentId : undefined, lon: e.lon, lat: e.lat };
      if (sameScene && detectionsAll) {
        const pd = pendingDet.current;
        pendingDet.current = null;
        const f =
          (pd.id && detectionsAll.features.find((x) => x.properties.id === pd.id)) ||
          [...detectionsAll.features].sort((a, b) => {
            const [ax, ay] = centroid(a),
              [bx, by] = centroid(b);
            return (ax - (pd.lon ?? 0)) ** 2 + (ay - (pd.lat ?? 0)) ** 2 - ((bx - (pd.lon ?? 0)) ** 2 + (by - (pd.lat ?? 0)) ** 2);
          })[0];
        if (f) {
          if (f.properties.artifact) setLayers((l) => ({ ...l, artifacts: true }));
          openDetection(f, true);
        }
        return;
      }
      // one flight: the pending selection flies to the finding once its scene is loaded
      selectRegion(r.id, { date: e.date, model: e.model, fly: false });
    },
    [manifest, regionId, date, model, detectionsAll, selectRegion, openDetection],
  );

  const showReviewItem = useCallback(
    (it: ReviewItem) => {
      if (!manifest) return;
      setTab('map');
      pendingDet.current = { id: it.id, lon: it.lon, lat: it.lat };
      selectRegion(it.region, { date: it.date, model: it.model, fly: false });
    },
    [manifest, selectRegion],
  );

  const defaultCompare = useCallback((): { a: SceneRef; b: SceneRef } | null => {
    if (!manifest) return null;
    const r = region ?? bestRegion(manifest);
    if (!r) return null;
    const a = { region: r.id, date: date && region ? date : summaryDate(r)!.date };
    const reliable = rankRegions(manifest.regions).ok.filter((x) => x.id !== r.id && (x.summary?.n_detections ?? 0) > 0);
    const other = reliable[0] ?? manifest.regions.filter((x) => x.id !== r.id).sort((x, y) => (y.summary?.n_detections ?? 0) - (x.summary?.n_detections ?? 0))[0];
    if (other) return { a, b: { region: other.id, date: summaryDate(other)!.date } };
    return null;
  }, [manifest, region, date]);

  const openCompare = useCallback(() => {
    const c = defaultCompare();
    if (c) {
      setTab('map');
      setCompare(c);
    }
  }, [defaultCompare]);

  const copyLink = useCallback(async () => {
    camera.current = getCamera();
    syncUrl();
    try {
      await navigator.clipboard.writeText(location.href);
      showToast('Ссылка скопирована — откроет тот же вид карты');
    } catch {
      showToast('Ссылка в адресной строке — скопируйте её вручную');
    }
  }, [syncUrl, showToast]);

  const openCheck = useCallback((on: boolean) => {
    setCheckMode(on);
    clearSelection();
    if (!on) {
      setCheckPair(null);
      return;
    }
    setTab('map');
    setCompare(null);
    setLayers((l) => ({ ...l, drift: false, currents: false, wind: false, h3: false, h3_3d: false, zones: false }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectCheckPair = useCallback(
    (p: CheckPair) => {
      setCheckPair(p);
      if (!manifest) return;
      const r = manifest.regions.find((x) => x.id === p.region);
      if (!r) return;
      clearSelection();
      setRegionId(r.id);
      const d2 = r.dates.find((d) => d.date === p.date2);
      setDate((d2 ?? r.dates.find((d) => d.date === p.date1) ?? summaryDate(r))!.date);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [manifest],
  );

  // ---- tour ----
  const stateRef = useRef({ detections, zones, region, dateEntry, manifest, feed, checkSummary, paths });
  stateRef.current = { detections, zones, region, dateEntry, manifest, feed, checkSummary, paths };
  const tourApi: TourApi = {
    get: () => stateRef.current,
    selectRegion: (id, o) => selectRegion(id, o),
    setLayers: (l) => setLayers((x) => ({ ...x, ...l })),
    toggleLayer,
    setView: changeView,
    openDetection: (f) => openDetection(f, true),
    closeDetection: () => setSelected(null),
    openCompare,
    closeCompare: () => setCompare(null),
    caption: (step, total, text) => setTourCaption(text ? { step, total, text } : null),
    openZone: (z) => openZone(z, true),
    closeZone: () => setZone(null),
    openPlace: (h) => openPlace(h, true),
    closePlace: () => setPlace(null),
    waitIdle,
    ensureGlobe: () => {},
    setLeftTab,
    highlightFeed: (key) => setFeedSel(key),
    openCheck,
    selectCheckPair,
    setTab,
    showToast,
  };
  const tourApiRef = useRef(tourApi);
  tourApiRef.current = tourApi;

  const startTour = useCallback(() => {
    tourAbort.current?.abort();
    const ac = new AbortController();
    tourAbort.current = ac;
    (window as any).__tourRunning = true;
    runTour(tourApiRef, ac.signal).finally(() => {
      (window as any).__tourRunning = false;
      setTourCaption(null);
    });
  }, []);
  const stopTour = useCallback(() => {
    tourAbort.current?.abort();
    setTourCaption(null);
  }, []);

  useEffect(() => {
    if (!manifest || !url.tour) return;
    const t = setInterval(() => {
      if (window.__mapReady && paths.size) {
        clearInterval(t);
        startTour();
      }
    }, 200);
    return () => clearInterval(t);
  }, [manifest, url.tour, startTour, paths]);

  // Esc closes the innermost thing
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || tab === 'review') return;
      const tg = e.target as HTMLElement;
      if (tg && /INPUT|SELECT|TEXTAREA/.test(tg.tagName)) return;
      if (compare) setCompare(null);
      else if (selected) setSelected(null);
      else if (place) setPlace(null);
      else if (zone) setZone(null);
      else if (checkMode) openCheck(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [compare, selected, place, zone, checkMode, tab, openCheck]);

  const rightMode: RightMode | null = checkMode ? 'check' : !region ? null : selected ? 'det' : place ? 'place' : zone ? 'zone' : view;

  // ---- test / screenshot hooks ----
  useEffect(() => {
    window.__app = {
      version: 'v2',
      region: regionId,
      date,
      model,
      view,
      layers,
      basemap,
      offline,
      prefs: () => loadPrefs(),
      sceneReady: !!scene && (!layers.rgb || !!rgbImg) && !!detections,
      h3Ready: !!h3 && geoReady,
      driftReady: !!drift && geoReady,
      nDetections: detections?.features.length ?? 0,
      nArtifacts: nArts,
      nFeed: feed?.items.length ?? 0,
      feedSource: feed?.source ?? null,
      flowFields: flowInfo.length,
      flowFps: flowLayer.current?.fps ?? null,
      check: checkMode ? { pair: checkPair ? `${checkPair.region}:${checkPair.date1}` : null, loaded: !!checkData } : null,
      bestRegion: manifest ? (manifest.demo?.region ?? bestRegion(manifest)?.id ?? null) : null,
      hasEnsemble: !!driftRaw?.ensemble?.length,
      rightOpen: !!rightMode,
      rightMode,
      largestDetectionScreen: () => {
        if (!shownDet?.features.length || !ctl.map) return null;
        const f = [...shownDet.features].sort((a, b) => b.properties.area_m2 - a.properties.area_m2)[0];
        const p = ctl.map.project(centroid(f) as any);
        const r = ctl.map.getContainer().getBoundingClientRect();
        return { x: p.x + r.left, y: p.y + r.top, id: f.properties.id };
      },
      isMoving: () => !!ctl.map?.isMoving(),
      tiles: () => ({ loaded: !!ctl.map?.areTilesLoaded(), zoom: ctl.map?.getZoom(), style: (ctl.map?.getStyle() as any)?.name ?? null }),
      zoneScreen: (rank: number) => {
        const z = zones?.zones.find((x) => x.rank === rank);
        if (!z || !ctl.map) return null;
        const p = ctl.map.project([z.lon, z.lat] as any);
        const r = ctl.map.getContainer().getBoundingClientRect();
        return { x: p.x + r.left, y: p.y + r.top, h3: z.h3 };
      },
      zoneCard: zone?.rank ?? null,
      placeCard: place?.h3 ?? null,
      detCard: selected?.properties.id ?? null,
      tab,
      projection,
      setDriftHour: (h: number) => {
        anim.hour = h;
        anim.listeners.forEach((l) => l(h));
        ctl.render();
      },
      startTour,
    };
  });

  // ---- fps meter ----
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

  if (manifest === undefined) return <div className="boot">Загрузка данных…</div>;
  if (manifest === null)
    return (
      <div className="boot">
        <div style={{ textAlign: 'center', maxWidth: 420 }}>
          <div style={{ color: 'var(--text)', fontWeight: 500 }}>Нет данных</div>
          <div className="note" style={{ marginTop: 8 }}>
            Сервис не нашёл manifest.json в корне данных. См. /health.
          </div>
        </div>
      </div>
    );

  const threshold = manifest.models?.[model]?.threshold ?? zones?.threshold ?? null;
  const hasAnyDrift = !!region?.dates.some((d) => d.drift);
  const imgSource = (manifest.sources?.[0]?.name ?? 'Copernicus Sentinel-2').split(' (')[0];
  const nZones = zones?.zones.length ?? 0;

  return (
    <div className={`app ${leftCollapsed ? 'left-collapsed' : ''} ${rightMode ? 'right-open' : ''} ${rightMode === 'det' ? 'right-wide' : ''}`}>
      <LeftColumn
        manifest={manifest}
        regionId={regionId}
        feed={feed}
        feedSel={feedSel}
        tab={leftTab}
        onTab={setLeftTab}
        onFeed={openFeed}
        onRegion={(id) => {
          setTab('map');
          setCompare(null);
          setCheckMode(false);
          selectRegion(id);
        }}
        collapsed={leftCollapsed}
        onCollapse={() => setLeftCollapsed((v) => !v)}
        onWorld={() => {
          setCompare(null);
          setTab('map');
          selectRegion(null);
        }}
      />

      <main className="main" data-testid="main">
        <MapView
          regions={manifest.regions}
          activeRegion={regionId}
          bounds={dateEntry?.bounds ?? region?.bounds ?? null}
          rgbImg={checkMode && !checkPair ? null : rgbImg}
          probImg={probImg}
          detections={checkMode ? null : shownDet}
          artifacts={checkMode ? null : artSplit.arts}
          h3={h3}
          h3Scale={h3Scale}
          h3Max={h3Max}
          hoverId={hover?.kind === 'det' ? (hover.props as DetProps).id : null}
          drift={checkMode ? null : drift}
          layers={layers}
          selectedId={selected?.properties.id ?? null}
          onHover={setHover}
          onClickDet={(f) => openDetection(f)}
          onClickRegion={(id) => selectRegion(id)}
          basemap={basemap}
          offline={offline}
          onOffline={(on) => {
            setOffline(on);
            if (on) showToast('Нет сети — офлайн-подложка');
          }}
          projection={projection}
          route={route}
          initialCamera={url.camera}
          zones={zones?.zones ?? null}
          showZones={(layers.zones || !!route) && !!region && !checkMode}
          activeZone={zone?.rank ?? null}
          onCamera={(c) => {
            camera.current = c;
            syncUrl();
          }}
          onZoneClick={(z) => openZone(z, false)}
          onClickH3={(c) => openPlace(c.h3)}
          osm={osm}
          sources={null}
          check={checkOverlay}
        />
        {hover && <Tooltip hover={hover} manifest={manifest} />}

        {region && !checkMode && (
          <div className="actions" data-testid="actions" role="tablist" aria-label="Что показать по району">
            <button className={view === 'findings' && !zone && !place ? 'on' : ''} onClick={() => changeView('findings')} data-testid="act-findings">
              Кандидаты<span className="n">{detections ? detections.features.length : ''}</span>
            </button>
            <button className={view === 'zones' || !!zone ? 'on' : ''} onClick={() => changeView('zones')} data-testid="act-zones" disabled={!scene || (!!zones && !nZones)}>
              Зоны<span className="n">{nZones || ''}</span>
            </button>
            <button className={view === 'history' ? 'on' : ''} onClick={() => changeView('history')} data-testid="act-history">
              История<span className="n">{region.dates.length}</span>
            </button>
            {hasAnyDrift && (
              <button className={view === 'drift' ? 'on' : ''} onClick={() => changeView('drift')} data-testid="act-drift">
                Дрейф
              </button>
            )}
            {paths.has('/api/review/queue') && (
              <button className={tab === 'review' ? 'on' : ''} onClick={() => setTab('review')} data-testid="act-review">
                Проверка
              </button>
            )}
          </div>
        )}
        {!region && tab === 'map' && !compare && (
          <div className="actions" style={{ padding: '0 16px', alignItems: 'center', height: 42, color: 'var(--text-2)' }} data-testid="world-hint">
            Выберите район
          </div>
        )}

        <Toolbar
          layers={layers}
          onLayer={userToggleLayer}
          basemap={basemap}
          offline={offline}
          onBasemap={(b) => {
            setBasemap(b);
            setOffline(false);
            savePrefs({ basemap: b });
          }}
          projection={projection}
          onProjection={(pr) => {
            changeProjection(pr);
            savePrefs({ projection: pr });
          }}
          hasRegion={!!region}
          hasDrift={!!dateEntry?.drift}
          flowAvail={flowAvail}
          paths={paths}
          contextOk={contextOk}
          nArtifacts={nArts}
          models={manifest.models}
          kind={manifest.kind}
          model={model}
          sceneModels={dateEntry?.models ?? []}
          onModel={(m) => {
            setSelected(null);
            wantModel.current = m;
            setModel(m);
            savePrefs({ model: m });
          }}
          confAvail={confAvail}
          onlyConfirmed={onlyConf}
          onOnlyConfirmed={setOnlyConf}
          onCompare={openCompare}
          compareOn={!!compare}
          onTour={tourCaption ? stopTour : startTour}
          tourOn={!!tourCaption}
          onCopy={copyLink}
        />

        {region && tab === 'map' && !compare && (
          <Legend
            manifest={manifest}
            model={model}
            threshold={threshold}
            layers={layers}
            scale={h3Scale}
            confAvail={confAvail}
            nArtifacts={nArts}
            flow={flowInfo}
            check={checkMode && !!checkData}
          />
        )}

        {layers.drift && driftRaw && drift && !checkMode && (
          <DriftPlayer key={`${sk}-${view}`} drift={driftRaw} onRender={() => ctl.render()} flow={flowInfo} autoplay={view === 'drift' && !tourCaption} />
        )}

        <div className="attrib" data-testid="attribution">
          {!offline && basemap === 'dark' && '© CARTO, © OpenStreetMap contributors'}
          {!offline && basemap === 'satellite' && 'Tiles © Esri — Esri, Maxar, Earthstar Geographics'}
          {(offline || basemap === 'none') && 'Natural Earth'}
          {` · Снимки: ${imgSource}`}
          {layers.osm ? ' · Объекты: © OpenStreetMap contributors (ODbL)' : ''}
        </div>

        {tourCaption && (
          <div className="tour-cap" data-testid="tour-caption">
            <div className="tc-top">
              <span>
                Демо-тур · {tourCaption.step} из {tourCaption.total}
              </span>
              <button className="btn ghost sm" onClick={stopTour} data-testid="tour-stop">
                Остановить
              </button>
            </div>
            <div className="tc-text">{tourCaption.text}</div>
            <div className="tc-prog">
              <span style={{ width: `${(tourCaption.step / tourCaption.total) * 100}%` }} />
            </div>
          </div>
        )}
        {toast && (
          <div className="toast" role="status">
            {toast}
          </div>
        )}
      </main>

      <RightPanel
        mode={rightMode}
        manifest={manifest}
        region={region}
        dateEntry={dateEntry}
        model={model}
        timeseries={timeseries}
        detections={detections}
        detectionsAll={detectionsAll}
        otherDet={otherDet}
        otherModel={otherModel}
        nArtifacts={nArts}
        nConfirmed={nConf}
        zones={zones}
        h3={h3}
        rgbImg={rgbImg}
        threshold={threshold}
        selected={selected}
        zone={zone}
        place={place}
        paths={paths}
        contextOk={contextOk}
        incidents={incidents}
        onIncidentChanged={() => setIncTick((x) => x + 1)}
        onDate={selectDate}
        onZone={(z) => openZone(z)}
        onDetection={(f) => openDetection(f, true)}
        onPlace={openPlace}
        onCloseDet={() => setSelected(null)}
        onView={changeView}
        onCloseZone={() => setZone(null)}
        onClosePlace={() => setPlace(null)}
        onWorld={() => selectRegion(null)}
        onToast={showToast}
        routeOn={routeOn}
        route={route}
        onRoute={(on) => {
          setRouteOn(on);
          if (on && !layers.zones) toggleLayer('zones', true);
        }}
        onCompare={openCompare}
        onCheck={() => openCheck(true)}
        onLayer={userToggleLayer}
        drift={driftRaw}
        layers={layers}
        flowAvail={flowAvail}
        check={{ summary: checkSummary, pair: checkPair, data: checkData, onPair: selectCheckPair, onClose: () => openCheck(false) }}
      />

      {tab === 'review' && paths.has('/api/review/queue') && (
        <Suspense fallback={<div className="view" />}>
          <ReviewView
            manifest={manifest}
            initialRegion={regionId ?? bestRegion(manifest)?.id ?? null}
            onToast={showToast}
            onShowOnMap={showReviewItem}
            onClose={() => {
              setTab('map');
              setIncTick((x) => x + 1);
            }}
          />
        </Suspense>
      )}

      {compare && (
        <Suspense fallback={<div className="view" />}>
          <CompareView manifest={manifest} compare={compare} model={model} basemap={offline ? 'none' : basemap} onClose={() => setCompare(null)} onChange={setCompare} />
        </Suspense>
      )}
    </div>
  );
}
