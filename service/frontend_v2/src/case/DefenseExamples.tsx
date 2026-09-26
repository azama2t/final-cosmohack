// jury_s44 п.10: the 5 defence examples of the task (GET /api/v3/defense_examples, CONTRACTS_V3 «приёмка 16:03 п.4»):
// верно / пропуск / судно / пена / анализ невозможен — crop, quality mask, result, basis, status; a click opens the snapshot.
import { useEffect, useState } from 'react';
import { API_BASE, get } from './api3';
import { dateRu } from './fmt';

interface Ex {
  kind: string;
  label: string;
  zone_id: string | null;
  scene_key: string;
  datetime: string | null;
  title: string;
  image: { crop_url?: string | null; rgb_url?: string | null; quality_url?: string | null; crop_note?: string | null };
  verdict: string;
  basis: string;
  status_label: string;
}

const SHORT: Record<string, string> = {
  success: 'Верно',
  miss: 'Пропуск',
  false_alarm: 'Судно',
  background_error: 'Пена',
  no_analysis: 'Анализ невозможен',
};

export default function DefenseExamples({ onOpen }: { onOpen: (e: { zone_id: string | null; scene_key: string }) => void }) {
  const [d, setD] = useState<{ examples: Ex[]; note?: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    const ac = new AbortController();
    get<any>('/api/v3/defense_examples', {}, ac.signal)
      .then(setD)
      .catch((e) => {
        if (!ac.signal.aborted) setErr(String(e?.message ?? e));
      });
    return () => ac.abort();
  }, []);
  if (err) return <div className="c-err c-pad" data-testid="defense-error">Примеры недоступны: {err}</div>;
  if (!d) return <div className="note c-pad">Загрузка примеров…</div>;
  return (
    <section className="c-def" data-testid="defense-examples">
      <div className="c-def-h">Примеры: верно / пропуск / судно / пена / анализ невозможен</div>
      {d.note && <div className="c-line faint c-def-note">{d.note}</div>}
      <div className="c-def-grid">
        {d.examples.map((e) => {
          const img = e.image.crop_url || e.image.rgb_url;
          return (
            <button key={e.kind} className="c-def-card" onClick={() => onOpen(e)} data-testid={`defense-${e.kind}`} title="Открыть снимок на карте">
              <div className="c-def-k">
                <b>{SHORT[e.kind] ?? e.kind}</b> · {e.label}
              </div>
              <div className="c-def-imgs">
                {img && <img src={API_BASE + img} alt="Вырезка снимка" loading="lazy" />}
                {e.image.quality_url && <img src={API_BASE + e.image.quality_url} alt="Маска качества" loading="lazy" className="q" />}
              </div>
              <div className="c-def-t">
                {e.title} · {dateRu(e.datetime)}
              </div>
              <div className="c-def-r">
                <span className="faint">Результат:</span> {e.verdict}
              </div>
              <div className="c-def-r">
                <span className="faint">Основание:</span> {e.basis}
              </div>
              <div className="c-def-r">
                <span className="faint">Статус:</span> {e.status_label}
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
