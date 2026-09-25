// «Приоритет обследования» — ranking formula of src/macroplastic/grid/zones.py (same as service/place.py).
// Used as a fallback when /api/zone is absent; the backend's `why` is preferred when available.
import type { Zone } from '../types';
import type { Why } from './api';

export const REPEAT_BONUS = 0.5;
export const FORMULA = 'score = flagged_water_px × mean_prob × (1 + 0.5 × (repeat_dates − 1))';
export const FORMULA_TEXT =
  'Приоритет обследования = число пикселей воды с признаками мусора в ячейке × средняя уверенность модели на этих пикселях × ' +
  'бонус повторяемости (1 + 0.5 за каждую дополнительную дату, на которой в ячейке тоже были находки). ' +
  'Ранжируются только ячейки с наблюдаемой водой ≥ 50 %.';
export const PRIORITY_NOTE = 'приоритет обследования — ранжирование по снимку, не измеренная опасность';

export interface Verdict {
  /** zone №1 by rank — always the main recommendation (L27, orchestrator decision) */
  top: Zone;
  /** true / false when confirmation data exist; null for old files without n_confirmed */
  topConfirmed: boolean | null;
  /** when №1 is not confirmed: the best-ranked zone confirmed by the second model (closest to №1 in the ranking) */
  alt: Zone | null;
}

/**
 * «Куда отправить обследование» (L27): zone №1 by rank with a badge «подтверждена / не подтверждена второй моделью»;
 * if №1 is not confirmed — the best-ranked confirmed zone as a second line. The ranking table and the verdict
 * therefore never disagree on which zone is first.
 */
export function verdict(zones: Zone[]): Verdict | null {
  if (!zones.length) return null;
  const top = [...zones].sort((a, b) => a.rank - b.rank)[0];
  const known = typeof top.n_confirmed === 'number';
  const topConfirmed = known ? (top.n_confirmed ?? 0) > 0 : null;
  const alt =
    topConfirmed === false
      ? zones.filter((z) => z !== top && (z.n_confirmed ?? 0) > 0).sort((a, b) => a.rank - b.rank)[0] ?? null
      : null;
  return { top, topConfirmed, alt };
}

/**
 * Zone for the tour step: №1 if confirmed (or no confirmation data), else the best-ranked confirmed zone,
 * else №1 marked as unconfirmed. `why` is the explicit caption for the tour.
 */
export function verdictZone(zones: Zone[]): { zone: Zone; unconfirmed: boolean; why: string } | null {
  const v = verdict(zones);
  if (!v) return null;
  if (v.topConfirmed !== false)
    return {
      zone: v.top,
      unconfirmed: false,
      why: v.topConfirmed ? 'Зона №1 подтверждена второй моделью (согласие моделей, не проверка на месте).' : 'Зона №1.',
    };
  if (v.alt)
    return {
      zone: v.alt,
      unconfirmed: false,
      why: `Зона №1 не подтверждена второй моделью — показываем подтверждённую зону №${v.alt.rank}.`,
    };
  return { zone: v.top, unconfirmed: true, why: 'Зона №1 не подтверждена второй моделью; подтверждённых зон на этой дате нет.' };
}

/** flagged px of a zone: explicit field, else area / 100 m² (one 10 m pixel). */
export const zonePx = (z: Zone) => z.flagged_water_px ?? Math.round((z.area_m2 ?? 0) / 100);
export const repeatFactor = (rep: number | null | undefined) => 1 + REPEAT_BONUS * Math.max((rep ?? 1) - 1, 0);
export const baseScore = (z: Zone) => zonePx(z) * (z.mean_prob ?? 0) * repeatFactor(z.repeat_dates);
/** Ranking score: score_terms total (L38) > zones.json score > recomputed base formula. */
export const zoneScore = (z: Zone) => scoreTerms(z)?.score ?? (typeof z.score === 'number' ? z.score : baseScore(z));

