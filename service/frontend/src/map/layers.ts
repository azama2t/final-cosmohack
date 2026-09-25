import { BitmapLayer, GeoJsonLayer, PathLayer, ScatterplotLayer } from '@deck.gl/layers';
import type { Layer, PickingInfo } from '@deck.gl/core';
import type { Bounds, DetProps, Feature, FC, H3Props, Layers, Region } from '../types';
import { ACCENT_RGB, h3Color, h3Elevation, h3LineColor, type H3Scale } from '../lib/style';
import type { DriftFile } from '../types';
import type { RoutePlan } from '../lib/route';

type GeoModule = typeof import('@deck.gl/geo-layers');
let geo: GeoModule | null = null;
let geoPromise: Promise<GeoModule> | null = null;
/** Lazy chunk with H3HexagonLayer and TripsLayer — loaded only when H3 or drift is switched on. */
export function loadGeo(): Promise<GeoModule> {
  if (!geoPromise) geoPromise = import('@deck.gl/geo-layers').then((m) => (geo = m));
  return geoPromise;
}
export const geoLoaded = () => geo !== null;

type Trip = { id: number; path: [number, number][]; ts: number[] };
export interface PreparedDrift {
  trips: Trip[];
  starts: [number, number][];
  /** optional ensemble members (other wind drift factors) — uncertainty cloud */
  ensemble: { wdf: number; trips: Trip[] }[];
}

export interface HoverInfo {
  x: number;
  y: number;
  kind: 'det' | 'h3';
  props: DetProps | H3Props;
}

export interface LayerCtx {
  regions: Region[];
  activeRegion: string | null;
  bounds: Bounds | null;
  rgbImg: HTMLImageElement | null;
  probImg: HTMLImageElement | null;
  detections: FC<DetProps> | null;
  /** L38: excluded artifacts (seam / wake / ship) — drawn muted only when the legend switch is on */
  artifacts?: FC<DetProps> | null;
  h3: FC<H3Props> | null;
  h3Scale: H3Scale;
  /** max non-zero share_permille of the scene (normalises 3D heights) */
  h3Max: number;
  /** current map zoom (halo fades out when polygons become visible) */
  zoom: number;
  hoverId: string | null;
  drift: PreparedDrift | null;
  hour: number;
  /** show the wind-factor ensemble cloud (player toggle) */
  spread: boolean;
  layers: Layers;
  selectedId: string | null;
  onHover: (h: HoverInfo | null) => void;
  onClickDet: (f: Feature<DetProps>) => void;
  onClickRegion: (id: string) => void;
  /** L20: click on an H3 cell → place card */
  onClickH3?: (p: H3Props) => void;
  /** L27: «Порядок посещения зон» (draft order, straight lines) */
  route?: RoutePlan | null;
}

const boundsPoly = (b: Bounds) => [
  [
    [b[0], b[1]],
    [b[2], b[1]],
    [b[2], b[3]],
    [b[0], b[3]],
    [b[0], b[1]],
  ],
];

