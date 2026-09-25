// «Экспорт»: files of what is filtered now, a link that repeats the view, saved queries (contract section 8).
import { useEffect, useState } from 'react';
import { ApiErr, API_BASE, apiUrl, get } from './api';
import type { Filters } from './map';
import type { BBox } from './geo';
import { fmtDate } from './geo';

/** contract query object (section 8) */
export interface ApiQuery {
  bbox: number[] | null;
  date_from: string | null;
  date_to: string | null;
  statuses: string[];
  sources: string[];
  profiles: string[];
  layers: string[];
  scene_id: string | null;
}
interface Saved {
  query_id: string;
  name: string;
  created_at: string;
  query: ApiQuery;
}

export function toQuery(f: Filters, bbox: BBox | null): ApiQuery {
  const layers: string[] = [];
  if (f.obs) layers.push('observations');
  if (f.zones) layers.push('zones');
  return {
    bbox: bbox ? bbox.map((x) => Math.round(x * 1e4) / 1e4) : null,
    date_from: f.from,
    date_to: f.to,
    statuses: [],
    sources: f.sources ?? [],
    profiles: [],
    layers,
    scene_id: null,
  };
}
export function fromQuery(q: Partial<ApiQuery>, base: Filters): Filters {
  const layers = Array.isArray(q.layers) ? q.layers : null;
  return {
    ...base,
    from: q.date_from ?? null,
    to: q.date_to ?? null,
    sources: Array.isArray(q.sources) && q.sources.length ? q.sources : null,
    obs: layers ? layers.includes('observations') : base.obs,
    zones: layers ? layers.includes('zones') : base.zones,
  };
}

const b64u = (s: string) => btoa(unescape(encodeURIComponent(s))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const unb64u = (s: string) => decodeURIComponent(escape(atob(s.replace(/-/g, '+').replace(/_/g, '/'))));

/** ?q=<base64url JSON> — a link repeats the query without the backend */
export function queryFromUrl(): Partial<ApiQuery> | null {
  const q = new URLSearchParams(location.search).get('q');
  if (!q) return null;
  try {
    const j = JSON.parse(unb64u(q));
    return j && typeof j === 'object' ? j : null;
  } catch {
    return null;
  }
}
export function linkFor(q: ApiQuery): string {
  const u = new URL(location.href);
  u.search = '';
  u.hash = '';
  u.searchParams.set('q', b64u(JSON.stringify(q)));
  if (API_BASE) u.searchParams.set('api', API_BASE);
  return u.toString();
}

async function send(method: 'POST' | 'DELETE', path: string, body?: unknown): Promise<any> {
  let r: Response;
  try {
    r = await fetch(`${API_BASE}${path}`, { method, headers: body ? { 'Content-Type': 'application/json' } : undefined, body: body ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiErr('UNAVAILABLE', 'Сервис недоступен');
  }
  if (r.status === 204) return null;
  const j = await r.json().catch(() => null);
  if (!r.ok) throw new ApiErr(j?.error?.code ?? 'HTTP', j?.error?.message ?? `HTTP ${r.status}`, r.status);
  return j;
}

export default function ExportSection(p: { filters: Filters; viewBBox: () => BBox | null; onApply: (q: Partial<ApiQuery>) => void; oil?: boolean }) {
  const f = p.filters;
  const params = { date_from: f.from, date_to: f.to, source: f.sources ?? undefined };
  const items: { layer: string; label: string }[] = [
    { layer: 'observations', label: 'Полевые измерения' },
    { layer: 'zones', label: 'Полосы обследования' },
    { layer: 'detections', label: 'Подозрительные пиксели' },
  ];
  const [saved, setSaved] = useState<Saved[] | null>(null);
  const [name, setName] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);

  const reload = () =>
    get<{ queries: Saved[] }>('/api/v3/queries')
      .then((j) => setSaved(j.queries ?? []))
      .catch((e) => {
        setSaved([]);
        setMsg(e?.message ?? 'Сервис недоступен');
      });
  useEffect(() => {
    reload();
  }, []);

  const save = async () => {
    const n = name.trim() || `Запрос ${new Date().toLocaleString('ru-RU')}`;
    try {
      await send('POST', '/api/v3/queries', { name: n, query: toQuery(f, p.viewBBox()) });
      setName('');
      setMsg('Сохранено');
      reload();
    } catch (e: any) {
      setMsg(e?.message ?? 'Не удалось сохранить');
    }
  };
  const del = async (id: string) => {
    try {
      await send('DELETE', `/api/v3/queries/${encodeURIComponent(id)}`);
      reload();
    } catch (e: any) {
      setMsg(e?.message ?? 'Не удалось удалить');
    }
  };
  const copyLink = async () => {
    const l = linkFor(toQuery(f, p.viewBBox()));
    setLink(l);
    try {
      await navigator.clipboard.writeText(l);
      setMsg('Ссылка скопирована');
    } catch {
      setMsg(null);
    }
  };

  return (
    <div className="export">
      <div className="sub">Файлы · текущие фильтры</div>
      {items.map((it) => (
        <div key={it.layer} className="exp-row">
          <span>{it.label}</span>
          <a className="btn sm" href={apiUrl('/api/v3/export', { layer: it.layer, format: 'geojson', ...params })} download>
            GeoJSON
          </a>
          <a className="btn sm" href={apiUrl('/api/v3/export', { layer: it.layer, format: 'csv', ...params })} download>
            CSV
          </a>
        </div>
      ))}
      {p.oil && (
        <div className="exp-row" title="Отдельный экспериментальный класс: площадь пятна, не объём/масса">
          <span>
            Нефтяное пятно <i className="exp">эксперимент</i>
          </span>
          <a className="btn sm" href={apiUrl('/api/v3/oil/export', { format: 'geojson', date_from: f.from, date_to: f.to })} download>
            GeoJSON
          </a>
          <a className="btn sm" href={apiUrl('/api/v3/oil/export', { format: 'csv', date_from: f.from, date_to: f.to })} download>
            CSV
          </a>
        </div>
      )}
      <div className="sub">Ссылка</div>
      <button className="btn" onClick={copyLink} data-testid="copy-link">
        Скопировать ссылку на этот вид
      </button>
      {link && <input className="link" readOnly value={link} onFocus={(e) => e.currentTarget.select()} />}
      <div className="sub">Сохранённые запросы</div>
      <div className="row">
        <input className="name" placeholder="Название" value={name} onChange={(e) => setName(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && save()} />
        <button className="btn" onClick={save} data-testid="save-query">
          Сохранить
        </button>
      </div>
      {msg && <div className="muted small">{msg}</div>}
      {saved?.map((s) => (
        <div key={s.query_id} className="q-row">
          <button className="q-name" title="Применить: фильтры и область" onClick={() => p.onApply(s.query)}>
            {s.name}
            <span className="muted"> · {fmtDate(s.created_at)}</span>
          </button>
          <a className="btn sm" href={apiUrl('/api/v3/export', { layer: 'observations', format: 'csv', query_id: s.query_id })} download title="Измерения этого запроса, CSV">
            CSV
          </a>
          <button className="btn sm ghost" title="Удалить" onClick={() => del(s.query_id)}>
            ×
          </button>
        </div>
      ))}
      {saved && !saved.length && <div className="muted small">Пока нет</div>}
    </div>
  );
}
