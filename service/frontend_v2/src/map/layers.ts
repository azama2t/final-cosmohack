import { BitmapLayer, GeoJsonLayer, PathLayer, ScatterplotLayer } from '@deck.gl/layers';
import type { Layer, PickingInfo } from '@deck.gl/core';
import type { Bounds, DetProps, Feature, FC, H3Props, Layers, Region } from '../types';
import { ACCENT_RGB, h3Color, h3Elevation, h3LineColor, type H3Scale } from '../lib/style';
import type { DriftFile } from '../types';
import type { RoutePlan } from '../lib/route';

type GeoModule = typeof import('@deck.gl/geo-layers');
let geo: GeoModule | null = null;
let geoPromise: Promise<GeoModule> | null = null;
/** Lazy chunk with H3HexagonLayer and TripsLayer — loaded only when H3 or drift is on. */
export function loadGeo(): Promise<GeoModule> {
  if (!geoPromise) geoPromise = import('@deck.gl/geo-layers').then((m) => (geo = m));
  return geoPromise;
}
export const geoLoaded = () => geo !== null;

type Trip = { id: number; path: [number, number][]; ts: number[] };
export interface PreparedDrift {
  trips: Trip[];
  starts: [number, number][];
  ensemble: { wdf: number; trips: Trip[] }[];
}

export interface HoverInfo {
  x: number;
  y: number;
  kind: 'det' | 'h3' | 'osm' | 'source';
  props: any;
}

/** Drift forecast check (pair t1 → t2): particle fan at t2, its contour, findings of t2 marked hit / miss. */
export interface CheckOverlay {
  fan: [number, number][];
  contour: any | null;
  targets: { lon: number; lat: number; hit: boolean; id?: string }[];
}

