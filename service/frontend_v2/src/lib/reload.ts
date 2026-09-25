/** Reload the page once when hashed chunks disappeared after a rebuild (guarded against reload loops). */
export function reloadOnceAfterBuild(): boolean {
  try {
    if (sessionStorage.getItem('reloaded-after-build')) return false;
    sessionStorage.setItem('reloaded-after-build', '1');
  } catch {
    return false;
  }
  location.reload();
  return true;
}

/** Wraps a dynamic import: on failure → one reload (the promise then never settles), else rethrow. */
export function lazyRetry<T>(load: () => Promise<T>): () => Promise<T> {
  return () =>
    load().catch((err) => {
      if (reloadOnceAfterBuild()) return new Promise<T>(() => {});
      throw err;
    });
}