export function buildLayers(c: LayerCtx): Layer[] {
  const out: Layer[] = [];
  const L = c.layers;

  // Region footprints (always; cheap)
  out.push(
    new GeoJsonLayer({
      id: 'region-footprints',
      data: {
        type: 'FeatureCollection',
        features: c.regions.map((r) => ({
          type: 'Feature',
          geometry: { type: 'Polygon', coordinates: boundsPoly(r.bounds) },
          properties: { id: r.id, active: r.id === c.activeRegion },
        })),
      } as any,
      stroked: true,
      filled: true,
      getFillColor: (f: any) => (f.properties.active ? [0, 0, 0, 0] : [...ACCENT_RGB, 22]),
      getLineColor: (f: any) => (f.properties.active ? [0, 0, 0, 0] : [...ACCENT_RGB, 200]),
      lineWidthUnits: 'pixels',
      getLineWidth: (f: any) => (f.properties.active ? 1 : 1.5),
      pickable: !c.activeRegion,
      onClick: (info: PickingInfo) => {
        const id = (info.object as any)?.properties?.id;
        if (id) c.onClickRegion(id);
      },
      updateTriggers: { getFillColor: c.activeRegion, getLineColor: c.activeRegion, getLineWidth: c.activeRegion },
    }),
  );

  if (c.bounds && L.rgb && c.rgbImg) {
    out.push(
      new BitmapLayer({
        id: 'rgb',
        image: c.rgbImg as any,
        bounds: c.bounds as any,
        textureParameters: { minFilter: 'linear', magFilter: 'linear' } as any,
      }),
    );
  }
  if (c.bounds && L.prob && c.probImg) {
    out.push(
      new BitmapLayer({
        id: 'prob',
        image: c.probImg as any,
        bounds: c.bounds as any,
        opacity: 0.95,
        textureParameters: { minFilter: 'linear', magFilter: 'nearest' } as any,
      }),
    );
  }

  if (L.h3 && c.h3 && geo) {
    const H3HexagonLayer = geo.H3HexagonLayer;
    const extruded = L.h3_3d;
    // land / outside the scene (no observed water at all) is never drawn; in 3D also skip «no data» cells
    const cells = c.h3.features.filter(
      (f) => f.properties.observed_water_px > 0 && (!extruded || f.properties.share_permille !== null),
    );
    const vmax = c.h3Max;
    out.push(
      new H3HexagonLayer<Feature<H3Props>>({
        id: 'h3',
        data: cells,
        getHexagon: (f) => f.properties.h3,
        getFillColor: (f) => h3Color(f.properties.share_permille, c.h3Scale) as any,
        extruded,
        getElevation: (f) => h3Elevation(f.properties.share_permille, vmax),
        elevationScale: 1,
        coverage: extruded ? 0.84 : 1,
        stroked: !extruded,
        filled: true,
        getLineColor: (f) => h3LineColor(f.properties.share_permille) as any,
        lineWidthUnits: 'pixels',
        getLineWidth: (f) => ((f.properties.share_permille ?? 0) > 0 ? 1.2 : 0.8),
        material: { ambient: 0.6, diffuse: 0.55, shininess: 24, specularColor: [60, 60, 60] } as any,
        pickable: true,
        autoHighlight: true,
        highlightColor: [255, 255, 255, 70],
        transitions: { getElevation: { duration: 900 } } as any,
        onHover: (info: PickingInfo) =>
          c.onHover(info.object ? { x: info.x, y: info.y, kind: 'h3', props: (info.object as any).properties } : null),
        onClick: (info: PickingInfo) => {
          const o = info.object as any;
          if (o && c.onClickH3) c.onClickH3(o.properties);
        },
        updateTriggers: {
          getFillColor: [c.h3, c.h3Scale],
          getElevation: [c.h3, vmax],
          getLineColor: c.h3,
          getLineWidth: c.h3,
        },
      }) as any,
    );
  }

  // L38: excluded artifacts — grey outline, no glow/halo, under the findings; a small thin grey ring keeps them
  // findable at region zoom (fades out where the polygons themselves are visible)
  if (L.detections && c.artifacts?.features.length) {
    const arts = c.artifacts.features;
    const hotA = c.hoverId ?? c.selectedId;
    const fadeA = Math.min(1, Math.max(0, (14.6 - c.zoom) / 1.8));
    const onHoverArt = (info: PickingInfo) => {
      const o = info.object as any;
      c.onHover(o ? { x: info.x, y: info.y, kind: 'det', props: o.properties } : null);
    };
    const onClickArt = (info: PickingInfo) => {
      if (info.object) c.onClickDet(info.object as any);
    };
    if (fadeA > 0.05)
      out.push(
        new ScatterplotLayer<Feature<DetProps>>({
          id: 'artifacts-ring',
          data: arts,
          getPosition: (f) => centroid(f),
          getRadius: (f) => (f.properties.id === hotA ? 7 : 5),
          radiusUnits: 'pixels',
          stroked: true,
          filled: true,
          getFillColor: [0, 0, 0, 1],
          getLineColor: (f) => (f.properties.id === hotA ? [226, 232, 240, 255] : [158, 168, 180, Math.round(210 * fadeA)]),
          lineWidthUnits: 'pixels',
          getLineWidth: 1.25,
          pickable: fadeA > 0.3,
          onHover: onHoverArt,
          onClick: onClickArt,
          parameters: { depthTest: false } as any,
          updateTriggers: { getLineColor: [fadeA, hotA], getRadius: hotA },
        }),
      );
    out.push(
      new GeoJsonLayer<DetProps>({
        id: 'artifacts',
        data: c.artifacts as any,
        stroked: true,
        filled: true,
        getFillColor: (f: any) => (f.properties.id === hotA ? [200, 208, 218, 70] : [158, 168, 180, 28]),
        getLineColor: [158, 168, 180, 220],
        lineWidthUnits: 'pixels',
        getLineWidth: 1.25,
        lineWidthMinPixels: 1,
        pickable: true,
        parameters: { depthTest: false } as any,
        onHover: onHoverArt,
        onClick: onClickArt,
        updateTriggers: { getFillColor: hotA },
      }),
    );
    const selA = c.selectedId ? arts.find((f) => f.properties.id === c.selectedId) : undefined;
    if (selA)
      out.push(
        new GeoJsonLayer({
          id: 'artifact-selected',
          data: selA as any,
          stroked: true,
          filled: false,
          getLineColor: [226, 232, 240, 255],
          lineWidthUnits: 'pixels',
          getLineWidth: 2,
          parameters: { depthTest: false } as any,
        }),
      );
  }

  if (L.detections && c.detections) {
    const feats = c.detections.features;
    const onHoverDet = (info: PickingInfo) => {
      const o = info.object as any;
      c.onHover(o ? { x: info.x, y: info.y, kind: 'det', props: o.properties } : null);
    };
    const onClickDet = (info: PickingInfo) => {
      if (info.object) c.onClickDet(info.object as any);
    };
    // «halo»: screen-space marker at each spot centroid so that 1–3 px spots are findable at region zoom.
    // Radius grows with sqrt(area); fades out from zoom 13 → 14.5 where the polygons themselves are visible.
    const fade = Math.min(1, Math.max(0.12, (14.6 - c.zoom) / 1.8));
    const haloR = (f: Feature<DetProps>) => Math.min(22, 8.5 + 0.11 * Math.sqrt(Math.max(0, f.properties.area_m2)));
    const dense = feats.length > 400;
    const hot = c.hoverId ?? c.selectedId;
    if (!dense)
      out.push(
        new ScatterplotLayer<Feature<DetProps>>({
          id: 'detections-glow',
          data: feats,
          getPosition: (f) => centroid(f),
          getRadius: (f) => haloR(f) * 2.1,
          radiusUnits: 'pixels',
          filled: true,
          stroked: true,
          getFillColor: [...ACCENT_RGB, Math.round(58 * fade)],
          getLineColor: [...ACCENT_RGB, Math.round(70 * fade)],
          lineWidthUnits: 'pixels',
          getLineWidth: 1,
          parameters: { depthTest: false } as any,
          updateTriggers: { getFillColor: fade, getLineColor: fade },
        }),
      );
    out.push(
      new ScatterplotLayer<Feature<DetProps>>({
        id: 'detections-halo',
        data: feats,
        getPosition: (f) => centroid(f),
        getRadius: (f) => (f.properties.id === hot ? haloR(f) + 3 : haloR(f)),
        radiusUnits: 'pixels',
        stroked: true,
        filled: true,
        getFillColor: (f) => (f.properties.id === hot ? [255, 190, 170, Math.round(110 * fade)] : [...ACCENT_RGB, Math.round(60 * fade)]),
        getLineColor: (f) => (f.properties.id === hot ? [255, 244, 236, 255] : [...ACCENT_RGB, Math.round(245 * fade)]),
        lineWidthUnits: 'pixels',
        getLineWidth: (f) => (f.properties.id === hot ? 3 : 2.5),
        pickable: fade > 0.3,
        onHover: onHoverDet,
        onClick: onClickDet,
        parameters: { depthTest: false } as any,
        transitions: { getRadius: 150 } as any,
        updateTriggers: { getFillColor: [fade, hot], getLineColor: [fade, hot], getRadius: hot, getLineWidth: hot },
      }),
    );
    out.push(
      new GeoJsonLayer<DetProps>({
        id: 'detections',
        data: c.detections as any,
        stroked: true,
        filled: true,
        getFillColor: (f: any) => (f.properties.id === hot ? [255, 190, 170, 230] : [...ACCENT_RGB, 170]),
        getLineColor: [255, 190, 170, 255],
        lineWidthUnits: 'pixels',
        getLineWidth: 1.5,
        lineWidthMinPixels: 1,
        pickable: true,
        autoHighlight: true,
        highlightColor: [255, 236, 214, 230],
        parameters: { depthTest: false } as any,
        onHover: onHoverDet,
        onClick: onClickDet,
        updateTriggers: { getFillColor: hot },
      }),
    );
    // L15: confirmed by the other model within 20 m -> second (outer) ring in pale cream; stays visible at high zoom
    const conf = feats.filter((f) => f.properties.confirmed === true);
    if (conf.length) {
      const cf = Math.max(0.55, fade);
      out.push(
        new ScatterplotLayer<Feature<DetProps>>({
          id: 'detections-confirmed',
          data: conf,
          getPosition: (f) => centroid(f),
          getRadius: (f) => (f.properties.id === hot ? haloR(f) + 3 : haloR(f)) + 5,
          radiusUnits: 'pixels',
          stroked: true,
          filled: false,
          getLineColor: [255, 236, 196, Math.round(255 * cf)],
          lineWidthUnits: 'pixels',
          getLineWidth: 2,
          pickable: false,
          parameters: { depthTest: false } as any,
          updateTriggers: { getLineColor: cf, getRadius: hot },
        }),
      );
    }
    if (c.selectedId) {
      const sel = feats.find((f) => f.properties.id === c.selectedId);
      if (sel)
        out.push(
          new GeoJsonLayer({
            id: 'detection-selected',
            data: sel as any,
            stroked: true,
            filled: false,
            getLineColor: [255, 255, 255, 255],
            lineWidthUnits: 'pixels',
            getLineWidth: 3,
            parameters: { depthTest: false } as any,
          }),
        );
    }
  }

  if (L.drift && c.drift && geo) {
    if (c.spread && c.drift.ensemble.length) {
      // uncertainty range: where particles of the other wind-factor runs are at the current hour
      const pts = c.drift.ensemble.flatMap((m) => m.trips);
      out.push(
        new ScatterplotLayer({
          id: 'drift-spread-glow',
          data: pts,
          getPosition: (d: any) => positionAt(d, c.hour),
          getRadius: 11,
          radiusUnits: 'pixels',
          getFillColor: [196, 170, 255, 34],
          updateTriggers: { getPosition: c.hour },
          parameters: { depthTest: false } as any,
        }),
        new ScatterplotLayer({
          id: 'drift-spread',
          data: pts,
          getPosition: (d: any) => positionAt(d, c.hour),
          getRadius: 2.2,
          radiusUnits: 'pixels',
          getFillColor: [214, 196, 255, 170],
          updateTriggers: { getPosition: c.hour },
          parameters: { depthTest: false } as any,
        }),
      );
    }
    out.push(
      new PathLayer({
        id: 'drift-ghost',
        data: c.drift.trips,
        getPath: (d: any) => d.path,
        getColor: [150, 200, 255, 30],
        getWidth: 1,
        widthUnits: 'pixels',
        parameters: { depthTest: false } as any,
      }),
    );
    out.push(
      new geo.TripsLayer({
        id: 'drift-trips',
        data: c.drift.trips,
        getPath: (d: any) => d.path,
        getTimestamps: (d: any) => d.ts,
        getColor: [140, 220, 255],
        widthUnits: 'pixels',
        getWidth: 2.2,
        capRounded: true,
        jointRounded: true,
        trailLength: 10,
        fadeTrail: true,
        currentTime: c.hour,
        parameters: { depthTest: false } as any,
      }) as any,
    );
    out.push(
      new ScatterplotLayer({
        id: 'drift-heads',
        data: c.drift.trips,
        getPosition: (d: any) => positionAt(d, c.hour),
        getRadius: 3,
        radiusUnits: 'pixels',
        getFillColor: [220, 245, 255, 235],
        updateTriggers: { getPosition: c.hour },
        parameters: { depthTest: false } as any,
      }),
    );
  }
  // L27: draft visiting order — casing + line; numbers and the port are DOM markers (MapView)
  if (c.route && c.route.path.length > 1) {
    out.push(
      new PathLayer({
        id: 'route-casing',
        data: [{ path: c.route.path }],
        getPath: (d: any) => d.path,
        getColor: [7, 17, 31, 200],
        getWidth: 6,
        widthUnits: 'pixels',
        capRounded: true,
        jointRounded: true,
      }),
      new PathLayer({
        id: 'route',
        data: [{ path: c.route.path }],
        getPath: (d: any) => d.path,
        getColor: [255, 214, 140, 235],
        getWidth: 2.5,
        widthUnits: 'pixels',
        capRounded: true,
        jointRounded: true,
      }),
    );
  }
  return out;
}

