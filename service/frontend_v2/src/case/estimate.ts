// §34 п.2–3: research estimate of items/km² for a satellite find (flat calibration on the PLP targets:
// N = detector pixels × [470; 670] items, C = N / zone area). Numbers only from the API (properties.research_estimate);
// never for «недостаточно данных» / ships / foam / «не обнаружено» / a scene of the detector's training set.
import { isFind } from './CaseMap';

export interface ResEst {
  /** point value, items/km² */
  v: number;
  lo: number | null;
  hi: number | null;
  /** the API's own wording (if given) */
  label: string | null;
  note: string | null;
  /** «N ≈ … [lo–hi] шт. в зоне» */
  nItemsLabel: string | null;
  basis: string | null;
  method: string | null;
  caveats: string[];
  notWhat: string | null;
  /** the number shown after «≥ ~» (a lower bound: detector pixels below the threshold are not counted) */
  lb: number;
  /** items in the zone (bottles PET 1.5 l equivalent) */
  nItems: number | null;
  /** «требует проверки» (not confirmed by the Cózar catalogue) — shown muted */
  muted: boolean;
  /** API wording, if it gives one (L131 «оценка: формулировки») */
  short: string | null;
  context: string | null;
  /** n_items_display is the lower bound of the zone's items (API ≥ 14:01) */
  nItemsLower: boolean;
  essence: string | null;
  spread: string | null;
  /** «пиксели маски × 470–670 / площадь контура = пересчёт доли покрытия» */
  formula: string | null;
  /** the calibration in force («плоская калибровка по мишеням PLP …») */
  calibration: string | null;
  /** §36 п.2: the wording of the 470–670 scenario from the API (full / short), if given */
  scenario: string | null;
  scenarioShort: string | null;
  /** §39 п.3: the ready line of the scenario from the API (research_estimate.scenario_line) */
  scenarioLabel: string | null;
  /** «Исследовательский сценарий (мишени PLP)» (research_estimate.scenario_title) */
  scenarioTitle: string | null;
}

/** jury 14:30: one short line under the number; everything else — under (i) */
export const RES_CONTEXT_SHORT = 'внутри нити, с полевыми шт./км² не сравнивать: другой масштаб';
export const cap = (t: string) => t.charAt(0).toUpperCase() + t.slice(1);
/** jury 14:30: «разница в 3–4 порядка ожидаема» → «другой масштаб» */
const scaleWording = (t: string | null) => (t ? t.replace(/разница в 3[–-]4 порядка ожидаема/g, 'другой масштаб') : t);
/** §34 (оркестратор 13:5x): context next to every estimate */
export const RES_CONTEXT = 'плотность внутри нити, в пересчёте на бутылки PET 1,5 л; с полевыми шт./км² не сравнивать';

/** the short caption required next to every estimate (§34 п.3) */
export const RES_CAPTION =
  'исследовательский сценарий по искусственным мишеням PLP (допущения: предметы размера бутылки PET 1,5 л, покрытие 28–40 %) — не доверительный интервал, на природе не проверено; на площадь контура зоны; пиксели ниже порога детектора не учтены';
/** the list caption (the card and the «i» carry the full one) */
export const RES_CAPTION_LIST = 'исследовательский сценарий по искусственным мишеням PLP (бутылки PET 1,5 л, покрытие 28–40 %), на природе не проверено';
/** the full caption (§34 п.2, §36 п.2) — card «i» / details (fallback when the API gives no wording) */
export const RES_NOTE =
  'Исследовательская оценка, не измерение. Сценарий по искусственным мишеням PLP: детектор отмечает пиксель 10 м при покрытии 28–40 % ≈ 470–670 ' +
  'предметов размера бутылки PET 1,5 л на 100 м²; N зоны = пиксели детектора × [470; 670], C = N / площадь контура зоны. 470–670 — допущение ' +
  'сценария, не доверительный интервал; на природных скоплениях не проверено; мелкие предметы → больше штук.';

const n = (x: any): number | null => (typeof x === 'number' && Number.isFinite(x) ? x : null);

