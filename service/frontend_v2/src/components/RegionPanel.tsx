import { useEffect, useMemo, useState } from 'react';
import type { DetProps, Feature, Region } from '../types';
import type { RightProps } from './RightPanel';
import { apiGet } from '../lib/api';
import { apiAvailable, dataUrl, isFlagged, modelPath, regionReliability } from '../lib/data';
import { fmtArea, fmtDate, fmtDateShort, fmtNum, fmtPct, fmtPermille, modelLabel, rankColor, rgbStr } from '../lib/style';
import { centroid } from '../map/layers';
import { fmtKm } from '../lib/route';
import TsChart from './TsChart';
import Calendar from './Calendar';

type P = RightProps & { region: Region };

/** Sensor of a scene: Sentinel-2 by the scene id / source, else a neutral «спутниковый снимок» (any organiser data). */
export function sensorOf(sceneId?: string | null, source?: string | null): { name: string; px: number | null } {
  if (/^S2[ABCD]?_/i.test(sceneId ?? '') || /sentinel-?2/i.test(source ?? '')) return { name: 'Sentinel-2', px: 10 };
  if (/^L[COT]0?[89]_/i.test(sceneId ?? '') || /landsat/i.test(source ?? '')) return { name: 'Landsat', px: 30 };
  return { name: 'спутниковый снимок', px: null };
}

/** «S2A_MSIL2A_20260530T160841_…» → «16:08 UTC» */
export function sceneTime(sceneId?: string | null): string | null {
  const m = /_(\d{8})T(\d{2})(\d{2})\d{2}_/.exec(sceneId ?? '');
  return m ? `${m[2]}:${m[3]} UTC` : null;
}

export const STATUS_RU: Record<string, string> = {
  detected: 'не проверено',
  under_review: 'на проверке',
  confirmed: 'подтверждено оператором',
  false_alarm: 'ложное (оператор)',
  resolved: 'закрыто',
  excluded: 'исключено системой',
};
export const STATUS_CLS: Record<string, string> = {
  detected: 's-detected',
  under_review: 's-review',
  confirmed: 's-confirmed',
  false_alarm: 's-false',
  resolved: 's-removed',
  excluded: 's-false',
};

function SceneLine({ p }: { p: P }) {
  const de = p.dateEntry;
  if (!de) return null;
  const t = sceneTime(de.scene_id);
  return (
    <>
      <div className="lead" data-testid="scene-line">
        Снимок {sensorOf(de.scene_id, de.source).name} · <b>{fmtDate(de.date)}</b>
        {t ? `, ${t}` : ''} <span className="faint">· облачность {fmtPct(de.cloud_frac)}</span>
      </div>
      {isFlagged(de) && (
        <div className="warnline" style={{ marginTop: 8 }}>
          Дымка или блик на снимке — признаки могут быть завышены
        </div>
      )}
      {!regionReliability(p.region).ok && !isFlagged(de) && (
        <div className="warnline" style={{ marginTop: 8 }}>
          {regionReliability(p.region).why}
        </div>
      )}
    </>
  );
}