export interface LayerCtx {
  regions: Region[];
  activeRegion: string | null;
  bounds: Bounds | null;
  rgbImg: HTMLImageElement | null;
  probImg: HTMLImageElement | null;
  detections: FC<DetProps> | null;
  artifacts?: FC<DetProps> | null;
  h3: FC<H3Props> | null;
  h3Scale: H3Scale;
  h3Max: number;
  zoom: number;
  hoverId: string | null;
  drift: PreparedDrift | null;
  hour: number;
  spread: boolean;
  layers: Layers;
  selectedId: string | null;
  onHover: (h: HoverInfo | null) => void;
  onClickDet: (f: Feature<DetProps>) => void;
  onClickRegion: (id: string) => void;
  onClickH3?: (p: H3Props) => void;
  route?: RoutePlan | null;
  osm?: FC<any> | null;
  sources?: FC<any> | null;
  check?: CheckOverlay | null;
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

const NO_DEPTH = { depthTest: false } as any;
const WHITE: [number, number, number] = [236, 238, 240];
const NEUTRAL: [number, number, number] = [168, 176, 186];

export function buildLayers(c: LayerCtx): Layer[] {
  const out: Layer[] = [];
  const L = c.layers;

  // region footprints: thin neutral outline on the overview; none for the active region
  out.push(
    new GeoJsonLayer({
      id: 'region-footprints',
      data: {
        type: 'FeatureCollection',
        features: c.regions
          .filter((r) => r.id !== c.activeRegion)
          .map((r) => ({ type: 'Feature', geometry: { type: 'Polygon', coordinates: boundsPoly(r.bounds) }, properties: { id: r.id } })),
      } as any,
      stroked: true,
      filled: true,
      getFillColor: [236, 238, 240, 8],
      getLineColor: [236, 238, 240, 80],
      lineWidthUnits: 'pixels',
      getLineWidth: 1,
      pickable: !c.activeRegion,
      onClick: (info: PickingInfo) => {
        const id = (info.object as any)?.properties?.id;
        if (id) c.onClickRegion(id);
      },
    }),
  );

  if (c.bounds && L.rgb && c.rgbImg)
    out.push(
      new BitmapLayer({
        id: 'rgb',
        image: c.rgbImg as any,
        bounds: c.bounds as any,
        textureParameters: { minFilter: 'linear', magFilter: 'linear' } as any,
      }),
    );
  if (c.bounds && L.prob && c.probImg)
    out.push(
      new BitmapLayer({
        id: 'prob',
        image: c.probImg as any,
        bounds: c.bounds as any,
        opacity: 0.9,
        textureParameters: { minFilter: 'linear', magFilter: 'nearest' } as any,
      }),
    );

  if (L.h3 && c.h3 && geo) {
    const H3HexagonLayer = geo.H3HexagonLayer;
    const extruded = L.h3_3d;
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
        coverage: extruded ? 0.86 : 1,
        stroked: !extruded,
        filled: true,
        getLineColor: (f) => h3LineColor(f.properties.share_permille) as any,
        lineWidthUnits: 'pixels',
        getLineWidth: 1,
        material: { ambient: 0.62, diffuse: 0.5, shininess: 12, specularColor: [40, 40, 40] } as any,
        pickable: true,
        autoHighlight: true,
        highlightColor: [255, 255, 255, 50],
        transitions: { getElevation: { duration: 200 } } as any,
        onHover: (info: PickingInfo) =>
          c.onHover(info.object ? { x: info.x, y: info.y, kind: 'h3', props: (info.object as any).properties } : null),
        onClick: (info: PickingInfo) => {
          const o = info.object as any;
          if (o && c.onClickH3) c.onClickH3(o.properties);
        },
        updateTriggers: { getFillColor: [c.h3, c.h3Scale], getElevation: [c.h3, vmax], getLineColor: c.h3 },
      }) as any,
    );
  }

  // OSM objects (farms, beaches, ports, protected areas) — neutral, thin; demo layer
  if (L.osm && c.osm?.features?.length) {
    out.push(
      new GeoJsonLayer({
        id: 'osm',
        data: c.osm as any,
        stroked: true,
        filled: true,
        pointType: 'circle',
        getFillColor: (f: any) => (f.properties?.threat ? [236, 238, 240, 36] : [168, 176, 186, 20]),
        getLineColor: (f: any) => (f.properties?.threat ? [236, 238, 240, 220] : [168, 176, 186, 150]),
        lineWidthUnits: 'pixels',
        getLineWidth: 1,
        getPointRadius: 4,
        pointRadiusUnits: 'pixels',
        pickable: true,
        onHover: (info: PickingInfo) =>
          c.onHover(info.object ? { x: info.x, y: info.y, kind: 'osm', props: (info.object as any).properties } : null),
        parameters: NO_DEPTH,
      }),
    );
  }

  // excluded artifacts — grey outline, no halo
  if (L.detections && L.artifacts && c.artifacts?.features.length) {
    const hot = c.hoverId ?? c.selectedId;
    const fadeA = Math.min(1, Math.max(0, (14.6 - c.zoom) / 1.8));
    const onHover = (info: PickingInfo) => {
      const o = info.object as any;
      c.onHover(o ? { x: info.x, y: info.y, kind: 'det', props: o.properties } : null);
    };
    const onClick = (info: PickingInfo) => {
      if (info.object) c.onClickDet(info.object as any);
    };
    if (fadeA > 0.05)
      out.push(
        new ScatterplotLayer<Feature<DetProps>>({
          id: 'artifacts-ring',
          data: c.artifacts.features,
          getPosition: (f) => centroid(f),
          getRadius: (f) => (f.properties.id === hot ? 7 : 5),
          radiusUnits: 'pixels',
          stroked: true,
          filled: true,
          getFillColor: [0, 0, 0, 1],
          getLineColor: (f) => (f.properties.id === hot ? [...WHITE, 255] : [...NEUTRAL, Math.round(200 * fadeA)]) as any,
          lineWidthUnits: 'pixels',
          getLineWidth: 1,
          pickable: fadeA > 0.3,
          onHover,
          onClick,
          parameters: NO_DEPTH,
          updateTriggers: { getLineColor: [fadeA, hot], getRadius: hot },
        }),
      );
    out.push(
      new GeoJsonLayer<DetProps>({
        id: 'artifacts',
        data: c.artifacts as any,
        stroked: true,
        filled: true,
        getFillColor: [...NEUTRAL, 26],
        getLineColor: [...NEUTRAL, 220],
        lineWidthUnits: 'pixels',
        getLineWidth: 1,
        pickable: true,
        parameters: NO_DEPTH,
        onHover,
        onClick,
      }),
    );
  }

  if (L.detections && c.detections) {
    const feats = c.detections.features;
    const onHover = (info: PickingInfo) => {
      const o = info.object as any;
      c.onHover(o ? { x: info.x, y: info.y, kind: 'det', props: o.properties } : null);
    };
    const onClick = (info: PickingInfo) => {
      if (info.object) c.onClickDet(info.object as any);
    };
    // screen-space ring at each spot so that 1–3 px spots are findable at region zoom; fades out at high zoom
    const fade = Math.min(1, Math.max(0.15, (14.6 - c.zoom) / 1.8));
    const ringR = (f: Feature<DetProps>) => Math.min(18, 6 + 0.09 * Math.sqrt(Math.max(0, f.properties.area_m2)));
    const hot = c.hoverId ?? c.selectedId;
    out.push(
      new ScatterplotLayer<Feature<DetProps>>({
        id: 'detections-ring',
        data: feats,
        getPosition: (f) => centroid(f),
        getRadius: (f) => (f.properties.id === hot ? ringR(f) + 3 : ringR(f)),
        radiusUnits: 'pixels',
        stroked: true,
        filled: true,
        getFillColor: (f) => [...ACCENT_RGB, f.properties.id === hot ? Math.round(70 * fade) : Math.round(30 * fade)] as any,
        getLineColor: (f) => (f.properties.id === hot ? [...WHITE, 255] : [...ACCENT_RGB, Math.round(235 * fade)]) as any,
        lineWidthUnits: 'pixels',
        getLineWidth: 1.5,
        pickable: fade > 0.3,
        onHover,
        onClick,
        parameters: NO_DEPTH,
        transitions: { getRadius: 180 } as any,
        updateTriggers: { getFillColor: [fade, hot], getLineColor: [fade, hot], getRadius: hot },
      }),
    );
    out.push(
      new GeoJsonLayer<DetProps>({
        id: 'detections',
        data: c.detections as any,
        stroked: true,
        filled: true,
        getFillColor: (f: any) => (f.properties.id === hot ? [...ACCENT_RGB, 230] : [...ACCENT_RGB, 170]) as any,
        getLineColor: [...ACCENT_RGB, 255],
        lineWidthUnits: 'pixels',
        getLineWidth: 1,
        pickable: true,
        autoHighlight: true,
        highlightColor: [255, 255, 255, 90],
        parameters: NO_DEPTH,
        onHover,
        onClick,
        updateTriggers: { getFillColor: hot },
      }),
    );
    // second-model agreement is shown in the evidence card, not as an extra ring on the map (one mark per finding)
    const conf: Feature<DetProps>[] = [];
    if (conf.length)
      out.push(
        new ScatterplotLayer<Feature<DetProps>>({
          id: 'detections-confirmed',
          data: conf,
          getPosition: (f) => centroid(f),
          getRadius: (f) => (f.properties.id === hot ? ringR(f) + 3 : ringR(f)) + 4,
          radiusUnits: 'pixels',
          stroked: true,
          filled: false,
          getLineColor: [...WHITE, Math.round(190 * Math.max(0.55, fade))] as any,
          lineWidthUnits: 'pixels',
          getLineWidth: 1,
          parameters: NO_DEPTH,
          updateTriggers: { getLineColor: fade, getRadius: hot },
        }),
      );
    if (c.selectedId) {
      const sel = feats.find((f) => f.properties.id === c.selectedId);
      if (sel)
        out.push(
          new GeoJsonLayer({
            id: 'detection-selected',
            data: sel as any,
            stroked: true,
            filled: false,
            getLineColor: [...WHITE, 255],
            lineWidthUnits: 'pixels',
            getLineWidth: 2,
            parameters: NO_DEPTH,
          }),
        );
    }
  }

  // persistent sources: thin white ring
  if (L.sources && c.sources?.features?.length) {
    out.push(
      new GeoJsonLayer({
        id: 'sources',
        data: c.sources as any,
        stroked: true,
        filled: true,
        pointType: 'circle',
        getFillColor: [0, 0, 0, 1],
        getLineColor: [...WHITE, 230],
        getPointRadius: 12,
        pointRadiusUnits: 'pixels',
        lineWidthUnits: 'pixels',
        getLineWidth: 1.5,
        pickable: true,
        onHover: (info: PickingInfo) =>
          c.onHover(info.object ? { x: info.x, y: info.y, kind: 'source', props: (info.object as any).properties } : null),
        parameters: NO_DEPTH,
      }),
    );
  }

  if (L.drift && c.drift && geo) {
    if (c.spread && c.drift.ensemble.length) {
      const pts = c.drift.ensemble.flatMap((m) => m.trips);
      out.push(
        new ScatterplotLayer({
          id: 'drift-spread',
          data: pts,
          getPosition: (d: any) => positionAt(d, c.hour),
          getRadius: 2,
          radiusUnits: 'pixels',
          getFillColor: [...NEUTRAL, 110],
          updateTriggers: { getPosition: c.hour },
          parameters: NO_DEPTH,
        }),
      );
    }
    out.push(
      new PathLayer({
        id: 'drift-ghost',
        data: c.drift.trips,
        getPath: (d: any) => d.path,
        getColor: [...WHITE, 22],
        getWidth: 1,
        widthUnits: 'pixels',
        parameters: NO_DEPTH,
      }),
      new geo.TripsLayer({
        id: 'drift-trips',
        data: c.drift.trips,
        getPath: (d: any) => d.path,
        getTimestamps: (d: any) => d.ts,
        getColor: WHITE,
        opacity: 0.8,
        widthUnits: 'pixels',
        getWidth: 1.5,
        capRounded: true,
        jointRounded: true,
        trailLength: 10,
        fadeTrail: true,
        currentTime: c.hour,
        parameters: NO_DEPTH,
      }) as any,
      new ScatterplotLayer({
        id: 'drift-heads',
        data: c.drift.trips,
        getPosition: (d: any) => positionAt(d, c.hour),
        getRadius: 2.2,
        radiusUnits: 'pixels',
        getFillColor: [...WHITE, 240],
        updateTriggers: { getPosition: c.hour },
        parameters: NO_DEPTH,
      }),
    );
  }

  // drift forecast check: fan at t2 (neutral dots), its contour (thin white line), findings of t2 (hit = filled)
  if (c.check) {
    if (c.check.contour)
      out.push(
        new GeoJsonLayer({
          id: 'check-contour',
          data: c.check.contour,
          stroked: true,
          filled: true,
          getFillColor: [...WHITE, 14],
          getLineColor: [...WHITE, 200],
          lineWidthUnits: 'pixels',
          getLineWidth: 1,
          parameters: NO_DEPTH,
        }),
      );
    out.push(
      new ScatterplotLayer({
        id: 'check-fan',
        data: c.check.fan,
        getPosition: (d: any) => d,
        getRadius: 1.6,
        radiusUnits: 'pixels',
        getFillColor: [...NEUTRAL, 150],
        parameters: NO_DEPTH,
      }),
      new ScatterplotLayer({
        id: 'check-targets',
        data: c.check.targets,
        getPosition: (d: any) => [d.lon, d.lat],
        getRadius: 7,
        radiusUnits: 'pixels',
        stroked: true,
        filled: true,
        getFillColor: (d: any) => (d.hit ? [...ACCENT_RGB, 220] : [0, 0, 0, 0]) as any,
        getLineColor: [...ACCENT_RGB, 255],
        lineWidthUnits: 'pixels',
        getLineWidth: 1.5,
        parameters: NO_DEPTH,
      }),
    );
  }

  if (c.route && c.route.path.length > 1)
    out.push(
      new PathLayer({
        id: 'route',
        data: [{ path: c.route.path }],
        getPath: (d: any) => d.path,
        getColor: [...WHITE, 200],
        getWidth: 1.5,
        widthUnits: 'pixels',
        capRounded: true,
        jointRounded: true,
      }),
    );
  return out;
}

export function centroid(f: Feature<any>): [number, number] {
  const g = f.geometry;
  const ring: number[][] = g.type === 'MultiPolygon' ? g.coordinates[0][0] : g.type === 'Point' ? [g.coordinates] : g.coordinates[0];
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

export function positionAt(d: { path: [number, number][]; ts: number[] }, h: number): [number, number] {
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
    .map((p) => ({ id: p.id, path: p.path.map((q) => [q[0], q[1]] as [number, number]), ts: p.path.map((q) => q[2]) }));

export function prepareDrift(d: DriftFile): PreparedDrift {
  const trips = toTrips(d.particles);
  const ensemble = (Array.isArray(d.ensemble) ? d.ensemble : [])
    .filter((m) => m && Array.isArray(m.particles) && m.particles.length)
    .map((m) => ({ wdf: m.wind_drift_factor, trips: toTrips(m.particles) }));
  return { trips, starts: trips.map((t) => t.path[0]), ensemble };
}