// ---- L38: zones.json zones[].score_terms (same normalisation as service/place.py score_terms()) ----
export interface ScoreTerm {
  name: string;
  label: string;
  value: number;
  kind: 'base' | 'mult' | 'sub' | 'info';
  present?: boolean;
  note?: string;
}
export interface ScoreTerms {
  terms: ScoreTerm[];
  base: number;
  agreement: number;
  date_penalty: number;
  score: number;
  mode: 'mult' | 'sub';
}
const TERM_ALIASES: Record<string, string[]> = {
  base: ['base', 'base_score', 'score_base', 'raw', 'raw_score', 'base_px_prob_repeat'],
  agreement: ['agreement', 'agree', 'agreement_mult', 'agree_mult', 'agreement_factor', 'agree_factor', 'confirm_mult',
    'confirmed_mult', 'consensus', 'consensus_mult', 'models_agreement'],
  date_penalty: ['date_penalty', 'unreliable_penalty', 'penalty', 'date_mult', 'reliability', 'reliability_mult',
    'unreliable', 'unreliable_date', 'date_factor', 'quality_penalty', 'unreliable_mult'],
  score: ['score', 'total', 'final', 'final_score', 'score_final'],
};
const ALIAS: Record<string, string> = Object.fromEntries(
  Object.entries(TERM_ALIASES).flatMap(([k, al]) => al.map((a) => [a, k])),
);
export const TERM_LABELS: Record<string, string> = {
  base: 'базовый балл (пиксели × уверенность × повторяемость)',
  agreement: 'множитель согласия моделей',
  date_penalty: 'штраф за ненадёжную дату',
};
const num = (v: unknown): number | null => {
  if (typeof v === 'number' && Number.isFinite(v)) return v;
  if (v && typeof v === 'object') {
    for (const k of ['value', 'factor', 'mult', 'contribution', 'v']) {
      const x = (v as any)[k];
      if (typeof x === 'number' && Number.isFinite(x)) return x;
    }
  }
  return null;
};

/** Normalised score_terms of a zone (dict / nested dict / list forms), null when absent or without a base. */
export function scoreTerms(z: Pick<Zone, 'score_terms'> | null | undefined): ScoreTerms | null {
  const raw = z?.score_terms as any;
  if (!raw) return null;
  const items: [string, unknown, string | undefined, string | undefined][] = [];
  if (Array.isArray(raw)) {
    for (const it of raw) {
      const k = it && typeof it === 'object' ? it.name ?? it.key ?? it.id : null;
      if (k) items.push([String(k), it, it.label, it.note]);
    }
  } else if (typeof raw === 'object') {
    for (const [k, v] of Object.entries(raw))
      items.push([k, v, v && typeof v === 'object' ? (v as any).label : undefined, v && typeof v === 'object' ? (v as any).note : undefined]);
  }
  const known: Record<string, number> = {};
  const labels: Record<string, string> = {};
  const notes: Record<string, string> = {};
  const extra: ScoreTerm[] = [];
  for (const [k, v, lab, note] of items) {
    const canon = ALIAS[k.toLowerCase()];
    const n = num(v);
    if (canon && n !== null && !(canon in known)) {
      known[canon] = n;
      if (lab) labels[canon] = String(lab);
      if (note) notes[canon] = String(note);
    } else if (n !== null) extra.push({ name: k, label: String(lab ?? k), value: n, kind: 'info' });
  }
  if (!('base' in known)) return null;
  const base = known.base;
  const agree = known.agreement ?? 1;
  const pen = known.date_penalty ?? 1;
  let mode: 'mult' | 'sub' = 'mult';
  let total = known.score;
  if (total === undefined) total = base * agree * pen;
  else if ('date_penalty' in known) {
    const tol = 0.01 * Math.max(1, Math.abs(total));
    if (Math.abs(base * agree * pen - total) > tol && Math.abs(base * agree - pen - total) <= tol) mode = 'sub';
  }
  const t = (name: string, value: number, kind: ScoreTerm['kind'], present: boolean): ScoreTerm => ({
    name,
    label: labels[name] ?? TERM_LABELS[name],
    value,
    kind,
    present,
    ...(notes[name] ? { note: notes[name] } : {}),
  });
  return {
    terms: [
      t('base', base, 'base', true),
      t('agreement', agree, 'mult', 'agreement' in known),
      t('date_penalty', pen, mode === 'sub' ? 'sub' : 'mult', 'date_penalty' in known),
      ...extra,
    ],
    base,
    agreement: agree,
    date_penalty: pen,
    score: total,
    mode,
  };
}

