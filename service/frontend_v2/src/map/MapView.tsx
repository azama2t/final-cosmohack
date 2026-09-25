import { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import { MapboxOverlay } from '@deck.gl/mapbox';
import type { Basemap, Camera, Projection, Region, Zone } from '../types';
import { buildLayers, type LayerCtx } from './layers';
import { anim, angDist, ctl, darkStyle, ESRI_TILES, CARTO_DARK, fitOverview, offlineStyle, satelliteStyle, withProjection } from './controller';
import { fmtKm } from '../lib/route';
import { fmtPermille, rankColor, rgbStr } from '../lib/style';
import { rankRegions, regionReliability, shortName } from '../lib/data';
import { attachStars } from './stars';

/** style of the effective basemap; «dark» needs a fetch (CARTO GL style) — null here */
function syncStyle(b: Basemap, proj: Projection): any | null {
  if (b === 'satellite') return satelliteStyle(proj);
  if (b === 'none') return offlineStyle(proj);
  return null;
}

/** one cheap request to the online basemap: resolves true when the network is back */
function probeOnline(b: Basemap): Promise<boolean> {
  if (b === 'dark') return fetch(CARTO_DARK, { cache: 'no-store' }).then((r) => r.ok, () => false);
  return new Promise((res) => {
    const img = new Image();
    img.onload = () => res(true);
    img.onerror = () => res(false);
    img.src = ESRI_TILES.replace('{z}', '0').replace('{y}', '0').replace('{x}', '0') + `?probe=${Date.now()}`;
  });
}

export interface MapViewProps extends Omit<LayerCtx, 'hour' | 'zoom' | 'spread'> {
  /** the user's basemap (never changed by the map itself) */
  basemap: Basemap;
  /** temporary offline fallback: online tiles do not load (does not touch the user's choice) */
  offline: boolean;
  onOffline: (on: boolean) => void;
  projection: Projection;
  initialCamera?: Camera;
  zones: Zone[] | null;
  showZones: boolean;
  activeZone: number | null;
  onCamera: (c: Camera) => void;
  onZoneClick: (z: Zone) => void;
}

declare global {
  interface Window {
    __mapReady?: boolean;
    __fps?: number;
    __app?: any;
  }
}

export default function MapView(p: MapViewProps) {
  const el = useRef<HTMLDivElement>(null);
  const stars = useRef<HTMLDivElement>(null);
  const props = useRef(p);
  props.current = p;
  const regionMarkers = useRef<maplibregl.Marker[]>([]);
  const zoneMarkers = useRef<maplibregl.Marker[]>([]);
  const routeMarkers = useRef<maplibregl.Marker[]>([]);
  const loaded = useRef(false);
  const halo = useRef<HTMLDivElement>(null);
  const eff: Basemap = p.offline ? 'none' : p.basemap;
  const appliedStyle = useRef<Basemap | null>(null);

  // ---- init once ----
  useEffect(() => {
    const init = p.initialCamera;
    ctl.projection = p.projection;
    const map = new maplibregl.Map({
      container: el.current!,
      style: syncStyle(eff, p.projection) ?? offlineStyle(p.projection),
      center: init ? [init.lon, init.lat] : [-40, 12],
      zoom: init ? init.zoom : 1.6,
      pitch: init?.pitch ?? 0,
      bearing: init?.bearing ?? 0,
      maxPitch: 70,
      attributionControl: false,
      fadeDuration: 200,
      canvasContextAttributes: { antialias: true, powerPreference: 'high-performance' } as any,
    } as any);
    ctl.map = map;
    if (!init && p.regions.length) fitOverview(p.regions.map((r) => r.bounds), 0);
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric', maxWidth: 100 }), 'bottom-left');

    const overlay = new MapboxOverlay({
      interleaved: false,
      layers: [],
      getCursor: ({ isHovering, isDragging }) => (isDragging ? 'grabbing' : isHovering ? 'pointer' : 'grab'),
      onAfterRender: () => {
        if (!window.__mapReady && loaded.current && (overlay as any)._deck?.props?.layers?.length) {
          window.__mapReady = true;
          (window as any).__mapReadyAt = performance.now();
        }
      },
    });
    map.addControl(overlay as any);
    ctl.overlay = overlay;
    appliedStyle.current = syncStyle(eff, p.projection) ? eff : null;
    // Offline detector: ONLY consecutive failures of the online basemap tiles (a successful tile resets the count),
    // or no tile at all within 12 s. Errors of our own data never switch the basemap. The listener also keeps
    // network errors out of the console.
    let streak = 0;
    let lastOk = 0;
    let styleAt = performance.now();
    const isOnlineTile = (e: any) => e?.sourceId === 'esri' || /arcgisonline|cartocdn|carto\.com/.test(String(e?.error?.url ?? e?.error?.message ?? ''));
    map.on('error', (e: any) => {
      if (!isOnlineTile(e) || props.current.offline || props.current.basemap === 'none') return;
      streak++;
      if (streak >= 4) props.current.onOffline(true);
    });
    map.on('sourcedata', (e: any) => {
      if (e.tile && e.sourceId === 'esri') {
        streak = 0;
        lastOk = performance.now();
      }
    });
    const watchdog = setInterval(() => {
      const cur = props.current;
      if (cur.offline || cur.basemap !== 'satellite' || appliedStyle.current !== 'satellite') return;
      if (!lastOk && streak > 0 && performance.now() - styleAt > 12000) cur.onOffline(true);
    }, 3000);
    (map as any).__resetStyleClock = () => {
      styleAt = performance.now();
      lastOk = 0;
      streak = 0;
    };
    let fadeBucket = -1;
    map.on('zoom', () => {
      const z = map.getZoom();
      const b = z < 12.6 ? 0 : z > 14.8 ? 99 : Math.round(z * 5);
      if (b !== fadeBucket) {
        fadeBucket = b;
        ctl.render();
      }
    });
    map.on('movestart', () => (ctl.moving = true));
    map.on('moveend', () => {
      ctl.moving = false;
      const c = map.getCenter();
      props.current.onCamera({ lon: c.lng, lat: c.lat, zoom: map.getZoom(), pitch: map.getPitch(), bearing: map.getBearing() });
    });
    map.on('load', () => {
      loaded.current = true;
      ctl.render();
    });
    const detachStars = attachStars(stars.current!, halo.current!);
    return () => {
      clearInterval(watchdog);
      detachStars();
      regionMarkers.current.forEach((m) => m.remove());
      zoneMarkers.current.forEach((m) => m.remove());
      routeMarkers.current.forEach((m) => m.remove());
      map.remove();
      ctl.map = null;
      ctl.overlay = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- basemap: the user's choice (default Esri satellite); the offline outline only while tiles do not load ----
  useEffect(() => {
    const map = ctl.map!;
    let cancelled = false;
    if (appliedStyle.current === eff) return; // the initial style already is this one: no second setStyle (no flash)
    const apply = (st: any) => {
      appliedStyle.current = eff;
      (map as any).__resetStyleClock?.();
      map.setStyle(st, { diff: false });
    };
    const st = syncStyle(eff, ctl.projection);
    if (st) apply(st);
    else
      darkStyle()
        .then((s) => !cancelled && apply(withProjection(s, ctl.projection)))
        .catch(() => !cancelled && props.current.onOffline(true));
    return () => {
      cancelled = true;
    };
  }, [eff]);

  // ---- offline: quietly probe the network, return to the user's basemap when it is back ----
  useEffect(() => {
    if (!p.offline || p.basemap === 'none') return;
    let alive = true;
    const probe = () => probeOnline(props.current.basemap).then((ok) => alive && ok && props.current.onOffline(false));
    const t = setInterval(probe, 20000);
    window.addEventListener('online', probe);
    return () => {
      alive = false;
      clearInterval(t);
      window.removeEventListener('online', probe);
    };
  }, [p.offline, p.basemap]);

  // ---- projection (map ⇄ globe) ----
  const firstProj = useRef(true);
  useEffect(() => {
    const map = ctl.map!;
    ctl.projection = p.projection;
    if (firstProj.current) {
      firstProj.current = false;
      return;
    }
    const apply = () => {
      const s = withProjection({}, p.projection);
      map.setProjection(s.projection);
      try {
        map.setSky(s.sky);
      } catch {
        /* no sky support */
      }
      ctl.render();
    };
    if ((map as any).style?._loaded) apply();
    else map.once('style.load', apply);
  }, [p.projection]);

  // ---- deck layers ----
  ctl.render = () => {
    const o = ctl.overlay;
    if (!o) return;
    o.setProps({ layers: buildLayers({ ...props.current, hour: anim.hour, spread: anim.spread, zoom: ctl.map?.getZoom() ?? 0 }) });
  };
  useEffect(() => {
    ctl.render();
  });

  // ---- region markers (overview only) ----
  useEffect(() => {
    const map = ctl.map!;
    regionMarkers.current.forEach((m) => m.remove());
    regionMarkers.current = [];
    if (p.activeRegion) return;
    const { ok, bad } = rankRegions(p.regions);
    const ranked = [...ok, ...bad];
    for (const r of ranked) {
      const d = document.createElement('button');
      const unrel = bad.includes(r);
      const hot = (r.summary?.n_detections ?? 0) > 0;
      d.className = `rm ${unrel ? 'bad' : hot ? 'hot' : ''}`;
      d.setAttribute('data-testid', `region-marker-${r.id}`);
      const idx = r.summary?.index_permille;
      d.title = `${r.name} — индекс ${fmtPermille(idx)} ‰${unrel ? ` · ненадёжный снимок: ${regionReliability(r).why}` : ''}`;
      d.innerHTML = `<span class="d"></span><span class="n">${esc(shortName(r.name))}</span>`;
      d.onclick = (e) => {
        e.stopPropagation();
        props.current.onClickRegion(r.id);
      };
      regionMarkers.current.push(new maplibregl.Marker({ element: d, anchor: 'left' }).setLngLat(r.center).addTo(map));
    }
    const layout = () => {
      type Box = { x0: number; y0: number; x1: number; y1: number };
      const placed: Box[] = [];
      const hit = (b: Box) => placed.some((q) => b.x0 < q.x1 && b.x1 > q.x0 && b.y0 < q.y1 && b.y1 > q.y0);
      const cont = map.getContainer().getBoundingClientRect();
      const lp = document.querySelector('[data-panel="left"]:not(.collapsed)')?.getBoundingClientRect();
      const minX = (lp ? lp.right - cont.left : 0) + 4;
      const maxX = cont.width - 8;
      const els = regionMarkers.current.map((mk) => mk.getElement());
      els.forEach((e) => e.classList.remove('compact', 'flip'));
      const sizes = els.map((e) => [e.offsetWidth || 160, e.offsetHeight || 22]);
      regionMarkers.current.forEach((mk, i) => {
        const e = els[i];
        e.style.zIndex = String(10 + els.length - i);
        const ll = mk.getLngLat();
        const c = map.getCenter();
        // globe: labels on the far side or close to the limb are hidden (they would overlap in a thin strip)
        if ((map as any).transform?.isLocationOccluded?.(ll) || (ctl.projection === 'globe' && map.getZoom() < 4 && angDist([ll.lng, ll.lat], [c.lng, c.lat]) > 68)) {
          e.classList.add('occluded');
          return;
        }
        e.classList.remove('occluded');
        const pt = map.project(mk.getLngLat());
        const [w, h] = sizes[i];
        const y0 = pt.y - h / 2 - 1,
          y1 = pt.y + h / 2 + 1;
        const right = { x0: pt.x - 8, y0, x1: pt.x - 8 + w, y1 };
        const left = { x0: pt.x + 8 - w, y0, x1: pt.x + 8, y1 };
        const fits = (b: Box) => !hit(b) && b.x0 >= minX && b.x1 <= maxX;
        if (fits(right)) {
          mk.setOffset([-8, 0]);
          placed.push(right);
        } else if (fits(left)) {
          e.classList.add('flip');
          mk.setOffset([8 - w, 0]);
          placed.push(left);
        } else {
          e.classList.add('compact');
          mk.setOffset([-8, 0]);
          placed.push({ x0: pt.x - 8, y0: pt.y - 8, x1: pt.x + 8, y1: pt.y + 8 });
        }
      });
    };
    layout();
    const t = setTimeout(layout, 300);
    map.on('moveend', layout);
    map.on('resize', layout);
    return () => {
      clearTimeout(t);
      map.off('moveend', layout);
      map.off('resize', layout);
    };
  }, [p.activeRegion, p.regions]);

  // ---- zone markers: square with rank, coloured by the sequential priority scale ----
  useEffect(() => {
    const map = ctl.map!;
    zoneMarkers.current.forEach((m) => m.remove());
    zoneMarkers.current = [];
    if (!p.showZones || !p.zones) return;
    const n = p.zones.length;
    for (const z of p.zones) {
      const d = document.createElement('button');
      d.className = `zm ${p.activeZone === z.rank ? 'on' : ''}`;
      d.setAttribute('data-testid', `zone-marker-${z.rank}`);
      d.title = `Приоритет обследования №${z.rank}`;
      d.style.background = rgbStr(rankColor(z.rank, Math.max(n, 5)));
      d.textContent = String(z.rank);
      d.onclick = (e) => {
        e.stopPropagation();
        props.current.onZoneClick(z);
      };
      zoneMarkers.current.push(new maplibregl.Marker({ element: d }).setLngLat([z.lon, z.lat]).addTo(map));
    }
  }, [p.zones, p.showZones, p.activeZone]);

  // ---- visiting order: port + numbers ----
  useEffect(() => {
    const map = ctl.map!;
    routeMarkers.current.forEach((m) => m.remove());
    routeMarkers.current = [];
    const r = p.route;
    if (!r) return;
    const port = document.createElement('div');
    port.className = 'route-port';
    port.setAttribute('data-testid', 'route-port');
    port.innerHTML = `<span class="p"></span><span class="t">${esc(r.port.name)}</span>`;
    routeMarkers.current.push(new maplibregl.Marker({ element: port, anchor: 'left', offset: [-4, 0] }).setLngLat([r.port.lon, r.port.lat]).addTo(map));
    for (const l of r.legs) {
      const d = document.createElement('div');
      d.className = 'route-num';
      d.title = `${l.n}-й по порядку: зона №${l.zone.rank}, ${fmtKm(l.km)} км по прямой`;
      d.textContent = String(l.n);
      const mid: [number, number] = [(l.from[0] + l.to[0]) / 2, (l.from[1] + l.to[1]) / 2];
      routeMarkers.current.push(new maplibregl.Marker({ element: d }).setLngLat(mid).addTo(map));
    }
  }, [p.route]);

  return (
    <>
      <div className="space" aria-hidden>
        <div ref={stars} className="stars" />
        <div ref={halo} className="halo" />
      </div>
      <div ref={el} className="map" data-testid="map" />
    </>
  );
}

function esc(s: string) {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
}
