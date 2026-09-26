// Satellite scene zone (/api/v3/scene_zones, contract 3.10): three separated blocks —
// «Измерено» (by the image), «Вероятно» (detector probability + signs of foam/glint/ship/cloud/coast), «Количество»
// («концентрация по снимку не подтверждена»; no items/km2 scenario — INBOX §23 п.2) — and «Поле рядом»
// (independent field counts C = N/A; measurement ≠ estimate). Numbers only from the API.
import Info from '../components/Info';
import { API_BASE, type Feat, type FC, type Meta } from './api3';
import { dateRu, dateTimeRu, num, pct } from './fmt';
import { geomCenter, isFind, szKey, SZ_COLORS } from './CaseMap';
import DriftTab from './DriftTab';
import { cap, RES_CAPTION, RES_NOTE, researchEst, scenarioLine } from './estimate';
import { classGloss, confirmation, excludedLabel, flaggedLabel, notCheckedLabel, quantityLine, statusLabel, whatLabel } from './zoneinfo';

/** §33: the source of every number next to it */
function Src({ k }: { k: 'image' | 'field' | 'photo' | 'research' | 'none' | 'model' | 'era5' | 'mask' }) {
  const t = {
    image: 'по снимку',
    mask: 'маска качества (SCL)',
    era5: 'ERA5 (реанализ)',
    model: 'оценка детектора',
    field: 'измерено в поле',
    photo: 'посчитано по детальному фото',
    research: 'исследовательская оценка',
    none: 'нет данных',
  }[k];
  return <span className={`c-src c-src-${k}`}>{t}</span>;
}

export interface SceneZoneProps {
  kind: 'detection_zone';
  layer_kind: 'scene_zone';
  zone_id: string;
  scene_key: string;
  scene_kind: string;
  scene_kind_label?: string;
  scene_id: string;
  title: string;
  datetime: string | null;
  detection_status: string;
  detection_label: string;
  detection_reason: string | null;
  verification?: string;
  training_scene?: string | null;
  training_scene_note?: string | null;
  wind_high?: boolean;
  wind_note?: string | null;
  concentration_status: string;
  status_note?: string;
  flags: string[];
  area_km2: number | null;
  n_cozar_filaments: number;
  crop_url?: string | null;
  crop_note?: string;
  measured: {
    zone_area_km2: number | null;
    suspicious_area_m2: number | null;
    n_pixels: number | null;
    n_objects: number | null;
    water_km2: number | null;
    lwd_m2_km2: number | null;
    quality: { valid_water_fraction: number | null; cloud_fraction: number | null; glint_fraction: number | null };
    model: { weights: string; sha256_short?: string; sha256?: string; trained_at?: string; threshold?: number; harmonize?: string };
  };
  probable: {
    prob_max: number | null;
    prob_mean: number | null;
    status?: string;
    cozar_note?: string | null;
    context?: string;
    signs: Record<string, { flag: boolean; rule: string; [k: string]: any }> & { note?: string } | null;
  };
  scenario: null;
  scenario_reason?: string;
  quantity?: { status: string; label: string; detail: string };
  concentration_label?: string;
  // §39 (L131 «§39 бэкенд готов»)
  status?: string;
  status_label?: string;
  confirmation?: string | null;
  confirmation_label?: string | null;
  class?: string | null;
  classification?: {
    class?: string | null;
    class_label?: string | null;
    what_label?: string | null;
    excluded_label?: string | null;
    flagged_label?: string | null;
    not_checked_label?: string | null;
    composition?: string | null;
    [k: string]: any;
  } | null;
  quantity_line?: string | null;
  research_estimate?: any;
  field_nearby: {
    items: {
      source: string;
      segment_id: number;
      ship: string;
      date: string;
      days_from_scene: number;
      distance_km: number;
      n_items: number;
      area_km2: number;
      size_class: string;
      c_items_km2: number | null;
      ci95_lo: number | null;
      ci95_hi: number | null;
    }[];
    nearest_organizer_sample?: { sample_id: string; source_id: string; distance_km: number };
    authors_calibration?: { C: number; lo_typ: number; hi_typ: number; ours_raw_C: number; label: string; source: string; size_class: string } | null;
    note: string;
  } | null;
}

