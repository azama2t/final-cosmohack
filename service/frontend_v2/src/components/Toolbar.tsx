import { useEffect, useRef, useState } from 'react';
import type { Basemap, LayerKey, Layers, ModelInfo, Projection } from '../types';
import { modelTitle } from '../lib/style';
import { AGREE_NOTE } from '../lib/confirm';

interface Props {
  layers: Layers;
  onLayer: (k: LayerKey, on?: boolean) => void;
  basemap: Basemap;
  /** temporary offline fallback is active (the chosen basemap is kept) */
  offline: boolean;
  onBasemap: (b: Basemap) => void;
  projection: Projection;
  onProjection: (p: Projection) => void;
  hasRegion: boolean;
  hasDrift: boolean;
  flowAvail: string[];
  paths: Set<string>;
  contextOk: boolean;
  nArtifacts: number;
  models: Record<string, ModelInfo>;
  /** manifest.kind: «organizer» = organiser chips, otherwise live L2A scenes (model titles depend on it) */
  kind?: string;
  model: string;
  sceneModels: string[];
  onModel: (m: string) => void;
  confAvail: boolean;
  onlyConfirmed: boolean;
  onOnlyConfirmed: (v: boolean) => void;
  onCompare: () => void;
  compareOn: boolean;
  onTour: () => void;
  tourOn: boolean;
  onCopy: () => void;
}

const Ico = {
  layers: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
      <path d="M8 2 1.5 5.5 8 9l6.5-3.5L8 2Z" />
      <path d="m1.5 8.5 6.5 3.5 6.5-3.5" />
    </svg>
  ),
  compare: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
      <rect x="1.5" y="3" width="5.5" height="10" rx="1" />
      <rect x="9" y="3" width="5.5" height="10" rx="1" />
    </svg>
  ),
  link: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden>
      <path d="M6.5 9.5a3 3 0 0 0 4.2 0l2.3-2.3a3 3 0 0 0-4.2-4.2l-.9.9" />
      <path d="M9.5 6.5a3 3 0 0 0-4.2 0L3 8.8a3 3 0 0 0 4.2 4.2l.9-.9" />
    </svg>
  ),
  play: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
      <path d="M5 3.2v9.6L12.5 8 5 3.2Z" />
    </svg>
  ),
  stop: (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
      <rect x="4" y="4" width="8" height="8" rx="1" />
    </svg>
  ),
};

