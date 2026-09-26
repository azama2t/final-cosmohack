// L151 · §54 п.2: host of the «Дроны» panel, mounted once next to CaseApp (main.tsx) like DynamicsHost.
// Entry points (one-liners anywhere):
//   import { openDrones } from './DronesHost';     onClick={() => openDrones()}            // list of sets
//   openDrones('martin2021')                                                                // a given set
//   in the «Район» <select>:  <DronesOptions />  +  onChange: if (openDronesFromValue(v)) return;
// URL: ?drones=1 (list) or ?drones=<set id> opens the panel on load.
import { useEffect, useState } from 'react';
import { get } from './api3';
import DronesPanel from './DronesPanel';

const EV = 'mp:drones';
export const DRONES_PREFIX = 'drones:';

export function openDrones(set?: string | null) {
  window.dispatchEvent(new CustomEvent<{ set: string | null }>(EV, { detail: { set: set ?? null } }));
}

/** for the «Район» select: value "drones:" (all) or "drones:<set>" -> opens the panel, returns true (caller stops) */
export function openDronesFromValue(v: string): boolean {
  if (!v || !v.startsWith(DRONES_PREFIX)) return false;
  openDrones(v.slice(DRONES_PREFIX.length) || null);
  return true;
}

interface SetShort {
  id: string;
  name: string;
  sensor: string;
  n_frames: number;
}
let setsP: Promise<SetShort[]> | null = null;
function loadSets(): Promise<SetShort[]> {
  if (!setsP)
    setsP = get<{ sets: SetShort[] }>('/api/v3/drones')
      .then((d) => d.sets)
      .catch((e) => {
        setsP = null;
        throw e;
      });
  return setsP;
}

/** <optgroup> «Дроны» for the region select (values drones:<set>); nothing if the API has no drones index */
export function DronesOptions() {
  const [sets, setSets] = useState<SetShort[] | null>(null);
  useEffect(() => {
    loadSets().then(setSets, () => setSets([]));
  }, []);
  if (!sets || !sets.length) return null;
  return (
    <optgroup label="Дроны — детальные кадры (не спутник)">
      <option value={DRONES_PREFIX}>Все наборы кадров ({sets.length})</option>
      {sets.map((s) => (
        <option key={s.id} value={DRONES_PREFIX + s.id}>
          {s.name} ({s.n_frames} кадр.{s.sensor !== 'drone' ? ', не дрон' : ''})
        </option>
      ))}
    </optgroup>
  );
}

/** a small button «Дроны» (e.g. in the left panel or «Ещё ▾») */
export function DronesButton({ className = 'btn sm ghost' }: { className?: string }) {
  return (
    <button className={className} onClick={() => openDrones()} data-testid="drones-open" title="Детальные кадры с дронов: предметы по классам — не спутник">
      Дроны: кадры с предметами
    </button>
  );
}

export default function DronesHost() {
  const [st, setSt] = useState<{ set: string | null } | null>(() => {
    const v = new URLSearchParams(location.search).get('drones');
    return v == null ? null : { set: v === '1' || v === '' ? null : v };
  });
  useEffect(() => {
    const on = (e: Event) => setSt({ set: (e as CustomEvent<{ set: string | null }>).detail?.set ?? null });
    window.addEventListener(EV, on);
    return () => window.removeEventListener(EV, on);
  }, []);
  if (!st) return null;
  return <DronesPanel initialSet={st.set} onClose={() => setSt(null)} />;
}
