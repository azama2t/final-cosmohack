// jury_s44 п.10: the 5 defence examples of the task (GET /api/v3/defense_examples, CONTRACTS_V3 «приёмка 16:03 п.4»):
// верно / пропуск / судно / пена / анализ невозможен — crop, quality mask, result, basis, status; a click opens the snapshot.
import { API_BASE } from './api3';
import { QcOffline, useCachedGet } from './QcFallback';
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
  status_note?: string | null;
}

const SHORT: Record<string, string> = {
  success: 'Верно',
  miss: 'Пропуск',
  false_alarm: 'Судно',
  background_error: 'Пена',
  no_analysis: 'Анализ невозможен',
};

export default function DefenseExamples({ onOpen }: { onOpen: (e: { zone_id: string | null; scene_key: string }) => void }) {
  // §48 (L142): last successful answer kept; unavailable → one compact line + the saved examples
  const q = useCachedGet<{ examples: Ex[]; note?: string }>('/api/v3/defense_examples');
  const d = q.data;
  const off = q.err ? <QcOffline lastOk={q.lastOk} loading={q.loading} onRetry={q.retry} what="примеры" testid="defense-error" /> : null;
  if (!d) return off ?? <div className="note c-pad">Загрузка примеров…</div>;
  return (
    <section className="c-def" data-testid="defense-examples">
      {off}
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
              {e.image.quality_url && (
                <div className="c-def-cap faint">слева — снимок и пиксели детектора; справа — маска качества: тёмное — годная вода, белое — облака, жёлтое — блик, серое — суша</div>
              )}
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
                {e.status_note && (
                  <div className="faint c-def-sn" data-testid={`defense-note-${e.kind}`}>
                    {e.status_note}
                  </div>
                )}
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}