export interface SzExample {
  kind: 'success' | 'false_alarm';
  label: string;
  zone_id: string;
  crop_url: string;
  title: string;
  note: string;
}

export interface SceneZoneDetail extends Feat<SceneZoneProps> {
  detections: FC<any> | null;
  examples?: SzExample[];
  scene: { preview_url: string | null; quality_url: string | null; bounds?: number[] | null; wind10m_ms?: number | null; sun_zenith_deg?: number | null; lwd_m2_km2?: number | null; water_km2?: number | null } | null;
}

/** jury 14:55: the zone name carries its район — «Альборан · 30SXE · зона 1» (the held-out scene's API title has only the tile) */
export function zoneTitle(p: { title: string; scene_kind?: string }): string {
  return p.scene_kind === 'demo' && !p.title.startsWith('Альборан') ? `Альборан · ${p.title}` : p.title;
}

export const SZ_COLOR: Record<string, string> = SZ_COLORS; // §44 п.2: one class colour set — map = list = card = legend
const SIGN_RU: Record<string, string> = { foam: 'пена', glint: 'блик', ship: 'судно / кильватер', seam: 'шов / граница яркости', coast: 'берег / прибой ближе 300 м', shallow: 'мелководье / мутная вода', cloud: 'облака ≥ 20 % зоны', wind: 'ветер > 5 м/с (правило Cózar 2024)' };


