// Formatting and dictionary lookups for the case mode. Labels and colours come from /meta; the few strings here
// are unit names and translations of raw registry notes that the API does not label yet.
import type { Labeled, Meta, Pair } from './api3';

const nfCache = new Map<number, Intl.NumberFormat>();
const nf = (d: number) => {
  if (!nfCache.has(d)) nfCache.set(d, new Intl.NumberFormat('ru-RU', { maximumFractionDigits: d, minimumFractionDigits: 0 }));
  return nfCache.get(d)!;
};

/** null → «—» (never 0) */
export function num(v: number | null | undefined, digits?: number): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const d = digits ?? (Math.abs(v) >= 100 ? 0 : Math.abs(v) >= 10 ? 1 : Math.abs(v) >= 1 ? 2 : 3);
  return nf(d).format(v);
}
export const signed = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? '—' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${num(Math.abs(v), d)}`);
export const pct = (f: number | null | undefined) => (f === null || f === undefined ? '—' : `${nf(0).format(f * 100)} %`);

export const UNIT_RU: Record<string, string> = { items_km2: 'шт./км²', 'items/km2': 'шт./км²', 'g/km2': 'г/км²', km2: 'км²', km: 'км', hours: 'ч' };
export const unitRu = (u: string | null | undefined) => (u ? UNIT_RU[u] ?? u : '');

export function dateRu(s: string | null | undefined): string {
  if (!s) return '—';
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  return m ? `${m[3]}.${m[2]}.${m[1]}` : s;
}
export function dateTimeRu(s: string | null | undefined): string {
  if (!s) return '—';
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
  return m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]} UTC` : dateRu(s);
}

export function label(list: Labeled[] | undefined, id: string | null | undefined): string {
  if (!id) return '—';
  return list?.find((x) => x.id === id)?.label ?? id;
}
export function color(list: Labeled[] | undefined, id: string | null | undefined, fallback = '#868e96'): string {
  const c = list?.find((x) => x.id === id)?.color;
  return c ? c.slice(0, 7) : fallback;
}

/** scope label; all_litter is ALWAYS «весь мусор (не только пластик)» */
export function scopeRu(meta: Meta, scope: string | null | undefined): string {
  if (scope === 'all_litter') return 'весь мусор (не только пластик)';
  const l = label(meta.target_scopes, scope);
  return l.charAt(0).toLowerCase() + l.slice(1);
}

export function profileRu(meta: Meta, id: string | null | undefined): string {
  if (!id) return 'профиль не указан';
  return label(meta.measurement_profiles, id);
}

export function sourceShort(meta: Meta, id: string | null | undefined): string {
  return label(meta.sources, id);
}

// raw registry notes (registry_note) that have no reject code yet → short Russian text
const NOTE_RU: Record<string, string> = {
  sync_unreliable_drift: 'дрейф за Δt больше допуска',
  'cloud_cover>60': 'облачность сцены > 60 %',
  'dt>1d': 'разрыв во времени > 1 сут',
  mission_not_launched: 'миссия ещё не запущена',
  no_scene_in_window: 'нет снимка в окне',
  point_outside_footprint: 'точка вне снимка',
  'mission_not_target(L7)': 'Landsat-7 не используется',
};

export function pairReasons(meta: Meta, p: Pair): string[] {
  const out = p.reject_reasons.map((r) => label(meta.reject_reasons, r));
  const notes = (p.registry_note ?? '').split(';').filter(Boolean);
  // the drift rule has no code in the API yet — show it from the registry note
  for (const n of notes) if (n === 'sync_unreliable_drift' && !out.length) out.push(NOTE_RU[n]);
  if (p.quality_decision === 'reject' && !out.length) out.push('маска качества: непригодно');
  if (!out.length && p.status === 'rejected') out.push(notes.map((n) => NOTE_RU[n] ?? n).join(', ') || 'причина не указана');
  return out;
}

