import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource/inter/400.css';
import '@fontsource/inter/500.css';
import '@fontsource/inter/600.css';
import 'maplibre-gl/dist/maplibre-gl.css';
import './styles.css';
import App from './App';
import { reloadOnceAfterBuild } from './lib/reload';

// after a rebuild the old hashed chunks are gone → a lazy import in an open tab fails: reload once
window.addEventListener('vite:preloadError', (e) => {
  if (reloadOnceAfterBuild()) e.preventDefault();
});
window.addEventListener('load', () => setTimeout(() => sessionStorage.removeItem('reloaded-after-build'), 10000));

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
