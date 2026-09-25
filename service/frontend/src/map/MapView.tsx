import { useEffect, useRef } from 'react';
import maplibregl from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import { MapboxOverlay } from '@deck.gl/mapbox';
import type { Basemap, Camera, Region, Zone } from '../types';
import { buildLayers, type LayerCtx } from './layers';
import { anim, ctl, darkStyle, fitOverview, offlineStyle, satelliteStyle } from './controller';
import { fmtPermille } from '../lib/style';

export interface MapViewProps extends Omit<LayerCtx, 'hour' | 'zoom'> {
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
    o.setProps({ layers: buildLayers({ ...props.current, hour: anim.hour, zoom: ctl.map?.getZoom() ?? 0 }) });
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
      d.innerHTML = `<span class="rm-dot"></span><span class="rm-name">${esc(r.name)}</span><span class="rm-val">${fmtPermille(
        idx,
      )} ‰</span>`;
      d.onclick = (e) => {
        e.stopPropagation();
        props.current.onClickRegion(r.id);
      };
      regionMarkers.current.push(new maplibregl.Marker({ element: d, anchor: 'left' }).setLngLat(r.center).addTo(map));
    }
    // simple label collision avoidance: higher-ranked labels stay, others are nudged vertically
    const layout = () => {
      const placed: { x: number; y: number; w: number; h: number }[] = [];
      for (const mk of regionMarkers.current) {
        const el = mk.getElement();
        const pt = map.project(mk.getLngLat());
        const w = el.offsetWidth || 180,
          h = el.offsetHeight || 30;
        let dy = 0;
        const hit = (y: number) => placed.some((q) => pt.x < q.x + q.w && pt.x + w > q.x && y - h / 2 < q.y + q.h / 2 && y + h / 2 > q.y - q.h / 2);
        for (let k = 1; k <= 6 && hit(pt.y + dy); k++) dy = (k % 2 ? -1 : 1) * Math.ceil(k / 2) * (h + 6);
        mk.setOffset([0, dy]);
        placed.push({ x: pt.x, y: pt.y + dy, w, h });
      }
    };
    layout();
    const t = setTimeout(layout, 300); // after fonts
    map.on('zoomend', layout);
    return () => {
      clearTimeout(t);
      map.off('zoomend', layout);
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
