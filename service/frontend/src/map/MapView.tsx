import { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { MapboxOverlay } from '@deck.gl/mapbox';
import type { Basemap, Camera, Region, Zone } from '../types';
import { buildLayers, type LayerCtx } from './layers';
import { anim, ctl, darkStyle, fitOverview, offlineStyle, satelliteStyle } from './controller';
import { fmtPermille } from '../lib/style';
import { shortName } from '../lib/data';

export interface MapViewProps extends Omit<LayerCtx, 'hour' | 'zoom' | 'spread'> {
  basemap: Basemap;
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
  const loaded = useRef(false);

  // ---- init once ----
  useEffect(() => {
    const regions = p.regions;
    const init = p.initialCamera;
    const map = new maplibregl.Map({
      container: el.current!,
      style: offlineStyle(),
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
    if (p.basemap === 'none') map.setStyle(offlineStyle(), { diff: false });
    else if (p.basemap === 'satellite') map.setStyle(satelliteStyle(), { diff: false });
    else
      darkStyle()
        .then((s) => !cancelled && map.setStyle(s, { diff: false }))
        .catch(() => !cancelled && props.current.onBasemapFailed('dark'));
    return () => {
      cancelled = true;
    };
  }, [p.basemap]);

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
    const ranked = [...p.regions].sort((a, b) => (b.summary?.index_permille ?? -1) - (a.summary?.index_permille ?? -1));
    for (const r of ranked) {
      const d = document.createElement('button');
      d.className = 'region-marker';
      d.setAttribute('data-testid', `region-marker-${r.id}`);
      const idx = r.summary?.index_permille;
      d.title = `${r.name} — ${fmtPermille(idx)} ‰`;
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
    layout();
    const t = setTimeout(layout, 300); // after fonts
    map.on('zoomend', layout);
    map.on('resize', layout);
    return () => {
      clearTimeout(t);
      map.off('zoomend', layout);
      map.off('resize', layout);
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

  return <div ref={el} className="map" data-testid="map" />;
}


function esc(s: string) {
  return s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]!);
}
