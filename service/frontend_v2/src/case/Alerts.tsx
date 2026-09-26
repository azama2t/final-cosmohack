// §51 п.7 (L142): alert level «слабый / средний / высокий» — UI only. The rule and the numbers are the backend's
// (docs/ALERTS.md, L141: importance_rank = size × shore × drift, cap «не выше средний» without confirmation).
// Nothing is computed here: a zone without `alert_level` in the API shows no badge (never a guessed level).
// Satellite zones: the material is not determined by the model — said so in the card.
import { useSyncExternalStore } from 'react';
import { num } from './fmt';

export type AlertLevel = 'high' | 'mid' | 'low';
export const ALERT_RU: Record<AlertLevel, string> = { high: 'высокий', mid: 'средний', low: 'слабый' };
export const ALERT_ORDER: AlertLevel[] = ['high', 'mid', 'low'];

/** API value → level (Russian words of docs/ALERTS.md or English codes); null when absent/unknown */
export function alertLevel(p: Record<string, any> | null | undefined): AlertLevel | null {
  const v = p?.alert_level;
  if (typeof v !== 'string') return null;
  const s = v.trim().toLowerCase();
  if (s.startsWith('выс') || s === 'high') return 'high';
  if (s.startsWith('сред') || s === 'medium' || s === 'mid') return 'mid';
  if (s.startsWith('слаб') || s === 'low' || s === 'weak') return 'low';
  return null;
}

/** the badge in list rows and in the card */
export function AlertBadge({ p, compact = false }: { p: Record<string, any>; compact?: boolean }) {
  const lv = alertLevel(p);
  if (!lv) return null;
  const rank = typeof p.importance_rank === 'number' ? p.importance_rank : null;
  return (
    <span className={`c-alert ${lv}`} data-testid="alert-badge" data-level={lv} title={`Уровень алерта: ${ALERT_RU[lv]}${rank !== null ? ` · ранг важности ${rank} из ${p.importance_rank_max ?? 27}` : ''} (правило docs/ALERTS.md)`}>
      <i aria-hidden />
      {compact ? ALERT_RU[lv] : `алерт: ${ALERT_RU[lv]}`}
    </span>
  );
}

const pct = (v: any) => (typeof v === 'number' ? `${num(v <= 1 && v > 0 ? v * 100 : v, 0)} %` : null);

/** one block for the card (tab «Главное»): level, why (rank = size × shore × drift), shore, drift, material */
export function AlertCardLine({ p }: { p: Record<string, any> }) {
  const lv = alertLevel(p);
  const hasAny = lv || p.importance_rank != null || p.shore_km != null || p.alert_material_note;
  if (!hasAny) return null;
  const f = (k: string) => (typeof p[k] === 'number' ? p[k] : null);
  const factors = [f('size_factor'), f('shore_factor'), f('drift_factor')];
  const rank = f('importance_rank');
  const capped = p.alert_capped === true || p.alert_cap_reason;
  const material = p.alert_material ?? null;
  return (
    <div className="sec c-alert-sec" data-testid="alert-card">
      <div className="c-line">
        <span className="faint">Уровень алерта:</span> {lv ? <AlertBadge p={p} compact /> : <span className="faint">не рассчитан{p.alert_reason ? ` — ${p.alert_reason}` : ''}</span>}
        {rank !== null && (
          <span className="faint">
            {' '}
            · ранг {num(rank, 0)} из {p.importance_rank_max ?? 27}{factors.every((x) => x !== null) ? ` = размер ${factors[0]} × берег ${factors[1]} × дрейф ${factors[2]}` : ''}
          </span>
        )}
      </div>
      {capped && (
        <div className="c-line faint" data-testid="alert-cap">
          {typeof p.alert_cap_reason === 'string' ? p.alert_cap_reason : 'без независимого подтверждения — не выше «средний»'}
        </div>
      )}
      {(p.shore_km != null || p.shore_km_reason) && (
        <div className="c-line faint" data-testid="alert-shore">
          До берега: {p.shore_km != null ? `≈ ${num(p.shore_km, p.shore_km < 10 ? 1 : 0)} км (${p.shore_km_note ?? 'грубо, Natural Earth 1:110m'})` : p.shore_km_reason}
        </div>
      )}
      {(p.stranded_pct_72h != null || p.drift_reason) && (
        <div className="c-line faint" data-testid="alert-drift">
          Выброс на берег за 72 ч: {p.stranded_pct_72h != null ? `${pct(p.stranded_pct_72h)} частиц модели (эксперимент, не число предметов)` : p.drift_reason}
        </div>
      )}
      <div className="c-line faint" data-testid="alert-material">
        Материал: {material ? String(material) : (p.alert_material_note ?? 'материал по снимку не определяется')}
      </div>
    </div>
  );
}

