// L27 «Порядок посещения зон»: draft visiting order from the nearest port through the survey zones
// (greedy nearest neighbour, straight-line distances). NOT a navigational route: no coastline, depth,
// traffic or weather — the UI always says «черновой порядок, не навигационный маршрут».
import type { Zone } from '../types';

export interface Port {
  name: string;
  lon: number;
  lat: number;
}

/**
 * Ports per region id. Coordinates are approximate (±1 km) harbour entrances / main quays, taken by hand from
 * public maps (OpenStreetMap / Wikipedia port articles). Assumption: a survey boat leaves from the nearest
 * commercial or fishing port of the region; the list is a small hand-made dictionary, not a port database.
 */
export const PORTS: Record<string, Port[]> = {
  accra: [
    { name: 'порт Тема', lon: -0.0076, lat: 5.6315 },
    { name: 'рыбацкая гавань Джеймстаун (Аккра)', lon: -0.2135, lat: 5.5335 },
  ],
  bali: [{ name: 'порт Беноа', lon: 115.2105, lat: -8.7445 }],
  danang: [{ name: 'порт Тьенша (Дананг)', lon: 108.2185, lat: 16.1245 }],
  durban: [{ name: 'порт Дурбан', lon: 31.0445, lat: -29.8715 }],
  haiti: [{ name: 'порт Порт-о-Пренс', lon: -72.3525, lat: 18.5535 }],
  honduras: [
    { name: 'порт Пуэрто-Кортес', lon: -87.9425, lat: 15.8445 },
    { name: 'Омоа', lon: -88.0395, lat: 15.7785 },
  ],
  jakarta: [{ name: 'порт Танджунг-Приок', lon: 106.8845, lat: -6.0975 }],
  lagos: [{ name: 'порт Апапа (Лагос)', lon: 3.3705, lat: 6.4395 }],
  manila: [{ name: 'порт Манила (Южная гавань)', lon: 120.9665, lat: 14.5855 }],
  santo_domingo: [{ name: 'порт Санто-Доминго (Дон-Диего)', lon: -69.8795, lat: 18.4725 }],
  scotland: [
    { name: 'порт Лит (Эдинбург)', lon: -3.1735, lat: 55.9835 },
    { name: 'порт Метил', lon: -3.0135, lat: 56.1795 },
    { name: 'гавань Анструтер', lon: -2.6985, lat: 56.2215 },
  ],
  tiber: [{ name: 'порт Фьюмичино', lon: 12.2225, lat: 41.7725 }],
};

const R_EARTH_KM = 6371.0088;
export function haversineKm(a: [number, number], b: [number, number]): number {
  const toR = Math.PI / 180;
  const dLat = (b[1] - a[1]) * toR;
  const dLon = (b[0] - a[0]) * toR;
  const s = Math.sin(dLat / 2) ** 2 + Math.cos(a[1] * toR) * Math.cos(b[1] * toR) * Math.sin(dLon / 2) ** 2;
  return 2 * R_EARTH_KM * Math.asin(Math.min(1, Math.sqrt(s)));
}

export interface RouteLeg {
  /** visit order, 1-based */
  n: number;
  zone: Zone;
  from: [number, number];
  to: [number, number];
  km: number;
}
export interface RoutePlan {
  port: Port;
  /** true if the region has no port in the dictionary and the start is the centroid of the zones */
  portGuessed: boolean;
  legs: RouteLeg[];
  path: [number, number][];
  totalKm: number;
}

/** Greedy nearest neighbour from the nearest port (to the zones' centroid) through all given zones. */
export function planRoute(regionId: string, zones: Zone[]): RoutePlan | null {
  if (!zones.length) return null;
  const cx = zones.reduce((a, z) => a + z.lon, 0) / zones.length;
  const cy = zones.reduce((a, z) => a + z.lat, 0) / zones.length;
  const cands = PORTS[regionId] ?? [];
  const port = cands.length
    ? [...cands].sort((a, b) => haversineKm([a.lon, a.lat], [cx, cy]) - haversineKm([b.lon, b.lat], [cx, cy]))[0]
    : { name: 'центр зон (порт района не задан)', lon: cx, lat: cy };
  const left = [...zones];
  let cur: [number, number] = [port.lon, port.lat];
  const legs: RouteLeg[] = [];
  const path: [number, number][] = [cur];
  while (left.length) {
    let bi = 0;
    let bd = Infinity;
    left.forEach((z, i) => {
      const d = haversineKm(cur, [z.lon, z.lat]);
      if (d < bd - 1e-9 || (Math.abs(d - bd) <= 1e-9 && z.rank < left[bi].rank)) {
        bd = d;
        bi = i;
      }
    });
    const z = left.splice(bi, 1)[0];
    const to: [number, number] = [z.lon, z.lat];
    legs.push({ n: legs.length + 1, zone: z, from: cur, to, km: bd });
    path.push(to);
    cur = to;
  }
  return { port, portGuessed: !cands.length, legs, path, totalKm: legs.reduce((a, l) => a + l.km, 0) };
}

export const fmtKm = (km: number) =>
  km < 10
    ? km.toLocaleString('ru-RU', { minimumFractionDigits: 1, maximumFractionDigits: 1 })
    : Math.round(km).toLocaleString('ru-RU');
