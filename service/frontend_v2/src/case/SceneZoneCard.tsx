import { useState } from 'react';
// Satellite scene zone (/api/v3/scene_zones, contract 3.10): three separated blocks —
// «Измерено» (by the image), «Вероятно» (detector probability + signs of foam/glint/ship/cloud/coast), «Количество»
// («концентрация по снимку не подтверждена»; no items/km2 scenario — INBOX §23 п.2) — and «Поле рядом»
// (independent field counts C = N/A; measurement ≠ estimate). Numbers only from the API.
import Info from '../components/Info';
import { API_BASE, type Feat, type FC, type Meta } from './api3';
import { dateRu, dateTimeRu, num, pct } from './fmt';
import { geomCenter, isFind, szKey, SZ_COLORS } from './CaseMap';
import DriftTab from './DriftTab';
import PhotoRoles from './PhotoRoles';
import { parseCsv } from './Cards';
import { cap, RES_CAPTION, RES_NOTE, researchEst, scenarioLine } from './estimate';
import { AlertCardLine } from './Alerts'; // §51 п.7 L142
import { openHelp } from './QcPanel'; // §50 P2-9 L142
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
  status_reason?: string | null;
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
  nPairs,
  tab = 'main',
  onTab = () => undefined,
}: {
  /** §47 п.4: accepted field pairs (meta.summary.n_confirmed_pairs) — the conclusion line */
  nPairs?: number | null;
  tab?: CardTab;
  onTab?: (t: CardTab) => void;
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
      {(onBack || onStudio) && (
        <div className="sz-actbar" data-testid="sz-nav">
          {onBack && (
            <button className="btn sm ghost" onClick={onBack} data-testid="sz-back" title="Вернуться к снимку и списку его зон">
              ← к снимку
            </button>
          )}
          {/* §55 п.3: «Дрейф ▶» large and high-contrast in the top row (the tab «Дрейф» keeps the details) */}
          {onDrift ? (
            <button
              className={`btn sz-drift-top ${driftOn ? 'on' : ''}`}
              onClick={onDrift}
              aria-pressed={!!driftOn}
              data-testid="sz-drift-top"
              title="Прогноз дрейфа ≤ 72 ч: OpenDrift по течениям (HYCOM) и ветру (GFS) на дату снимка — эксперимент"
            >
              {driftOn ? 'Дрейф ✓' : 'Дрейф ▶'}
              <span className="drift-map-cap">модельный сценарий</span>
            </button>
          ) : (
            <span className="sz-drift-top-none" data-testid="sz-drift-top-none" title="Для даты этого снимка прогноз дрейфа (OpenDrift, HYCOM + GFS) не рассчитывался">
              Дрейф: нет расчёта
            </span>
          )}
          {onStudio && p.detection_status !== 'not_detected' && (
            <button className="btn sz-studio-btn" onClick={onStudio} data-testid="sz-studio" title="Снимок, маска качества и детекция этой зоны">
              Открыть в студии →
            </button>
          )}
        </div>
      )}
      <div className="rp-body">
        {/* §47 п.4: the conclusion in a frame first, then tabs «Главное | Качество | Подробно | Дрейф | Выгрузка» */}
        <div className={`sz-verdict ${szKey(p)}`} data-testid="sz-verdict">
          {(p as any).is_large && (
            <span className="c-large" data-testid="sz-card-large" title={(p as any).large_reason ?? 'маска ≥ 0,1 км² или длина ≥ 500 м'}>
              крупное скопление
            </span>
          )}
          {verdict(p, nPairs)}
        </div>
        <QuantityBlock p={p} />
        <div className="sz-tabs" role="tablist" data-testid="card-tabs">
          {TABS.map(([k, l]) => (
            <button
              key={k}
              role="tab"
              aria-selected={tab === k}
              className={`sz-tab ${tab === k ? 'on' : ''} ${k === 'drift' && !isFind(p) ? 'off' : ''}`}
              onClick={() => onTab(k)}
              data-testid={`card-tab-${k}`}
              title={k === 'drift' && !isFind(p) ? 'Прогноз дрейфа показываем для находок; для этой зоны — в «Подробно»' : undefined}
            >
              {l}
            </button>
          ))}
        </div>
        {tab === 'main' && (
          <div data-testid="card-pane-main">
            <div className="sec sz-qtop" data-testid="sz-qtop">
          <div className="sz-what" data-testid="sz-class-what">
                <i className="sz-cls-sw" style={{ background: SZ_COLOR[szKey(p)] ?? '#868e96' }} aria-hidden />
                <span className="faint">Что это:</span> <b>{whatLabel(p)}</b>
              </div>
              {p.classification?.organic_label && (
            <div className="c-line sz-organic faint" data-testid="sz-organic">
              {p.classification.organic_label}
            </div>
          )}
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
            <AlertCardLine p={p as any} />
            <ZoneThumb zone={zone} scene={detail?.scene ?? null} />
            <PhotoRoles props={p as any} />
          </div>
        )}
        {tab === 'quality' && (
          <div data-testid="card-pane-quality">
            <QualityPane zone={zone} detail={detail} />
            <div className="sec sz-qbody">
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
          </div>
        )}
        {tab === 'drift' && (
          <div data-testid="card-pane-drift">
            {isFind(p) ? (
              <DriftTab zone={p as any} onShowOnMap={onDrift ?? null} mapOn={driftOn} />
            ) : (
              <div className="sec c-line faint">Прогноз дрейфа показываем для находок; для зоны «{statusLabel(p)}» он — во вкладке «Подробно».</div>
            )}
          </div>
        )}
        {tab === 'export' && (
          <div data-testid="card-pane-export">
            <ZoneExport zone={zone} />
          </div>
        )}
        {tab === 'details' && (
          <div data-testid="card-pane-details">
            <div className="sec sz-qbody">
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
            </div>
            {!isFind(p) && <DriftTab zone={p as any} onShowOnMap={onDrift ?? null} mapOn={driftOn} />}
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

        {/* §50 P2-9 (L142): «Как выглядит удача и ошибка» moved to «Справка / Методика» (QcPanel) — one link here */}
        {!!detail?.examples?.length && (
          <div className="sec c-line" data-testid="sz-examples">
            <button className="link" onClick={openHelp} data-testid="sz-examples-help">
              Как выглядит удача и ошибка — в «Справка / Методика» →
            </button>
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
          </div>
        )}
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

/** §47 п.4: card tabs */
export type CardTab = 'main' | 'quality' | 'details' | 'drift' | 'export';
const TABS: [CardTab, string][] = [
  ['main', 'Главное'],
  ['quality', 'Качество'],
  ['details', 'Подробно'],
  ['drift', 'Дрейф'],
  ['export', 'Выгрузка'],
];

/** §47 п.4: the conclusion line in a frame (numbers from the API: status, flags, meta.summary.n_confirmed_pairs) */
function verdict(p: SceneZoneProps, nPairs: number | null | undefined): string {
  const pairs = `Принятых полевых пар: ${nPairs ?? 0}.`;
  const st = p.status ?? p.detection_status;
  if (st === 'detected') {
    const training = p.confirmation === 'training_scene';
    return training
      ? `Есть детекция плавающего материала на снимке обучения детектора (не независимая проверка). Пластик и количество шт./км² по этому снимку не подтверждены. ${pairs}`
      : `Есть детекция плавающего материала. Пластик и количество шт./км² по этому снимку не подтверждены. ${pairs}`;
  }
  if (st === 'not_detected') return 'Не обнаружено: детектор оценил снимок, объектов плавающего материала нет.';
  const why = flaggedLabel(p) ?? p.status_reason ?? p.detection_reason ?? null;
  return `Недостаточно данных${why ? `: ${why}` : ''}. Загрязнение по этому снимку не подтверждено.`;
}

/** «Качество»: the quality mask of the zone (the same numbers as «Подробно») + the snapshot and its mask */
function QualityPane({ zone, detail }: { zone: Feat<SceneZoneProps>; detail: SceneZoneDetail | null }) {
  const m = zone.properties.measured;
  const sc = detail?.scene;
  return (
    <div className="sec" data-testid="sz-quality-pane">
      <div className="c-line sz-qline" data-testid="sz-quality-line">
        <span className="faint">Маска качества:</span> вода {pct(m.quality?.valid_water_fraction)} · облака {pct(m.quality?.cloud_fraction)} · блик{' '}
        {pct(m.quality?.glint_fraction)}
        {sc?.wind10m_ms !== null && sc?.wind10m_ms !== undefined ? ` · ветер ${num(sc.wind10m_ms, 1)} м/с (ERA5)` : ''}
      </div>
      {(sc?.preview_url || sc?.quality_url) && (
        <div className="sz-qimgs">
          {sc?.preview_url && (
            <figure>
              <img src={API_BASE + sc.preview_url} alt="Снимок" loading="lazy" />
              <figcaption>снимок Sentinel-2</figcaption>
            </figure>
          )}
          {sc?.quality_url && (
            <figure>
              <img src={API_BASE + sc.quality_url} alt="Маска качества" loading="lazy" className="q" />
              <figcaption>маска: тёмное — годная вода, белое — облака, жёлтое — блик, серое — суша</figcaption>
            </figure>
          )}
        </div>
      )}
    </div>
  );
}

const csvCell = (v: string) => (/[",\n\r]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
function download(name: string, body: string, type: string) {
  const url = URL.createObjectURL(new Blob([body], { type }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 2000);
}

/** «Выгрузка»: this zone only — GeoJSON = the API feature, CSV = its row of the API export of the snapshot */
function ZoneExport({ zone }: { zone: Feat<SceneZoneProps> }) {
  const p = zone.properties;
  const [err, setErr] = useState<string | null>(null);
  const sceneCsv = `${API_BASE}/api/v3/export?layer=scene_zones&format=csv&scene_key=${encodeURIComponent(p.scene_key)}`;
  const csv = async () => {
    setErr(null);
    try {
      const r = await fetch(sceneCsv);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const rows = parseCsv((await r.text()).replace(/^﻿/, ''));
      const h = rows[0] ?? [];
      const iz = h.indexOf('zone_id');
      const row = rows.find((x, i) => i > 0 && x[iz] === p.zone_id);
      if (!row) throw new Error('строки зоны в выгрузке нет');
      download(`${p.zone_id}.csv`, '﻿' + [h, row].map((x) => x.map(csvCell).join(',')).join('\r\n') + '\r\n', 'text/csv;charset=utf-8');
    } catch (e: any) {
      setErr(String(e?.message ?? e));
    }
  };
  return (
    <div className="sec" data-testid="sz-export">
      <div className="c-line">Эта зона ({p.zone_id}) — те же поля, что в общей выгрузке API:</div>
      <div className="sz-exp-btns">
        <button className="btn sm" onClick={csv} data-testid="sz-export-csv">
          CSV зоны
        </button>
        <button
          className="btn sm"
          onClick={() => download(`${p.zone_id}.geojson`, JSON.stringify({ type: 'FeatureCollection', features: [zone] }, null, 1), 'application/geo+json')}
          data-testid="sz-export-geojson"
        >
          GeoJSON зоны
        </button>
        <a className="btn sm ghost" href={sceneCsv} download data-testid="sz-export-scene">
          CSV всего снимка
        </a>
      </div>
      {err && <div className="c-err" data-testid="sz-export-err">Не удалось: {err}</div>}
    </div>
  );
}

/** §51 п.2 = §50 P0 (1): «Оценка количества» right under the conclusion — by image / by field / by photo; fields of the
 *  API (L131: quantity_by_image, field_estimate, units); the unit switch changes only the «по снимку» row */
type Unit = 'items' | 'area' | 'cover';
function QuantityBlock({ p }: { p: SceneZoneProps }) {
  const [unit, setUnit] = useState<Unit>('items');
  const fe: any = (p as any).field_estimate ?? null;
  const u: any = (p as any).units ?? null;
  const qImg: string = (p as any).quantity_by_image ?? 'не определено';
  const hasArea = u && typeof u.area_m2_per_km2 === 'number';
  const fieldTxt = fe
    ? typeof fe.value === 'number'
      ? fe.basis === 'basin_profile'
        ? `${num(fe.value, fe.value < 10 ? 1 : 0)}${typeof fe.lo === 'number' && typeof fe.hi === 'number' ? ` [${num(fe.lo, fe.lo < 10 ? 2 : 0)}–${num(fe.hi, fe.hi < 10 ? 1 : 0)}]` : ''} шт./км² — ${fe.basin_profile?.profile_note ?? 'профиль акватории по полевым данным — не измерение этого участка'} (${fe.basin_profile?.basin_name ?? ''}; ${fe.source ?? ''}${fe.profile ? `, ${fe.profile}` : ''})`
        : `${num(fe.value, fe.value < 10 ? 1 : 0)} шт./км² (${fe.source ?? fe.source_id ?? 'поле'}, ${/^\d{4}-\d\d-\d\d/.test(String(fe.date)) ? dateRu(fe.date) : fe.date ?? '—'}, ${num(fe.distance_km, 0)} км от зоны)`
      : fe.nearest
        ? `нет измерений в районе (до ${num(fe.region_km ?? 50, 0)} км); ближайшее — ${fe.nearest.source ?? fe.nearest.source_id}, ${num(fe.nearest.distance_km, 0)} км, другое место`
        : String(fe.label ?? fe.reason ?? 'нет измерений в районе').replace(/^По полю:\s*/, '')
    : 'нет данных';
  const imgTxt =
    unit === 'items' || !hasArea
      ? qImg
      : unit === 'area'
        ? `${num(u.area_m2_per_km2, 0)} м² скоплений на км² — ${u.area_note ?? 'площадь, не предметы'}`
        : `${num(u.coverage_pct, 2)} % покрытия — ${u.area_note ?? 'площадь, не предметы'}`;
  return (
    <div className="sec sz-quant" data-testid="sz-quantity">
      <div className="sz-quant-h">
        <b>Оценка количества</b>
        {hasArea && (
          <span className="sz-units" role="group" aria-label="Единицы" data-testid="sz-units">
            {(
              [
                ['items', 'шт./км²'],
                ['area', 'м²/км²'],
                ['cover', '% покрытия'],
              ] as [Unit, string][]
            ).map(([k, l]) => (
              <button key={k} className={unit === k ? 'on' : ''} onClick={() => setUnit(k)} data-testid={`sz-unit-${k}`} aria-pressed={unit === k}>
                {l}
              </button>
            ))}
          </span>
        )}
      </div>
      <div className="c-line sz-qty" data-testid="sz-plain-qty">
        <span className="faint">По снимку:</span> {imgTxt}
      </div>
      <div className="c-line" data-testid="sz-q-field">
        <span className="faint">По полю:</span> {fieldTxt}
        {fe?.trust_note && fe?.basis !== 'basin_profile' ? <span className="faint"> — {fe.trust_note}</span> : null}
      </div>
      <div className="c-line" data-testid="sz-q-photo">
        <span className="faint">По фото:</span> доступно при детальном снимке зоны (счёт по фото)
      </div>
      {(fe?.basin_profile?.method || fe?.method_note) && (
        <div className="c-line tiny faint" data-testid="sz-q-method">
          Метод: {fe.basis === 'basin_profile' && fe.basin_profile?.method ? fe.basin_profile.method : fe.method_note}
        </div>
      )}
    </div>
  );
}