/** Δt with the uncertainty of an unknown observation time (S4: date only → ±12 h) */
export function dtTxt(p: Pair & { dt_uncertainty_h?: number | null }): string {
  const d = signed(p.dt_hours);
  if (p.time_known === false) return `${d} ±${num(p.dt_uncertainty_h ?? 12, 0)}`;
  return d;
}
export const dtTitle = (p: Pair & { dt_uncertainty_h?: number | null }) =>
  p.time_known === false ? `время наблюдения неизвестно, ±${num(p.dt_uncertainty_h ?? 12, 0)} ч` : undefined;

export function pairDecision(p: Pair): string {
  return p.status === 'accepted' ? 'Принята' : 'Отклонена';
}

export const shortId = (s: string | null | undefined, n = 28) => (!s ? '—' : s.length > n ? s.slice(0, n - 1) + '…' : s);

/** «S3:HE419_MarLitter_transect29» → «HE419 · трансекта 29» */
export function eventRu(ev: string | null | undefined): string {
  if (!ev) return '—';
  const s = ev.replace(/^S\d:/, '');
  const m = s.match(/^(.*?)_?MarLitter_transect(\d+)$/);
  if (m) return `${m[1]} · трансекта ${m[2]}`;
  const d = s.match(/^(DOORS\d):(.+)$/);
  if (d) return `${d[1]} · трансекта ${d[2]}`;
  const g = s.match(/^MSM41_litter-(T\d+)$/);
  if (g) return `MSM41 · трансекта ${g[1]}`;
  if (/^S1:/.test(ev)) return `станция ${s}`;
  return s.replace(/_/g, ' ');
}

export function plural(n: number, one: string, few: string, many: string): string {
  const a = Math.abs(n) % 100,
    b = a % 10;
  const w = a > 10 && a < 20 ? many : b === 1 ? one : b >= 2 && b <= 4 ? few : many;
  return `${n} ${w}`;
}

export const missionShort = (m: string | null | undefined) => (!m ? '—' : m.replace('Sentinel-', 'S').replace('Landsat-', 'L'));

// ---- quality flags of the organisers' CSV (README_macroplastic_dataset.md) → one short line; raw codes go to «i»
const FLAG_RU: Record<string, string> = {
  all_litter_not_plastic: 'весь мусор, не только пластик',
  object_time_is_parent_interval: 'время — интервал события',
  author_area_replaces_endpoint_distance: 'площадь авторская',
  start_coordinate_corrected_to_midpoint: 'координата — середина трансекты',
  source_object_row_not_reconstructed: 'объектная запись не сверена',
  source_total_vs_object_count_conflict: 'сумма и объекты расходятся',
  category_dictionary_corrected: 'категория по словарю источника',
  companion_count_differs_sum_float_items: 'правило числителя задано',
  zero_only_for_defined_scope: 'ноль только для своей категории',
  numerator_area_unavailable: 'нет N или площади для проверки',
  source_not_revalidated_in_repair: 'проверка происхождения ограничена',
  object_row_match_ambiguous_or_unresolved: 'сопоставление неоднозначно',
  size_filter_corrected: 'размер по фильтру источника',
  interrupted_transect_summary_area: 'площадь прерванной трансекты',
  summary_area_rounded_differs_from_segment_sum: 'площадь округлена',
};
export const flagRu = (f: string) => FLAG_RU[f] ?? f.replace(/_/g, ' ');

/** «3,5 / 3,0» — drift shift over Δt vs tolerance, km */
export function driftTxt(p: { drift_shift_km?: number | null; tolerance_km?: number | null }): string {
  if (p.drift_shift_km === null || p.drift_shift_km === undefined) return '—';
  return p.tolerance_km === null || p.tolerance_km === undefined ? num(p.drift_shift_km, 1) : `${num(p.drift_shift_km, 1)} / ${num(p.tolerance_km, 1)}`;
}
export function driftTitle(p: { drift_scenarios_km?: Record<string, number> | null; dt_drift_hours?: number | null }): string | undefined {
  const s = p.drift_scenarios_km;
  if (!s) return undefined;
  return `дрейф за ${num(p.dt_drift_hours ?? null, 1)} ч: низкий ${num(s.low ?? null, 1)} · типичный ${num(s.typical ?? null, 1)} · высокий ${num(s.high ?? null, 1)} км`;
}

