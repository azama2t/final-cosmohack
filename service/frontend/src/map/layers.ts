import { BitmapLayer, GeoJsonLayer, PathLayer, ScatterplotLayer } from '@deck.gl/layers';
import type { Layer, PickingInfo } from '@deck.gl/core';
import type { Bounds, DetProps, Feature, FC, H3Props, Layers, Region } from '../types';
import { ACCENT_RGB, h3Color, h3Elevation, type H3Scale } from '../lib/style';

type GeoModule = typeof import('@deck.gl/geo-layers');
let geo: GeoModule | null = null;
let geoPromise: Promise<GeoModule> | null = null;
/** Lazy chunk with H3HexagonLayer and TripsLayer — loaded only when H3 or drift is switched on. */
export function loadGeo(): Promise<GeoModule> {
  if (!geoPromise) geoPromise = import('@deck.gl/geo-layers').then((m) => (geo = m));
  return geoPromise;
}
export const geoLoaded = () => geo !== null;

export interface PreparedDrift {
  trips: { id: number; path: [number, number][]; ts: number[] }[];
  starts: [number, number][];
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
  h3: FC<H3Props> | null;
  h3Scale: H3Scale;
  drift: PreparedDrift | null;
  hour: number;
  layers: Layers;
  selectedId: string | null;
  onHover: (h: HoverInfo | null) => void;
  onClickDet: (f: Feature<DetProps>) => void;
  onClickRegion: (id: string) => void;
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
    out.push(
      new H3HexagonLayer<Feature<H3Props>>({
        id: 'h3',
        data: c.h3.features,
        getHexagon: (f) => f.properties.h3,
        getFillColor: (f) => h3Color(f.properties.share_permille, c.h3Scale) as any,
        extruded,
        getElevation: (f) => h3Elevation(f.properties.share_permille),
        elevationScale: 1,
        coverage: extruded ? 0.86 : 1,
        stroked: !extruded,
        getLineColor: [170, 200, 240, 38],
        lineWidthUnits: 'pixels',
        getLineWidth: 1,
        material: { ambient: 0.55, diffuse: 0.55, shininess: 24, specularColor: [60, 60, 60] } as any,
        pickable: true,
        autoHighlight: true,
        highlightColor: [255, 255, 255, 70],
        transitions: { getElevation: { duration: 900 } } as any,
        onHover: (info: PickingInfo) =>
          c.onHover(info.object ? { x: info.x, y: info.y, kind: 'h3', props: (info.object as any).properties } : null),
        updateTriggers: { getFillColor: [c.h3, c.h3Scale], getElevation: c.h3 },
      }) as any,
    );
  }

  if (L.detections && c.detections) {
    out.push(
      new GeoJsonLayer<DetProps>({
        id: 'detections',
        data: c.detections as any,
        stroked: true,
        filled: true,
        getFillColor: [...ACCENT_RGB, 150],
        getLineColor: [255, 190, 170, 255],
        lineWidthUnits: 'pixels',
        getLineWidth: 1.5,
        lineWidthMinPixels: 1,
        pickable: true,
        autoHighlight: true,
        highlightColor: [255, 236, 214, 230],
        parameters: { depthTest: false } as any,
        onHover: (info: PickingInfo) =>
          c.onHover(info.object ? { x: info.x, y: info.y, kind: 'det', props: (info.object as any).properties } : null),
        onClick: (info: PickingInfo) => {
          if (info.object) c.onClickDet(info.object as any);
        },
      }),
    );
    // glow halo around each spot so that tiny spots are visible at region zoom (skip when spots are dense)
    if (c.detections.features.length <= 150)
      out.push(
      new ScatterplotLayer<Feature<DetProps>>({
        id: 'detections-halo',
        data: c.detections.features,
        getPosition: (f) => centroid(f),
        getRadius: (f) => Math.max(60, Math.sqrt(f.properties.area_m2) * 1.2),
        radiusUnits: 'meters',
        radiusMinPixels: 10,
        radiusMaxPixels: 40,
        stroked: true,
        filled: true,
        getFillColor: [...ACCENT_RGB, 26],
        getLineColor: [...ACCENT_RGB, 210],
        lineWidthUnits: 'pixels',
        getLineWidth: 1.5,
        parameters: { depthTest: false } as any,
      }),
    );
    if (c.selectedId) {
      const sel = c.detections.features.find((f) => f.properties.id === c.selectedId);
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

export function prepareDrift(particles: { id: number; path: [number, number, number][] }[]): PreparedDrift {
  const trips = particles.map((p) => ({
    id: p.id,
    path: p.path.map((q) => [q[0], q[1]] as [number, number]),
    ts: p.path.map((q) => q[2]),
  }));
  return { trips, starts: trips.map((t) => t.path[0]) };
}