export function FindingsView(p: P) {
  const feats = p.detections?.features ?? null;
  const [all, setAll] = useState(false);
  const list = useMemo(() => {
    if (!feats) return [];
    const pr = (f: Feature<DetProps>) => p.incidents?.get(f.properties.id)?.priority ?? 99;
    return [...feats].sort((a, b) => pr(a) - pr(b) || b.properties.area_m2 - a.properties.area_m2);
  }, [feats, p.incidents]);
  const area = feats?.reduce((a, f) => a + (f.properties.area_m2 || 0), 0) ?? 0;
  const [av, au] = fmtArea(area);
  const reviewed = feats ? feats.filter((f) => ['confirmed', 'false_alarm'].includes(p.incidents?.get(f.properties.id)?.status ?? '')).length : 0;
  const nConfirmedHuman = feats ? feats.filter((f) => p.incidents?.get(f.properties.id)?.status === 'confirmed').length : 0;
  return (
    <>
      <section className="sec" data-testid="region-panel">
        <SceneLine p={p} />
        {feats === null ? (
          <p className="note" style={{ marginTop: 12 }}>
            Загрузка находок…
          </p>
        ) : feats.length ? (
          <>
            <p style={{ marginTop: 12 }} data-testid="findings-summary">
              Модель {modelLabel(p.model)} отметила <b className="accent">{feats.length}</b> {plural(feats.length, 'участок', 'участка', 'участков')} с признаками
              плавающего материала, всего {av} {au}. Это <b>приоритет проверки</b>, а не подтверждённый мусор.
            </p>
            <p className="hint-line" data-testid="findings-reviewed">
              Проверено человеком: {reviewed} из {feats.length}
              {nConfirmedHuman ? ` · подтверждено оператором ${nConfirmedHuman}` : ''}.
            </p>
            <p className="hint-line">Нажмите на жёлтое кольцо на карте или на строку ниже — откроется карточка доказательств.</p>
          </>
        ) : (
          <p style={{ marginTop: 12 }}>
            На этом снимке модель не отметила признаков плавающего материала. Другие даты — в «Истории».
          </p>
        )}
      </section>

      {list.length > 0 && (
        <section className="sec" data-testid="findings-list">
          <div className="sec-h">
            <h3>Находки снимка</h3>
            <span className="aside">по приоритету проверки</span>
          </div>
          <div className="list">
            {(all ? list : list.slice(0, 8)).map((f, i) => {
              const pr = f.properties;
              const [a, u] = fmtArea(pr.area_m2);
              const inc = p.incidents?.get(pr.id);
              const st = inc?.status ?? 'detected';
              return (
                <button key={pr.id} className="li" onClick={() => p.onDetection(f)} data-testid={`finding-row-${i}`}>
                  <span className="rank" style={{ background: 'transparent', border: '1.5px solid var(--accent)', color: 'var(--text)', borderRadius: '50%' }}>
                    {i + 1}
                  </span>
                  <span className="l-main">
                    <span>
                      {a} {u} · уверенность {pr.mean_prob.toFixed(2)}
                    </span>
                    <span className="l-sub" style={{ display: 'block' }}>
                      <span className={`status ${STATUS_CLS[st] ?? ''}`} style={{ fontSize: 12 }}>
                        {STATUS_RU[st] ?? st}
                      </span>
                      {typeof pr.confirmed === 'boolean' ? (pr.confirmed ? ' · вторая модель тоже видит' : ' · вторая модель не видит') : ''}
                      {inc?.priority ? ` · в зоне №${inc.priority}` : ''}
                    </span>
                  </span>
                  <span className="l-val">›</span>
                </button>
              );
            })}
          </div>
          {list.length > 8 && (
            <button className="link small" style={{ marginTop: 12 }} onClick={() => setAll((v) => !v)}>
              {all ? 'Свернуть' : `Все ${list.length}`}
            </button>
          )}
        </section>
      )}

      {p.nArtifacts > 0 && (
        <section className="sec">
          <p className="note" data-testid="artifacts-note">
            Ещё {p.nArtifacts} объектов модель выделила, но сборка исключила их как артефакты (шов детекторов, след судна, судно). В счёт не
            входят. Показать на карте — «Настройки карты» → «Исключённые артефакты».
          </p>
        </section>
      )}
      <HonestNote p={p} />
    </>
  );
}

