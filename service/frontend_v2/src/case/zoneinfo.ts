// INBOX §39 п.1–2, §40 п.2: the zone status is strictly one of the four statuses of the task statement; «Что это» (class)
// is the first large line of the card; confirmation by the Cózar catalogue is a separate line; «excluded / not checked»
// under it. Texts from the API (L131 «§39 бэкенд готов»: status_label, confirmation_label, classification.*);
// the fallbacks below build the same from detection_status / verification / probable.signs (no new claims).
import { isFind } from './CaseMap';

const str = (x: any) => (typeof x === 'string' && x ? x : null);
/** «Исключено: пена, блик» → «пена, блик» (the card prints the prefix itself) */
const strip = (s: string | null, prefix: string) => (s && s.startsWith(prefix) ? s.slice(prefix.length).trim() : s);

/** «обнаружено» | «не обнаружено» | «недостаточно данных» | «исследовательская оценка» */
export const STATUS4_RU: Record<string, string> = {
  detected: 'обнаружено',
  not_detected: 'не обнаружено',
  insufficient_data: 'недостаточно данных',
  research_estimate: 'исследовательская оценка',
};

const status4 = (p: any): string => str(p.status) ?? p.detection_status;

export function statusLabel(p: any): string {
  const s = str(p.status_label);
  if (s) return s;
  if (p.detection_status === 'detected' && !isFind(p)) return STATUS4_RU.insufficient_data;
  return STATUS4_RU[p.detection_status] ?? String(p.detection_status ?? '—');
}

/** «Подтверждение: …» — only for detections (null = no line) */
export function confirmation(p: any): string | null {
  if (p.confirmation === 'training_scene') return 'снимок обучения детектора (не независимая проверка); находкой не считается';
  const l = str(p.confirmation_label);
  if (l) return l;
  const c = p.confirmation;
  if (c && typeof c === 'object' && str(c.label)) return c.label;
  if (status4(p) !== 'detected') return null;
  return p.verification === 'level_B_cozar' ? 'совпадает с разметкой Cózar 2024 (B)' : 'независимой разметки нет';
}

export const CLASS_RU = 'плавающий материал (класс MARIDA Marine Debris; пластик не подтверждён)';

const SIGN_RU: Record<string, string> = {
  ship: 'судно/кильватер',
  seam: 'шов/граница яркости',
  foam: 'пена',
  glint: 'блик',
  cloud: 'облака',
  coast: 'берег/прибой',
  shallow: 'мелководье/мутная вода',
  wind: 'ветер > 5 м/с',
};
const SIGN_ORDER = ['ship', 'seam', 'foam', 'glint', 'cloud', 'coast', 'shallow'];

/** backgrounds the rules checked and did not find (a flag that fired is not «excluded») */
export function excludedLabel(p: any): string | null {
  const c = p.classification;
  if (c && typeof c === 'object' && 'excluded_label' in c) return strip(str(c.excluded_label), 'Исключено:');
  const s = str(p.excluded_label);
  if (s) return strip(s, 'Исключено:');
  const sg = p.probable?.signs ?? {};
  const fired = new Set<string>(p.flags ?? []);
  const out = SIGN_ORDER.filter((k) => sg[k] && !sg[k].flag && !fired.has(k)).map((k) => SIGN_RU[k]);
  if (!fired.has('wind') && !p.wind_high) out.push(SIGN_RU.wind);
  return out.length ? out.join(', ') : null;
}

/** signs that fired (the reason for «недостаточно данных»): «судно/кильватер, пена» */
export function flaggedLabel(p: any): string | null {
  const c = p.classification;
  if (c && typeof c === 'object' && 'flagged_label' in c) return strip(str(c.flagged_label), 'Признаки:');
  const fired: string[] = p.flags ?? [];
  return fired.length ? fired.map((k) => SIGN_RU[k] ?? k).join(', ') : null;
}

export function notCheckedLabel(p: any): string {
  return (
    strip(str(p.classification?.not_checked_label) ?? str(p.not_checked_label), 'Не проверяется:') ??
    'водоросли/саргассум, древесина и прочая органика (флага нет)'
  );
}

/** «Что это: …» without the prefix — the class of the zone (§40 п.2: the first large line of the card) */
export function whatLabel(p: any): string {
  const w = strip(str(p.classification?.what_label), 'Что это:');
  if (w) return w;
  const st = status4(p);
  if (st === 'detected') return str(p.classification?.class_label) ?? str(p.class_label) ?? CLASS_RU;
  if (st === 'insufficient_data') return 'не определено (признаки ложного срабатывания или ветер — см. «Признаки»)';
  return 'объектов детектора нет';
}

/** §40 п.2: short class + confirmation for list rows and tooltips: «плавающий материал · Cózar B» */
export function classShort(p: any): string {
  const st = status4(p);
  if (st === 'detected') {
    const cf = str(p.confirmation) ?? (p.verification === 'level_B_cozar' ? 'cozar_b' : 'none');
    const tail = cf === 'cozar_b' ? 'Cózar B' : cf === 'training_scene' ? 'снимок обучения (не независимая проверка)' : 'без разметки';
    return `плавающий материал · ${tail}`;
  }
  if (st === 'insufficient_data') {
    const f = flaggedLabel(p);
    return f ? `не определено · ${f}` : 'не определено';
  }
  return 'объектов нет';
}

/** «Количество предметов по этому снимку не определено» + the reason in brackets (API quantity_line) */
export function quantityLine(p: any): { head: string; why: string | null } {
  const q = str(p.quantity_line) ?? str(p.quantity?.label);
  const head = 'Количество предметов по этому снимку не определено';
  if (!q) return { head, why: 'перенос «снимок → штуки» не подтверждён: природных пар нет (см. ISPRA 604)' };
  const m = q.match(/^(.*?)\s*\((.*)\)\s*$/);
  return m ? { head: m[1], why: m[2] } : { head: q, why: null };
}

/** the legend classes (§40 п.2: the same as in the list) */
export const LEGEND_CLASSES: { k: string; t: string }[] = [
  { k: 'find-b', t: 'плавающий материал · Cózar B' },
  { k: 'find', t: 'плавающий материал · без разметки' },
  { k: 'insuf', t: 'не определено (признаки ложного срабатывания / ветер)' },
  { k: 'none', t: 'объектов нет' },
];
