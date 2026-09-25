import { useEffect, useRef, useState } from 'react';
import maplibregl from 'maplibre-gl';
import { MapboxOverlay } from '@deck.gl/mapbox';
import { BitmapLayer, GeoJsonLayer, ScatterplotLayer } from '@deck.gl/layers';
import { centroid } from '../map/layers';
import type { Basemap, Manifest, SceneRef } from '../types';
import { getImage, isFlagged, loadDetections, shortName } from '../lib/data';
import { offlineStyle, satelliteStyle } from '../map/controller';
import { apiCompare, mergeApi, sceneStats, type SceneStats } from '../lib/stats';
import { ACCENT_RGB, fmtArea, fmtDate, fmtNum, fmtPct, fmtPermille, modelLabel } from '../lib/style';

interface Props {
  manifest: Manifest;
  compare: { a: SceneRef; b: SceneRef };
  model: string;
  basemap: Basemap;
  onClose: () => void;
  onChange: (c: { a: SceneRef; b: SceneRef }) => void;
}

type Maps = React.MutableRefObject<(maplibregl.Map | null)[]>;

// compare maps: Esri imagery when the main map is on satellite, else the calm offline outline (the S2 crops carry the context)
const baseStyle = (b: Basemap) => (b === 'satellite' ? satelliteStyle('mercator') : offlineStyle('mercator'));

type Row = { label: string; get: (s: SceneStats) => number | null; fmt: (v: number | null) => string; unit?: string };
const ROWS: Row[] = [
  { label: 'Индекс, средний по ячейкам', get: (s) => s.meanIndex, fmt: fmtPermille, unit: '‰' },
  { label: 'Индекс, Σ помечено / Σ вода', get: (s) => s.pooledIndex, fmt: fmtPermille, unit: '‰' },
  { label: 'Макс. индекс ячейки', get: (s) => s.maxCell, fmt: fmtPermille, unit: '‰' },
  { label: 'Обнаружений', get: (s) => s.nDetections, fmt: (v) => fmtNum(v) },
  { label: 'Площадь пятен', get: (s) => s.areaM2, fmt: (v) => (v === null ? '—' : (v < 0 ? '−' : '') + fmtArea(Math.abs(v)).join(' ')) },
  { label: 'Зона №1, индекс', get: (s) => s.topZone, fmt: fmtPermille, unit: '‰' },
  { label: 'Облачность', get: (s) => s.cloud, fmt: (v) => fmtPct(v) },
  { label: 'Ячеек с данными', get: (s) => s.cellsObserved, fmt: (v) => fmtNum(v) },
];