export function ZonesView(p: P) {
  const zones = p.zones?.zones ?? [];
  const [all, setAll] = useState(false);
  return (
    <>
      <section className="sec">
        <SceneLine p={p} />
        <p style={{ marginTop: 12 }}>
          Зоны обследования — ячейки сетки H3 (~0,7 км²), где больше всего воды с признаками материала, с учётом повторяемости по датам,
          уверенности и согласия моделей. <b>Порядок проверки, не измеренная опасность.</b>
        </p>
      </section>
      {zones.length > 0 ? (
        <section className="sec" data-testid="zones-list">
          <div className="sec-h">
            <h3>Зоны по приоритету</h3>
            <span className="aside">{zones.length}</span>
          </div>
          <div className="list">
            {(all ? zones : zones.slice(0, 6)).map((z) => {
              const [a, u] = fmtArea(z.area_m2);
              return (
                <button key={z.rank} className={`li ${p.zone?.rank === z.rank ? 'on' : ''}`} onClick={() => p.onZone(z)} data-testid={`zone-row-${z.rank}`}>
                  <span className="rank" style={{ background: rgbStr(rankColor(z.rank, Math.max(zones.length, 5))) }}>
                    {z.rank}
                  </span>
                  <span className="l-main">
                    <span>
                      индекс {fmtPermille(z.index)} ‰ · {a} {u}
                    </span>
                    <span className="l-sub" style={{ display: 'block' }}>
                      {z.n_detections ?? '—'} {plural(z.n_detections ?? 0, 'участок', 'участка', 'участков')}
                      {(z.repeat_dates ?? 1) > 1 ? ` · повтор на ${z.repeat_dates} датах` : ''}
                    </span>
                  </span>
                  <span className="l-val">почему ›</span>
                </button>
              );
            })}
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 12, gap: 8 }}>
            {zones.length > 6 ? (
              <button className="link small" onClick={() => setAll((v) => !v)}>
                {all ? 'Свернуть' : `Все ${zones.length}`}
              </button>
            ) : (
              <span />
            )}
            <button className="menu-row" style={{ padding: 0, width: 'auto' }} onClick={() => p.onRoute(!p.routeOn)} data-testid="route-toggle">
              <span className={`check ${p.routeOn ? 'on' : ''}`} aria-hidden />
              <span className="small">Порядок посещения</span>
            </button>
          </div>
          {p.route && (
            <p className="note" style={{ marginTop: 8 }} data-testid="route-summary">
              От «{p.route.port.name}»: {p.route.legs.map((l) => l.zone.rank).join(' → ')}, ≈ {fmtKm(p.route.totalKm)} км по прямой. Черновой
              порядок, не навигационный маршрут.
            </p>
          )}
        </section>
      ) : (
        <section className="sec note">На этой дате зон нет.</section>
      )}
      <section className="sec">
        <div className="sec-h">
          <h3>Сетка индекса H3</h3>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <button className={`btn sm ${p.layers.h3 ? 'on' : ''}`} onClick={() => p.onLayer('h3')} data-testid="zones-h3">
            Показать сетку
          </button>
          <button className={`btn sm ${p.layers.h3_3d ? 'on' : ''}`} onClick={() => p.onLayer('h3_3d')} data-testid="zones-h3-3d">
            В объёме (3D)
          </button>
        </div>
        <p className="note" style={{ marginTop: 8 }}>
          Цвет и высота — доля наблюдаемой воды с признаками материала в ячейке, ‰.
        </p>
      </section>
      <HonestNote p={p} />
    </>
  );
}

export function HistoryView(p: P) {
  const r = p.region;
  const de = p.dateEntry;
  const ts = p.timeseries?.find((t) => t.date === de?.date && t.model === p.model) ?? null;
  const nDet = p.detections?.features.length ?? null;
  const [av, au] = fmtArea(ts?.total_debris_area_m2 ?? null);
  return (
    <>
      <section className="sec">
        <div className="sec-h">
          <h3>Даты снимков</h3>
          <span className="aside">точка — дымка или блик</span>
        </div>
        <div className="dates" data-testid="date-strip">
          {r.dates.map((d) => (
            <button
              key={d.date}
              className={`${d.date === de?.date ? 'on' : ''} ${isFlagged(d) ? 'flag' : ''}`}
              onClick={() => p.onDate(d.date)}
              data-testid={`date-${d.date}`}
              title={isFlagged(d) ? 'дымка/блик — находки могут быть завышены' : d.scene_id}
            >
              {fmtDateShort(d.date).slice(0, 5)}
              <small>{d.date.slice(0, 4)}</small>
            </button>
          ))}
        </div>
        <div className="kv three" style={{ marginTop: 16 }} data-testid="kpi">
          <div>
            <div className="k">Индекс</div>
            <div className="v">
              {fmtPermille(ts?.mean_index ?? null)}
              <small>‰</small>
            </div>
            <div className="h">доля воды с признаками</div>
          </div>
          <div>
            <div className="k">Находки</div>
            <div className="v accent">{nDet === null ? '…' : fmtNum(nDet)}</div>
            <div className="h">{p.nConfirmed !== null ? `с согласием моделей ${p.nConfirmed}` : 'участков'}</div>
          </div>
          <div>
            <div className="k">Площадь</div>
            <div className="v">
              {av}
              <small>{au}</small>
            </div>
            <div className="h">пикселей ≥ порога</div>
          </div>
        </div>
      </section>
      {p.timeseries && p.timeseries.length > 1 && de && (
        <section className="sec">
          <div className="sec-h">
            <h3>Индекс по датам</h3>
            <span className="aside">сплошная — {modelLabel(p.model)}, пунктир — другая модель</span>
          </div>
          <TsChart rows={p.timeseries} model={p.model} date={de.date} models={p.manifest.models} onDate={p.onDate} />
        </section>
      )}
      <Calendar region={r} model={p.model} date={de?.date ?? null} timeseries={p.timeseries} onDate={p.onDate} />
      <section className="sec">
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn sm" onClick={p.onCompare} data-testid="compare-open-region">
            Сравнить с другим районом
          </button>
        </div>
      </section>
      {de && <Export region={r.id} date={de.date} model={p.model} p={p} />}
      <HonestNote p={p} />
    </>
  );
}

