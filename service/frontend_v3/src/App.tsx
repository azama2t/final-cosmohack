// v3 shell (L94): the map fills the screen; everything else lives in ONE collapsible left column
// (sections «Участки / Студия / Слои / Экспорт»). No floating cards over the map except the small click popup.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiErr, apiUrl, get, hasOilApi, type OilMeta, type OilProps, studioScenes, VIEW_LABEL, VIEW_ORDER, type FC, type Feat, type Meta, type ObsProps, type Scene, type StudioScene, type ZoneProps } from './api';
import { CONC_BREAKS, CONC_COLORS, MapCtl, type Cam, type Filters, type Pick, type PickKind } from './map';
import { bboxRing, DEFAULT_FILTERS, loadSites, loadUi, newId, saveSites, saveUi, STATUS_LABEL, type Section, type Site } from './state';
import { fmtDate, fmtNum, fmtTime, geomBounds, inRing, padBBox, ringBBox, bboxHit, inBBox, plural, type BBox } from './geo';
import Studio from './Studio';
import { Icon } from './icons';
import ExportSection, { fromQuery, queryFromUrl, type ApiQuery } from './Export';

export const COL_W = 360;
export const RAIL_W = 56;

type Cand = { kind: PickKind; id: string } | { kind: 'area'; ring: number[][]; obsIds: string[]; zoneIds: string[] };
interface Snap {
  cam: Cam;
  filters: Filters;
  cand: Cand | null;
  section: Section;
}

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]!);
const obsCenter = (f: { geometry: any; properties: ObsProps }): number[] | null =>
  Array.isArray(f.properties.track_center) ? f.properties.track_center : f.geometry?.type === 'Point' ? f.geometry.coordinates : null;
const inDates = (d: string | null | undefined, f: Filters) => !d || ((!f.from || d.slice(0, 10) >= f.from) && (!f.to || d.slice(0, 10) <= f.to));

