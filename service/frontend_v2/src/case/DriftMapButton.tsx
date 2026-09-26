// INBOX §54 п.4 (jury checkpoint 21:10): «кнопка "Дрейф" видна на карте рядом с выбранной находкой, а не только во
// вкладке» — this is that overlay. DriftTab.tsx (the card's «Дрейф» tab) stays the detailed panel (source, forcing
// time, assumptions, check numbers); this component is only the visible on-map affordance next to the selected
// zone's marker, so the jury doesn't have to open the card to notice a forecast exists.
// Implementation: portals into document.body (like DemoTour.tsx) and tracks the selected zone's screen position
// on every map move/resize via ctl.map.project — no layout space needed inside CaseApp's aside/card, and no edits
// to CaseMap.tsx's own marker rendering.
// Honesty (§54): the calculation must be real HYCOM (currents) + GFS (wind) forcing on the SCENE's own date, never
// "today's weather" for a historical scene. `path` (data/live/<region>/<date>/drift.json) already encodes the
// published run's date; drift.ts `driftDateReason` cross-checks it against the zone's own `datetime` — verified 0
// mismatches across all 44 current data/live/*/drift.json (out/tmp_check_drift.py), kept here as a live guard, not
// just a one-off check. No path, or a date mismatch → this renders the reason text, never a silent button.
// Mount point (L132, one line near <DemoTour/> in CaseApp.tsx's root render):
//   <DriftMapButton zone={selSz} path={selDriftPath} on={!!drift} onToggle={toggleDrift} />
// (selSz, selDriftPath, drift, toggleDrift already exist in CaseApp.tsx — see its `toggleDrift` callback.)
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { ctl } from '../map/controller';
import { geomCenter } from './CaseMap';
import { driftDateReason } from './drift';

export interface DriftMapButtonZone {
  geometry?: any;
  properties?: { datetime?: string | null; [k: string]: any };
}

export interface DriftMapButtonProps {
  /** the selected scene-zone feature — same object CaseApp already holds as `selSz` */
  zone: DriftMapButtonZone | null;
  /** published drift.json path for this zone, or null — CaseApp already computes this as `selDriftPath` */
  path: string | null;
  /** is the forecast layer currently drawn on the map (DriftLayer.ts) */
  on: boolean;
  onToggle: () => void;
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export default function DriftMapButton({ zone, path, on, onToggle }: DriftMapButtonProps) {
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const center = zone?.geometry ? geomCenter(zone.geometry) : null;
  const cx = center?.[0], cy = center?.[1];

  useEffect(() => {
    const map = ctl.map;
    if (!map || cx == null || cy == null) {
      setPos(null);
      return;
    }
    const update = () => {
      const rect = map.getContainer().getBoundingClientRect();
      const p = map.project([cx, cy] as [number, number]);
      const x = clamp(rect.left + p.x, rect.left + 24, rect.right - 24);
      const y = clamp(rect.top + p.y, rect.top + 36, rect.bottom - 24);
      // mobile: the open card panel is a full-screen overlay above the map (same physical area) — a marker
      // computed from map.project would still land there, but nothing of the map is actually visible; only show
      // the button when that point really hits the map's own canvas, not the card sitting on top of it.
      const topEl = document.elementFromPoint(x, y);
      const mapVisible = !!topEl?.closest('[data-testid="map"]');
      setPos(mapVisible ? { x, y } : null);
    };
    update();
    map.on('move', update);
    map.on('resize', update);
    // no map event fires when only the card opens/closes (right-open) or a layout media query flips — poll
    // cheaply so the button also disappears/reappears as the card is opened/closed on narrow screens.
    const iv = window.setInterval(update, 300);
    return () => {
      map.off('move', update);
      map.off('resize', update);
      window.clearInterval(iv);
    };
  }, [cx, cy]);

  if (!zone || !pos) return null;

  const reason = path
    ? driftDateReason(path, zone.properties ?? {})
    : 'для этой сцены нет опубликованного прогноза дрейфа — расчёт (OpenDrift, HYCOM + GFS) на дату снимка не выполнялся';
  const show = !reason;

  return createPortal(
    <div
      className="drift-map-wrap"
      data-testid="drift-map-overlay"
      style={{ transform: `translate3d(${pos.x}px, ${pos.y}px, 0) translate(-50%, -130%)` }}
    >
      {show ? (
        <button
          className={`btn sm drift-map-btn ${on ? 'on' : ''}`}
          onClick={onToggle}
          data-testid="drift-map-btn"
          title="Дрейф: сценарий OpenDrift по реальным течениям (HYCOM) и ветру (GFS) на дату сцены, горизонт ≤ 72 ч"
        >
          {on ? 'Дрейф ✓' : 'Дрейф ▶'}
          <span className="drift-map-cap">модельный сценарий, не наблюдаемое перемещение</span>
        </button>
      ) : (
        <div className="drift-map-none" data-testid="drift-map-none" title={reason}>
          Дрейф: нет расчёта
        </div>
      )}
    </div>,
    document.body,
  );
}
