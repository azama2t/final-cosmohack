import { useEffect, useRef, useState } from 'react';
import type { Basemap, LayerKey, Layers, ModelInfo, Projection } from '../types';
import { modelTitle } from '../lib/style';
import { AGREE_NOTE } from '../lib/confirm';

interface Props {
  layers: Layers;
  onLayer: (k: LayerKey, on?: boolean) => void;
  basemap: Basemap;
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

/** Right-top: ONE menu with the technical settings (model, basemap, projection, H3, extra layers) + share / compare / tour. */
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
  const needR = !p.hasRegion ? 'выберите район' : '';
  const row = (k: LayerKey, label: string, hint = '', disabled = false) => (
    <button className="menu-row" disabled={disabled} onClick={() => p.onLayer(k)} data-testid={`layer-${k}`} aria-pressed={L[k]}>
      <span className={`check ${L[k] && !disabled ? 'on' : ''}`} aria-hidden />
      <span>{label}</span>
      {hint && <span className="mr-hint">{hint}</span>}
    </button>
  );
  const flowHint = (k: string) => (!p.hasRegion ? 'выберите район' : !p.paths.has('/api/flow') ? 'нет данных' : p.flowAvail.includes(k) ? '' : 'нет поля для даты');

  return (
    <div className="toolbar" ref={box} data-testid="toolbar">
      <button className={`btn ${open ? 'on' : ''}`} onClick={() => setOpen((v) => !v)} data-testid="layers-menu" aria-expanded={open}>
        Настройки карты
      </button>
      <button className={`btn ${p.compareOn ? 'on' : ''}`} onClick={p.onCompare} data-testid="compare-open">
        Сравнить
      </button>
      <button className="btn" onClick={p.onCopy} data-testid="copy-link" title="Скопировать ссылку на текущий вид">
        Ссылка
      </button>
      <button className={`btn ${p.tourOn ? 'on' : ''}`} onClick={p.onTour} data-testid="tour-start">
        {p.tourOn ? 'Стоп' : 'Демо-тур'}
      </button>

      {open && (
        <div className="menu" data-testid="layers-panel" role="menu">
          <div className="menu-group">Модель</div>
          {Object.keys(p.models ?? {}).map((m) => {
            const t = modelTitle(m, { kind: p.kind, models: p.models });
            return (
              <div key={m}>
                <button className="menu-row" disabled={!p.sceneModels.includes(m)} onClick={() => p.onModel(m)} data-testid={`model-${m}`} title={t.hint}>
                  <span className={`radio ${p.model === m ? 'on' : ''}`} aria-hidden />
                  <span>{t.label}</span>
                </button>
                {t.variant && (
                  <div className="menu-group" style={{ paddingTop: 0 }} data-testid={`model-${m}-hint`}>
                    {t.hint}
                  </div>
                )}
              </div>
            );
          })}
          <button className="menu-row" disabled={!p.confAvail} onClick={() => p.onOnlyConfirmed(!p.onlyConfirmed)} data-testid="only-confirmed">
            <span className={`check ${p.onlyConfirmed && p.confAvail ? 'on' : ''}`} aria-hidden />
            <span>Только с согласием второй модели</span>
          </button>
          <div className="menu-group" style={{ paddingTop: 0 }}>
            {AGREE_NOTE}
          </div>
          <div className="menu-sep" />
          <div className="menu-group">Слои</div>
          {row('rgb', 'Снимок Sentinel-2', needR, !p.hasRegion)}
          {row('prob', 'Вероятность модели', needR, !p.hasRegion)}
          {row('h3', 'Индекс по сетке H3', needR, !p.hasRegion)}
          {row('h3_3d', 'H3 в объёме (3D)', needR, !p.hasRegion)}
          {row('artifacts', 'Исключённые артефакты', p.hasRegion ? String(p.nArtifacts || '0') : needR, !p.hasRegion)}
          {row('osm', 'Объекты OSM', !p.hasRegion ? needR : !p.contextOk ? 'нет данных' : '', !p.hasRegion || !p.contextOk)}
          {row('currents', 'Частицы течений', flowHint('currents'), !!flowHint('currents'))}
          {row('wind', 'Частицы ветра', flowHint('wind'), !!flowHint('wind'))}
          <div className="menu-sep" />
          <div className="menu-group">Подложка</div>
          {(
            [
              ['dark', 'Тёмная (CARTO)'],
              ['satellite', 'Спутник (Esri)'],
              ['none', 'Без сети: только снимки и берег'],
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