const COMPASS = ['север', 'северо-восток', 'восток', 'юго-восток', 'юг', 'юго-запад', 'запад', 'северо-запад'];
/** «за 72 ч центр облака частиц смещается на ~N км на северо-запад» from drift.json (start vs end positions). */
function driftSummary(d: RightProps['drift']): { km: number; dir: string; h: number } | null {
  const ps = (d?.particles ?? []).filter((x) => x.path?.length > 1);
  if (!ps.length) return null;
  const mean = (pick: (x: (typeof ps)[0]) => number[]) => {
    let a = 0,
      b = 0;
    for (const x of ps) {
      const q = pick(x);
      a += q[0];
      b += q[1];
    }
    return [a / ps.length, b / ps.length];
  };
  const s = mean((x) => x.path[0]);
  const e = mean((x) => x.path[x.path.length - 1]);
  const h = Math.max(...ps.map((x) => x.path[x.path.length - 1][2] ?? 72));
  const dx = (e[0] - s[0]) * 111.32 * Math.cos((s[1] * Math.PI) / 180);
  const dy = (e[1] - s[1]) * 111.32;
  const km = Math.hypot(dx, dy);
  const ang = ((Math.atan2(dx, dy) * 180) / Math.PI + 360) % 360;
  return { km, dir: COMPASS[Math.round(ang / 45) % 8], h };
}

export function DriftView(p: P) {
  const de = p.dateEntry;
  const f = p.drift?.forcing ?? {};
  const sum = useMemo(() => driftSummary(p.drift), [p.drift]);
  const [near, setNear] = useState<any[] | null>(null);
  useEffect(() => {
    let alive = true;
    setNear(null);
    if (!de?.drift || !p.paths.has('/api/threats') || !p.contextOk) return;
    apiGet<any>('/api/threats', { region: p.region.id, date: de.date }).then((t) => alive && setNear(t?.objects ?? t?.threats ?? []));
    return () => {
      alive = false;
    };
  }, [p.region.id, de?.date, de?.drift, p.paths, p.contextOk]);
  return (
    <>
      <section className="sec" data-testid="drift-panel">
        <SceneLine p={p} />
        {de?.drift ? (
          <p style={{ marginTop: 12 }}>
            Куда течения и ветер могут унести материал за 72 часа после снимка. Частицы стартуют от находок; светлые линии — их пути, серое облако —
            разброс при другом ветровом коэффициенте. <b>Демонстрационный прогноз, не валидирован.</b>
          </p>
        ) : null}
        {de?.drift && sum ? (
          <p className="lead" style={{ marginTop: 12 }} data-testid="drift-summary">
            За {Math.round(sum.h)} ч центр облака частиц смещается примерно на {fmtNum(sum.km, sum.km < 10 ? 1 : 0)} км на {sum.dir}.
          </p>
        ) : null}
        {de?.drift ? null : (
          <p style={{ marginTop: 12 }}>Для этой даты прогноза дрейфа нет. Прогнозы есть только для свежих снимков — выберите другую дату в «Истории».</p>
        )}
      </section>
      {de?.drift && (
        <section className="sec">
          <div className="sec-h">
            <h3>Что двигает частицы</h3>
          </div>
          <button className="menu-row" style={{ padding: '4px 0' }} disabled={!p.flowAvail.includes('currents')} onClick={() => p.onLayer('currents')} data-testid="drift-currents">
            <span className={`check ${p.layers.currents ? 'on' : ''}`} aria-hidden />
            <span>Поверхностные течения</span>
          </button>
          <button className="menu-row" style={{ padding: '4px 0' }} disabled={!p.flowAvail.includes('wind')} onClick={() => p.onLayer('wind')} data-testid="drift-wind">
            <span className={`check ${p.layers.wind ? 'on' : ''}`} aria-hidden />
            <span>Ветер 10 м</span>
          </button>
          <dl className="rows" style={{ marginTop: 12 }}>
            <dt>Модель дрейфа</dt>
            <dd>{f.model ?? 'OpenDrift'}</dd>
            <dt>Течения</dt>
            <dd>{short(f.currents)}</dd>
            <dt>Ветер</dt>
            <dd>{short(f.wind)}</dd>
            <dt>Ветровой коэффициент</dt>
            <dd>{f.wind_drift_factor ?? '—'}</dd>
          </dl>
        </section>
      )}
      {near && near.length > 0 && (
        <section className="sec" data-testid="drift-osm">
          <div className="sec-h">
            <h3>Рядом с путями частиц</h3>
            <span className="aside">демо-прогноз, не валидирован</span>
          </div>
          <p className="note" style={{ color: 'var(--text-2)' }}>
            {near.slice(0, 6).map((t, i) => (
              <span key={t.object_id ?? i}>
                {i ? '; ' : ''}
                {t.kind_ru}
                {t.name ? ` «${t.name}»` : ''}
              </span>
            ))}
            {near.length > 6 ? ` и ещё ${near.length - 6}` : ''}.
          </p>
          <p className="note" style={{ marginTop: 8 }}>
            Объекты OpenStreetMap, которые задевает облако частиц демо-прогноза. Это не предупреждение и не оценка ущерба. Показать на карте —
            «Настройки карты» → «Объекты OSM». © OpenStreetMap contributors (ODbL).
          </p>
        </section>
      )}
      {p.paths.has('/api/drift_check') && (
        <section className="sec">
          <div className="sec-h">
            <h3>
              Проверка прогноза<span className="exp-tag">эксперимент</span>
            </h3>
          </div>
          <p className="note">Сопоставление прогноза со следующим снимком того же района и с базовой линией «материал остался на месте».</p>
          <button className="btn sm" style={{ marginTop: 8 }} onClick={p.onCheck} data-testid="check-open">
            Открыть эксперимент
          </button>
        </section>
      )}
    </>
  );
}

