// L140 · §51 п.3: host of the «Динамика района» panel, mounted once next to CaseApp (main.tsx), so an entry point is
// a one-liner anywhere:  import { openDynamics } from './DynamicsHost';  onClick={() => openDynamics({ region, scene })}
// region may be omitted when a scene key is given (resolved via /api/v3/scene_zones/scenes).
// Picking a date in the panel opens that snapshot through the same UI element a user would press
// (timeline mark or list row with data-scene), so CaseApp keeps full control of its state.
import { useEffect, useState } from 'react';
import { get } from './api3';
import DynamicsPanel from './DynamicsPanel';

const EV = 'mp:dynamics';
export interface DynOpen {
  region?: string | null;
  scene?: string | null;
}
export function openDynamics(o: DynOpen) {
  window.dispatchEvent(new CustomEvent<DynOpen>(EV, { detail: o }));
}

let regionOfScene: Promise<Map<string, string>> | null = null;
function sceneRegions(): Promise<Map<string, string>> {
  if (!regionOfScene) {
    regionOfScene = get<{ scenes: { scene_key: string; region?: string }[] }>('/api/v3/scene_zones/scenes')
      .then((d) => new Map(d.scenes.filter((s) => s.region).map((s) => [s.scene_key, s.region!])))
      .catch((e) => {
        regionOfScene = null;
        throw e;
      });
  }
  return regionOfScene;
}

function pickScene(key: string) {
  const sel = [`[data-testid="timeline-scene"][data-scene="${key}"]`, `[data-testid="scene-item"][data-scene="${key}"]`];
  for (const s of sel) {
    const el = document.querySelector(s);
    if (el) {
      el.dispatchEvent(new MouseEvent('click', { bubbles: true }));
      return true;
    }
  }
  return false;
}

export default function DynamicsHost() {
  const [st, setSt] = useState<{ region: string; scene: string | null } | null>(null);
  useEffect(() => {
    const on = (e: Event) => {
      const o = (e as CustomEvent<DynOpen>).detail ?? {};
      if (o.region) setSt({ region: o.region, scene: o.scene ?? null });
      else if (o.scene)
        sceneRegions().then(
          (m) => {
            const r = m.get(o.scene!);
            if (r) setSt({ region: r, scene: o.scene! });
          },
          () => undefined,
        );
    };
    window.addEventListener(EV, on);
    return () => window.removeEventListener(EV, on);
  }, []);
  if (!st) return null;
  return (
    <DynamicsPanel
      region={st.region}
      scene={st.scene}
      onScene={(k) => {
        if (pickScene(k)) return setSt((x) => (x ? { ...x, scene: k } : x));
        // another snapshot is open and the timeline is folded: back to the list, then its row
        const back = document.querySelector<HTMLElement>('[data-testid="scene-back"]');
        if (back) {
          back.click();
          setTimeout(() => pickScene(k) && setSt((x) => (x ? { ...x, scene: k } : x)), 450);
        }
      }}
      onClose={() => setSt(null)}
    />
  );
}
