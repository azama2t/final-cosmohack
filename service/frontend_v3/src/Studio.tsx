// Studio (L94): one site in the left column + the map. Dates timeline, views that really exist for the scene,
// detections, field measurements nearby, quality, drift as a SCENARIO, work status, export. Real API data only.
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { apiUrl, fromScene, get, studioScenes, VIEW_LABEL, VIEW_ORDER, type Feat, type Meta, type ObsProps, type Pair, type Scene, type StudioScene, type ViewKind, type ZoneDetail, type ZoneProps, type OilProps } from './api';
import type { MapCtl } from './map';
import { bboxHit, dayKey, fmtDate, fmtNum, fmtPct, fmtTime, plural } from './geo';
import { STATUS_LABEL, type Site, type WorkStatus } from './state';
import { Icon } from './icons';

const LEVEL_TEXT: Record<string, string> = {
  A: 'снимок и полевое число связаны по времени, месту, площади и категории',
  B: 'подтверждённая разметка скопления на снимке (без числа предметов)',
  C: 'кандидат для ручной проверки',
  D: 'отвергнутый кандидат / проверенный отрицательный пример',
};

interface Item {
  id: string; // product id (= /api/v3/scenes scene_id when known)
  studioId: string | null; // id in /api/v3/studio (for the detail: level_records)
  date: string; // ISO datetime
  mission: string;
  source: string | null;
  views: Partial<Record<ViewKind, string>>;
  overlay: Partial<Record<ViewKind, boolean>>;
  viewSource: Partial<Record<ViewKind, string>>;
  viewVariant: Partial<Record<ViewKind, string>>;
  bounds: [number, number, number, number] | null;
  coords: number[][] | null;
  cloud: number | null;
  level: string | null;
  note: string | null;
  quality: StudioScene['quality'] | null;
  detector: StudioScene['detector'] | null;
  pairs: Pair[];
  zones: Feat<ZoneProps>[];
}

const missionShort = (m: string) => (m.startsWith('Sentinel-2') ? 'S2' : m.startsWith('Landsat-') ? 'L' + m.slice(8) : m.slice(0, 3));

