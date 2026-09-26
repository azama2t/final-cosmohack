// §70 п.B (Фёдор, 00:17, "чиним опубликованный сайт" — 20-минутное окно): the big floating «Дрейф» button on the
// map (Гондурас · Омоа · зона 5, 30.01.2025 and others) is gone per the fix — ONE «Дрейф» control lives in the
// card (DriftTab.tsx's own button), not a second one floating over the map on top of the zone marker/timeline.
// This component intentionally renders nothing now. Kept as a no-op (not deleted) because CaseApp.tsx — L132's
// file, not touched here per the §70 file split — still imports and mounts it with the same props; removing the
// file or changing its export shape would be a cross-owner edit under freeze. If a future task wants the on-map
// affordance back, restore from git history (this file, pre-§70) rather than re-inventing it.
export interface DriftMapButtonZone {
  geometry?: any;
  properties?: { datetime?: string | null; [k: string]: any };
}

export interface DriftMapButtonProps {
  zone: DriftMapButtonZone | null;
  path: string | null;
  on: boolean;
  onToggle: () => void;
}

export default function DriftMapButton(_props: DriftMapButtonProps) {
  return null;
}