export function centroid(f: Feature<any>): [number, number] {
  const g = f.geometry;
  const ring: number[][] = g.type === 'MultiPolygon' ? g.coordinates[0][0] : g.coordinates[0];
  let x = 0,
    y = 0;
  const n = ring.length > 1 ? ring.length - 1 : ring.length;
  for (let i = 0; i < n; i++) {
    x += ring[i][0];
    y += ring[i][1];
  }
  return [x / n, y / n];
}

export function featureBBox(f: Feature<any>): Bounds {
  const g = f.geometry;
  const polys: number[][][][] = g.type === 'MultiPolygon' ? g.coordinates : [g.coordinates];
  let b: Bounds = [180, 90, -180, -90];
  for (const p of polys)
    for (const pt of p[0]) b = [Math.min(b[0], pt[0]), Math.min(b[1], pt[1]), Math.max(b[2], pt[0]), Math.max(b[3], pt[1])];
  return b;
}

function positionAt(d: { path: [number, number][]; ts: number[] }, h: number): [number, number] {
  const ts = d.ts;
  if (h <= ts[0]) return d.path[0];
  for (let i = 1; i < ts.length; i++) {
    if (h <= ts[i]) {
      const k = (h - ts[i - 1]) / (ts[i] - ts[i - 1] || 1);
      const a = d.path[i - 1],
        b = d.path[i];
      return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k];
    }
  }
  return d.path[d.path.length - 1];
}

const toTrips = (particles: { id: number; path: [number, number, number][] }[]): Trip[] =>
  (particles ?? [])
    .filter((p) => p.path?.length)
    .map((p) => ({
      id: p.id,
      path: p.path.map((q) => [q[0], q[1]] as [number, number]),
      ts: p.path.map((q) => q[2]),
    }));

export function prepareDrift(d: DriftFile): PreparedDrift {
  const trips = toTrips(d.particles);
  const ensemble = (Array.isArray(d.ensemble) ? d.ensemble : [])
    .filter((m) => m && Array.isArray(m.particles) && m.particles.length)
    .map((m) => ({ wdf: m.wind_drift_factor, trips: toTrips(m.particles) }));
  return { trips, starts: trips.map((t) => t.path[0]), ensemble };
}
