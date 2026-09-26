// L151 · §62 п.2 / §63 п.4: «Дроны и фото» on the map — one green-diamond marker per set (legend: .c-mk.dr).
// Place comes from /api/v3/drones map_point: "exact" = a real frame location of the set; "region" = the centre of the
// region the set names (label «точное место съёмки неизвестно»); "none" (UCWD, FML) = no marker, list only.
// Marker click -> the set in the «Дроны» panel; flyToDrones(id) (list click) -> fly to the marker and pulse it.
// Uses the shared map controller (ctl.map); HTML markers survive style/basemap reloads.
import maplibregl from 'maplibre-gl';
import { useEffect, useState } from 'react';
import { ctl } from '../map/controller';
import { get } from './api3';
import { openDrones } from './DronesHost';
import './drones.css';

interface MapPoint {
  kind: 'exact' | 'region' | 'none';
  lon: number | null;
  lat: number | null;
  frame: string | null;
  label?: string;
  note: string;
}
interface SetPt {
  id: string;
  name: string;
  sensor_label: string;
  n_frames: number;
  map_point?: MapPoint;
}

const pts = new Map<string, { lon: number; lat: number; el: HTMLElement }>();

/** list click: fly to the set's marker; false when the set has no place (UCWD, FML) or the layer is off */
export function flyToDrones(id: string): boolean {
  const p = pts.get(id);
  const map = ctl.map;
  if (!p || !map) return false;
  map.flyTo({ center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), 5), duration: 1400, essential: true });
  p.el.classList.remove('pulse');
  void p.el.offsetWidth;
  p.el.classList.add('pulse');
  return true;
}

export default function DronesMapLayer({ on }: { on: boolean }) {
  const [sets, setSets] = useState<SetPt[] | null>(null);
  const [map, setMap] = useState<maplibregl.Map | null>(ctl.map);
  useEffect(() => {
    if (!on || sets) return;
    get<{ sets: SetPt[] }>('/api/v3/drones').then((d) => setSets(d.sets), () => setSets([]));
  }, [on, sets]);
  useEffect(() => {
    // the map may be created after us or re-created (projection switch): follow ctl.map
    const t = setInterval(() => setMap((m) => (m === ctl.map ? m : ctl.map)), 700);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    if (!on || !map || !sets) return;
    const markers: maplibregl.Marker[] = [];
    for (const s of sets) {
      const p = s.map_point;
      if (!p || p.kind === 'none' || p.lon == null || p.lat == null) continue;
      const el = document.createElement('button');
      el.className = `dr-mk ${p.kind}`;
      el.type = 'button';
      el.setAttribute('data-testid', 'drones-marker');
      el.setAttribute('data-set', s.id);
      el.setAttribute('data-kind', p.kind);
      const where = p.kind === 'exact' ? 'место кадра из набора' : `${p.label ?? 'регион'} — точное место съёмки неизвестно`;
      el.title = `${s.name}\n${s.sensor_label} · ${s.n_frames} кадр.\n${where}\nдрон/детальное фото, не спутник`;
      el.setAttribute('aria-label', `${s.name}: ${where}`);
      const dia = document.createElement('span');
      dia.className = 'dr-mk-d';
      el.appendChild(dia);
      const lab = document.createElement('span');
      lab.className = 'dr-mk-lab';
      lab.textContent = p.kind === 'exact' ? s.name.split(' — ')[0] : `${s.name.split(' — ')[0]} · место неизвестно`;
      el.appendChild(lab);
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        openDrones(s.id);
      });
      const m = new maplibregl.Marker({ element: el, anchor: 'center' }).setLngLat([p.lon, p.lat]).addTo(map);
      markers.push(m);
      pts.set(s.id, { lon: p.lon, lat: p.lat, el });
    }
    return () => {
      markers.forEach((m) => m.remove());
      pts.clear();
    };
  }, [on, map, sets]);
  return null;
}
