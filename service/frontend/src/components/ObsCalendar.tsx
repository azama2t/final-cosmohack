import { useEffect, useMemo, useState } from 'react';
import type { Region, TsRow } from '../types';
import { apiGet, type CalRow, type CalStatus } from '../lib/api';
import { isFlagged } from '../lib/data';
import { fmtDate, fmtNum, fmtPct } from '../lib/style';

interface Props {
  region: Region;
  model: string;
  date: string | null;
  timeseries: TsRow[] | null;
  onDate: (d: string) => void;
}

export const CAL_RU: Record<CalStatus, string> = {
  detected: 'обнаружено',
  clean: 'чисто',
  unreliable: 'ненадёжно',
  no_image: 'нет слоя модели',
};
const MONTHS = ['Я', 'Ф', 'М', 'А', 'М', 'И', 'И', 'А', 'С', 'О', 'Н', 'Д'];
const CLOUD_MAX = 0.5;

/** Fallback (no /api/calendar): status from manifest (cloud_frac, quality) and timeseries (n_detections). */
function localCalendar(region: Region, model: string, ts: TsRow[] | null): CalRow[] {
  return region.dates.map((d) => {
    const row = ts?.find((t) => t.date === d.date && t.model === model);
    const base = {
      date: d.date,
      model,
      scene_id: d.scene_id,
      cloud_frac: d.cloud_frac,
      quality_flags: isFlagged(d) ? ['haze'] : [],
      n_detections: row?.n_detections ?? 0,
      n_confirmed: d.n_confirmed?.[model],
    };
    if (!d.models.includes(model)) return { ...base, status: 'no_image' as const, reason: `нет слоя модели ${model} на эту дату` };
    const reasons: string[] = [];
    if ((d.cloud_frac ?? 0) > CLOUD_MAX) reasons.push(`облачность ${fmtPct(d.cloud_frac)} > 50 %`);
    if (isFlagged(d)) reasons.push('дымка/блик — находки могут быть завышены');
    if (reasons.length) return { ...base, status: 'unreliable' as const, reason: reasons.join('; ') };
    if (!row) return { ...base, status: 'unreliable' as const, reason: 'нет строки ряда для этой даты' };
    return row.n_detections > 0
      ? { ...base, status: 'detected' as const, reason: `${row.n_detections} находок модели ${model}` }
      : { ...base, status: 'clean' as const, reason: 'надёжное наблюдение, находок нет' };
  });
}

export default function ObsCalendar({ region, model, date, timeseries, onDate }: Props) {
  const [api, setApi] = useState<CalRow[] | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    setApi(undefined);
    apiGet<CalRow[]>('/api/calendar', { region: region.id, model }).then((r) => alive && setApi(Array.isArray(r) ? r : null));
    return () => {
      alive = false;
    };
  }, [region.id, model]);

  const rows = useMemo(
    () => (api ? api : api === null ? localCalendar(region, model, timeseries) : []),
    [api, region, model, timeseries],
  );
  const years = useMemo(() => [...new Set(rows.map((r) => r.date.slice(0, 4)))].sort(), [rows]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of rows) c[r.status] = (c[r.status] ?? 0) + 1;
    return c;
  }, [rows]);
  const cur = rows.find((r) => r.date === date);

  return (
    <section className="section" data-testid="obs-calendar">
      <div className="section-head">
        <h3>Календарь наблюдений</h3>
        <span className="muted small">{rows.length ? `${rows.length} ${rows.length === 1 ? 'снимок' : rows.length < 5 ? 'снимка' : 'снимков'}` : ''}</span>
      </div>
      {api === undefined ? (
        <div className="cal-ph" />
      ) : (
        <>
          <div className="cal-grid" role="grid" aria-label="Даты снимков по годам и месяцам">
            <span />
            {MONTHS.map((m, i) => (
              <span key={i} className="cal-m">
                {m}
              </span>
            ))}
            {years.map((y) => (
              <CalYear key={y} y={y} rows={rows.filter((r) => r.date.startsWith(y))} date={date} onDate={onDate} />
            ))}
          </div>
          <div className="cal-legend">
            {(['detected', 'clean', 'unreliable'] as CalStatus[]).map((s) => (
              <span key={s} className={counts[s] ? '' : 'zero'}>
                <i className={`cal-dot st-${s}`} /> {CAL_RU[s]} · {counts[s] ?? 0}
              </span>
            ))}
            {counts.no_image ? (
              <span>
                <i className="cal-dot st-no_image" /> {CAL_RU.no_image} · {counts.no_image}
              </span>
            ) : null}
          </div>
          <p className="cal-caption" data-testid="calendar-caption">
            <b>Только даты реальных снимков</b>, без интерполяции: пустой месяц — снимка нет. <b>Ненадёжно</b> — облака &gt; 50 %, дымка
            или блик, или наблюдаемой воды &lt; 30 %.
          </p>
          {cur && (
            <div className={`cal-cur st-${cur.status}`} data-testid="calendar-current">
              <b>{fmtDate(cur.date)}:</b> {CAL_RU[cur.status]} — {cur.reason}
              {typeof cur.n_confirmed === 'number' && cur.status !== 'no_image' ? `; уверенных ${cur.n_confirmed}` : ''}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function CalYear({ y, rows, date, onDate }: { y: string; rows: CalRow[]; date: string | null; onDate: (d: string) => void }) {
  return (
    <>
      <span className="cal-y">{y}</span>
      {Array.from({ length: 12 }, (_, m) => {
        const inM = rows.filter((r) => Number(r.date.slice(5, 7)) === m + 1);
        return (
          <span key={m} className="cal-cell">
            {inM.map((r) => (
              <button
                key={r.date}
                className={`cal-dot st-${r.status} ${r.date === date ? 'cur' : ''}`}
                onClick={() => r.status !== 'no_image' && onDate(r.date)}
                disabled={r.status === 'no_image'}
                data-testid={`cal-dot-${r.date}`}
                title={`${fmtDate(r.date)} — ${CAL_RU[r.status]}: ${r.reason}${r.cloud_frac !== null ? ` · облака ${fmtPct(r.cloud_frac)}` : ''}${
                  r.observed_frac_water != null ? ` · наблюдалось воды ${fmtPct(r.observed_frac_water)}` : ''
                }${r.status !== 'no_image' ? ` · пятен ${fmtNum(r.n_detections)}` : ''}`}
              />
            ))}
          </span>
        );
      })}
    </>
  );
}