/** Right-top: ONE «Слои» menu (model, layers, basemap, projection) + icon buttons compare / link / tour. */
export default function Toolbar(p: Props) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    window.addEventListener('mousedown', off);
    window.addEventListener('keydown', esc);
    return () => {
      window.removeEventListener('mousedown', off);
      window.removeEventListener('keydown', esc);
    };
  }, [open]);
  (window as any).__layersMenu = setOpen;

  const L = p.layers;
  const row = (k: LayerKey, label: string, hint = '', disabled = false) => (
    <button className="menu-row" disabled={disabled} onClick={() => p.onLayer(k)} data-testid={`layer-${k}`} aria-pressed={L[k]} title={hint || undefined}>
      <span className={`check ${L[k] && !disabled ? 'on' : ''}`} aria-hidden />
      <span>{label}</span>
    </button>
  );
  const flowHint = (k: string) => (!p.hasRegion ? 'выберите район' : !p.paths.has('/api/flow') ? 'нет данных' : p.flowAvail.includes(k) ? '' : 'нет поля для даты');
  const needR = p.hasRegion ? '' : 'выберите район';

  return (
    <div className="toolbar" ref={box} data-testid="toolbar">
      <button className={`btn ${open ? 'on' : ''}`} onClick={() => setOpen((v) => !v)} data-testid="layers-menu" aria-expanded={open} title="Модель, слои, подложка, проекция">
        {Ico.layers}
        Слои
      </button>
      <button className={`btn icon ${p.compareOn ? 'on' : ''}`} onClick={p.onCompare} data-testid="compare-open" title="Сравнить два снимка" aria-label="Сравнить два снимка">
        {Ico.compare}
      </button>
      <button className="btn icon" onClick={p.onCopy} data-testid="copy-link" title="Скопировать ссылку на этот вид" aria-label="Скопировать ссылку">
        {Ico.link}
      </button>
      <button className={`btn icon ${p.tourOn ? 'on' : ''}`} onClick={p.onTour} data-testid="tour-start" title={p.tourOn ? 'Остановить демо-тур' : 'Демо-тур'} aria-label={p.tourOn ? 'Остановить демо-тур' : 'Демо-тур'}>
        {p.tourOn ? Ico.stop : Ico.play}
      </button>

      {open && (
        <div className="menu" data-testid="layers-panel" role="menu">
          <div className="menu-group">Модель</div>
          {Object.keys(p.models ?? {}).map((m) => {
            const t = modelTitle(m, { kind: p.kind, models: p.models });
            return (
              <button key={m} className="menu-row" disabled={!p.sceneModels.includes(m)} onClick={() => p.onModel(m)} data-testid={`model-${m}`} title={t.hint}>
                <span className={`radio ${p.model === m ? 'on' : ''}`} aria-hidden />
                <span>{m === 'lgbm' ? 'Наша модель (LGBM)' : m === 'mdd' ? 'MDD (открытая)' : t.label}</span>
              </button>
            );
          })}
          <button className="menu-row" disabled={!p.confAvail} onClick={() => p.onOnlyConfirmed(!p.onlyConfirmed)} data-testid="only-confirmed" title={AGREE_NOTE}>
            <span className={`check ${p.onlyConfirmed && p.confAvail ? 'on' : ''}`} aria-hidden />
            <span>Только где обе модели согласны</span>
          </button>
          <div className="menu-sep" />
          <div className="menu-group">Слои</div>
          {row('rgb', 'Снимок Sentinel-2', needR, !p.hasRegion)}
          {row('prob', 'Вероятность модели', needR, !p.hasRegion)}
          {row('h3', 'Индекс H3', needR, !p.hasRegion)}
          {row('h3_3d', 'Индекс H3 в 3D', needR, !p.hasRegion)}
          {row('artifacts', `Исключённые артефакты${p.hasRegion ? ` (${p.nArtifacts || 0})` : ''}`, needR, !p.hasRegion)}
          {row('osm', 'Объекты OSM', !p.hasRegion ? needR : !p.contextOk ? 'нет данных' : '', !p.hasRegion || !p.contextOk)}
          {row('currents', 'Частицы течений', flowHint('currents'), !!flowHint('currents'))}
          {row('wind', 'Частицы ветра', flowHint('wind'), !!flowHint('wind'))}
          <div className="menu-sep" />
          <div className="menu-group">Подложка{p.offline ? ' · сейчас офлайн' : ''}</div>
          {(
            [
              ['satellite', 'Спутник (Esri)'],
              ['dark', 'Тёмная (CARTO)'],
              ['none', 'Без подложки'],
            ] as [Basemap, string][]
          ).map(([b, l]) => (
            <button key={b} className="menu-row" onClick={() => p.onBasemap(b)} data-testid={`basemap-${b}`}>
              <span className={`radio ${p.basemap === b ? 'on' : ''}`} aria-hidden />
              <span>{l}</span>
            </button>
          ))}
          <div className="menu-sep" />
          <div className="menu-group">Проекция</div>
          {(
            [
              ['globe', 'Глобус'],
              ['mercator', 'Плоская карта'],
            ] as [Projection, string][]
          ).map(([pr, l]) => (
            <button key={pr} className="menu-row" onClick={() => p.onProjection(pr)} data-testid={`proj-${pr}`}>
              <span className={`radio ${p.projection === pr ? 'on' : ''}`} aria-hidden />
              <span>{l}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
