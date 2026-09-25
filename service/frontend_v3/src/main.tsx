import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource/inter/400.css';
import '@fontsource/inter/600.css';
import 'maplibre-gl/dist/maplibre-gl.css';
import './styles.css';
import App from './App';

// after a rebuild the old hashed chunks are gone → reload once instead of a white screen
window.addEventListener('vite:preloadError', (e) => {
  try {
    if (sessionStorage.getItem('mp3-reloaded')) return;
    sessionStorage.setItem('mp3-reloaded', '1');
  } catch {
    return;
  }
  e.preventDefault();
  location.reload();
});
window.addEventListener('load', () => setTimeout(() => {
  try {
    sessionStorage.removeItem('mp3-reloaded');
  } catch {
    /* ignore */
  }
}, 10000));

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
