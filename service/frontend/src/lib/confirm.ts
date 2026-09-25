// L15 «уверенные находки»: cross-model confirmation (объект одной модели, у другой модели есть пиксель ≥ её порога
// в радиусе 20 м). Это согласие двух моделей, не проверка на месте. Старые данные без поля `confirmed` работают:
// всё ниже возвращает null / «нет данных», и переключатель выключается.
import type { DateEntry, DetProps, FC } from '../types';

export const AGREE_NOTE = 'согласие моделей, не проверка на месте';

/** Short model name for the agreement line. */
export const agreeName = (id: string | null | undefined) =>
  id === 'lgbm' ? 'LightGBM' : id === 'mdd' ? 'MDD' : id ? id.toUpperCase() : '—';

/** Confirmation data present for this date/model (field in detections or n_confirmed in the manifest). */
export function hasConfirm(de: DateEntry | null | undefined, fc: FC<DetProps> | null | undefined): boolean {
  if (de?.n_confirmed) return true;
  return !!fc?.features.some((f) => typeof f.properties.confirmed === 'boolean');
}

/** Number of confirmed detections of `model` on this date, null if unknown (old data / one model). */
export function nConfirmed(de: DateEntry | null | undefined, model: string, fc: FC<DetProps> | null | undefined): number | null {
  const v = de?.n_confirmed?.[model];
  if (typeof v === 'number') return v;
  if (!hasConfirm(de, fc) || !fc) return null;
  return fc.features.filter((f) => f.properties.confirmed === true).length;
}

export function onlyConfirmed(fc: FC<DetProps> | null): FC<DetProps> | null {
  if (!fc) return fc;
  return { ...fc, features: fc.features.filter((f) => f.properties.confirmed === true) };
}

/** «подтверждено LightGBM в радиусе 20 м» / «только MDD» / null (no data). */
export function agreeText(p: DetProps): string | null {
  if (typeof p.confirmed !== 'boolean') return null;
  return p.confirmed ? `подтверждено ${agreeName(p.confirmed_by)} в радиусе 20 м` : `только ${agreeName(p.model)}`;
}
