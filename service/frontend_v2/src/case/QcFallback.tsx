// §48 (L142, Egor's screenshot 04): «Проверка качества» must not turn into a broken page when the API does not answer.
// The last successful answer of each quality endpoint is kept in localStorage (only what the API returned, with the time);
// on failure one compact line is shown — «Сервис проверки качества временно недоступен · последняя успешная проверка <время> ·
// Повторить» — and the saved answer below it. Nothing is invented: no saved answer → only the line.
import { useCallback, useEffect, useRef, useState } from 'react';
import { get } from './api3';

const KEY = (path: string) => `qc-cache:${path}`;

export interface Cached<T> {
  t: number;
  data: T;
}

export function readCache<T>(path: string): Cached<T> | null {
  try {
    const s = localStorage.getItem(KEY(path));
    if (!s) return null;
    const c = JSON.parse(s);
    return c && typeof c.t === 'number' && c.data != null ? (c as Cached<T>) : null;
  } catch {
    return null;
  }
}

export function writeCache<T>(path: string, data: T) {
  try {
    localStorage.setItem(KEY(path), JSON.stringify({ t: Date.now(), data }));
  } catch {
    /* quota / private mode: no cache, still works */
  }
}

export const timeRu = (t: number) => {
  const d = new Date(t);
  const today = new Date().toDateString() === d.toDateString();
  const hm = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  return today ? `сегодня в ${hm}` : `${d.toLocaleDateString('ru-RU')} в ${hm}`;
};

/** GET with the saved last success. With `parent` the first answer comes from the caller (CaseApp loads /metrics itself);
 *  «Повторить» always asks the API again here. Returns data (fresh, else saved), lastOk (time of the saved one), err, loading, retry. */
export function useCachedGet<T>(path: string, parent?: { data: T | null; err: string | null }) {
  const [loc, setLoc] = useState<{ data: T | null; err: string | null; loading: boolean }>({ data: null, err: null, loading: false });
  const ac = useRef<AbortController | null>(null);
  const load = useCallback(() => {
    ac.current?.abort();
    const c = (ac.current = new AbortController());
    setLoc((v) => ({ ...v, err: null, loading: true }));
    get<T>(path, {}, c.signal)
      .then((d) => {
        writeCache(path, d);
        setLoc({ data: d, err: null, loading: false });
      })
      .catch((e) => {
        if (!c.signal.aborted) setLoc((v) => ({ ...v, err: String(e?.message ?? e), loading: false }));
      });
  }, [path]);
  useEffect(() => {
    if (!parent) load();
    return () => ac.current?.abort();
  }, [path]); // eslint-disable-line react-hooks/exhaustive-deps
  // one «Повторить» for the whole panel: every quality request that failed asks again
  const errRef = useRef(false);
  useEffect(() => {
    const on = () => errRef.current && load();
    window.addEventListener('qc-retry', on);
    return () => window.removeEventListener('qc-retry', on);
  }, [load]);
  const pData = parent?.data ?? null;
  useEffect(() => {
    if (pData) writeCache(path, pData);
  }, [pData, path]);
  const data = loc.data ?? pData;
  const err = data ? null : loc.err ?? parent?.err ?? null;
  errRef.current = !!err;
  const saved = err ? readCache<T>(path) : null;
  const loading = loc.loading || (!data && !err && (!parent || !parent.err));
  return { data: data ?? saved?.data ?? null, lastOk: saved?.t ?? null, err, loading, retry: () => window.dispatchEvent(new Event('qc-retry')) };
}

/** the compact line instead of a red page */
export function QcOffline({ lastOk, loading, onRetry, what, testid }: { lastOk: number | null; loading: boolean; onRetry: () => void; what?: string; testid?: string }) {
  return (
    <div className="c-offline" role="status" data-what={what} data-testid={testid ?? 'qc-offline'}>
      <span className="c-offline-dot" aria-hidden />
      <span>
        Сервис проверки качества временно недоступен ·{' '}
        {lastOk ? <>последняя успешная проверка {timeRu(lastOk)} — показан её результат</> : <>сохранённого результата в этом браузере нет</>} ·{' '}
      </span>
      <button className="c-offline-btn" onClick={onRetry} disabled={loading} data-testid={`${testid ?? 'qc-offline'}-retry`}>
        {loading ? 'Проверяем…' : 'Повторить'}
      </button>
      <span className="c-offline-loc">Карта, снимки и карточки зон работают; маска качества и исключённые фоны выбранной зоны — в её карточке.</span>
    </div>
  );
}
