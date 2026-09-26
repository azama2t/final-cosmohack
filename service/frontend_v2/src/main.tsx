import { lazy, StrictMode, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import 'maplibre-gl/dist/maplibre-gl.css';
import './styles.css';
import { reloadOnceAfterBuild } from './lib/reload';
import MobileShell from './mobile/MobileShell'; // L140 §45: mobile layer (≤ 820 px), CSS + chrome only

// L66: «Кейс» (API v3) is the default mode; the earlier «живые снимки» scenario stays at ?mode=live.
const MODE = new URLSearchParams(location.search).get('mode');
const LIVE = MODE === 'live';
const PHOTO = MODE === 'photo'; // L109: счётчик предметов по фото (отдельный модуль, src/photo/)
const App = lazy(() => import('./App'));
const CaseApp = lazy(() => import('./case/CaseApp'));
const PhotoApp = lazy(() => import('./photo/PhotoApp'));

// after a rebuild the old hashed chunks are gone → a lazy import in an open tab fails: reload once
window.addEventListener('vite:preloadError', (e) => {
  if (reloadOnceAfterBuild()) e.preventDefault();
});
window.addEventListener('load', () => setTimeout(() => sessionStorage.removeItem('reloaded-after-build'), 10000));

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Suspense fallback={<div className="boot">Загрузка…</div>}>{PHOTO ? <PhotoApp /> : LIVE ? <App /> : <CaseApp />}</Suspense>
    {!PHOTO && !LIVE && <MobileShell />}
  </StrictMode>,
);
