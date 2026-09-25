import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Basemap, Camera, DetProps, Feature, LayerKey, Layers, Manifest, Region, SceneRef, Zone } from './types';
import {
  bestRegion,
  getImage,
  loadDetections,
  loadDrift,
  loadH3,
  loadManifest,
  loadTimeseries,
  loadZones,
  modelPath,
} from './lib/data';
import { useAsync } from './lib/hooks';
import { makeScale } from './lib/style';
import { DEFAULT_LAYERS, readUrl, writeUrl } from './lib/url';
import MapView from './map/MapView';
import { anim, ctl, fitOverview, flyToBounds, flyToPoint, getCamera, waitIdle } from './map/controller';
import { centroid, loadGeo, prepareDrift, type HoverInfo } from './map/layers';
import Header from './components/Header';
import LeftPanel from './components/LeftPanel';
import RightPanel from './components/RightPanel';
import Legend from './components/Legend';
import Tooltip from './components/Tooltip';
import DetectionCard from './components/DetectionCard';
import DriftPlayer from './components/DriftPlayer';
import Footer from './components/Footer';
import EmptyState from './components/EmptyState';
import { runTour, type TourApi } from './tour';

const CompareView = lazy(() => import('./components/CompareView'));

export default function App() {
  const url = useMemo(readUrl, []);
  const [manifest, setManifest] = useState<Manifest | null | undefined>(undefined);
  const [regionId, setRegionId] = useState<string | null>(null);
  const [date, setDate] = useState<string | null>(null);
  const [model, setModel] = useState<string>(url.model ?? 'mdd');
  const [layers, setLayers] = useState<Layers>(url.layers ?? DEFAULT_LAYERS);
  const [basemap, setBasemap] = useState<Basemap>(url.basemap ?? 'dark');
  const [selected, setSelected] = useState<Feature<DetProps> | null>(null);
  const [hover, setHover] = useState<HoverInfo | null>(null);
  const [compare, setCompare] = useState<{ a: SceneRef; b: SceneRef } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [tourCaption, setTourCaption] = useState<{ step: number; total: number; text: string } | null>(null);
  const [leftCollapsed, setLeftCollapsed] = useState(false);
  const [rightCollapsed, setRightCollapsed] = useState(false);
  const [geoReady, setGeoReady] = useState(false);
  const [zonePopup, setZonePopup] = useState<Zone | null>(null);
  const camera = useRef<Camera | undefined>(url.camera);
  const tourAbort = useRef<AbortController | null>(null);

  // ---- manifest ----
  useEffect(() => {
    loadManifest().then((m) => {
      if (!m || !m.regions.length) return setManifest(null);
      setManifest(m);
      const r = url.region ? m.regions.find((x) => x.id === url.region) : undefined;
      if (r) {
        setRegionId(r.id);
        const d = url.date && r.dates.some((x) => x.date === url.date) ? url.date : r.dates[r.dates.length - 1]?.date;
        setDate(d ?? null);
        const de = r.dates.find((x) => x.date === d);
        if (de && !de.models.includes(model)) setModel(de.models[0]);
        if (!url.camera) setTimeout(() => flyToBounds(de?.bounds ?? r.bounds, { duration: 0 }), 50);
      } else {
        const first = m.regions[0]?.dates.at(-1);
        if (first && !first.models.includes(model)) setModel(first.models[0] ?? model);
      }
      if (url.compare) {
        const [a, b] = url.compare.split(',').map((s) => {
          const [region, date] = s.split(':');
          return { region, date };
        });
        if (a?.region && b?.region) setCompare({ a, b });
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const region: Region | null = useMemo(
    () => manifest?.regions.find((r) => r.id === regionId) ?? null,
    [manifest, regionId],
  );
  const dateEntry = useMemo(() => region?.dates.find((d) => d.date === date) ?? null, [region, date]);
  const modelOk = !!dateEntry && dateEntry.models.includes(model);
  const scene = region && dateEntry && modelOk ? { r: region.id, d: dateEntry.date, m: model } : null;
  const sk = scene ? `${scene.r}/${scene.d}/${scene.m}` : '';

  // ---- data for the current scene (heavy layers only when switched on) ----
  const rgbImg = useAsync(dateEntry && layers.rgb ? () => getImage(dateEntry.rgb) : null, [dateEntry?.rgb, layers.rgb]);
  const probImg = useAsync(
    scene && layers.prob ? () => getImage(modelPath(scene.r, scene.d, scene.m, 'prob.png')) : null,
    [sk, layers.prob],
  );
  const detections = useAsync(scene ? () => loadDetections(scene.r, scene.d, scene.m) : null, [sk]);
  const zones = useAsync(scene ? () => loadZones(scene.r, scene.d, scene.m) : null, [sk]);
  const noDet = !!detections && detections.features.length === 0;
  const needH3 = layers.h3 || !!selected || noDet;
  const h3 = useAsync(scene && needH3 ? () => loadH3(scene.r, scene.d, scene.m) : null, [sk, needH3]);
  const driftRaw = useAsync(
    dateEntry?.drift && layers.drift ? () => loadDrift(dateEntry.drift!) : null,
    [dateEntry?.drift, layers.drift],
  );
  const h3Scale = useMemo(() => makeScale(h3?.features.map((f) => f.properties.share_permille) ?? []), [h3]);
  const h3Max = useMemo(
    () => (h3?.features ?? []).reduce((a, f) => Math.max(a, f.properties.share_permille ?? 0), 0),
    [h3],
  );
  const drift = useMemo(() => (driftRaw ? prepareDrift(driftRaw.particles) : null), [driftRaw]);
  const timeseries = useAsync(region ? () => loadTimeseries(region.id) : null, [region?.id]);

  // when drift is switched on, frame the whole forecast (particles leave the scene bounds)
  useEffect(() => {
    if (!layers.drift || !drift || !region) return;
    let b = region.bounds;
    for (const t of drift.trips)
      for (const [x, y] of t.path) b = [Math.min(b[0], x), Math.min(b[1], y), Math.max(b[2], x), Math.max(b[3], y)];
    flyToBounds(b, { duration: 1500 });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drift, layers.drift]);

  useEffect(() => {
    if ((layers.h3 || layers.drift) && !geoReady) loadGeo().then(() => setGeoReady(true));
  }, [layers.h3, layers.drift, geoReady]);

  // ---- URL state ----
  const syncUrl = useCallback(() => {
    writeUrl({
      region: regionId ?? undefined,
      date: regionId ? date ?? undefined : undefined,
      model,
      layers,
      basemap,
      camera: camera.current,
      compare: compare ? `${compare.a.region}:${compare.a.date},${compare.b.region}:${compare.b.date}` : undefined,
    });
  }, [regionId, date, model, layers, basemap, compare]);
  useEffect(syncUrl, [syncUrl]);

  // ---- actions ----
  const showToast = useCallback((t: string) => {
    setToast(t);
    setTimeout(() => setToast((x) => (x === t ? null : x)), 2600);
  }, []);

  const selectRegion = useCallback(
    (id: string | null) => {
      setSelected(null);
      setHover(null);
      setZonePopup(null);
      if (!manifest) return;
      if (!id) {
        setRegionId(null);
        fitOverview(manifest.regions.map((r) => r.bounds));
        return;
      }
      const r = manifest.regions.find((x) => x.id === id);
      if (!r) return;
      setRegionId(r.id);
      const latest = r.dates.find((d) => d.date === r.summary?.latest_date) ?? r.dates[r.dates.length - 1];
      setDate(latest?.date ?? null);
      if (latest && !latest.models.includes(model)) setModel(latest.models[0]);
      flyToBounds(latest?.bounds ?? r.bounds, { pitch: layers.h3_3d ? 50 : 0, bearing: layers.h3_3d ? -18 : 0 });
    },
    [manifest, model, layers.h3_3d],
  );

  const toggleLayer = useCallback((k: LayerKey, on?: boolean) => {
    setLayers((l) => {
      const v = on ?? !l[k];
      const n = { ...l, [k]: v };
      if (k === 'h3_3d') {
        if (v) n.h3 = true;
        ctl.map?.easeTo({ pitch: v ? 52 : 0, bearing: v ? -18 : 0, duration: 1200 });
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

  const selectDate = useCallback(
    (d: string) => {
      setSelected(null);
      setDate(d);
      const de = region?.dates.find((x) => x.date === d);
      if (de && !de.models.includes(model)) setModel(de.models[0]);
    },
    [region, model],
  );

  const openDetection = useCallback((f: Feature<DetProps>) => {
    setSelected(f);
    setHover(null);
  }, []);

  const showZone = useCallback(
    (z: Zone) => {
      if (!layers.zones) toggleLayer('zones', true);
      setZonePopup(z);
      flyToPoint(z.lon, z.lat, 14);
    },
    [layers.zones, toggleLayer],
  );

  const defaultCompare = useCallback((): { a: SceneRef; b: SceneRef } | null => {
    if (!manifest) return null;
    const r = region ?? bestRegion(manifest);
    if (!r) return null;
    const a = { region: r.id, date: date && region ? date : r.dates[r.dates.length - 1].date };
    // B: the other region with most detections (a comparison with an empty scene tells little)
    const other = manifest.regions
      .filter((x) => x.id !== r.id)
      .sort((x, y) => (y.summary?.n_detections ?? 0) - (x.summary?.n_detections ?? 0))[0];
    if (other) return { a, b: { region: other.id, date: other.dates[other.dates.length - 1].date } };
    const prev = r.dates.filter((d) => d.date !== a.date).at(-1);
    return { a, b: { region: r.id, date: prev?.date ?? a.date } };
  }, [manifest, region, date]);

  const openCompare = useCallback(() => {
    const c = defaultCompare();
    if (c) setCompare(c);
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

  // ---- tour ----
  const stateRef = useRef({ detections, zones, region, dateEntry, manifest });
  stateRef.current = { detections, zones, region, dateEntry, manifest };
  const tourApi: TourApi = {
    get: () => stateRef.current,
    selectRegion,
    setLayers: (l) => setLayers((x) => ({ ...x, ...l })),
    toggleLayer,
    openDetection,
    closeDetection: () => setSelected(null),
    openCompare,
    closeCompare: () => setCompare(null),
    caption: (step, total, text) => setTourCaption(text ? { step, total, text } : null),
    showZone,
    closeZone: () => setZonePopup(null),
    waitIdle,
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
      if (window.__mapReady) {
        clearInterval(t);
        startTour();
      }
    }, 200);
    return () => clearInterval(t);
  }, [manifest, url.tour, startTour]);

  // ---- test / screenshot hooks ----
  useEffect(() => {
    window.__app = {
      region: regionId,
      date,
      model,
      layers,
      sceneReady: !!scene && (!layers.rgb || !!rgbImg) && !!detections,
      h3Ready: !!h3 && geoReady,
      driftReady: !!drift && geoReady,
      nDetections: detections?.features.length ?? 0,
      largestDetectionScreen: () => {
        if (!detections?.features.length || !ctl.map) return null;
        const f = [...detections.features].sort((a, b) => b.properties.area_m2 - a.properties.area_m2)[0];
        const p = ctl.map.project(centroid(f) as any);
        const r = ctl.map.getContainer().getBoundingClientRect();
        return { x: p.x + r.left, y: p.y + r.top, id: f.properties.id };
      },
      isMoving: () => !!ctl.map?.isMoving(),
      setDriftHour: (h: number) => {
        anim.hour = h;
        anim.listeners.forEach((l) => l(h));
        ctl.render();
      },
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
  if (manifest === null) return <EmptyState />;

  const threshold = manifest.models[model]?.threshold ?? zones?.threshold ?? null;
  const tsRow = timeseries?.find((t) => t.date === date && t.model === model) ?? null;

  return (
    <div
      className={`app ${leftCollapsed ? 'left-collapsed' : ''} ${rightCollapsed ? 'right-collapsed' : ''} ${compare ? 'comparing' : ''} ${layers.drift && driftRaw ? 'has-drift' : ''}`}
    >
      <Header
        manifest={manifest}
        region={region}
        onHome={() => {
          setCompare(null);
          selectRegion(null);
        }}
        onTour={tourCaption ? stopTour : startTour}
        tourRunning={!!tourCaption}
        onCopy={copyLink}
      />
      <div className="stage">
        <MapView
          regions={manifest.regions}
          activeRegion={regionId}
          bounds={dateEntry?.bounds ?? region?.bounds ?? null}
          rgbImg={rgbImg}
          probImg={probImg}
          detections={detections}
          h3={h3}
          h3Scale={h3Scale}
          h3Max={h3Max}
          hoverId={hover?.kind === 'det' ? (hover.props as DetProps).id : null}
          drift={drift}
          layers={layers}
          selectedId={selected?.properties.id ?? null}
          onHover={setHover}
          onClickDet={openDetection}
          onClickRegion={selectRegion}
          basemap={basemap}
          initialCamera={url.camera}
          zones={zones?.zones ?? null}
          showZones={layers.zones && !!region}
          onCamera={(c) => {
            camera.current = c;
            syncUrl();
          }}
          onBasemapFailed={(b) => {
            setBasemap('none');
            showToast(
              b === 'dark'
                ? 'Тёмная подложка недоступна (нет сети) — офлайн-режим'
                : 'Спутниковые тайлы недоступны — офлайн-режим',
            );
          }}
          onZoneClick={(z) => setZonePopup(z)}
        />
        {hover && <Tooltip hover={hover} manifest={manifest} />}

        <LeftPanel
          manifest={manifest}
          region={region}
          date={date}
          model={model}
          layers={layers}
          basemap={basemap}
          hasDrift={!!dateEntry?.drift}
          collapsed={leftCollapsed}
          onCollapse={() => setLeftCollapsed((v) => !v)}
          onRegion={selectRegion}
          onDate={selectDate}
          onModel={(m) => {
            setSelected(null);
            setModel(m);
          }}
          onLayer={toggleLayer}
          onBasemap={setBasemap}
        />
        <RightPanel
          manifest={manifest}
          region={region}
          dateEntry={dateEntry}
          model={model}
          tsRow={tsRow}
          timeseries={timeseries}
          detections={detections}
          zones={zones}
          h3={h3}
          collapsed={rightCollapsed}
          onCollapse={() => setRightCollapsed((v) => !v)}
          onRegion={selectRegion}
          onDate={selectDate}
          onZone={showZone}
          onCompare={openCompare}
          compareActive={!!compare}
          compare={compare}
          setCompare={setCompare}
          onToast={showToast}
        />

        {region && (
          <Legend
            manifest={manifest}
            model={model}
            threshold={threshold}
            layers={layers}
            date={date}
            scale={h3Scale}
          />
        )}

        {layers.drift && driftRaw && drift && (
          <DriftPlayer drift={driftRaw} onRender={() => ctl.render()} />
        )}

        {selected && dateEntry && (
          <DetectionCard
            key={selected.properties.id}
            feature={selected}
            dateEntry={dateEntry}
            rgbImg={rgbImg}
            h3={h3}
            manifest={manifest}
            threshold={threshold}
            onClose={() => setSelected(null)}
            onToast={showToast}
          />
        )}

        {zonePopup && region && (
          <div className="zone-popup glass" data-testid="zone-popup">
            <button className="icon-btn close" onClick={() => setZonePopup(null)} aria-label="Закрыть" data-testid="zone-popup-close">
              ×
            </button>
            <div className="eyebrow">Приоритет обследования</div>
            <div className="zp-title">
              Зона №{zonePopup.rank} <span className="muted">· {zonePopup.index === null ? '—' : zonePopup.index.toFixed(2)} ‰</span>
            </div>
            <div className="zp-reason">{zonePopup.reason}</div>
            <div className="zp-coords mono">
              {zonePopup.lat.toFixed(5)}, {zonePopup.lon.toFixed(5)}
            </div>
          </div>
        )}

        {compare && (
          <Suspense fallback={<div className="compare-view loading">Загрузка сравнения…</div>}>
            <CompareView manifest={manifest} compare={compare} model={model} basemap={basemap} onClose={() => setCompare(null)} />
          </Suspense>
        )}

        {tourCaption && (
          <div className="tour-caption glass" data-testid="tour-caption">
            <div className="tour-step">
              Демо-тур · {tourCaption.step}/{tourCaption.total}
            </div>
            <div className="tour-text">{tourCaption.text}</div>
            <div className="tour-progress">
              <div style={{ width: `${(tourCaption.step / tourCaption.total) * 100}%` }} />
            </div>
            <button className="btn ghost small" onClick={stopTour} data-testid="tour-stop">
              Остановить
            </button>
          </div>
        )}

        {toast && (
          <div className="toast" role="status">
            {toast}
          </div>
        )}
        <Footer manifest={manifest} basemap={basemap} />
      </div>
    </div>
  );
}