// ---- filter by level (one small store shared by the list and its header; not in the URL) ----
type Filt = AlertLevel | 'all';
let cur: Filt = 'all';
const subs = new Set<() => void>();
export function setAlertFilter(v: Filt) {
  cur = v;
  subs.forEach((f) => f());
}
export function useAlertFilter(): Filt {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => cur,
  );
}
/** keep the rows whose zone matches the current level filter */
export function byAlert<T>(rows: T[], get: (r: T) => Record<string, any>, f: Filt): T[] {
  return f === 'all' ? rows : rows.filter((r) => alertLevel(get(r)) === f);
}

// ---- §51 п.9: organic flag (NDVI/FAI, experiment; backend likely_organic true/false/null) ----
export const ORGANIC_COLOR = '#51cf66';
type OrgF = 'all' | 'yes' | 'no';
let curOrg: OrgF = 'all';
const subsOrg = new Set<() => void>();
export function setOrganicFilter(v: OrgF) {
  curOrg = v;
  subsOrg.forEach((f) => f());
}
export function useOrganicFilter(): OrgF {
  return useSyncExternalStore(
    (f) => {
      subsOrg.add(f);
      return () => subsOrg.delete(f);
    },
    () => curOrg,
  );
}
/** yes: likely_organic === true; no: === false (null = not evaluated → only in «все») */
export function byOrganic<T>(rows: T[], get: (r: T) => Record<string, any>, f: OrgF): T[] {
  return f === 'all' ? rows : rows.filter((r) => get(r).likely_organic === (f === 'yes'));
}
export function OrganicLegend() {
  return (
    <span className="c-lg-cl" data-testid="legend-organic" title="Флаг по спектру (NDVI ≥ 0,20 и FAI > 0), эксперимент; моделью органика отдельно не выделяется">
      <i className="c-sw-org" style={{ borderColor: ORGANIC_COLOR }} /> вероятно органика (флаг NDVI/FAI, эксперимент)
    </span>
  );
}

/** «Алерт: все · высокий N · средний N · слабый N» + «Органика: все · вероятно органика N · без флага N» */
export function AlertFilter({ zones }: { zones: Record<string, any>[] }) {
  const f = useAlertFilter();
  const fo = useOrganicFilter();
  const n: Record<AlertLevel, number> = { high: 0, mid: 0, low: 0 };
  let oy = 0,
    on = 0;
  for (const p of zones) {
    const lv = alertLevel(p);
    if (lv) n[lv]++;
    if (p.likely_organic === true) oy++;
    else if (p.likely_organic === false) on++;
  }
  const hasA = !!(n.high || n.mid || n.low);
  const hasO = !!(oy || on);
  if (!hasA && !hasO) return null;
  return (
    <div className="c-alert-filter" role="group" aria-label="Фильтр по уровню алерта и органике" data-testid="alert-filter">
      {hasA && (
        <>
          <span className="faint">Алерт:</span>
          <button className={f === 'all' ? 'on' : ''} onClick={() => setAlertFilter('all')} data-testid="alert-f-all">
            все
          </button>
          {ALERT_ORDER.map((lv) => (
            <button key={lv} className={`${f === lv ? 'on' : ''} ${lv}`} onClick={() => setAlertFilter(f === lv ? 'all' : lv)} disabled={!n[lv]} data-testid={`alert-f-${lv}`}>
              <i aria-hidden /> {ALERT_RU[lv]} {n[lv]}
            </button>
          ))}
        </>
      )}
      {hasO && (
        <span className="c-org-f" data-testid="organic-filter">
          <span className="faint">Органика:</span>
          <button className={fo === 'all' ? 'on' : ''} onClick={() => setOrganicFilter('all')} data-testid="organic-f-all">
            все
          </button>
          <button className={`org ${fo === 'yes' ? 'on' : ''}`} onClick={() => setOrganicFilter(fo === 'yes' ? 'all' : 'yes')} disabled={!oy} data-testid="organic-f-yes" title="флаг NDVI/FAI, эксперимент">
            <i aria-hidden /> вероятно органика {oy}
          </button>
          <button className={fo === 'no' ? 'on' : ''} onClick={() => setOrganicFilter(fo === 'no' ? 'all' : 'no')} disabled={!on} data-testid="organic-f-no">
            без флага {on}
          </button>
        </span>
      )}
    </div>
  );
}
