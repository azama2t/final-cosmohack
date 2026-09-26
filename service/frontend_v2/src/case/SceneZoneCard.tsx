// Satellite scene zone (/api/v3/scene_zones, contract 3.10): three separated blocks —
// «Измерено» (by the image), «Вероятно» (detector probability + signs of foam/glint/ship/cloud/coast), «Количество»
// («концентрация по снимку не подтверждена»; no items/km2 scenario — INBOX §23 п.2) — and «Поле рядом»
// (independent field counts C = N/A; measurement ≠ estimate). Numbers only from the API.
import Info from '../components/Info';
import { API_BASE, type Feat, type FC, type Meta } from './api3';
import { dateRu, dateTimeRu, num, pct } from './fmt';

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
  scene: { preview_url: string | null; quality_url: string | null; wind10m_ms?: number | null; sun_zenith_deg?: number | null; lwd_m2_km2?: number | null; water_km2?: number | null } | null;
}

export const SZ_COLOR: Record<string, string> = { detected: '#ff8c42', unverified: '#d9b870', not_detected: '#2b8a3e', insufficient_data: '#868e96' };
const SIGN_RU: Record<string, string> = { foam: 'пена', glint: 'блик', ship: 'судно / кильватер', seam: 'шов / граница яркости', coast: 'берег / прибой ближе 300 м', shallow: 'мелководье / мутная вода', cloud: 'облака ≥ 20 % зоны', wind: 'ветер > 5 м/с (правило Cózar 2024)' };


export default function SceneZoneCard({
  zone,
  detail,
  onClose,
  onZone,
}: {
  meta: Meta;
  zone: Feat<SceneZoneProps>;
  detail: SceneZoneDetail | null;
  onClose: () => void;
  onZone?: (id: string) => void;
}) {
  const p = zone.properties;
  const m = p.measured;
  const pr = p.probable;
  const qn = p.quantity;
  const fn = p.field_nearby;
  const signs = pr.signs;
  return (
    <div className="right-inner" data-testid="scene-zone-card">
      <div className="rp-head">
        <div className="rp-titles">
          <div className="rp-kicker">
            <span className="c-kind szone" aria-hidden />
            Спутниковая зона · {p.scene_kind_label ?? p.scene_kind}
          </div>
          <div className="rp-title" data-testid="card-title">
            {p.title}
          </div>
          <div className="rp-sub">Sentinel-2 · {dateTimeRu(p.datetime)}</div>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Закрыть" data-testid="card-close">
          ✕
        </button>
      </div>
      <div className="rp-body">
        <div className="sec">
          <span className="c-chip" data-testid="sz-status">
            <i style={{ background: SZ_COLOR[p.detection_status === 'detected' && p.verification !== 'level_B_cozar' ? 'unverified' : p.detection_status] ?? '#868e96' }} />
            {p.detection_label}
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
            <h3>Измерено по снимку</h3>
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
            <dt>Вероятность детектора, ср. / макс.</dt>
            <dd data-testid="sz-prob">
              {num(pr.prob_mean, 2)} / {num(pr.prob_max, 2)}
            </dd>
            <dt>Статус находки</dt>
            <dd>{pr.status}</dd>
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

        {/* ------------------------------------------------ 3. quantity (no scenario, INBOX §23 п.2) */}
        <div className="sec sz-block sz-quantity" data-testid="sz-quantity">
          <div className="sec-h">
            <h3>Количество</h3>
            <span className="aside">шт./км² по снимку не выдаём</span>
          </div>
          <div className="c-line sz-qstatus" data-testid="sz-quantity-status">
            <b>{qn?.label ?? p.concentration_label ?? 'концентрация по снимку не подтверждена'}</b>
          </div>
          <details className="sz-basis" data-testid="sz-quantity-more">
            <summary>подробнее</summary>
            <div className="c-line">{qn?.detail ?? p.scenario_reason}</div>
          </details>
        </div>

        {/* ------------------------------------------------ what next (§31 п.2 г, no numbers) */}
        <div className="sec sz-block sz-next" data-testid="sz-next">
          <div className="sec-h">
            <h3>Что дальше</h3>
          </div>
          <ol className="sz-next-l">
            <li>снять детально (дрон, камера с судна) — спутник даёт только площадь</li>
            <li>
              посчитать предметы — <a href="?mode=photo">пример в «Фото»</a>
            </li>
            <li>сверить с полем рядом (ниже)</li>
          </ol>
        </div>

        {/* ------------------------------------------------ field nearby */}
        <div className="sec sz-block sz-field" data-testid="sz-field">
          <div className="sec-h">
            <h3>Поле рядом</h3>
            <span className="aside">измерение ≠ оценка</span>
          </div>
          {fn?.items?.length ? (
            <table className="c-ptable">
              <thead>
                <tr>
                  <th>Отрезок</th>
                  <th className="r">км</th>
                  <th className="r">N / A</th>
                  <th className="r">C = N/A, шт./км²</th>
                </tr>
              </thead>
              <tbody>
                {fn.items.map((x) => (
                  <tr key={x.segment_id} data-testid="sz-field-row">
                    <td title={x.source}>
                      ADIS {x.ship} · {dateRu(x.date)}
                    </td>
                    <td className="r">{num(x.distance_km, 0)}</td>
                    <td className="r">
                      {x.n_items} / {num(x.area_km2, 3)}
                    </td>
                    <td className="r">
                      {num(x.c_items_km2)} <span className="faint">[{num(x.ci95_lo)}–{num(x.ci95_hi)}]</span>
                      {(x as any).authors_cal_10cm_items_km2 !== null && (x as any).authors_cal_10cm_items_km2 !== undefined && (
                        <div className="faint tiny" data-testid="sz-field-cal" title="калибровка авторов ADIS (по тралу, de Vries 2026), класс > 10 см; не наша">
                          авторы ({">"} 10 см): {num((x as any).authors_cal_10cm_items_km2)}{' '}
                          {(x as any).authors_cal_10cm_lo95 !== null && (x as any).authors_cal_10cm_hi95 !== null
                            ? `[${num((x as any).authors_cal_10cm_lo95)}–${num((x as any).authors_cal_10cm_hi95)}]`
                            : '· интервал не дан'}{' '}
                          · наше без поправок {num((x as any).raw_10cm_items_km2)}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="c-line">полевых измерений рядом нет</div>
          )}
          <div className="c-line faint">
            {fn?.items?.[0]?.size_class ? `предметы ${fn.items[0].size_class}; 95 % интервал Пуассона. ` : ''}
            {fn?.note}
            {fn?.nearest_organizer_sample ? ` Ближайшее измерение CSV организаторов — ${fn.nearest_organizer_sample.sample_id}, ${num(fn.nearest_organizer_sample.distance_km, 0)} км.` : ''}
          </div>
          {fn?.authors_calibration && (
            <div className="c-line" data-testid="sz-field-authors" title={fn.authors_calibration.source}>
              Все отрезки ADIS ({fn.authors_calibration.size_class}): калибровка авторов ADIS (по тралу), не наша —{' '}
              <b>{num(fn.authors_calibration.C)}</b> шт./км², типичный интервал отрезка {num(fn.authors_calibration.lo_typ)}–{num(fn.authors_calibration.hi_typ)}; наше без
              поправки — <b>{num(fn.authors_calibration.ours_raw_C)}</b>
            </div>
          )}
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
      </div>
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
