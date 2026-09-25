import { useEffect, useMemo, useState } from 'react';
import type { Region, TsRow } from '../types';
import { apiGet, type CalRow, type CalStatus } from '../lib/api';
import { isFlagged } from '../lib/data';
import { fmtDate, fmtNum, fmtPct, isUnknownDate } from '../lib/style';

export const CAL_RU: Record<CalStatus, string> = {
  detected: 'обнаружено',
  clean: 'чисто',
  unreliable: 'ненадёжно',
  no_image: 'нет слоя модели',
};
const MONTHS = ['Я', 'Ф', 'М', 'А', 'М', 'И', 'И', 'А', 'С', 'О', 'Н', 'Д'];

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
    if (!d.models.includes(model)) return { ...base, status: 'no_image' as const, reason: `нет слоя модели ${model}` };
    const reasons: string[] = [];
    if ((d.cloud_frac ?? 0) > 0.5) reasons.push(`облачность ${fmtPct(d.cloud_frac)} > 50 %`);
    if (isFlagged(d)) reasons.push('дымка/блик — находки могут быть завышены');
    if (reasons.length) return { ...base, status: 'unreliable' as const, reason: reasons.join('; ') };
    return (row?.n_detections ?? 0) > 0
      ? { ...base, status: 'detected' as const, reason: `${row!.n_detections} находок` }
      : { ...base, status: 'clean' as const, reason: 'надёжное наблюдение, находок нет' };
  });
}

export default function Calendar({ region, model, date, timeseries, onDate }: { region: Region; model: string; date: string | null; timeseries: TsRow[] | null; onDate: (d: string) => void }) {
  const [api, setApi] = useState<CalRow[] | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    setApi(undefined);
    apiGet<CalRow[]>('/api/calendar', { region: region.id, model }).then((r) => alive && setApi(Array.isArray(r) ? r : null));
    return () => {
      alive = false;
    };
  }, [region.id, model]);
  const rows = useMemo(() => (api ? api : api === null ? localCalendar(region, model, timeseries) : []), [api, region, model, timeseries]);
  const years = useMemo(() => [...new Set(rows.map((r) => r.date.slice(0, 4)))].sort(), [rows]);
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of rows) c[r.status] = (c[r.status] ?? 0) + 1;
    return c;
  }, [rows]);
  const cur = rows.find((r) => r.date === date);

  return (
    <section className="sec" data-testid="obs-calendar">
      <div className="sec-h">
        <h3>Календарь наблюдений</h3>
        <span className="aside">{rows.length} реальных снимков</span>
      </div>
      <div className="cal" role="grid" aria-label="Даты снимков по годам и месяцам">
        <span />
        {MONTHS.map((m, i) => (
          <span key={i} className="cm">
            {m}
          </span>
        ))}
        {years.map((y) => (
          <Year key={y} y={y} rows={rows.filter((r) => r.date.startsWith(y))} date={date} onDate={onDate} />
        ))}
      </div>
      <div className="cal-legend">
        {(['detected', 'clean', 'unreliable'] as CalStatus[]).map((s) => (
          <span key={s}>
            <i className={`cal-dot st-${s}`} /> {CAL_RU[s]} {counts[s] ?? 0}
          </span>
        ))}
      </div>
      {cur && (
        <p className="note" style={{ marginTop: 8 }} data-testid="calendar-current">
          <b>{fmtDate(cur.date)}:</b> {CAL_RU[cur.status]} — {cur.reason}
        </p>
      )}
      <p className="note" style={{ marginTop: 8 }} data-testid="calendar-caption">
        Только даты реальных снимков, без интерполяции. Ненадёжно — облака &gt; 50 %, дымка или блик, или наблюдаемой воды &lt; 30 %.
      </p>
    </section>
  );
}

function Year({ y, rows, date, onDate }: { y: string; rows: CalRow[]; date: string | null; onDate: (d: string) => void }) {
  return (
    <>
      <span>{rows.length && rows.every((r) => isUnknownDate(r.date)) ? 'год ?' : y}</span>
      {Array.from({ length: 12 }, (_, m) => (
        <span key={m} className="cell">
          {rows
            .filter((r) => Number(r.date.slice(5, 7)) === m + 1)
            .map((r) => (
              <button
                key={r.date}
                className={`cal-dot st-${r.status} ${r.date === date ? 'cur' : ''}`}
                onClick={() => r.status !== 'no_image' && onDate(r.date)}
                disabled={r.status === 'no_image'}
                data-testid={`cal-dot-${r.date}`}
                title={`${fmtDate(r.date)} — ${CAL_RU[r.status]}: ${r.reason}${r.cloud_frac !== null ? ` · облака ${fmtPct(r.cloud_frac)}` : ''}${
                  r.status !== 'no_image' ? ` · пятен ${fmtNum(r.n_detections)}` : ''
                }`}
              />
            ))}
        </span>
      ))}
    </>
  );
}
