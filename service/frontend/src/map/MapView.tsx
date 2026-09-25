import { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { MapboxOverlay } from '@deck.gl/mapbox';
import type { Basemap, Camera, Projection, Region, Zone } from '../types';
import { buildLayers, type LayerCtx } from './layers';
import { anim, ctl, darkStyle, fitOverview, offlineStyle, satelliteStyle, withProjection } from './controller';
import { fmtKm } from '../lib/route';
import { fmtPermille } from '../lib/style';
import { rankRegions, regionReliability, shortName } from '../lib/data';

export interface MapViewProps extends Omit<LayerCtx, 'hour' | 'zoom' | 'spread'> {
  basemap: Basemap;
  /** L27: «Карта | Глобус» */
  projection: Projection;
  initialCamera?: Camera;
  zones: Zone[] | null;
  showZones: boolean;
  onCamera: (c: Camera) => void;
  onBasemapFailed: (b: Basemap) => void;
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
  const props = useRef(p);
  props.current = p;
  const regionMarkers = useRef<maplibregl.Marker[]>([]);
  const zoneMarkers = useRef<maplibregl.Marker[]>([]);
  const routeMarkers = useRef<maplibregl.Marker[]>([]);
  const loaded = useRef(false);

  // ---- init once ----
  useEffect(() => {
    const regions = p.regions;
    const init = p.initialCamera;
    ctl.projection = p.projection;
    const map = new maplibregl.Map({
      container: el.current!,
      style: offlineStyle(p.projection),
      center: init ? [init.lon, init.lat] : [0, 10],
      zoom: init ? init.zoom : 1.6,
      pitch: init?.pitch ?? 0,
      bearing: init?.bearing ?? 0,
      maxPitch: 70,
      attributionControl: false,
      fadeDuration: 150,
      canvasContextAttributes: { antialias: true, powerPreference: 'high-performance' } as any,
    } as any);
    ctl.map = map;
    if (!init && regions.length) fitOverview(regions.map((r) => r.bounds), 0);
    map.addControl(new maplibregl.ScaleControl({ unit: 'metric', maxWidth: 120 }), 'bottom-left');

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

    // swallow tile/style network errors (they are shown in the UI, not in console spam)
    let tileErrors = 0;
    map.on('error', (e: any) => {
      tileErrors++;
      if (tileErrors === 8 && props.current.basemap === 'satellite') props.current.onBasemapFailed('satellite');
      void e;
    });
    // halo fade depends on zoom: re-render deck layers when the fade bucket changes
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

    return () => {
      regionMarkers.current.forEach((m) => m.remove());
      zoneMarkers.current.forEach((m) => m.remove());
      routeMarkers.current.forEach((m) => m.remove());
      map.remove();
      ctl.map = null;
      ctl.overlay = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---- basemap ----
  useEffect(() => {
    const map = ctl.map!;
    let cancelled = false;
    if (p.basemap === 'none') map.setStyle(offlineStyle(ctl.projection), { diff: false });
    else if (p.basemap === 'satellite') map.setStyle(satelliteStyle(ctl.projection), { diff: false });
    else
      darkStyle()
        .then((s) => !cancelled && map.setStyle(withProjection(s, ctl.projection), { diff: false }))
        .catch(() => !cancelled && props.current.onBasemapFailed('dark'));
    return () => {
      cancelled = true;
    };
  }, [p.basemap]);

  // ---- L27: projection (map ⇄ globe) without reloading the basemap ----
  const firstProj = useRef(true);
  useEffect(() => {
    const map = ctl.map!;
    ctl.projection = p.projection;
    if (firstProj.current) {
      firstProj.current = false; // the initial style already carries the projection
      return;
    }
    const apply = () => {
      const s = withProjection({}, p.projection);
      map.setProjection(s.projection);
      try {
        map.setSky(s.sky ?? {});
      } catch {
        /* older style without sky support */
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

  // ---- region markers (overview) ----
  useEffect(() => {
    const map = ctl.map!;
    regionMarkers.current.forEach((m) => m.remove());
    regionMarkers.current = [];
    if (p.activeRegion) return;
    // L34: reliable regions first (by index, drawn on top); regions whose latest scene has haze/glint — muted, yellow ring
    const { ok, bad } = rankRegions(p.regions);
    const ranked = [...ok, ...bad];
    for (const r of ranked) {
      const d = document.createElement('button');
      const unrel = bad.includes(r);
      d.className = `region-marker ${unrel ? 'unreliable' : ''}`;
      d.setAttribute('data-testid', `region-marker-${r.id}`);
      const idx = r.summary?.index_permille;
      d.title = `${r.name} — ${fmtPermille(idx)} ‰${unrel ? ` · ненадёжный снимок: ${regionReliability(r).why}` : ''}`;
      d.innerHTML = `<span class="rm-dot"></span><span class="rm-name">${esc(shortName(r.name))}</span><span class="rm-val">${fmtPermille(
        idx,
      )} ‰</span>`;
      d.onclick = (e) => {
        e.stopPropagation();
        props.current.onClickRegion(r.id);
      };
      regionMarkers.current.push(new maplibregl.Marker({ element: d, anchor: 'left' }).setLngLat(r.center).addTo(map));
    }
    // label layout without overlaps, in index order (highest first, drawn on top):
    // 1) label to the right of the dot; 2) if it overlaps a placed label/dot or runs under a panel — label to the left;
    // 3) otherwise the marker collapses to a dot, its label is shown on hover.
    // L27: on the globe some regions are on the far side — say so (the list on the left has all of them)
    const hint = document.createElement('div');
    hint.className = 'globe-hint';
    hint.setAttribute('data-testid', 'globe-hint');
    hint.style.display = 'none';
    map.getContainer().appendChild(hint);
    const layout = () => {
      type Box = { x0: number; y0: number; x1: number; y1: number };
      const placed: Box[] = [];
      const hit = (b: Box) => placed.some((q) => b.x0 < q.x1 && b.x1 > q.x0 && b.y0 < q.y1 && b.y1 > q.y0);
      const cont = map.getContainer().getBoundingClientRect();
      const lp = document.querySelector('.panel-left:not(.collapsed)')?.getBoundingClientRect();
      const rp = document.querySelector('.panel-right:not(.collapsed)')?.getBoundingClientRect();
      const minX = (lp ? lp.right - cont.left : 0) + 4;
      const maxX = (rp ? rp.left - cont.left : cont.width) - 4;
      const els = regionMarkers.current.map((mk) => mk.getElement());
      els.forEach((el) => el.classList.remove('compact', 'flip'));
      const sizes = els.map((el) => [el.offsetWidth || 180, el.offsetHeight || 30]);
      const n = els.length;
      regionMarkers.current.forEach((mk, i) => {
        const el = els[i];
        el.style.zIndex = String(10 + n - i);
        // globe: a label on the far side of the sphere takes no space (MapLibre marks it as covered)
        if ((map as any).transform?.isLocationOccluded?.(mk.getLngLat())) {
          el.classList.add('occluded');
          return;
        }
        el.classList.remove('occluded');
        const pt = map.project(mk.getLngLat());
        const [w, h] = sizes[i];
        const y0 = pt.y - h / 2 - 2,
          y1 = pt.y + h / 2 + 2;
        const right = { x0: pt.x - 15, y0, x1: pt.x - 13 + w + 2, y1 };
        const left = { x0: pt.x + 13 - w - 2, y0, x1: pt.x + 15, y1 };
        const fits = (b: Box) => !hit(b) && b.x0 >= minX && b.x1 <= maxX;
        if (fits(right)) {
          mk.setOffset([-13, 0]);
          placed.push(right);
        } else if (fits(left)) {
          el.classList.add('flip');
          mk.setOffset([13 - w, 0]);
          placed.push(left);
        } else {
          el.classList.add('compact');
          mk.setOffset([-13, 0]);
          placed.push({ x0: pt.x - 15, y0: pt.y - 14, x1: pt.x + 15, y1: pt.y + 14 });
        }
      });
    };
    const layoutAll = () => {
      layout();
      const n = regionMarkers.current.filter((m) => m.getElement().classList.contains('occluded')).length;
      const w = n % 10 === 1 && n % 100 !== 11 ? 'район' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'района' : 'районов';
      hint.textContent = n ? `Ещё ${n} ${w} на обратной стороне глобуса — потяните глобус или выберите в списке слева` : '';
      hint.style.display = n ? '' : 'none';
    };
    layoutAll();
    const t = setTimeout(layoutAll, 300); // after fonts
    map.on('zoomend', layoutAll);
    map.on('moveend', layoutAll); // globe rotation changes which labels are visible
    map.on('resize', layoutAll);
    return () => {
      clearTimeout(t);
      hint.remove();
      map.off('moveend', layoutAll);
      map.off('zoomend', layoutAll);
      map.off('resize', layoutAll);
    };
  }, [p.activeRegion, p.regions]);

  // ---- zone markers (pulsing, rank number) ----
  useEffect(() => {
    const map = ctl.map!;
    zoneMarkers.current.forEach((m) => m.remove());
    zoneMarkers.current = [];
    if (!p.showZones || !p.zones) return;
    for (const z of p.zones) {
      const d = document.createElement('button');
      d.className = `zone-marker ${z.rank <= 3 ? 'top' : ''}`;
      d.setAttribute('data-testid', `zone-marker-${z.rank}`);
      d.title = `Приоритет обследования №${z.rank}`;
      d.innerHTML = `<span class="zm-pulse"></span><span class="zm-num">${z.rank}</span>`;
      d.onclick = (e) => {
        e.stopPropagation();
        props.current.onZoneClick(z);
      };
      zoneMarkers.current.push(new maplibregl.Marker({ element: d }).setLngLat([z.lon, z.lat]).addTo(map));
    }
  }, [p.zones, p.showZones]);

  // ---- L27: «Порядок посещения зон» — port pin + visit numbers at the middle of each leg ----
  useEffect(() => {
    const map = ctl.map!;
    routeMarkers.current.forEach((m) => m.remove());
    routeMarkers.current = [];
    const r = p.route;
    if (!r) return;
    const port = document.createElement('div');
    port.className = 'route-port';
    port.setAttribute('data-testid', 'route-port');
    port.innerHTML = `<span class="rp-dot"></span><span class="rp-name">${esc(r.port.name)}</span>`;
    routeMarkers.current.push(new maplibregl.Marker({ element: port, anchor: 'left', offset: [-6, 0] }).setLngLat([r.port.lon, r.port.lat]).addTo(map));
    for (const l of r.legs) {
      const d = document.createElement('div');
      d.className = 'route-num';
      d.title = `${l.n}-й по порядку: зона №${l.zone.rank}, ${fmtKm(l.km)} км по прямой от предыдущей точки`;
      d.textContent = String(l.n);
      const mid: [number, number] = [(l.from[0] + l.to[0]) / 2, (l.from[1] + l.to[1]) / 2];
      routeMarkers.current.push(new maplibregl.Marker({ element: d }).setLngLat(mid).addTo(map));
    }
    // a visit number on a leg shorter than ~46 px would cover the zone markers: hide it at this zoom
    const fit = () => {
      // port label: to the left of the pin when it would run under the right panel
      const pm = routeMarkers.current[0];
      if (pm) {
        const el = pm.getElement();
        const pt = map.project(pm.getLngLat());
        const cont = map.getContainer().getBoundingClientRect();
        const rp = document.querySelector('.panel-right:not(.collapsed)')?.getBoundingClientRect();
        const maxX = (rp ? rp.left : cont.right) - cont.left - 8;
        const flip = pt.x + el.offsetWidth > maxX;
        el.classList.toggle('flip', flip);
        pm.setOffset(flip ? [6 - el.offsetWidth, 0] : [-6, 0]);
      }
      r.legs.forEach((l, i) => {
        const a = map.project(l.from as any),
          b = map.project(l.to as any);
        const el = routeMarkers.current[i + 1]?.getElement();
        if (el) el.style.visibility = Math.hypot(a.x - b.x, a.y - b.y) < 46 ? 'hidden' : '';
      });
    };
    fit();
    map.on('moveend', fit);
    return () => {
      map.off('moveend', fit);
    };
  }, [p.route]);

  return <div ref={el} className="map" data-testid="map" />;
}


function esc(s: string) {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
}