export default function App() {
  const ui0 = useMemo(loadUi, []);
  const mapEl = useRef<HTMLDivElement>(null);
  const ctl = useRef<MapCtl | null>(null);
  const [collapsed, setCollapsed] = useState(ui0.collapsed);
  const [section, setSection] = useState<Section>(ui0.section);
  const [filters, setFilters] = useState<Filters>(ui0.filters);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [obs, setObs] = useState<FC<ObsProps> | null>(null);
  const [zones, setZones] = useState<FC<ZoneProps> | null>(null);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [studioList, setStudioList] = useState<StudioScene[]>([]);
  const [oil, setOil] = useState<{ meta: OilMeta; fc: FC<OilProps> } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [offline, setOffline] = useState(false);
  const [cand, setCand] = useState<Cand | null>(null);
  const [sites, setSites] = useState<Site[]>(loadSites);
  const [active, setActive] = useState<string | null>(ui0.active);
  const [lasso, setLasso] = useState(false);
  const [areaEmpty, setAreaEmpty] = useState(false);
  const back = useRef<Snap | null>(null);
  const [hasBack, setHasBack] = useState(false);
  const [ready, setReady] = useState(false);

  const obsById = useMemo(() => new Map((obs?.features ?? []).map((f) => [f.id, f])), [obs]);
  const zoneById = useMemo(() => new Map((zones?.features ?? []).map((f) => [f.id, f])), [zones]);
  const oilById = useMemo(() => new Map((oil?.fc.features ?? []).map((f) => [String(f.id ?? f.properties.id), f])), [oil]);
  const sceneByKey = useMemo(() => new Map(studioList.map((s) => [s.key, s])), [studioList]);
  const srcLabel = useMemo(() => new Map((meta?.sources ?? []).map((s) => [s.id, s.label])), [meta]);
  const activeSite = sites.find((s) => s.id === active) ?? null;
  const colW = collapsed ? RAIL_W : COL_W;

  // ---------------------------------------------------------------- refs for map callbacks (created once)
  const st = useRef({ obsById, zoneById, filters, sceneByKey, oilById });
  st.current = { obsById, zoneById, filters, sceneByKey, oilById };

  // ---------------------------------------------------------------- map
  useEffect(() => {
    const c = new MapCtl(
      mapEl.current!,
      {
        onPick: (p) => onPick.current(p),
        onLasso: (ring) => onLasso.current(ring),
        onOffline: (on) => setOffline(on),
        onReady: () => setReady(true),
        onMoveEnd: () => persist.current(),
      },
      ui0.cam,
      colW,
    );
    ctl.current = c;
    if (!ui0.cam) c.map.panBy([-colW / 2, 0], { duration: 0 });
    return () => c.map.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => ctl.current?.setOffline(offline), [offline]);
  useEffect(() => ctl.current?.setPadLeft(colW), [colW]);

  // ---------------------------------------------------------------- data (real API only)
  const load = useCallback(() => {
    setErr(null);
    Promise.all([
      get<Meta>('/api/v3/meta'),
      get<FC<ObsProps>>('/api/v3/observations', { geometry: 'line', limit: 5000 }),
      get<FC<ZoneProps>>('/api/v3/zones', { limit: 1000 }),
      get<{ scenes: Scene[] }>('/api/v3/scenes', { limit: 1000 }),
    ])
      .then(([m, o, z, s]) => {
        performance.mark('mp3-data');
        setMeta(m);
        setObs(o);
        setZones(z);
        setScenes(s.scenes ?? []);
        // scenes with real views (studio API, L95): after the first paint, never blocking it
        studioScenes(null).then((l) => l && setStudioList(l));
        // experimental oil layer (API 3.9): only when its API is there
        hasOilApi().then((ok) => {
          if (!ok) return;
          Promise.all([get<OilMeta>('/api/v3/oil/meta'), get<FC<OilProps>>('/api/v3/oil/spills', { limit: 5000 })])
            .then(([om, of]) => setOil({ meta: om, fc: of }))
            .catch(() => {});
        });
      })
      .catch((e) => setErr(e instanceof ApiErr ? e.message : 'Сервис недоступен'));
  }, []);
  useEffect(load, [load]);

  useEffect(() => ctl.current?.setObs(obs), [obs]);
  useEffect(() => ctl.current?.setZones(zones), [zones]);
  useEffect(() => ctl.current?.setOil(oil?.fc ?? null), [oil]);
  useEffect(() => ctl.current?.setScenes(studioList.map((s) => ({ id: s.key, coords: s.coords, datetime: s.datetime }))), [studioList]);
  useEffect(() => ctl.current?.setFilters(filters), [filters]);
  useEffect(() => {
    ctl.current?.setSites(sites.map((s) => ({ id: s.id, ring: s.ring, active: s.id === active })));
    saveSites(sites);
  }, [sites, active]);
  useEffect(() => {
    ctl.current?.setSelection(cand && cand.kind !== 'area' ? { kind: cand.kind, id: cand.id } : null);
    if (!lasso) ctl.current?.setArea(cand?.kind === 'area' && !active ? cand.ring : null);
  }, [cand, active, lasso]);

  // ---------------------------------------------------------------- persistence (camera on moveend)
  const persist = useRef(() => {});
  persist.current = () => saveUi({ collapsed, section, filters, cam: ctl.current?.camera() ?? null, active });
  useEffect(() => persist.current(), [collapsed, section, filters, active]);

  // ---------------------------------------------------------------- picking
  const popupFor = (p: { kind: PickKind; id: string }): string => {
    if (p.kind === 'oil') {
      const o = st.current.oilById.get(p.id);
      return o
        ? `<div class="pop-k">нефтяное пятно · эксперимент</div><div><b>${fmtNum(o.properties.area_km2, 3)}</b> км² · ${esc(fmtDate(o.properties.date))}</div>`
        : '';
    }
    if (p.kind === 'scene') {
      const s = st.current.sceneByKey.get(p.id);
      return s ? `<div class="pop-k">снимок</div><div>${esc(fmtDate(s.datetime))} · ${esc(s.mission)}</div>` : '';
    }
    if (p.kind === 'obs') {
      const f = st.current.obsById.get(p.id);
      if (!f) return '';
      const v = f.properties.concentration_items_km2;
      const val = v === null || v === undefined ? 'объект' : `<b>${fmtNum(v)}</b> шт./км²`;
      return `<div class="pop-k">измерение</div><div>${val} · ${esc(fmtDate(f.properties.date_utc))}</div>`;
    }
    const z = st.current.zoneById.get(p.id);
    if (!z) return '';
    return `<div class="pop-k">полоса обследования</div><div>${esc(fmtDate(z.properties.datetime))} · ${esc(z.properties.mission ?? '')}</div>`;
  };
  const onPick = useRef<(p: Pick | null) => void>(() => {});
  onPick.current = (p) => {
    const c = ctl.current!;
    if (!p) {
      c.hidePopup();
      setCand((x) => (x && x.kind !== 'area' ? null : x));
      return;
    }
    c.showPopup(p.lngLat, popupFor(p));
    setCand({ kind: p.kind, id: p.id });
    setAreaEmpty(false);
    if (!active) setSection('sites');
    setCollapsed(false);
  };

  const onLasso = useRef<(ring: number[][]) => void>(() => {});
  onLasso.current = (ring) => {
    setLasso(false);
    const f = st.current.filters;
    const b = ringBBox(ring);
    const obsIds: string[] = [];
    if (f.obs)
      for (const o of st.current.obsById.values()) {
        const c = obsCenter(o);
        if (!c || !inBBox(c, b) || !inRing(c[0], c[1], ring)) continue;
        if (!inDates(o.properties.date_utc, f) || (f.sources && !f.sources.includes(o.properties.source_id))) continue;
        obsIds.push(o.id);
      }
    const zoneIds: string[] = [];
    if (f.zones)
      for (const z of st.current.zoneById.values()) {
        const zb = geomBounds(z.geometry);
        if (!zb || !inDates(z.properties.datetime, f)) continue;
        const cx = (zb[0] + zb[2]) / 2,
          cy = (zb[1] + zb[3]) / 2;
        if (inRing(cx, cy, ring)) zoneIds.push(z.id);
      }
    ctl.current?.hidePopup();
    setAreaEmpty(!obsIds.length && !zoneIds.length);
    setCand({ kind: 'area', ring, obsIds, zoneIds });
  };

  useEffect(() => {
    const c = ctl.current;
    if (!c) return;
    if (lasso) c.startLasso();
    else if (c.lassoActive) c.stopLasso();
  }, [lasso]);

  useEffect(() => {
    const k = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (lasso) setLasso(false);
      else {
        setCand(null);
        ctl.current?.hidePopup();
      }
    };
    window.addEventListener('keydown', k);
    return () => window.removeEventListener('keydown', k);
  }, [lasso]);

  // ---------------------------------------------------------------- candidate → site → studio
  const siteFromCand = (c: Cand): Site | null => {
    const now = new Date().toISOString();
    if (c.kind === 'area') {
      const b = ringBBox(c.ring);
      const n = sites.filter((s) => s.kind === 'area').length + 1;
      return { id: newId(), name: `Область ${n}`, kind: 'area', ref: null, ring: c.ring, bbox: b, obsIds: c.obsIds, zoneIds: c.zoneIds, status: 'new', created: now };
    }
    const existing = sites.find((s) => s.ref === c.id);
    if (existing) return existing;
    let base: BBox | null = null;
    let name = '';
    const linked = new Set<string>();
    if (c.kind === 'obs') {
      const f = obsById.get(c.id);
      if (!f) return null;
      base = geomBounds(f.geometry);
      name = `${srcLabel.get(f.properties.source_id) ?? f.properties.region} · ${fmtDate(f.properties.date_utc)}`;
      linked.add(f.id);
    } else if (c.kind === 'zone') {
      const z = zoneById.get(c.id);
      if (!z) return null;
      base = geomBounds(z.geometry);
      name = `Полоса · ${fmtDate(z.properties.datetime)}`;
      for (const s of z.properties.support?.linked_sample_ids ?? []) linked.add(s);
    } else if (c.kind === 'oil') {
      const o = oilById.get(c.id);
      if (!o) return null;
      base = geomBounds(o.geometry);
      name = `Нефтяное пятно · ${fmtDate(o.properties.date)}`;
    } else {
      const s = sceneByKey.get(c.id);
      if (!s?.bounds) return null;
      base = s.bounds;
      const place = s.region ? s.region.replace(/_/g, ' ').replace(/^./, (ch) => ch.toUpperCase()) : s.eventId ? s.eventId.split(':')[0] : 'Снимок';
      name = `${place} · ${fmtDate(s.datetime)}`;
      for (const x of s.fieldIds ?? []) linked.add(x);
    }
    if (!base) return null;
    const b = c.kind === 'scene' ? base : padBBox(base, 3);
    for (const o of obsById.values()) {
      const cc = obsCenter(o);
      if (cc && inBBox(cc, b)) linked.add(o.id);
    }
    const zoneIds: string[] = [];
    for (const z of zoneById.values()) {
      const zb = geomBounds(z.geometry);
      if (zb && (bboxHit(zb, b) || (z.properties.support?.linked_sample_ids ?? []).some((s) => linked.has(s)))) zoneIds.push(z.id);
    }
    return { id: newId(), name, kind: c.kind, ref: c.id, ring: bboxRing(b), bbox: b, obsIds: [...linked], zoneIds, status: 'new', created: now };
  };

  const openStudio = (s: Site) => {
    const c = ctl.current!;
    if (!back.current) back.current = { cam: c.camera(), filters, cand, section };
    setHasBack(true);
    setSites((list) => (list.some((x) => x.id === s.id) ? list : [s, ...list]));
    setActive(s.id);
    setSection('studio');
    setCollapsed(false);
    c.hidePopup();
    c.fitBBox(s.bbox, 12);
  };
  const goBack = () => {
    const c = ctl.current!;
    const b = back.current;
    back.current = null;
    setHasBack(false);
    setActive(null);
    c.setOverlay(null);
    c.setDetections(null);
    if (!b) {
      setSection('sites');
      return;
    }
    setFilters(b.filters);
    setCand(b.cand);
    setSection(b.section === 'studio' ? 'sites' : b.section);
    c.flyCam(b.cam, 1000);
    if (b.cand && b.cand.kind !== 'area') {
      const k = b.cand;
      let p: number[] | null = null;
      if (k.kind === 'obs') p = obsCenter(obsById.get(k.id) as any);
      else if (k.kind === 'zone') {
        const zb = geomBounds(zoneById.get(k.id)?.geometry);
        p = zb ? [(zb[0] + zb[2]) / 2, (zb[1] + zb[3]) / 2] : null;
      } else {
        const sb = sceneByKey.get(k.id)?.bounds;
        p = sb ? [(sb[0] + sb[2]) / 2, (sb[1] + sb[3]) / 2] : null;
      }
      if (p) c.showPopup(p as [number, number], popupFor(k));
    }
  };
  const updateSite = (id: string, patch: Partial<Site>) => setSites((l) => l.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  const removeSite = (id: string) => {
    setSites((l) => l.filter((s) => s.id !== id));
    if (active === id) goBack();
  };

  // ---------------------------------------------------------------- queries: ?q= link and saved queries
  const viewBBox = (): BBox | null => {
    const m = ctl.current?.map;
    if (!m) return null;
    const b = m.getBounds();
    const w = Math.max(-180, b.getWest()),
      e = Math.min(180, b.getEast()),
      so = Math.max(-85, b.getSouth()),
      n = Math.min(85, b.getNorth());
    // the whole globe in view = no spatial filter
    return e - w > 300 ? null : [w, so, e, n];
  };
  const applyQuery = (q: Partial<ApiQuery>) => {
    setFilters((f) => fromQuery(q, f));
    if (Array.isArray(q.bbox) && q.bbox.length === 4) ctl.current?.fitBBox(q.bbox as BBox, 12, 1200);
  };
  const urlQ = useRef(queryFromUrl());
  useEffect(() => {
    if (!ready || !urlQ.current) return;
    applyQuery(urlQ.current);
    urlQ.current = null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  // ---------------------------------------------------------------- render
  const nav: { id: Section; label: string; icon: string }[] = [
    { id: 'sites', label: 'Участки', icon: 'pin' },
    { id: 'studio', label: 'Студия', icon: 'film' },
    { id: 'layers', label: 'Слои', icon: 'layers' },
    { id: 'export', label: 'Экспорт', icon: 'download' },
  ];

  return (
    <div className={`app ${collapsed ? 'collapsed' : ''}`} style={{ ['--col' as any]: `${colW}px` }}>
      <div ref={mapEl} className="map" data-testid="map" />
      <aside className="col" data-testid="column">
        <div className="col-head">
          <button className="icon-btn" title={collapsed ? 'Развернуть' : 'Свернуть'} onClick={() => setCollapsed((v) => !v)} data-testid="left-toggle">
            <Icon name={collapsed ? 'expand' : 'collapse'} />
          </button>
          {!collapsed && <div className="brand">Морской мусор</div>}
        </div>
        <nav className="nav">
          {nav.map((n) => (
            <button
              key={n.id}
              className={`nav-i ${section === n.id ? 'on' : ''}`}
              title={n.label}
              data-testid={`nav-${n.id}`}
              onClick={() => {
                setSection(n.id);
                setCollapsed(false);
              }}
            >
              <Icon name={n.icon} />
              {!collapsed && <span>{n.label}</span>}
              {!collapsed && n.id === 'sites' && sites.length > 0 && <em className="badge">{sites.length}</em>}
            </button>
          ))}
        </nav>
        {/* collapsed: hidden, not unmounted — the studio keeps its scene on the map */}
        {(
          <div className="col-body" hidden={collapsed}>
            {err && (
              <div className="err" role="alert" data-testid="api-error">
                <b>{err}</b>
                <button className="btn" onClick={load}>
                  Повторить
                </button>
              </div>
            )}
            {section === 'sites' && (
              <SitesSection
                cand={cand}
                sceneByKey={sceneByKey}
                oilById={oilById}
                areaEmpty={areaEmpty}
                obsById={obsById}
                zoneById={zoneById}
                srcLabel={srcLabel}
                sites={sites}
                active={active}
                lasso={lasso}
                onLasso={() => setLasso((v) => !v)}
                onCandClose={() => {
                  setCand(null);
                  ctl.current?.hidePopup();
                }}
                onToStudio={() => {
                  const s = cand && siteFromCand(cand);
                  if (s) openStudio(s);
                }}
                onOpen={openStudio}
                onRemove={removeSite}
              />
            )}
            {/* the studio stays mounted while another section is open: its scene stays on the map */}
            {activeSite && meta && (
              <div hidden={section !== 'studio'}>
                <Studio
                  key={activeSite.id}
                  site={activeSite}
                  meta={meta}
                  obsById={obsById}
                  zoneById={zoneById}
                  scenes={scenes}
                  oil={oil ? oil.fc.features : null}
                  ctl={ctl.current!}
                  hasBack={hasBack}
                  onBack={goBack}
                  onUpdate={(p) => updateSite(activeSite.id, p)}
                  onRemove={() => removeSite(activeSite.id)}
                />
              </div>
            )}
            {section === 'studio' && !activeSite && <div className="hint">{sites.length ? 'Откройте участок из списка' : 'Сначала выберите участок на карте'}</div>}
            {section === 'studio' && !activeSite && sites.length > 0 && <SiteList sites={sites} active={active} onOpen={openStudio} onRemove={removeSite} />}
            {section === 'layers' && <LayersSection nScenes={studioList.length} oil={oil} meta={meta} filters={filters} setFilters={setFilters} offline={offline} obs={obs} zones={zones} />}
            {section === 'export' && <ExportSection filters={filters} viewBBox={viewBBox} onApply={applyQuery} oil={!!oil} />}
          </div>
        )}
        {!collapsed && section !== 'sites' && section !== 'studio' && sites.length > 0 && (
          <div className="col-foot">
            <SiteList sites={sites} active={active} onOpen={openStudio} onRemove={removeSite} compact />
          </div>
        )}
      </aside>
      {!ready && <div className="boot">Загрузка карты…</div>}
    </div>
  );
}

// ================================================================== sections
function SitesSection(p: {
  cand: Cand | null;
  sceneByKey: Map<string, StudioScene>;
  oilById: Map<string, Feat<OilProps>>;
  areaEmpty: boolean;
  obsById: Map<string, { id: string; geometry: any; properties: ObsProps }>;
  zoneById: Map<string, { id: string; geometry: any; properties: ZoneProps }>;
  srcLabel: Map<string, string>;
  sites: Site[];
  active: string | null;
  lasso: boolean;
  onLasso: () => void;
  onCandClose: () => void;
  onToStudio: () => void;
  onOpen: (s: Site) => void;
  onRemove: (id: string) => void;
}) {
  const c = p.cand;
  return (
    <>
      <div className="row">
        <button className={`btn wide ${p.lasso ? 'on' : ''}`} onClick={p.onLasso} data-testid="lasso" title="Обвести область мышью на карте">
          <Icon name="lasso" />
          {p.lasso ? 'Ведите мышью по карте' : 'Обвести'}
        </button>
      </div>
      {c && <CandCard c={c} {...p} />}
      {!c && !p.sites.length && <div className="hint">Кликните точку или обведите область</div>}
      <SiteList sites={p.sites} active={p.active} onOpen={p.onOpen} onRemove={p.onRemove} />
    </>
  );
}

function CandCard(p: {
  c: Cand;
  sceneByKey: Map<string, StudioScene>;
  oilById: Map<string, Feat<OilProps>>;
  areaEmpty: boolean;
  obsById: Map<string, { id: string; geometry: any; properties: ObsProps }>;
  zoneById: Map<string, { id: string; geometry: any; properties: ZoneProps }>;
  srcLabel: Map<string, string>;
  onCandClose: () => void;
  onToStudio: () => void;
}) {
  const c = p.c;
  let title = '';
  let lines: string[] = [];
  if (c.kind === 'obs') {
    const f = p.obsById.get(c.id);
    if (!f) return null;
    const v = f.properties.concentration_items_km2;
    title = 'Измерение';
    lines = [
      v === null || v === undefined ? 'отдельный объект, без плотности' : `${fmtNum(v)} шт./км²${f.properties.size_class ? ` · ${f.properties.size_class}` : ''}`,
      `${fmtDate(f.properties.date_utc)} · ${p.srcLabel.get(f.properties.source_id) ?? f.properties.region}`,
    ];
  } else if (c.kind === 'zone') {
    const z = p.zoneById.get(c.id);
    if (!z) return null;
    title = 'Полоса обследования';
    lines = [`${fmtDate(z.properties.datetime)} · ${z.properties.mission ?? ''}`, z.properties.quality_decision === 'accept' ? 'маски качества: пригодна' : 'маски качества: отклонена'];
  } else if (c.kind === 'oil') {
    const o = p.oilById.get(c.id);
    if (!o) return null;
    title = 'Нефтяное пятно · эксперимент';
    lines = [
      `${fmtNum(o.properties.area_km2, 3)} км²${o.properties.scene_frac !== null && o.properties.scene_frac !== undefined ? ` · ${fmtNum(o.properties.scene_frac * 100, 2)} % воды снимка` : ''}`,
      `${fmtDate(o.properties.date)} · площадь, не объём/масса`,
    ];
  } else if (c.kind === 'scene') {
    const s = p.sceneByKey.get(c.id);
    if (!s) return null;
    title = 'Снимок';
    lines = [`${fmtDate(s.datetime)} ${fmtTime(s.datetime)} · ${s.mission}`, VIEW_ORDER.filter((k) => s.views[k]).map((k) => VIEW_LABEL[k]).join(' · ')];
  } else {
    title = 'Область';
    const a = c as Extract<Cand, { kind: 'area' }>;
    lines = [`${a.obsIds.length} ${plural(a.obsIds.length, 'измерение', 'измерения', 'измерений')} · ${a.zoneIds.length} ${plural(a.zoneIds.length, 'полоса', 'полосы', 'полос')}`];
  }
  return (
    <div className="card" data-testid="cand-card">
      <div className="card-h">
        <span>{title}</span>
        <button className="icon-btn sm" title="Закрыть" onClick={p.onCandClose}>
          <Icon name="x" />
        </button>
      </div>
      {lines.map((l, i) => (
        <div key={i} className={i ? 'muted' : ''}>
          {l}
        </div>
      ))}
      {c.kind === 'area' && p.areaEmpty ? (
        <div className="muted">Внутри нет данных — обведите другое место</div>
      ) : (
        <button className="btn primary" onClick={p.onToStudio} data-testid="to-studio">
          В студию
        </button>
      )}
    </div>
  );
}

export function SiteList(p: { sites: Site[]; active: string | null; onOpen: (s: Site) => void; onRemove: (id: string) => void; compact?: boolean }) {
  if (!p.sites.length) return null;
  return (
    <div className={`sites ${p.compact ? 'compact' : ''}`} data-testid="site-list">
      {!p.compact && <div className="sub">Мои участки</div>}
      {p.sites.map((s) => (
        <div key={s.id} className={`site ${s.id === p.active ? 'on' : ''}`} onClick={() => p.onOpen(s)} title={s.name}>
          <i className={`dot st-${s.status}`} title={STATUS_LABEL[s.status]} />
          <span className="site-n">{s.name}</span>
          <button
            className="icon-btn sm ghost"
            title="Убрать из списка"
            onClick={(e) => {
              e.stopPropagation();
              p.onRemove(s.id);
            }}
          >
            <Icon name="x" />
          </button>
        </div>
      ))}
    </div>
  );
}

function LayersSection(p: { nScenes: number; oil: { meta: OilMeta; fc: FC<OilProps> } | null; meta: Meta | null; filters: Filters; setFilters: (f: Filters) => void; offline: boolean; obs: FC<ObsProps> | null; zones: FC<ZoneProps> | null }) {
  const f = p.filters;
  const set = (patch: Partial<Filters>) => p.setFilters({ ...f, ...patch });
  const sources = p.meta?.sources ?? [];
  const dr = p.meta?.date_range;
  const toggleSrc = (id: string) => {
    const cur = f.sources ?? sources.map((s) => s.id);
    const next = cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id];
    set({ sources: next.length === sources.length ? null : next });
  };
  const dirty = f.from || f.to || f.sources || !f.obs || !f.zones || f.scenes === false || f.oil === false;
  const nObs = useMemo(() => {
    if (!p.obs) return null;
    let n = 0;
    for (const o of p.obs.features) {
      const d = o.properties.date_utc?.slice(0, 10) ?? null;
      if (d && ((f.from && d < f.from) || (f.to && d > f.to))) continue;
      if (f.sources && !f.sources.includes(o.properties.source_id)) continue;
      n++;
    }
    return n;
  }, [p.obs, f.from, f.to, f.sources]);
  return (
    <div className="layers">
      <label className="tog">
        <input type="checkbox" checked={f.obs} onChange={(e) => set({ obs: e.target.checked })} />
        <span>Полевые измерения</span>
        <em>{p.obs?.total ?? p.obs?.count ?? ''}</em>
      </label>
      <label className="tog">
        <input type="checkbox" checked={f.zones} onChange={(e) => set({ zones: e.target.checked })} />
        <span>Полосы обследования</span>
        <em>{p.zones?.total ?? p.zones?.count ?? ''}</em>
      </label>
      <label className="tog">
        <input type="checkbox" checked={f.scenes !== false} onChange={(e) => set({ scenes: e.target.checked })} />
        <span>Снимки с видами</span>
        <em>{p.nScenes || ''}</em>
      </label>
      {p.oil && (
        <label className="tog" title={(p.oil.meta.limitations ?? []).join('; ') || undefined} data-testid="oil-toggle">
          <input type="checkbox" checked={f.oil !== false} onChange={(e) => set({ oil: e.target.checked })} />
          <span>
            {p.oil.meta.class_label} <i className="exp">эксперимент</i>
          </span>
          <em>{p.oil.fc.total ?? p.oil.fc.features.length}</em>
        </label>
      )}
      <div className="sub">Даты</div>
      <div className="row dates">
        <input type="date" value={f.from ?? ''} min={dr?.min} max={dr?.max} onChange={(e) => set({ from: e.target.value || null })} aria-label="с" />
        <span>—</span>
        <input type="date" value={f.to ?? ''} min={dr?.min} max={dr?.max} onChange={(e) => set({ to: e.target.value || null })} aria-label="по" />
      </div>
      <div className="sub">Источники</div>
      {sources.map((s) => (
        <label key={s.id} className="tog">
          <input type="checkbox" checked={!f.sources || f.sources.includes(s.id)} onChange={() => toggleSrc(s.id)} />
          <span>{s.label}</span>
          <em>{s.n ?? ''}</em>
        </label>
      ))}
      {nObs === 0 && f.obs && <div className="err small">Нет наблюдений за выбранные даты и источники</div>}
      {dirty && (
        <button className="btn" onClick={() => p.setFilters(DEFAULT_FILTERS)} data-testid="reset-filters">
          Сбросить фильтры
        </button>
      )}
      <div className="sub">Легенда</div>
      <div className="legend">
        <div className="scale">
          {CONC_COLORS.map((c, i) => (
            <i key={i} style={{ background: c }} title={i === 0 ? `< ${CONC_BREAKS[0]}` : `≥ ${CONC_BREAKS[i - 1]}`} />
          ))}
        </div>
        <div className="scale-l">
          <span>1</span>
          <span>100</span>
          <span>1000+ шт./км²</span>
        </div>
        <div className="lg">
          <i className="lg-zero" /> измеренный ноль
        </div>
        <div className="lg">
          <i className="lg-item" /> объект без плотности
        </div>
        <div className="lg">
          <i className="lg-strip" /> полоса обследования (снимок-кандидат)
        </div>
        <div className="lg">
          <i className="lg-det" /> подозрительные пиксели детектора
        </div>
        <div className="lg">
          <i className="lg-scene" /> снимок с видами (с зума 4)
        </div>
        {p.oil && (
          <div className="lg">
            <i className="lg-oil" /> нефтяное пятно — площадь, не объём/масса
          </div>
        )}
      </div>
      <div className="sub">Подложка</div>
      <div className="muted small">{p.offline ? 'Тайлы недоступны — контур суши Natural Earth' : 'Esri World Imagery (© Esri, Maxar, Earthstar Geographics)'}</div>
    </div>
  );
}