// ---- exact (Garwood) 95 % Poisson interval for N items on A km² — fallback when the API has no ci95
function gammaln(x: number): number {
  const c = [76.18009172947146, -86.50532032941677, 24.01409824083091, -1.231739572450155, 0.1208650973866179e-2, -0.5395239384953e-5];
  let y = x;
  const t = x + 5.5 - (x + 0.5) * Math.log(x + 5.5);
  let s = 1.000000000190015;
  for (const v of c) s += v / ++y;
  return -t + Math.log((2.5066282746310005 * s) / x);
}
function gammaP(a: number, x: number): number {
  if (x <= 0) return 0;
  if (x < a + 1) {
    let sum = 1 / a,
      del = sum,
      ap = a;
    for (let n = 0; n < 500; n++) {
      del *= x / ++ap;
      sum += del;
      if (Math.abs(del) < Math.abs(sum) * 1e-12) break;
    }
    return sum * Math.exp(-x + a * Math.log(x) - gammaln(a));
  }
  let b = x + 1 - a,
    c = 1e300,
    d = 1 / b,
    h = d;
  for (let i = 1; i < 500; i++) {
    const an = -i * (i - a);
    b += 2;
    d = an * d + b;
    if (Math.abs(d) < 1e-300) d = 1e-300;
    c = b + an / c;
    if (Math.abs(c) < 1e-300) c = 1e-300;
    d = 1 / d;
    const del = d * c;
    h *= del;
    if (Math.abs(del - 1) < 1e-12) break;
  }
  return 1 - Math.exp(-x + a * Math.log(x) - gammaln(a)) * h;
}
function qgamma(p: number, a: number): number {
  let lo = 0,
    hi = a + 20 * Math.sqrt(a) + 50;
  for (let i = 0; i < 200; i++) {
    const m = (lo + hi) / 2;
    if (gammaP(a, m) < p) lo = m;
    else hi = m;
  }
  return (lo + hi) / 2;
}
export function poissonCI(n: number, areaKm2: number): [number, number] {
  const lo = n <= 0 ? 0 : qgamma(0.025, n);
  const hi = qgamma(0.975, n + 1);
  return [lo / areaKm2, hi / areaKm2];
}

/** first clause of an API reason without raw codes («cloud(qa_suspect:…)», «(DRIFT_TOO_LARGE)»); the raw text goes to «i» */
export function reasonRu(s: string | null | undefined): string | null {
  if (!s) return null;
  return s
    .split(';')[0]
    .replace(/cloud\([^)]*\)/g, 'облачность (маска QA)')
    .replace(/glint\([^)]*\)/g, 'блики')
    .replace(/\s*\([A-Z_, ]+\)/g, '')
    .trim();
}
const ZFLAG_RU: Record<string, string> = { pair_rejected_drift: 'пара отклонена по дрейфу' };
export const zoneFlagRu = (f: string) => ZFLAG_RU[f] ?? f.replace(/_/g, ' ');

/** concentration models / profile configs of the API → Russian (jury-6: no raw ids in the UI) */
export function modelRu(m: string | null | undefined): string {
  if (!m) return 'модель';
  if (/^median/.test(m)) return 'медиана профиля';
  if (/^ridge/.test(m)) return 'гребневая регрессия (лог.)';
  const k = m.match(/^knn(\d*)/);
  if (k) return `k ближайших соседей${k[1] ? ` (k = ${k[1]})` : ''}${/log/.test(m) ? ' (лог.)' : ''}`;
  if (/^gbm|lgbm|lightgbm/i.test(m)) return 'градиентный бустинг';
  return m.replace(/_/g, ' ');
}
const PCFG_RU: Record<string, string> = {
  S2_visual_total_plastic: 'Саргассово море: визуально с судна, весь пластик > 2 см',
  S1_trawl_total_plastic: 'Тихоокеанское мусорное пятно: трал, весь пластик',
  S3_visual_all_litter: 'Северное море: визуально с судна, весь мусор > 2 см',
  S4_visual_all_litter: 'Чёрное море: визуально с судна, весь мусор > 2,5 см',
};
export const profileCfgRu = (id: string) => PCFG_RU[id] ?? id.replace(/_/g, ' ');