export default function CompareView({ manifest, compare, model, basemap, onClose, onChange }: Props) {
  const maps = useRef<(maplibregl.Map | null)[]>([null, null]);
  const same = compare.a.region === compare.b.region;
  const [stats, setStats] = useState<[SceneStats | null, SceneStats | null] | null>(null);

  useEffect(() => {
    let alive = true;
    setStats(null);
    Promise.all([sceneStats(manifest, compare.a, model), sceneStats(manifest, compare.b, model), apiCompare(compare.a, compare.b, model)]).then(
      ([a, b, api]) => {
        if (!alive) return;
        setStats([a && mergeApi(a, api?.a), b && mergeApi(b, api?.b)]);
      },
    );
    return () => {
      alive = false;
    };
  }, [manifest, compare, model]);

  const set = (side: 'a' | 'b', ref: Partial<SceneRef>) => {
    const cur = { ...compare[side], ...ref };
    if (ref.region) {
      const r = manifest.regions.find((x) => x.id === ref.region);
      cur.date = r?.dates[r.dates.length - 1]?.date ?? cur.date;
    }
    onChange({ ...compare, [side]: cur });
  };

  const [a, b] = stats ?? [null, null];
  return (
    <div className="view" data-testid="compare-view">
      <div className="view-head" style={{ flexWrap: 'wrap' }}>
        <h2>Сравнение</h2>
        {(['a', 'b'] as const).map((side) => {
          const ref = compare[side];
          const r = manifest.regions.find((x) => x.id === ref.region);
          return (
            <div key={side} style={{ display: 'flex', alignItems: 'center', gap: 'var(--s1)' }} data-testid={`compare-picker-${side}`}>
              <span className="muted" style={{ fontWeight: 600 }}>
                {side.toUpperCase()}
              </span>
              <select
                className="sel"
                value={ref.region}
                onChange={(e) => set(side, { region: e.target.value })}
                data-testid={`compare-region-${side}`}
                aria-label={`Район ${side.toUpperCase()}`}
              >
                {manifest.regions.map((x) => (
                  <option key={x.id} value={x.id} title={x.name}>
                    {shortName(x.name)}
                  </option>
                ))}
              </select>
              <select
                className="sel"
                value={ref.date}
                onChange={(e) => set(side, { date: e.target.value })}
                data-testid={`compare-date-${side}`}
                aria-label={`Дата ${side.toUpperCase()}`}
              >
                {r?.dates.map((d) => (
                  <option key={d.date} value={d.date}>
                    {fmtDate(d.date)}
                    {isFlagged(d) ? ' · дымка/блик' : ''}
                  </option>
                ))}
              </select>
            </div>
          );
        })}
        <span className="faint small" style={{ marginLeft: 'auto' }}>
          {modelLabel(model, manifest.models[model]?.name)}
          {same ? ' · карты синхронны' : ''}
        </span>
        <button className="icon-btn" onClick={onClose} aria-label="Закрыть сравнение" title="Закрыть сравнение" data-testid="compare-close">
          ×
        </button>
      </div>

      <div className="view-body" style={{ display: 'flex', flexDirection: 'column' }}>
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 1,
            background: 'var(--line)',
            flex: '1 0 360px',
            minHeight: 360,
            borderBottom: '1px solid var(--line)',
          }}
        >
          <Pane side="a" refScene={compare.a} {...{ manifest, model, basemap, maps, same }} />
          <Pane side="b" refScene={compare.b} {...{ manifest, model, basemap, maps, same }} />
        </div>

        <section className="sec" style={{ flex: 'none', maxWidth: 960, width: '100%', alignSelf: 'center' }} data-testid="compare-panel">
          <div className="sec-h">
            <h3>Различия</h3>
            <span className="aside">{a?.source === 'api' ? 'источник: API сервиса' : stats ? 'посчитано в браузере из файлов слоя данных' : '…'}</span>
          </div>
          <table className="t" data-testid="compare-table">
            <thead>
              <tr>
                <th />
                <th className="r">A</th>
                <th className="r">B</th>
                <th className="r">B − A</th>
              </tr>
            </thead>
            <tbody>
              {ROWS.map((row) => {
                const va = a ? row.get(a) : null;
                const vb = b ? row.get(b) : null;
                const d = va !== null && vb !== null ? vb - va : null;
                // «+400 %» from 0.0001 → 0.0005 is noise: hide the relative change when A or B − A rounds to zero
                const looksZero = (v: number | null) => v === null || /^0(\s|$|,0*(\s|$))/.test(row.fmt(Math.abs(v)));
                const tiny = (v: number | null) => looksZero(v) || row.fmt(Math.abs(v as number)).startsWith('<');
                const rel = d !== null && va && !tiny(va) && !tiny(d) ? d / Math.abs(va) : null;
                const dZero = d !== null && looksZero(d);
                const u = row.unit ? ` ${row.unit}` : '';
                return (
                  <tr key={row.label}>
                    <td className="muted">{row.label}</td>
                    <td className="r">{stats ? row.fmt(va) + (va !== null ? u : '') : '…'}</td>
                    <td className="r">{stats ? row.fmt(vb) + (vb !== null ? u : '') : '…'}</td>
                    <td className="r">
                      {d === null ? '—' : dZero ? row.fmt(0) : tiny(d) ? '≈ 0' : `${d > 0 ? '+' : ''}${row.fmt(d)}${u}`}
                      {rel !== null && Number.isFinite(rel) && (
                        <span className="faint">
                          {' '}
                          ({rel > 0 ? '+' : ''}
                          {Math.round(rel * 100)} %)
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="note" style={{ marginTop: 'var(--s1)' }}>
            При разной облачности сравнение менее надёжно. Контуры — находки модели на снимке; согласие моделей, не проверка на месте.
          </p>
          <p className="tiny faint" style={{ marginTop: 'var(--s1)' }} data-testid="compare-caption">
            индекс по снимку, не масса пластика
          </p>
        </section>
      </div>
    </div>
  );
}

function Pane({
  side,
  refScene,
  manifest,
  model,
  basemap,
  maps,
  same,
}: {
  side: 'a' | 'b';
  refScene: SceneRef;
  manifest: Manifest;
  model: string;
  basemap: Basemap;
  maps: Maps;
  same: boolean;
}) {
  const el = useRef<HTMLDivElement>(null);
  const overlay = useRef<MapboxOverlay | null>(null);
  const applied = useRef<Basemap | null>(null);
  const [ready, setReady] = useState(false);
  const [nDet, setNDet] = useState<number | null>(null);
  const idx = side === 'a' ? 0 : 1;
  const region = manifest.regions.find((r) => r.id === refScene.region);
  const de = region?.dates.find((d) => d.date === refScene.date);
  const mdl = de && de.models.includes(model) ? model : de?.models[0];

  useEffect(() => {
    const b = de?.bounds ?? region?.bounds;
    setReady(false);
    const map = new maplibregl.Map({
      container: el.current!,
      style: baseStyle(basemap),
      bounds: b ? [[b[0], b[1]], [b[2], b[3]]] : undefined,
      fitBoundsOptions: { padding: 40 },
      attributionControl: { compact: true },
      fadeDuration: 0,
    });
    applied.current = basemap;
    map.on('error', () => {});
    maps.current[idx] = map;
    const o = new MapboxOverlay({ interleaved: false, layers: [] });
    map.addControl(o as any);
    overlay.current = o;
    map.on('load', () => setReady(true));
    // camera sync when both panes show the same region
    const sync = () => {
      if (!same || (map as any).__syncing) return;
      const other = maps.current[1 - idx];
      if (!other) return;
      (other as any).__syncing = true;
      other.jumpTo({ center: map.getCenter(), zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() });
      (other as any).__syncing = false;
    };
    map.on('move', sync);
    return () => {
      maps.current[idx] = null;
      overlay.current = null;
      map.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refScene.region, refScene.date, same]);

  useEffect(() => {
    const map = maps.current[idx];
    if (!map || applied.current === basemap) return;
    applied.current = basemap;
    map.setStyle(baseStyle(basemap), { diff: false });
  }, [basemap, idx, maps]);

  useEffect(() => {
    setNDet(null);
    if (!de || !mdl || !ready) return;
    let alive = true;
    Promise.all([getImage(de.rgb).catch(() => null), loadDetections(refScene.region, de.date, mdl)]).then(([img, det]) => {
      if (!alive || !overlay.current) return;
      const feats = det?.features ?? [];
      const finds = feats.filter((f) => !f.properties.artifact);
      const arts = feats.filter((f) => !!f.properties.artifact);
      setNDet(finds.length);
      overlay.current.setProps({
        layers: [
          img && new BitmapLayer({ id: `cmp-rgb-${side}`, image: img as any, bounds: de.bounds as any }),
          arts.length &&
            new GeoJsonLayer({
              id: `cmp-art-${side}`,
              data: { type: 'FeatureCollection', features: arts } as any,
              filled: false,
              getLineColor: [168, 176, 186, 200],
              lineWidthUnits: 'pixels',
              getLineWidth: 1,
              lineWidthMinPixels: 1,
            }),
          finds.length &&
            new GeoJsonLayer({
              id: `cmp-det-${side}`,
              data: { type: 'FeatureCollection', features: finds } as any,
              getFillColor: [...ACCENT_RGB, 70],
              getLineColor: [...ACCENT_RGB, 255],
              lineWidthUnits: 'pixels',
              getLineWidth: 1.5,
              lineWidthMinPixels: 1,
            }),
          finds.length &&
            finds.length <= 400 &&
            new ScatterplotLayer({
              id: `cmp-halo-${side}`,
              data: finds,
              getPosition: (f: any) => centroid(f),
              getRadius: (f: any) => Math.min(20, 8 + 0.1 * Math.sqrt(Math.max(0, f.properties.area_m2))),
              radiusUnits: 'pixels',
              stroked: true,
              filled: false,
              getLineColor: [...ACCENT_RGB, 230],
              lineWidthUnits: 'pixels',
              getLineWidth: 1.5,
            }),
        ].filter(Boolean) as any,
      });
      (window as any)[`__compareReady_${side}`] = true;
    });
    return () => {
      alive = false;
      (window as any)[`__compareReady_${side}`] = false;
    };
  }, [de, mdl, ready, refScene.region, side]);

  return (
    <div style={{ position: 'relative', background: 'var(--bg)', minWidth: 0 }} data-testid={`compare-pane-${side}`}>
      <div ref={el} className="map" />
      <div
        style={{
          position: 'absolute',
          zIndex: 2,
          top: 'var(--s1)',
          left: 'var(--s1)',
          display: 'flex',
          gap: 'var(--s1)',
          alignItems: 'baseline',
          background: 'var(--panel)',
          border: '1px solid var(--line)',
          borderRadius: 'var(--r)',
          padding: '4px 8px',
          fontSize: 12,
          maxWidth: 'calc(100% - 16px)',
        }}
      >
        <b style={{ fontWeight: 600 }}>{side.toUpperCase()}</b>
        <span title={region?.name} style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {region ? shortName(region.name) : refScene.region}
          <span className="muted"> · {fmtDate(refScene.date)}</span>
          {mdl && mdl !== model ? <span className="faint"> · {modelLabel(mdl)}</span> : null}
        </span>
        {nDet !== null && <span className={nDet ? 'accent' : 'faint'}>{fmtNum(nDet)} нах.</span>}
      </div>
    </div>
  );
}