function HonestNote({ p }: { p: P }) {
  return (
    <section className="sec">
      <p className="note">
        <b>Индекс по снимку, не масса пластика.</b> «Признаки плавающего материала» — это пиксели, где модель видит сходство с мусором; пена,
        водоросли и следы судов тоже бывают похожи. Согласие двух моделей — сигнал, не подтверждение. Сцена {p.dateEntry?.scene_id ?? '—'}.
      </p>
    </section>
  );
}

function short(s?: string) {
  if (!s) return '—';
  return s.split(/,|\(| global| hourly| 10 m/)[0].trim() || s;
}

export function plural(n: number, one: string, few: string, many: string) {
  const a = Math.abs(n) % 100,
    b = a % 10;
  if (a > 10 && a < 20) return many;
  if (b > 1 && b < 5) return few;
  if (b === 1) return one;
  return many;
}

function Export({ region, date, model, p }: { region: string; date: string; model: string; p: P }) {
  const [api, setApi] = useState(false);
  useEffect(() => {
    apiAvailable().then(setApi);
  }, []);
  const q = (layer: string) => `region=${encodeURIComponent(region)}&date=${date}&model=${model}&layer=${layer}`;
  const stem = (layer: string) => `${region}_${date}_${model}_${layer}`;
  const csvFallback = (layer: string) => {
    const rows: Record<string, unknown>[] =
      layer === 'detections'
        ? (p.detections?.features ?? []).map((f) => {
            const [lon, lat] = centroid(f);
            return { ...f.properties, lon: +lon.toFixed(6), lat: +lat.toFixed(6) };
          })
        : (p.zones?.zones ?? []).map((z) => ({ ...z, score_terms: undefined }));
    if (!rows.length) return p.onToast('Нет строк для выгрузки');
    const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))];
    const esc = (v: unknown) => {
      const s = v === null || v === undefined ? '' : String(v);
      return /[",;\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const csv = [cols.join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n');
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `${stem(layer)}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  };
  const rowsDef: [string, string][] = [
    ['detections', 'Находки'],
    ['zones', 'Зоны обследования'],
    ['h3', 'Сетка H3'],
  ];
  return (
    <section className="sec" data-testid="export">
      <div className="sec-h">
        <h3>Выгрузка</h3>
        <span className="aside">{api ? 'через API' : 'из файлов данных'}</span>
      </div>
      <table className="t">
        <tbody>
          {rowsDef.map(([k, l]) => (
            <tr key={k}>
              <td>{l}</td>
              <td className="r">
                <a
                  className="link"
                  href={api ? `/api/export?${q(k)}&format=geojson` : dataUrl(modelPath(region, date, model, k === 'zones' ? 'zones.json' : `${k}.geojson`))}
                  download={`${stem(k)}.geojson`}
                  data-testid={`export-${k}-geojson`}
                >
                  GeoJSON
                </a>
                <span className="faint"> · </span>
                {api ? (
                  <a className="link" href={`/api/export?${q(k)}&format=csv`} download={`${stem(k)}.csv`} data-testid={`export-${k}-csv`}>
                    CSV
                  </a>
                ) : k === 'h3' ? (
                  <span className="faint">CSV</span>
                ) : (
                  <button className="link" onClick={() => csvFallback(k)} data-testid={`export-${k}-csv`}>
                    CSV
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