export default function SceneZoneCard({
  zone,
  detail,
  onClose,
  onZone,
  onBack,
  onStudio,
  onField,
  num: zoneNum,
  onDrift,
  driftOn,
  driftCheck,
  driftScenes,
}: {
  /** jury_s44 п.3: how many snapshots of the layer have a forecast (shown when this one has none) */
  driftScenes?: number;
  /** §44 п.3: drift forecast of the snapshot (null = no published run for this date) */
  onDrift?: (() => void) | null;
  driftOn?: boolean;
  driftCheck?: string;
  meta: Meta;
  /** §34 п.3: the zone's number in the snapshot list / on the map */
  num?: number;
  zone: Feat<SceneZoneProps>;
  detail: SceneZoneDetail | null;
  onClose: () => void;
  onZone?: (id: string) => void;
  onBack?: () => void;
  onStudio?: () => void;
  onField?: (sampleId: string) => void;
}) {
  const p = zone.properties;
  const m = p.measured;
  const pr = p.probable;
  const qn = p.quantity;
  const fn = p.field_nearby;
  const signs = pr.signs;
  const est = researchEst(p);
  const ql = quantityLine(p);
  return (
    <div className="right-inner" data-testid="scene-zone-card">
      <div className="rp-head">
        <div className="rp-titles">
          <div className="rp-kicker">
            <span className="c-kind szone" aria-hidden />
            {zoneNum ? `Зона ${zoneNum} · ` : 'Спутниковая зона · '}
            {p.scene_kind_label ?? p.scene_kind}
          </div>
          <div className="rp-title" data-testid="card-title">
            {zoneTitle(p)}
          </div>
          <div className="rp-sub">Sentinel-2 · {dateTimeRu(p.datetime)}</div>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Закрыть" data-testid="card-close">
          ✕
        </button>
      </div>
      <div className="rp-body">
        {/* §44 п.1: что это → статус → снимок → количество → исключено → «Подробнее» (свёрнуто) */}
        <div className="sec sz-qtop" data-testid="sz-qtop">
          <div className="sz-what" data-testid="sz-class-what">
            <i className="sz-cls-sw" style={{ background: SZ_COLOR[szKey(p)] ?? '#868e96' }} aria-hidden />
            <span className="faint">Что это:</span> <b>{whatLabel(p)}</b>
          </div>
          {classGloss(p) && (
            <div className="sz-what-gloss faint" data-testid="sz-class-gloss">
              {classGloss(p)}
            </div>
          )}
          <div className="c-line sz-qstatus" data-testid="sz-plain-what">
            <span className="faint">Статус:</span> <b data-testid="sz-status">{statusLabel(p)}</b> <Src k="model" />
          </div>
          {confirmation(p) && (
            <div className="c-line sz-conf" data-testid="sz-confirm">
              <span className="faint">Подтверждение:</span> {confirmation(p)}
            </div>
          )}
        </div>
        <ZoneThumb zone={zone} scene={detail?.scene ?? null} />
        <div className="sec sz-qbody">
          <div className="c-line sz-qty" data-testid="sz-plain-qty">
            <b>{ql.head}</b> <Src k="none" />
            {ql.why && <div className="sz-qty-why faint">{ql.why}</div>}
          </div>
          {est && (
            <details className="sz-scen" data-testid="sz-scenario">
              <summary>{est.scenarioTitle ?? 'Исследовательский сценарий (мишени PLP)'}</summary>
              <div className="sz-scen-b" data-testid="sz-scenario-text">
                {scenarioLine(est)}{' '}
                <Info label="Как получено" align="right" testid="sz-est-info">
                  {est.scenario ? `${cap(est.scenario)}. ` : `${RES_CAPTION}. ${RES_NOTE} `}
                  {est.formula ? `${cap(est.formula)}. ` : ''}
                  {est.essence ? `${cap(est.essence)}. ` : ''}
                  {est.basis ? `Площадь — ${est.basis}. ` : ''}
                  {est.calibration ? `Действующая калибровка: ${est.calibration}. ` : ''}
                  {est.caveats.length ? `Ограничения: ${est.caveats.join('; ')}.` : ''}
                </Info>
              </div>
            </details>
          )}
          <div className="sz-class" data-testid="sz-class">
            {flaggedLabel(p) && (
              <div className="c-line" data-testid="sz-flagged">
                <span className="faint">Признаки:</span> {flaggedLabel(p)}
              </div>
            )}
            {excludedLabel(p) && (
              <div className="c-line" data-testid="sz-excluded">
                <span className="faint">Исключено:</span> {excludedLabel(p)}
              </div>
            )}
            <div className="c-line" data-testid="sz-notchecked">
              <span className="faint">Не проверяется:</span> {notCheckedLabel(p)}
            </div>
          </div>
            <div className="c-line sz-qty" data-testid="sz-plain-comp">
              <b>{p.classification?.composition ?? 'Состав не определён'}</b> <Src k="none" />
            </div>
        </div>
        {/* §47 п.6 / §48: content of «Дрейф» — DriftTab.tsx (L141). Findings → here (в карточке); «недостаточно
            данных · пена/судно» → только в «Подробно» ниже (см. sz-more). driftScenes (счётчик) больше не нужен —
            DriftTab сам показывает неактивное состояние с причиной, когда прогноза для сцены нет. */}
        {isFind(p) && <DriftTab zone={p as any} onShowOnMap={onDrift ?? null} mapOn={driftOn} />}
        {(onBack || onStudio) && (
          <div className="sec c-studio-bar" data-testid="sz-nav">
            {onBack && (
              <button className="btn sm ghost" onClick={onBack} data-testid="sz-back" title="Вернуться к прежнему положению карты и выбрать другую находку">
                ← Назад
              </button>
            )}
            {onStudio && p.detection_status !== 'not_detected' && (
              <button className="btn sm" onClick={onStudio} data-testid="sz-studio" title="Снимок, маска качества и детекция этой зоны">
                В студию →
              </button>
            )}
          </div>
        )}
        <details className="sec sz-more" data-testid="sz-more">
          <summary>Подробнее: маска качества, признаки, площадь, модель, координаты, ветер</summary>
            {!isFind(p) && <DriftTab zone={p as any} onShowOnMap={onDrift ?? null} mapOn={driftOn} />}
            <details className="sz-explore" data-testid="sz-explore">
              <summary className="btn sm">Исследовать дальше</summary>
              <ol className="sz-next-l">
                <li>детальный снимок зоны — дрон или камера с судна (спутник даёт только площадь пятна)</li>
                <li>
                  счёт предметов на детальном снимке — <a href="?mode=photo">счётчик в «Фото»</a> (шт. на кадр, при известной площади кадра — шт./м²)
                </li>
                <li>сверка с полевым измерением на этом же месте и в это же время</li>
              </ol>
            </details>
        <div className="sec sz-plain" data-testid="sz-plain">
          <dl className="c-dl sz-plain-dl">
            <dt>Снимок</dt>
            <dd data-testid="sz-plain-when">
              Sentinel-2, {dateTimeRu(p.datetime)}
              {(() => {
                const c = geomCenter(zone.geometry as any);
                return c ? ` · ${num(c[1], 3)}°, ${num(c[0], 3)}°` : '';
              })()}
            </dd>
            <dt>Маска качества</dt>
            <dd data-testid="sz-plain-quality">
              вода {pct(m.quality?.valid_water_fraction)} · облака {pct(m.quality?.cloud_fraction)} · блик {pct(m.quality?.glint_fraction)} <Src k="mask" />
              {detail?.scene?.wind10m_ms !== null && detail?.scene?.wind10m_ms !== undefined && (
                <>
                  {' '}
                  · ветер {num(detail.scene.wind10m_ms, 1)} м/с <Src k="era5" />
                </>
              )}
            </dd>
            <dt>Площадь зоны</dt>
            <dd data-testid="sz-plain-area">
              {num(m.zone_area_km2 !== null && m.zone_area_km2 !== undefined ? m.zone_area_km2 * 1e6 : null, 0)} м² <Src k="model" />
            </dd>
            <dt>Доля покрытия пикселями</dt>
            <dd data-testid="sz-plain-cover">
              {m.water_km2 && m.suspicious_area_m2 !== null ? `${num((m.suspicious_area_m2 / (m.water_km2 * 1e6)) * 100, 2)} % воды зоны` : '—'} ({num(m.suspicious_area_m2, 0)} м², {num(m.n_pixels, 0)} пикс. по 10 м) <Src k="model" />
            </dd>
            <dt>Оценка детектора</dt>
            <dd data-testid="sz-plain-conf">
              {pr.prob_mean !== null && pr.prob_mean !== undefined
                ? `средняя ${num(pr.prob_mean, 2)}, макс. ${(pr.prob_max ?? 0).toFixed(2).replace('.', ',')} (порог ${num(m.model?.threshold ?? null, 2)}; не откалибрована как вероятность); `
                : ''}
              {p.verification === 'level_B_cozar' ? 'совпадает с разметкой людей (каталог Cózar 2024)' : p.detection_status === 'detected' ? 'независимо не проверено' : '—'}
            </dd>
          </dl>
          {fn?.nearest_organizer_sample && fn.nearest_organizer_sample.distance_km <= 500 && (
            <button className="c-head-link" onClick={() => onField?.(fn.nearest_organizer_sample!.sample_id)} data-testid="sz-field-link">
              Ближайшее полевое измерение — {num(fn.nearest_organizer_sample.distance_km, 0)} км, в слое «Полевые измерения» →
            </button>
          )}
          {fn?.nearest_organizer_sample && fn.nearest_organizer_sample.distance_km > 500 && (
            <div className="c-line faint" data-testid="sz-field-link">
              ближе 500 км полевых измерений нет — сверка невозможна
            </div>
          )}
        </div>
        <div className="sec">
          <span className="c-chip" data-testid="sz-status-chip">
            <i style={{ background: SZ_COLOR[szKey(p)] ?? '#868e96' }} />
            {statusLabel(p)}
            {confirmation(p) ? ` · подтверждение: ${confirmation(p)}` : ''}
          </span>
          {p.detection_reason && <div className="c-line" data-testid="sz-reason">{p.detection_reason}</div>}
          {p.wind_note && (
            <div className="c-line sz-warn" data-testid="sz-wind">
              {p.wind_note}
            </div>
          )}
          {p.training_scene_note && (
            <div className="c-line sz-warn" data-testid="sz-training">
              {p.training_scene_note}
            </div>
          )}
          {p.status_note && <div className="c-line faint tiny">{p.status_note}</div>}
        </div>

        {p.crop_url && (
          <div className="sec" data-testid="sz-crop">
            <figure className="sz-crop">
              <img src={API_BASE + p.crop_url} alt="Вырезка зоны: снимок и пиксели детектора" />
              <figcaption>{p.crop_note}</figcaption>
            </figure>
          </div>
        )}

        {/* ------------------------------------------------ 1. measured */}
        <div className="sec sz-block sz-measured" data-testid="sz-measured">
          <div className="sec-h">
            <h3>По снимку: маска детектора и маска качества</h3>
            <Info label="Что измерено" align="right">
              Величины сняты со снимка и маски детектора без допущений о мусоре. LWD — м² подозрительных пикселей на км² пригодной воды зоны, как «litter windrow
              density» у Cózar et al. 2024. Это площадь покрытия, не число предметов.
            </Info>
          </div>
          <dl className="rows">
            <dt>Площадь зоны</dt>
            <dd data-testid="sz-area">{num(m.zone_area_km2, 2)} км²</dd>
            <dt>Подозрительные пиксели</dt>
            <dd data-testid="sz-px-area">
              {num(m.suspicious_area_m2, 0)} м² <span className="faint">· {num(m.n_pixels, 0)} пикс. · {num(m.n_objects, 0)} об.</span>
            </dd>
            <dt>LWD (м² на км² пригодной воды)</dt>
            <dd data-testid="sz-lwd">
              {num(m.lwd_m2_km2, 0)} <span className="faint">· вода {num(m.water_km2, 2)} км²</span>
              {(m as any).lwd_note && (
                <div className="c-line sz-warn tiny" data-testid="sz-lwd-note">
                  {(m as any).lwd_note}
                </div>
              )}
            </dd>
            <dt>Маска качества в зоне</dt>
            <dd data-testid="sz-quality">
              вода {pct(m.quality.valid_water_fraction)} · облака {pct(m.quality.cloud_fraction)} · блик {pct(m.quality.glint_fraction)}
            </dd>
            <dt>Модель</dt>
            <dd data-testid="sz-model" title={m.model.sha256}>
              {m.model.weights} · sha256 {m.model.sha256_short} · {dateRu(m.model.trained_at)} · порог {num(m.model.threshold ?? null, 2)} · без гармонизации
            </dd>
          </dl>
        </div>

        {/* ------------------------------------------------ 2. probable */}
        <div className="sec sz-block sz-probable" data-testid="sz-probable">
          <div className="sec-h">
            <h3>Вероятно</h3>
            <Info label="Как читать" align="right">
              Вероятность детектора — выход модели по пикселям (класс MARIDA Marine Debris: любой плавающий материал), не вероятность «пластика». Признаки пены,
              блика и судна — эвристики, на размеченных данных не проверены. {pr.context}
            </Info>
          </div>
          <dl className="rows">
            <dt>Оценка детектора, ср. / макс. (не вероятность)</dt>
            <dd data-testid="sz-prob">
              {num(pr.prob_mean, 2)} / {num(pr.prob_max, 2)}
            </dd>
            <dt>Статус</dt>
            <dd>{statusLabel(p)}</dd>
            {signs &&
              (['foam', 'glint', 'ship', 'seam', 'cloud', 'coast', 'shallow'] as const).map((k) =>
                signs[k] ? (
                  <FragmentRow key={k} k={SIGN_RU[k]} v={signs[k].flag ? 'есть' : 'нет'} on={signs[k].flag} rule={signs[k].rule} testid={`sz-sign-${k}`} />
                ) : null,
              )}
            {pr.cozar_note && (
              <>
                <dt>Каталог Cózar 2024</dt>
                <dd data-testid="sz-cozar">{pr.cozar_note}</dd>
              </>
            )}
          </dl>
        </div>

        {!!detail?.examples?.length && (
          <div className="sec" data-testid="sz-examples">
            <div className="sec-h">
              <h3>Как выглядит удача и ошибка</h3>
            </div>
            <div className="sz-ex">
              {detail.examples.map((e) => (
                <button key={e.zone_id} className={`sz-ex-i ${e.kind}`} onClick={() => onZone?.(e.zone_id)} data-testid={`sz-example-${e.kind}`} title={e.note}>
                  <img src={API_BASE + e.crop_url} alt={e.label} loading="lazy" />
                  <span>{e.label}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {detail?.scene && (detail.scene.preview_url || detail.scene.quality_url) && (
          <div className="sec">
            <div className="sec-h">
              <h3>Снимок и маска качества сцены</h3>
              <span className="aside">
                ветер {num(detail.scene.wind10m_ms ?? null, 1)} м/с · зенит Солнца {num(detail.scene.sun_zenith_deg ?? null, 0)}°
              </span>
            </div>
            <div className="c-crops">
              {detail.scene.preview_url && (
                <figure>
                  <img src={API_BASE + detail.scene.preview_url} alt="Снимок" loading="lazy" />
                  <figcaption>снимок (вся вырезка)</figcaption>
                </figure>
              )}
              {detail.scene.quality_url && (
                <figure>
                  <img src={API_BASE + detail.scene.quality_url} alt="Маска качества" loading="lazy" className="q" />
                  <figcaption>маска качества</figcaption>
                </figure>
              )}
            </div>
          </div>
        )}
        </details>
      </div>
    </div>
  );
}

/** §33а: thumbnail of the source S2 scene around the zone with the zone contour on it */
function ZoneThumb({ zone, scene }: { zone: Feat<SceneZoneProps>; scene: SceneZoneDetail['scene'] }) {
  const b = scene?.bounds;
  const g: any = zone.geometry;
  if (!scene?.preview_url || !b || b.length !== 4 || !g) return null;
  const [x0, y0, x1, y1] = b;
  const W = 1000;
  const H = (W * (y1 - y0)) / (x1 - x0);
  const px = (lon: number) => ((lon - x0) / (x1 - x0)) * W;
  const py = (lat: number) => ((y1 - lat) / (y1 - y0)) * H;
  const rings: number[][][] = g.type === 'Polygon' ? g.coordinates : g.type === 'MultiPolygon' ? g.coordinates.flat() : [];
  if (!rings.length) return null;
  let mx0 = Infinity, my0 = Infinity, mx1 = -Infinity, my1 = -Infinity;
  for (const r of rings) for (const [lo, la] of r) {
    mx0 = Math.min(mx0, px(lo)); mx1 = Math.max(mx1, px(lo)); my0 = Math.min(my0, py(la)); my1 = Math.max(my1, py(la));
  }
  const side = Math.max(mx1 - mx0, my1 - my0) * 2.2 + 60;
  const cx = (mx0 + mx1) / 2, cy = (my0 + my1) / 2;
  const vb = `${cx - side / 2} ${cy - side / 2} ${side} ${side}`;
  return (
    <div className="sec" data-testid="sz-thumb">
      <svg className="sz-thumb" viewBox={vb} preserveAspectRatio="xMidYMid slice" role="img" aria-label="Исходный снимок Sentinel-2 с контуром зоны">
        <rect x={cx - side} y={cy - side} width={side * 2} height={side * 2} fill="#0b1a2a" />
        <image href={API_BASE + scene.preview_url} x={0} y={0} width={W} height={H} preserveAspectRatio="none" style={{ imageRendering: 'pixelated' }} />
        {rings.map((r, i) => (
          <polygon key={i} points={r.map(([lo, la]) => `${px(lo)},${py(la)}`).join(' ')} fill="none" stroke="#ffffff" strokeWidth={side / 160} />
        ))}
      </svg>
      <div className="c-line faint tiny">исходный снимок Sentinel-2 (10 м) · белый контур — зона</div>
    </div>
  );
}

function FragmentRow({ k, v, on, rule, testid }: { k: string; v: string; on: boolean; rule: string; testid: string }) {
  return (
    <>
      <dt>Признак: {k}</dt>
      <dd data-testid={testid} className={on ? 'sz-on' : ''} title={rule}>
        {v}
      </dd>
    </>
  );
}
