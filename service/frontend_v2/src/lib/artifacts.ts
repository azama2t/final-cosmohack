// L38: objects the data build marked as artifacts (detections.geojson properties.artifact ∈ seam | wake | ship).
// They are NOT findings: excluded from the index, the zones and the KPI; hidden on the map unless the legend switch
// «показывать исключённые артефакты» is on (then drawn as a muted grey outline without a halo).
// Old data without the field: every function below is a no-op (all objects are findings, switch hidden).
import type { DetProps, FC } from '../types';

export const ARTIFACT_RU: Record<string, string> = {
  seam: 'шов детекторов',
  wake: 'кильватер',
  ship: 'судно',
};
export const ARTIFACT_NOTE = 'исключено из индекса и зон';

/** 'seam' | 'wake' | 'ship' | other non-empty string, or null for a normal detection. */
export function artifactOf(p: DetProps | null | undefined): string | null {
  const a = p?.artifact;
  if (a === null || a === undefined || a === false || (typeof a === 'string' && !a.trim())) return null;
  return typeof a === 'string' ? a.trim() : 'artifact';
}

export const artifactRu = (a: string | null | undefined) => (a && ARTIFACT_RU[a]) || 'артефакт';

/** «вероятно шов детекторов — исключено из индекса и зон» */
export const artifactText = (a: string | null | undefined) => `вероятно ${artifactRu(a)} — ${ARTIFACT_NOTE}`;

export interface SplitDet {
  /** findings (no artifact field) — everything the index, zones and KPI are about */
  real: FC<DetProps> | null;
  /** excluded artifacts; null when there are none (old data) */
  arts: FC<DetProps> | null;
  n: number;
}

export function splitArtifacts(fc: FC<DetProps> | null | undefined): SplitDet {
  if (!fc) return { real: fc ?? null, arts: null, n: 0 };
  let n = 0;
  for (const f of fc.features) if (artifactOf(f.properties)) n++;
  if (!n) return { real: fc, arts: null, n: 0 }; // same object: old data behaves exactly as before
  return {
    real: { ...fc, features: fc.features.filter((f) => !artifactOf(f.properties)) },
    arts: { ...fc, features: fc.features.filter((f) => !!artifactOf(f.properties)) },
    n,
  };
}

/** Count per kind, e.g. «шов детекторов: 3, судно: 1». */
export function artifactKinds(fc: FC<DetProps> | null | undefined): string {
  const m = new Map<string, number>();
  for (const f of fc?.features ?? []) {
    const a = artifactOf(f.properties);
    if (a) m.set(artifactRu(a), (m.get(artifactRu(a)) ?? 0) + 1);
  }
  return [...m].map(([k, v]) => `${k}: ${v}`).join(', ');
}