export default function Studio(p: {
  site: Site;
  meta: Meta;
  obsById: Map<string, Feat<ObsProps>>;
  zoneById: Map<string, Feat<ZoneProps>>;
  scenes: Scene[];
  /** experimental oil spills (null = layer off / API absent) */
  oil: Feat<OilProps>[] | null;
  ctl: MapCtl;
  hasBack: boolean;
  onBack: () => void;
  onUpdate: (patch: Partial<Site>) => void;
  onRemove: () => void;
}) {
  const { site, ctl } = p;
  const [pairs, setPairs] = useState<Pair[] | null>(null);
  const [studio, setStudio] = useState<StudioScene[] | null>(null);
  const [details, setDetails] = useState<Record<string, ZoneDetail>>({});
  const [sel, setSel] = useState<string | null>(null);
  const [view, setView] = useState<ViewKind | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [allObs, setAllObs] = useState(false);
  const [levelWhy, setLevelWhy] = useState<string | null>(null);

  const obs = useMemo(() => site.obsIds.map((id) => p.obsById.get(id)).filter((x): x is Feat<ObsProps> => !!x), [site, p.obsById]);
  const zones = useMemo(() => site.zoneIds.map((id) => p.zoneById.get(id)).filter((x): x is Feat<ZoneProps> => !!x), [site, p.zoneById]);
  const rr = useMemo(() => new Map((p.meta.reject_reasons ?? []).map((r) => [r.id, r.label])), [p.meta]);

  // ---------------------------------------------------------------- load: pairs of the site's measurements, studio scenes, zone details
  useEffect(() => {
    const ac = new AbortController();
    const ids = new Set(site.obsIds);
    const bySrc = new Map<string, string[]>();
    for (const o of obs) {
      const d = o.properties.date_utc;
      if (!d) continue;
      const a = bySrc.get(o.properties.source_id) ?? [];
      a.push(d);
      bySrc.set(o.properties.source_id, a);
    }
    const shift = (d: string, days: number) => new Date(Date.parse(d) + days * 864e5).toISOString().slice(0, 10);
    const reqs = [...bySrc.entries()].map(([src, ds]) => {
      ds.sort();
      return get<{ pairs: Pair[] }>('/api/v3/pairs', { source: src, date_from: shift(ds[0], -3), date_to: shift(ds[ds.length - 1], 3), limit: 5000 }, ac.signal).then((r) =>
        (r.pairs ?? []).filter((x) => ids.has(x.sample_id)),
      );
    });
    Promise.all(reqs)
      .then((a) => setPairs(a.flat()))
      .catch((e) => {
        if (e?.name !== 'AbortError') {
          setPairs([]);
          setLoadErr(e?.message ?? 'Сервис недоступен');
        }
      });
    studioScenes(site.bbox, ac.signal)
      .then(setStudio)
      .catch(() => {});
    Promise.all(
      site.zoneIds.slice(0, 40).map((z) =>
        get<ZoneDetail>(`/api/v3/zones/${encodeURIComponent(z)}`, {}, ac.signal).then(
          (d) => [z, d] as const,
          () => null,
        ),
      ),
    ).then((a) => {
      if (ac.signal.aborted) return;
      const m: Record<string, ZoneDetail> = {};
      for (const x of a) if (x) m[x[0]] = x[1];
      setDetails(m);
    });
    return () => ac.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [site.id]);

  // ---------------------------------------------------------------- timeline items (one per scene)
  const items = useMemo(() => {
    const m = new Map<string, Item>();
    const ensure = (id: string, date: string, mission: string, source: string | null): Item => {
      let it = m.get(id);
      if (!it) {
        it = { id, date, mission, source, studioId: null, views: {}, overlay: {}, viewSource: {}, viewVariant: {}, bounds: null, coords: null, cloud: null, level: null, note: null, quality: null, detector: null, pairs: [], zones: [] };
        m.set(id, it);
      }
      return it;
    };
    for (const pr of pairs ?? []) if (pr.scene_id && pr.scene_datetime) ensure(pr.scene_id, pr.scene_datetime, pr.mission ?? '', pr.catalog ?? null).pairs.push(pr);
    for (const z of zones) if (z.properties.scene_id && z.properties.datetime) ensure(z.properties.scene_id, z.properties.datetime, z.properties.mission ?? '', null).zones.push(z);
    const merge = (s: StudioScene) => {
      const it = ensure(s.key, s.datetime, s.mission, s.source);
      if (s.from === 'studio') {
        // the studio API knows the real grid corners and all views: it replaces the case-API previews
        it.studioId = s.id;
        it.views = { ...s.views };
        it.overlay = { ...s.overlay };
        it.viewSource = { ...s.viewSource };
        it.viewVariant = { ...s.viewVariant };
        it.coords = s.coords;
        it.bounds = s.bounds;
        it.mission = s.mission || it.mission;
        it.source = s.source ?? it.source;
        it.note = s.note ?? null;
        it.quality = s.quality ?? it.quality;
        it.detector = s.detector ?? it.detector;
      } else if (!Object.keys(it.views).length) {
        it.views = { ...s.views };
        it.overlay = { ...s.overlay };
        it.coords = s.coords;
        it.bounds = s.bounds;
        if (!it.source) it.source = s.source;
      }
      if (it.cloud === null) it.cloud = s.cloud_pct;
      it.level = s.level ?? it.level;
    };
    for (const s of p.scenes) {
      if (m.has(s.scene_id) || (s.bounds && bboxHit(s.bounds, site.bbox))) merge(fromScene(s));
    }
    for (const s of studio ?? []) merge(s);
    for (const it of m.values()) {
      if (it.cloud === null) it.cloud = it.pairs.find((x) => x.scene_cloud_pct !== null && x.scene_cloud_pct !== undefined)?.scene_cloud_pct ?? null;
      if (!it.coords) it.views = {}; // a view without a footprint cannot be placed on the map
    }
    return [...m.values()].sort((a, b) => a.date.localeCompare(b.date));
  }, [pairs, zones, p.scenes, studio, site.bbox]);

  // measurement days on the same timeline
  const days = useMemo(() => {
    const d = new Map<string, { scenes: Item[]; obs: number }>();
    for (const it of items) {
      const k = dayKey(it.date);
      const x = d.get(k) ?? { scenes: [], obs: 0 };
      x.scenes.push(it);
      d.set(k, x);
    }
    for (const o of obs) {
      const k = dayKey(o.properties.date_utc);
      if (!k) continue;
      const x = d.get(k) ?? { scenes: [], obs: 0 };
      x.obs++;
      d.set(k, x);
    }
    return [...d.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [items, obs]);

  // default scene: the one with a picture closest to the field date
  useEffect(() => {
    if (sel && items.some((i) => i.id === sel)) return;
    // a site opened from a scene footprint starts on that very scene
    if (site.kind === 'scene' && site.ref && items.some((i) => i.id === site.ref)) {
      setSel(site.ref);
      return;
    }
    const withViews = items.filter((i) => Object.keys(i.views).length);
    if (!withViews.length) {
      setSel(items[0]?.id ?? null);
      return;
    }
    const t0 = obs.length ? Date.parse(obs[0].properties.date_utc ?? '') : NaN;
    const best = Number.isFinite(t0) ? withViews.reduce((a, b) => (Math.abs(Date.parse(b.date) - t0) < Math.abs(Date.parse(a.date) - t0) ? b : a)) : withViews[0];
    setSel(best.id);
  }, [items, obs, sel, site.kind, site.ref]);

  const cur = items.find((i) => i.id === sel) ?? null;
  const sameDay = cur ? items.filter((i) => dayKey(i.date) === dayKey(cur.date)) : [];
  const kinds = cur ? VIEW_ORDER.filter((k) => cur.views[k]) : [];
  useEffect(() => {
    if (!cur) return setView(null);
    if (!view || !cur.views[view]) setView(kinds[0] ?? null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cur?.id, kinds.join()]);

  // ---------------------------------------------------------------- map: scene view overlay + detections
  useEffect(() => {
    const url = cur && view ? cur.views[view] : null;
    if (!cur || !url || !cur.coords) return ctl.setOverlay(null);
    const coords = cur.coords;
    if (cur.overlay[view!]) {
      // Детекция / Качество: transparent PNG over the picture of the same grid
      const baseUrl = cur.views.rgb ?? cur.views.spectral ?? null;
      ctl.setOverlay(baseUrl ? { url: baseUrl, coords } : null, { url, coords });
    } else ctl.setOverlay({ url, coords });
  }, [ctl, cur, view]);
  useEffect(() => {
    const feats: any[] = [];
    for (const d of Object.values(details))
      for (const f of d.detections?.features ?? []) feats.push({ ...f, properties: { ...f.properties, quality_rejected: !!f.properties.quality_rejected } });
    ctl.setDetections({ type: 'FeatureCollection', features: feats });
  }, [ctl, details]);
  useEffect(
    () => () => {
      ctl.setOverlay(null);
      ctl.setDetections(null);
      ctl.setDrift(null);
    },
    [ctl],
  );

  // ---------------------------------------------------------------- derived facts
  const det = useMemo(() => {
    let n = 0,
      inStrip = 0,
      qr = 0;
    for (const d of Object.values(details))
      for (const f of d.detections?.features ?? []) {
        n++;
        if (f.properties.in_strip) inStrip++;
        if (f.properties.quality_rejected) qr++;
      }
    return { n, inStrip, qr, loaded: Object.keys(details).length, zones: zones.length };
  }, [details, zones]);
  const curZone = cur?.zones[0]?.properties ?? null;
  const siteOil = useMemo(() => {
    let n = 0,
      km2 = 0,
      curN = 0;
    const [w, s, e, nn] = site.bbox;
    for (const f of p.oil ?? []) {
      const q = f.properties as any;
      const lon = q.lon,
        lat = q.lat;
      if (typeof lon !== 'number' || lon < w || lon > e || lat < s || lat > nn) continue;
      n++;
      km2 += f.properties.area_km2 ?? 0;
      if (cur && (f.properties.scene_id === cur.id || f.properties.scene_key === cur.studioId)) curN++;
    }
    return { n, km2, curN };
  }, [p.oil, site.bbox, cur]);
  /** detector objects of the strips on THIS scene (zones/{id}.detections) */
  const sceneDet = useMemo(() => {
    if (!cur || !cur.zones.length) return null;
    let n = 0;
    let any = false;
    for (const z of cur.zones) {
      const d = details[z.id];
      if (!d) continue;
      any = true;
      n += d.detections?.features.length ?? 0;
    }
    return any ? { n } : null;
  }, [cur, details]);
  const curPair = cur ? [...cur.pairs].sort((a, b) => Math.abs(a.dt_hours ?? 1e9) - Math.abs(b.dt_hours ?? 1e9))[0] ?? null : null;
  useEffect(() => {
    const o = curPair ? p.obsById.get(curPair.sample_id) : null;
    const c = o ? (o.properties.track_center ?? (o.geometry?.type === 'Point' ? o.geometry.coordinates : null)) : null;
    const d = curPair?.drift_scenarios_km;
    ctl.setDrift(
      c && d ? c : null,
      d ? [{ k: 'low', km: d.low ?? 0 }, { k: 'typical', km: d.typical ?? 0 }, { k: 'high', km: d.high ?? 0 }] : [],
    );
  }, [ctl, curPair, p.obsById]);
  const level = cur?.level && LEVEL_TEXT[cur.level] ? cur.level : null;
  // why this level: the registry record of the search (data/search/*/candidates.csv via the studio API)
  useEffect(() => {
    setLevelWhy(null);
    if (!level || !cur?.studioId) return;
    const ac = new AbortController();
    get<any>(`/api/v3/studio/scenes/${encodeURIComponent(cur.studioId)}`, {}, ac.signal)
      .then((d) => {
        const r = (d?.level_records ?? []).find((x: any) => x?.level === level) ?? d?.level_records?.[0];
        const why = r?.reason ? String(r.reason) : '';
        // skip a reason that only repeats the level definition
        if (why && why.slice(0, 12) !== (LEVEL_TEXT[level] ?? '').slice(0, 12)) setLevelWhy(why);
      })
      .catch(() => {});
    return () => ac.abort();
  }, [level, cur?.studioId]);

  const passport = () => {
    const doc = {
      site: { id: site.id, name: site.name, bbox: site.bbox, status: site.status, created: site.created },
      measurements: obs.map((o) => ({ sample_id: o.id, date: o.properties.date_utc, items_km2: o.properties.concentration_items_km2, profile: o.properties.measurement_profile, source: o.properties.source_id })),
      scenes: items.map((i) => ({ scene_id: i.id, datetime: i.date, mission: i.mission, source: i.source, views: Object.keys(i.views), evidence_level: i.level })),
      pairs: (pairs ?? []).map((x) => ({ pair_id: x.pair_id, status: x.status, reject_reasons: x.reject_reasons, dt_hours: x.dt_hours, drift_scenarios_km: x.drift_scenarios_km ?? null })),
      detections: { objects: det.n, in_strip: det.inStrip, quality_rejected: det.qr },
      exported_at: new Date().toISOString(),
    };
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(doc, null, 2)], { type: 'application/json' }));
    a.download = `site_${site.id}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };

  const bbox = site.bbox.map((x) => x.toFixed(4)).join(',');
  return (
    <div className="studio" data-testid="studio">
      <div className="st-head">
        <button className="btn back" onClick={p.onBack} data-testid="back" title="Вернуть карту, фильтры и выделение как были">
          <Icon name="back" /> Назад
        </button>
        <div className="st-name" title={site.name}>
          {site.name}
        </div>
      </div>
      <div className="seg" role="radiogroup" aria-label="Статус работы" data-testid="work-status">
        {(Object.keys(STATUS_LABEL) as WorkStatus[]).map((s) => (
          <button key={s} className={site.status === s ? 'on' : ''} onClick={() => p.onUpdate({ status: s })} role="radio" aria-checked={site.status === s}>
            {STATUS_LABEL[s]}
          </button>
        ))}
      </div>

      {loadErr && <div className="err">{loadErr}</div>}

      <div className="sub">Снимки по датам</div>
      {pairs === null ? (
        <div className="muted small">Загрузка…</div>
      ) : days.length ? (
        <div className="tl" data-testid="timeline">
          {days.map(([d, x]) => {
            const on = x.scenes.some((s) => s.id === sel);
            const pic = x.scenes.find((s) => Object.keys(s.views).length);
            return (
              <button
                key={d}
                className={`tl-d ${on ? 'on' : ''} ${pic ? 'pic' : ''} ${x.scenes.length ? '' : 'field'}`}
                disabled={!x.scenes.length}
                onClick={() => x.scenes.length && setSel((pic ?? x.scenes[0]).id)}
                title={`${fmtDate(d)}: ${x.scenes.length} ${plural(x.scenes.length, 'снимок', 'снимка', 'снимков')}${x.obs ? `, ${x.obs} ${plural(x.obs, 'измерение', 'измерения', 'измерений')}` : ''}`}
              >
                <span className="tl-date">{fmtDate(d).slice(0, 5)}</span>
                <span className="tl-y">{d.slice(0, 4)}</span>
                <span className="tl-m">
                  {[...x.scenes]
                    .sort((a, b) => Object.keys(b.views).length - Object.keys(a.views).length)
                    .slice(0, 3)
                    .map((s) => (
                    <i key={s.id} className={Object.keys(s.views).length ? 'sq pic' : 'sq'} />
                  ))}
                  {x.obs > 0 && <i className="ci" />}
                </span>
                {x.scenes.some((s) => s.level) && <span className="tl-lv">{x.scenes.find((s) => s.level)!.level}</span>}
              </button>
            );
          })}
        </div>
      ) : (
        <div className="muted small">Снимков для этого участка в реестре нет</div>
      )}

      {cur && sameDay.length > 1 && (
        <div className="sameday" role="tablist" aria-label="Снимки этого дня">
          {/* only the scenes with a picture are choices; the rest of the registry records are counted */}
          {sameDay
            .filter((s) => Object.keys(s.views).length || s.id === cur.id)
            .map((s) => (
              <button key={s.id} role="tab" aria-selected={s.id === cur.id} className={s.id === cur.id ? 'on' : ''} onClick={() => setSel(s.id)} title={s.id}>
                {missionShort(s.mission)} {fmtTime(s.date).replace(' UTC', '')}
                {Object.keys(s.views).length ? '' : ' · нет изображения'}
              </button>
            ))}
          {(() => {
            const n = sameDay.filter((s) => !Object.keys(s.views).length && s.id !== cur.id).length;
            return n ? (
              <span className="muted small" title="Сцены из реестра пар без сохранённого изображения">
                +{n} без изображения
              </span>
            ) : null;
          })()}
        </div>
      )}
      {cur && (
        <div className="scene" data-testid="scene">
          <div className="scene-h">
            <b>{fmtDate(cur.date)}</b> {fmtTime(cur.date)}
            {cur.level && LEVEL_TEXT[cur.level] && (
              <b className={`lv sm lv-${cur.level}`} title={`Уровень доказательности ${cur.level}: ${LEVEL_TEXT[cur.level]}`}>
                {cur.level}
              </b>
            )}
          </div>
          <div className="muted small" title={[cur.source, cur.note].filter(Boolean).join(' · ') || undefined}>
            {sourceShort(cur)}
          </div>
          {kinds.length > 0 ? (
            <div className="views" role="tablist" data-testid="views">
              {kinds.map((k) => (
                <button key={k} role="tab" aria-selected={view === k} className={view === k ? 'on' : ''} onClick={() => setView(k)}>
                  {VIEW_LABEL[k]}
                </button>
              ))}
            </div>
          ) : (
            <div className="muted small">Изображения этой сцены нет в данных</div>
          )}
          {view && <ViewLegend kind={view} item={cur} meta={p.meta} />}
        </div>
      )}

      {level && (
        <Block title="Доказательность">
          <div className="lvl">
            <b className={`lv lv-${level}`}>{level}</b>
            <span className="small">{LEVEL_TEXT[level]}</span>
          </div>
          {levelWhy && <div className="muted small">{levelWhy}</div>}
        </Block>
      )}

      <Block title="Детекции">
        {(() => {
          const d = cur?.detector ?? null;
          // the studio API says explicitly when the current detector was not run on this scene
          if (d && d.run === false)
            return (
              <div className="muted small" title={d.label ?? undefined} data-testid="det-line">
                Детектор не запускался
              </div>
            );
          const px = d ? d.pixels ?? d.n_det ?? d.n_above_threshold ?? null : null;
          const objs = d?.objects ?? (sceneDet ? sceneDet.n : null);
          if (!d && !det.zones) return <div className="muted small">Детектор не запускался</div>;
          if (!d && det.loaded < Math.min(det.zones, 40)) return <div className="muted small">Загрузка…</div>;
          const hint = [
            d?.label ?? null,
            d?.threshold !== null && d?.threshold !== undefined ? `порог вероятности ${fmtNum(d.threshold, 2)}` : null,
            det.zones ? `в полосах обследования участка: ${det.n} объектов, из них в полосе ${det.inStrip}` : null,
            det.qr ? `${det.qr} — на снимке, отклонённом масками качества` : null,
            'без полевого подтверждения',
          ]
            .filter(Boolean)
            .join('; ');
          return (
            <div className="small" title={hint} data-testid="det-line">
              Детектор: {objs !== null ? `${objs} ${plural(objs, 'объект', 'объекта', 'объектов')}` : px !== null ? '' : `${det.n} ${plural(det.n, 'объект', 'объекта', 'объектов')}`}
              {px !== null && (objs !== null ? ` (${fmtNum(px, 0)} пикс.)` : `${fmtNum(px, 0)} пикс. выше порога`)}
            </div>
          );
        })()}
      </Block>

      <Block title={`Полевые измерения рядом · ${obs.length}`}>
        {obs.slice(0, allObs ? obs.length : 6).map((o) => (
          <div
            key={o.id}
            className="obs-r click"
            title="Показать на карте"
            onClick={() => {
              const c = o.properties.track_center ?? (o.geometry?.type === 'Point' ? o.geometry.coordinates : null);
              if (!c) return;
              ctl.setSelection({ kind: 'obs', id: o.id });
              ctl.map.flyTo({ center: c as [number, number], zoom: Math.max(ctl.map.getZoom(), 11), duration: 900, essential: true });
            }}
          >
            <span>{o.properties.concentration_items_km2 === null ? 'объект' : `${fmtNum(o.properties.concentration_items_km2)} шт./км²`}</span>
            <span className="muted">
              {fmtDate(o.properties.date_utc)} · {o.properties.size_class ?? '—'}
            </span>
          </div>
        ))}
        {obs.length > 6 && !allObs && (
          <button className="link-btn" onClick={() => setAllObs(true)}>
            ещё {obs.length - 6}
          </button>
        )}
        {!obs.length && <div className="muted small">Рядом измерений нет</div>}
      </Block>

      {p.oil && (
        <Block title="Нефтяное пятно · эксперимент">
          {siteOil.n ? (
            <div className="small" title="Экспериментальный класс; оптика видит часть пятен, путает с тенями облаков и сликами" data-testid="oil-row">
              {siteOil.n} {plural(siteOil.n, 'пятно', 'пятна', 'пятен')} · {fmtNum(siteOil.km2, 3)} км²
              {siteOil.curN !== siteOil.n && ` (на этом снимке ${siteOil.curN})`}
              <div className="muted">площадь, не объём/масса</div>
            </div>
          ) : (
            <div className="muted small">Пятен не найдено</div>
          )}
        </Block>
      )}

      <Block title="Качество">
        {cur ? (
          <div className="small">
            <div>Облачность: {cur.cloud === null ? '—' : fmtPct(cur.cloud)}</div>
            {cur.quality?.decision && <div>Маски снимка: {cur.quality.decision === 'accept' ? 'пригоден' : cur.quality.decision === 'reject' ? 'отклонён' : cur.quality.decision}</div>}
            {cur.quality?.valid_water_frac !== null && cur.quality?.valid_water_frac !== undefined && <div>Чистой воды: {fmtPct(cur.quality.valid_water_frac)}</div>}
            {curZone && (
              <div>
                Маски в полосе: {curZone.quality_decision === 'accept' ? 'пригодна' : `отклонена${curZone.quality_reject_label ? ` — ${curZone.quality_reject_label}` : ''}`}
                {curZone.quality?.valid_fraction !== null && curZone.quality?.valid_fraction !== undefined && ` · чистой воды ${fmtPct(curZone.quality.valid_fraction)}`}
              </div>
            )}
          </div>
        ) : (
          <div className="muted small">Выберите снимок</div>
        )}
      </Block>

      {curPair && (
        <Block title="Дрейф — сценарий">
          <div className="small">
            <div>
              Разрыв во времени: {fmtNum(curPair.dt_hours === null ? null : Math.abs(curPair.dt_hours), 1)} ч{curPair.dt_hours !== null && (curPair.dt_hours < 0 ? ' (снимок раньше)' : ' (снимок позже)')}
            </div>
            {curPair.drift_scenarios_km && (
              <div>
                Смещение: {fmtNum(curPair.drift_scenarios_km.low ?? null, 1)} / {fmtNum(curPair.drift_scenarios_km.typical ?? null, 1)} / {fmtNum(curPair.drift_scenarios_km.high ?? null, 1)} км
                <span className="muted"> (мало / типично / много)</span>
              </div>
            )}
            {curPair.tolerance_km !== null && curPair.tolerance_km !== undefined && <div>Допуск: {fmtNum(curPair.tolerance_km, 1)} км</div>}
            <div className={curPair.status === 'accepted' ? 'ok' : 'muted'}>
              {curPair.status === 'accepted' ? 'Пара синхронна' : `Пара отклонена: ${curPair.reject_reasons.map((r) => rr.get(r) ?? r).join(', ')}`}
            </div>
            <div className="muted">Голубые круги на карте — сценарий, не доказательство того же скопления</div>
          </div>
        </Block>
      )}

      <Block title="Выгрузка">
        <div className="exp-row">
          <span>Измерения</span>
          <a className="btn sm" href={apiUrl('/api/v3/export', { layer: 'observations', format: 'geojson', bbox })} download>
            GeoJSON
          </a>
          <a className="btn sm" href={apiUrl('/api/v3/export', { layer: 'observations', format: 'csv', bbox })} download>
            CSV
          </a>
        </div>
        <div className="exp-row">
          <span>Полосы</span>
          <a className="btn sm" href={apiUrl('/api/v3/export', { layer: 'zones', format: 'geojson', bbox })} download>
            GeoJSON
          </a>
          <a className="btn sm" href={apiUrl('/api/v3/export', { layer: 'zones', format: 'csv', bbox })} download>
            CSV
          </a>
        </div>
        <div className="exp-row">
          <span>Паспорт участка</span>
          <button className="btn sm" onClick={passport} data-testid="passport">
            JSON
          </button>
        </div>
      </Block>
      <button className="btn ghost danger" onClick={p.onRemove}>
        Убрать участок
      </button>
    </div>
  );
}

function ViewLegend({ kind, item, meta }: { kind: ViewKind; item: Item; meta: Meta }) {
  if (kind === 'detection') {
    const t = item.detector?.threshold;
    return (
      <div className="vlg">
        <span>
          <i style={{ background: '#ff2d55' }} /> P ≥ {t !== null && t !== undefined ? fmtNum(t, 2) : 'порога'}
        </span>
        <span>
          <i style={{ background: '#ffd43b99' }} /> 0,2 ≤ P &lt; порога
        </span>
      </div>
    );
  }
  if (kind === 'quality') {
    return (
      <div className="vlg">
        {meta.quality_classes
          .filter((q) => q.id !== 'valid' && q.present !== false)
          .map((q) => (
            <span key={q.id}>
              <i style={{ background: q.color }} /> {q.label}
            </span>
          ))}
      </div>
    );
  }
  // one caption for the view really shown (never «FDI или SWIR», never a file name)
  const v = item.viewVariant[kind];
  const text =
    kind === 'spectral'
      ? v === 'swir'
        ? 'SWIR: B11 · B8 · B4'
        : item.viewSource.spectral || v === 'fdi'
          ? 'FDI — индекс плавающего мусора (Biermann 2020)'
          : null
      : kind === 'rgb'
        ? 'Цвет: B4 · B3 · B2'
        : null;
  return text ? <div className="vlg muted">{text}</div> : null;
}

/** «Sentinel-2B · L2A · вырезка» (the full catalog/collection string goes to the hover) */
function sourceShort(it: Item): string {
  const s = (it.source ?? '').toLowerCase();
  const lvl = /l2a/.test(s) ? 'L2A' : /l1c/.test(s) ? 'L1C' : /landsat-c2-l2|c2-l2|l2sp/.test(s + it.id.toLowerCase()) ? 'L2' : null;
  const cut = it.note && /вырезк/i.test(it.note) ? 'вырезка' : null;
  return [it.mission || null, lvl, cut].filter(Boolean).join(' · ') || 'источник не указан';
}

function Block(p: { title: string; children: ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <section className={`blk ${open ? '' : 'shut'}`}>
      <button className="blk-h" onClick={() => setOpen((v) => !v)}>
        <span>{p.title}</span>
        <Icon name={open ? 'up' : 'down'} />
      </button>
      {open && <div className="blk-b">{p.children}</div>}
    </section>
  );
}
