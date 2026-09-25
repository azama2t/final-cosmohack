// Data contracts — mirror docs/CONTRACTS.md
export type Bounds = [number, number, number, number]; // w, s, e, n
export type LonLat = [number, number];

export interface IndexInfo {
  name: string;
  unit: string;
  h3_res: number;
  formula: string;
  note: string;
}

export interface ModelInfo {
  name: string;
  threshold: number;
  url?: string;
  license?: string;
  note?: string;
}

export interface DateEntry {
  date: string;
  scene_id: string;
  source: string;
  cloud_frac: number | null;
  bounds: Bounds;
  rgb: string;
  thumb?: string;
  models: string[];
  drift: string | null;
  /** optional (newer data): scene quality flags */
  quality?: DateQuality;
  /** optional (L15, dates with >= 2 models): detections confirmed by the other model within 20 m, per model */
  n_confirmed?: Record<string, number>;
}

export interface DateQuality {
  glint_or_haze?: boolean;
  haze?: boolean;
  note?: string;
}

export interface RegionSummary {
  latest_date: string;
  index_permille: number | null;
  n_detections: number;
  total_debris_area_m2: number;
  haze?: boolean;
}

export interface Region {
  id: string;
  name: string;
  country?: string;
  tile?: string;
  center: LonLat;
  bounds: Bounds;
  zoom?: number;
  summary: RegionSummary;
  dates: DateEntry[];
}

export interface Source {
  name: string;
  url?: string;
  license?: string;
}

export interface Manifest {
  version: number;
  generated: string;
  kind: string;
  index: IndexInfo;
  models: Record<string, ModelInfo>;
  regions: Region[];
  sources: Source[];
}

export interface DetProps {
  id: string;
  region: string;
  date: string;
  area_m2: number;
  mean_prob: number;
  max_prob: number;
  model: string;
  /** optional (L15): the other model has a pixel >= its threshold within 2 px (20 m) of this object */
  confirmed?: boolean;
  /** optional (L15): id of the confirming model, null if not confirmed */
  confirmed_by?: string | null;
}

export interface H3Props {
  h3: string;
  res: number;
  date: string;
  flagged_water_px: number;
  observed_water_px: number;
  observed_frac: number;
  share_permille: number | null;
  n_detections: number;
  threshold: number;
  model: string;
}

export interface Feature<P> {
  type: 'Feature';
  id?: string | number;
  geometry: { type: string; coordinates: any };
  properties: P;
}

export interface FC<P> {
  type: 'FeatureCollection';
  features: Feature<P>[];
}

export interface Zone {
  rank: number;
  h3: string;
  index: number | null;
  area_m2: number;
  reason: string;
  lon: number;
  lat: number;
  repeat_dates?: number;
  mean_prob?: number;
  /** optional (L15): confirmed detections in this cell */
  n_confirmed?: number;
  /** optional (newer zones.json): terms of the ranking formula */
  flagged_water_px?: number;
  observed_frac?: number;
  n_detections?: number;
  score?: number;
}

export interface ZonesFile {
  region: string;
  date: string;
  model: string;
  threshold: number;
  quality?: DateQuality;
  zones: Zone[];
}

export interface DriftParticle {
  id: number;
  path: [number, number, number][];
}

export interface DriftFile {
  region: string;
  date: string;
  start_time: string;
  hours: number[];
  particles: DriftParticle[];
  forcing: { currents?: string; wind?: string; wind_drift_factor?: number; model?: string };
  note?: string;
  /** optional: extra runs with other wind drift factors (uncertainty range) */
  ensemble?: { wind_drift_factor: number; particles: DriftParticle[] }[];
}

export interface TsRow {
  date: string;
  model: string;
  total_debris_area_m2: number;
  mean_index: number | null;
  n_detections: number;
  cloud_frac: number | null;
}

export type LayerKey = 'rgb' | 'prob' | 'detections' | 'h3' | 'h3_3d' | 'zones' | 'drift';
export type Layers = Record<LayerKey, boolean>;
export type Basemap = 'dark' | 'satellite' | 'none';

export interface Camera {
  lon: number;
  lat: number;
  zoom: number;
  pitch: number;
  bearing: number;
}

export interface SceneRef {
  region: string;
  date: string;
}
