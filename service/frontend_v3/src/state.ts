// Client-side state that survives a reload: added sites (with work status) and UI prefs. localStorage in try/catch.
import type { BBox } from './geo';
import type { Cam, Filters } from './map';

export type WorkStatus = 'new' | 'work' | 'done';
export const STATUS_LABEL: Record<WorkStatus, string> = { new: 'Новый', work: 'В работе', done: 'Проверен' };

export interface Site {
  id: string;
  name: string;
  kind: 'obs' | 'zone' | 'scene' | 'oil' | 'area';
  /** what was clicked (obs sample_id / zone_id), null for an outlined area */
  ref: string | null;
  ring: number[][];
  bbox: BBox;
  obsIds: string[];
  zoneIds: string[];
  status: WorkStatus;
  created: string;
}

export type Section = 'sites' | 'studio' | 'layers' | 'export';

export interface UiPrefs {
  collapsed: boolean;
  section: Section;
  filters: Filters;
  cam: Cam | null;
  active: string | null;
}

const K_SITES = 'mp3.sites';
const K_UI = 'mp3.ui';

export const DEFAULT_FILTERS: Filters = { from: null, to: null, sources: null, obs: true, zones: true, scenes: true };

function read<T>(k: string, dflt: T): T {
  try {
    const s = localStorage.getItem(k);
    return s ? { ...dflt, ...JSON.parse(s) } : dflt;
  } catch {
    return dflt;
  }
}
function write(k: string, v: unknown) {
  try {
    localStorage.setItem(k, JSON.stringify(v));
  } catch {
    /* private mode / quota */
  }
}

export function loadSites(): Site[] {
  try {
    const s = localStorage.getItem(K_SITES);
    const a = s ? JSON.parse(s) : [];
    return Array.isArray(a) ? a.filter((x) => x && x.id && Array.isArray(x.ring) && Array.isArray(x.bbox)) : [];
  } catch {
    return [];
  }
}
export const saveSites = (s: Site[]) => write(K_SITES, s);

export function loadUi(): UiPrefs {
  const qp = new URLSearchParams(location.search);
  const u = read<UiPrefs>(K_UI, { collapsed: false, section: 'sites', filters: DEFAULT_FILTERS, cam: null, active: null });
  u.filters = { ...DEFAULT_FILTERS, ...(u.filters ?? {}) };
  // ?fresh=1 — a clean first screen (screenshots / perf runs)
  if (qp.get('fresh') === '1') return { collapsed: false, section: 'sites', filters: DEFAULT_FILTERS, cam: null, active: null };
  return u;
}
export const saveUi = (u: UiPrefs) => write(K_UI, u);

export const bboxRing = (b: BBox): number[][] => [
  [b[0], b[1]],
  [b[2], b[1]],
  [b[2], b[3]],
  [b[0], b[3]],
  [b[0], b[1]],
];

export const newId = () => 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5);
