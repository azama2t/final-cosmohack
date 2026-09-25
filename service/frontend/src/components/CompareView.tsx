import { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import { MapboxOverlay } from '@deck.gl/mapbox';
import { BitmapLayer, GeoJsonLayer, ScatterplotLayer } from '@deck.gl/layers';
import { centroid } from '../map/layers';
import type { Basemap, Manifest, SceneRef } from '../types';
import { getImage, loadDetections, shortName } from '../lib/data';
import { darkStyle, offlineStyle, satelliteStyle } from '../map/controller';
import { ACCENT_RGB, fmtDate } from '../lib/style';

interface Props {
  manifest: Manifest;
  compare: { a: SceneRef; b: SceneRef };
  model: string;
  basemap: Basemap;
  onClose: () => void;
}

export default function CompareView({ manifest, compare, model, basemap, onClose }: Props) {
  const maps = useRef<(maplibregl.Map | null)[]>([null, null]);
  const same = compare.a.region === compare.b.region;
  return (
    <div className="compare-view" data-testid="compare-view">
      <Pane side="a" refScene={compare.a} {...{ manifest, model, basemap, maps, same }} />
      <div className="compare-divider" />
      <Pane side="b" refScene={compare.b} {...{ manifest, model, basemap, maps, same }} />
      <button className="btn ghost small compare-x" onClick={onClose} data-testid="compare-exit">
        × закрыть сравнение
      </button>
    </div>
  );
}

function Pane({
  side,
  refScene,
  manifest,
  model,
  basemap,
  maps,
  same,
}: {
  side: 'a' | 'b';
  refScene: SceneRef;
  manifest: Manifest;
  model: string;
  basemap: Basemap;
  maps: React.MutableRefObject<(maplibregl.Map | null)[]>;
  same: boolean;
}) {
  const el = useRef<HTMLDivElement>(null);
  const overlay = useRef<MapboxOverlay | null>(null);
  const [ready, setReady] = useState(false);
  const idx = side === 'a' ? 0 : 1;
  const region = manifest.regions.find((r) => r.id === refScene.region);
  const de = region?.dates.find((d) => d.date === refScene.date);
  const mdl = de && de.models.includes(model) ? model : de?.models[0];

  useEffect(() => {
    const b = de?.bounds ?? region?.bounds;
    const map = new maplibregl.Map({
      container: el.current!,
      style: offlineStyle(),
      bounds: b ? [[b[0], b[1]], [b[2], b[3]]] : undefined,
      fitBoundsOptions: { padding: 40 },
      attributionControl: false,
      fadeDuration: 0,
    });
    map.on('error', () => {});
    maps.current[idx] = map;
    const o = new MapboxOverlay({ interleaved: false, layers: [] });
    map.addControl(o as any);
    overlay.current = o;
    map.on('load', () => setReady(true));
    // camera sync when both panes show the same region
    const sync = () => {
      if (!same || (map as any).__syncing) return;
      const other = maps.current[1 - idx];
      if (!other) return;
      (other as any).__syncing = true;
      other.jumpTo({ center: map.getCenter(), zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() });
      (other as any).__syncing = false;
    };
    map.on('move', sync);
    return () => {
      maps.current[idx] = null;
      map.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refScene.region, refScene.date, same]);

  useEffect(() => {
    const map = maps.current[idx];
    if (!map) return;
    if (basemap === 'satellite') map.setStyle(satelliteStyle(), { diff: false });
    else if (basemap === 'dark') darkStyle().then((s) => map.setStyle(s, { diff: false })).catch(() => {});
  }, [basemap, idx, refScene.region, refScene.date]);

  useEffect(() => {
    if (!de || !mdl || !ready) return;
    let alive = true;
    Promise.all([getImage(de.rgb).catch(() => null), loadDetections(refScene.region, de.date, mdl)]).then(([img, det]) => {
      if (!alive || !overlay.current) return;
      overlay.current.setProps({
        layers: [
          img &&
            new BitmapLayer({ id: `cmp-rgb-${side}`, image: img as any, bounds: de.bounds as any }),
          det &&
            new GeoJsonLayer({
              id: `cmp-det-${side}`,
              data: det as any,
              getFillColor: [...ACCENT_RGB, 170],
              getLineColor: [255, 200, 180, 255],
              lineWidthUnits: 'pixels',
              getLineWidth: 1.5,
              lineWidthMinPixels: 1,
            }),
          det &&
            det.features.length <= 400 &&
            new ScatterplotLayer({
              id: `cmp-halo-${side}`,
              data: det.features,
              getPosition: (f: any) => centroid(f),
              getRadius: (f: any) => Math.min(20, 8 + 0.1 * Math.sqrt(Math.max(0, f.properties.area_m2))),
              radiusUnits: 'pixels',
              stroked: true,
              filled: true,
              getFillColor: [...ACCENT_RGB, 55],
              getLineColor: [...ACCENT_RGB, 240],
              lineWidthUnits: 'pixels',
              getLineWidth: 2.5,
            }),
        ].filter(Boolean) as any,
      });
      (window as any)[`__compareReady_${side}`] = true;
    });
    return () => {
      alive = false;
      (window as any)[`__compareReady_${side}`] = false;
    };
  }, [de, mdl, ready, refScene.region, side]);

  return (
    <div className={`compare-pane side-${side}`}>
      <div ref={el} className="compare-map" />
      <div className="compare-label glass">
        <span className="cmp-tag">{side.toUpperCase()}</span>
        <span>
          <b title={region?.name}>{region ? shortName(region.name) : refScene.region}</b>
          <span className="muted"> · {fmtDate(refScene.date)}</span>
        </span>
      </div>
    </div>
  );
}