const f1 = (v: number) => v.toLocaleString('ru-RU', { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const f2 = (v: number) => v.toLocaleString('ru-RU', { maximumFractionDigits: 2, minimumFractionDigits: 2 });

/** Client-side «why» from zones.json fields (same structure as /api/zone → why). */
export function localWhy(z: Zone, zones: Zone[], nDates: number): Why | null {
  if (z.mean_prob === undefined) return null;
  const px = zonePx(z);
  const mp = z.mean_prob ?? 0;
  const rep = z.repeat_dates ?? 1;
  const rf = repeatFactor(rep);
  const base = px * mp * rf;
  const st = scoreTerms(z);
  const score = st ? st.score : base;
  let head = `${px} пикс. × ${f2(mp)} × ${f1(rf)} = ${f1(base)}`;
  if (st) {
    head = `базовый балл ${f1(st.base)}`;
    if (st.agreement !== 1) head += ` × согласие моделей ×${f2(st.agreement)}`;
    if (st.date_penalty !== 1) head += st.mode === 'sub' ? ` − штраф даты ${f1(st.date_penalty)}` : ` × штраф даты ×${f2(st.date_penalty)}`;
    head += ` = ${f1(score)}`;
  }
  const text: string[] = [];
  if (z.rank === 1) {
    const second = Math.max(0, ...zones.filter((x) => x.rank !== 1).map(zoneScore));
    text.push(
      second > 0
        ? `Первая в списке: ${head} — в ${f1(score / second)} раза больше, чем у второй зоны (${f1(second)}).`
        : `Единственная зона на эту дату: ${head}.`,
    );
  } else {
    const prev = zones.find((x) => x.rank === z.rank - 1);
    text.push(`${z.rank}-я в списке: ${head}${prev ? `; у зоны выше — ${f1(zoneScore(prev))}` : ''}.`);
  }
  const parts = [`главный вклад — площадь признаков: ${px} пикс. ≈ ${f1((px * 100) / 1e4)} га наблюдаемой воды`];
  if (mp >= 0.8) parts.push(`уверенность модели высокая (${f2(mp)})`);
  else if (mp) parts.push(`уверенность модели умеренная (${f2(mp)})`);
  if (rep > 1) parts.push(`находки повторяются на ${rep} из ${nDates} дат (множитель ×${f1(rf)})`);
  else if (nDates > 1) parts.push(`находки только на этой дате из ${nDates} (без бонуса повторяемости)`);
  const p = parts.join('; ');
  text.push(p.charAt(0).toUpperCase() + p.slice(1) + '.');
  if (st && st.agreement > 1) text.push(`Согласие моделей повышает балл в ${f2(st.agreement)} раза (согласие, не проверка на месте).`);
  if (st && st.date_penalty !== 1 && (st.mode === 'sub' || st.date_penalty < 1))
    text.push(
      `Дата ненадёжна (дымка/блик, облака или мало воды) — балл снижен штрафом ${st.mode === 'sub' ? `−${f1(st.date_penalty)}` : `×${f2(st.date_penalty)}`}.`,
    );
  return {
    ...(st ? { base_score: base, score_terms: st.terms, score_mode: st.mode } : {}),
    formula: FORMULA,
    formula_text: FORMULA_TEXT,
    score,
    text: text.join(' '),
    terms: [
      { name: 'flagged_water_px', label: 'пиксели воды с признаками мусора (10×10 м)', value: px, weight: 1, contribution: px },
      { name: 'mean_prob', label: 'средняя уверенность модели на этих пикселях', value: mp, weight: 1, contribution: mp },
      { name: 'repeat_dates', label: 'на скольких датах района в ячейке были находки', value: rep, weight: REPEAT_BONUS, contribution: rf },
    ],
  };
}
