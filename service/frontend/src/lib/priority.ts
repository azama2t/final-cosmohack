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
export const zoneScore = (z: Zone) => zonePx(z) * (z.mean_prob ?? 0) * repeatFactor(z.repeat_dates);

const f1 = (v: number) => v.toLocaleString('ru-RU', { maximumFractionDigits: 1, minimumFractionDigits: 1 });
const f2 = (v: number) => v.toLocaleString('ru-RU', { maximumFractionDigits: 2, minimumFractionDigits: 2 });

/** Client-side «why» from zones.json fields (same structure as /api/zone → why). */
export function localWhy(z: Zone, zones: Zone[], nDates: number): Why | null {
  if (z.mean_prob === undefined) return null;
  const px = zonePx(z);
  const mp = z.mean_prob ?? 0;
  const rep = z.repeat_dates ?? 1;
  const rf = repeatFactor(rep);
  const score = px * mp * rf;
  const head = `${px} пикс. × ${f2(mp)} × ${f1(rf)} = ${f1(score)}`;
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
  return {
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
