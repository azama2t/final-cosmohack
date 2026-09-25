import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import '@fontsource/inter/700.css';
import './styles.css';
import App from './App';
import { reloadOnceAfterBuild } from './lib/reload';

// L27: after `npm run build` the old hashed chunks are deleted → a lazy import in an open tab fails.
// Vite fires `vite:preloadError`; reload the page ONCE (state lives in the URL, the same view comes back).
window.addEventListener('vite:preloadError', (e) => {
  // only swallow the error when we really reload; otherwise let the import fail normally (no reload loop)
  if (reloadOnceAfterBuild()) e.preventDefault();
});
// the flag is cleared after a successful start, so a later rebuild can reload again
window.addEventListener('load', () => setTimeout(() => sessionStorage.removeItem('reloaded-after-build'), 10000));

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
