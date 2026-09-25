import type { Basemap, LayerKey, Layers, Projection } from '../types';

/**
 * The user's own choices (basemap, projection, model, light layers), kept in localStorage.
 * Written ONLY on an explicit user action (menu click); automatic states — the temporary offline fallback,
 * a model missing on some date, the layers a view turns on — never overwrite them.
 * localStorage may be unavailable (private mode, file://): every access is in try/catch.
 */
export interface Prefs {
  basemap?: Basemap;
  projection?: Projection;
  model?: string;
  layers?: Partial<Layers>;
}

const KEY = 'mp.v2.prefs';
/** Light layers worth remembering. Heavy ones (drift, particles, H3 3D) are on-demand only and never restored. */
export const PERSIST_LAYERS: LayerKey[] = ['rgb', 'prob', 'h3', 'artifacts', 'osm'];

export function loadPrefs(): Prefs {
  try {
    const s = JSON.parse(localStorage.getItem(KEY) || '{}');
    const p: Prefs = {};
    if (s.basemap === 'dark' || s.basemap === 'satellite' || s.basemap === 'none') p.basemap = s.basemap;
    if (s.projection === 'globe' || s.projection === 'mercator') p.projection = s.projection;
    if (typeof s.model === 'string' && s.model) p.model = s.model;
    if (s.layers && typeof s.layers === 'object') {
      const l: Partial<Layers> = {};
      for (const k of PERSIST_LAYERS) if (typeof s.layers[k] === 'boolean') l[k] = s.layers[k];
      p.layers = l;
    }
    return p;
  } catch {
    return {};
  }
}

export function savePrefs(patch: Prefs) {
  try {
    const cur = loadPrefs();
    const next: Prefs = { ...cur, ...patch };
    if (patch.layers) next.layers = { ...(cur.layers ?? {}), ...patch.layers };
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable: the URL still carries the state */
  }
}

export function clearPrefs() {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