export function researchEst(p: any): ResEst | null {
  if (!p || !isFind(p) || p.detection_status !== 'detected') return null;
  let r: any = p.research_estimate ?? p.concentration?.research_estimate ?? null;
  if (r === null || r === undefined) {
    // flat CSV-like fields (research_estimate_items_km2 / _lo / _hi), if the API gives them so
    const v0 = n(p.research_estimate_items_km2);
    if (v0 === null) return null;
    r = { items_km2: v0, lo: p.research_estimate_lo, hi: p.research_estimate_hi };
  }
  if (typeof r !== 'object') return null;
  const iv = Array.isArray(r.interval) ? r.interval : Array.isArray(r.items_km2_interval) ? r.items_km2_interval : Array.isArray(r.range) ? r.range : Array.isArray(r.ci) ? r.ci : null;
  const lo = n(r.lo) ?? n(r.items_km2_lo) ?? n(r.c_lo) ?? (iv ? n(iv[0]) : null);
  const hi = n(r.hi) ?? n(r.items_km2_hi) ?? n(r.c_hi) ?? (iv ? n(iv[1]) : null);
  let v = n(r.items_km2) ?? n(r.c_items_km2) ?? n(r.value) ?? n(r.estimate) ?? n(r.c) ?? n(r.mid);
  if (v === null && lo !== null && hi !== null) v = Math.sqrt(lo * hi);
  if (v === null) return null;
  const str = (x: any) => (typeof x === 'string' && x ? x : null);
  return {
    v,
    lo,
    hi,
    label: str(r.label),
    note: str(r.note),
    nItemsLabel: str(r.n_items_label),
    basis: str(r.basis),
    method: str(r.method),
    caveats: Array.isArray(r.caveats) ? r.caveats.filter((x: any) => typeof x === 'string') : [],
    notWhat: str(r.not_what),
    lb: n(r.display_value) ?? n(r.lower_bound) ?? v,
    nItems: n(r.n_items_display) ?? n(r.n_items?.value) ?? null,
    muted: typeof r.muted === 'boolean' ? r.muted : p.verification !== 'level_B_cozar',
    short: str(r.label_short) ?? str(r.display),
    context: scaleWording(str(r.context)),
    nItemsLower: n(r.n_items_display) !== null,
    essence: str(r.method_essence),
    spread: str(r.calibration_spread?.label),
    formula: str(r.formula_short),
    calibration: str(r.calibration_name),
    scenario: str(r.scenario) ?? str(r.scenario_label) ?? str(r.scenario?.label) ?? str(r.caption),
    scenarioShort: str(r.scenario_label_short) ?? str(r.scenario?.short) ?? str(r.caption_short),
    scenarioLabel: str(r.scenario_line) ?? str(r.scenario_label),
    scenarioTitle: str(r.scenario_title),
  };
}

const nf0 = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 0 });
/** the API value as is (it is already rounded to 3 significant digits — the same number as in the CSV export) */
export const approx = (v: number | null) => (v === null ? '—' : nf0.format(v));

const sig2 = new Intl.NumberFormat('ru-RU', { maximumSignificantDigits: 2 });
/** «~» numbers: 2 significant digits (the calibration spread is not estimated — no false precision) */
export const rough = (v: number | null) => (v === null ? '—' : sig2.format(v));

/** «≥ ~93 000 шт./км²» */
export function estTxt(e: ResEst): string {
  return `≥ ~${rough(e.lb)} шт./км²`;
}
/** §39 п.3: «≈ X шт./км², если бы это были предметы размера бутылки 1,5 л при покрытии 28–40 % (искусственные мишени
 *  PLP); на природе не проверено; на оценщике хуже ответа «0»» — shown only folded, under «Количество … не определено» */
export function scenarioLine(e: ResEst): string {
  return (
    e.scenarioLabel ??
    `≈ ${rough(e.v)} шт./км², если бы это были предметы размера бутылки 1,5 л при покрытии 28–40 % (искусственные мишени PLP); на природе не проверено; на оценщике хуже ответа «0»`
  );
}
/** «≈ 93 000 шт./км² · исследовательский сценарий» (or the API's own short wording) */
export function estLine(e: ResEst): string {
  return e.short ?? `≈ ${rough(e.v)} шт./км² · исследовательский сценарий`;
}
/** «≈ 390 000 шт. в пересчёте на бутылки PET 1,5 л» */
export function nItemsTxt(e: ResEst): string | null {
  if (e.nItems === null) return null;
  return e.nItemsLower ? `≈ ${rough(e.nItems)} шт. в пересчёте на бутылки PET 1,5 л (сценарий)` : `≈ ${rough(e.nItems)} шт. в пересчёте на бутылки PET 1,5 л`;
}
