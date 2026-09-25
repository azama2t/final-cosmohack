import { useEffect, useState } from 'react';

/** Runs an async loader when deps change; returns null while loading or when key is null. */
export function useAsync<T>(fn: (() => Promise<T | null>) | null, deps: unknown[]): T | null {
  const [val, setVal] = useState<T | null>(null);
  useEffect(() => {
    let alive = true;
    setVal(null);
    if (!fn) return;
    fn()
      .then((v) => alive && setVal(v))
      .catch(() => alive && setVal(null));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return val;
}

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
