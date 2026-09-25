import { shortName } from '../lib/data';
import type { Manifest, Region } from '../types';

interface Props {
  manifest: Manifest;
  region: Region | null;
  onHome: () => void;
  onTour: () => void;
  tourRunning: boolean;
  onCopy: () => void;
  tab: 'map' | 'review';
  onTab: (t: 'map' | 'review') => void;
  reviewAvail: boolean;
}

export default function Header({ manifest, region, onHome, onTour, tourRunning, onCopy, tab, onTab, reviewAvail }: Props) {
  const kind = manifest.kind;
  return (
    <header className="header">
      <button className="brand" onClick={onHome} data-testid="home-button" title="Обзор всех районов">
        <span className="brand-mark" aria-hidden>
          <svg viewBox="0 0 32 32" width="28" height="28">
            <circle cx="16" cy="16" r="15" fill="#0e2038" stroke="#1f3a5c" />
            <path d="M5 19c3-2 5-2 8 0s5 2 8 0 5-2 6-1" stroke="#4d7fb3" strokeWidth="1.6" fill="none" />
            <circle cx="19" cy="12" r="3.4" fill="#ff6b4a" />
          </svg>
        </span>
        <span className="brand-text">
          <span className="brand-title">Морской мусор</span>
          <span className="brand-sub">плавающий макропластик по снимкам Sentinel-2</span>
        </span>
      </button>
      {reviewAvail && (
        <div className="tabs" role="tablist" aria-label="Режим">
          <button role="tab" aria-selected={tab === 'map'} className={tab === 'map' ? 'on' : ''} onClick={() => onTab('map')} data-testid="tab-map">
            Карта
          </button>
          <button
            role="tab"
            aria-selected={tab === 'review'}
            className={tab === 'review' ? 'on' : ''}
            onClick={() => onTab('review')}
            data-testid="tab-review"
            title="Проверка человеком: очередь сомнительных находок и дообучение"
          >
            Проверка
          </button>
        </div>
      )}
      <div className="crumbs">
        {tab === 'review' ? (
          <span className="crumb-cur">Проверка человеком</span>
        ) : region ? (
          <>
            <button className="crumb-link" onClick={onHome} data-testid="crumb-overview">
              Все районы
            </button>
            <span className="crumb-sep">/</span>
            <span className="crumb-cur" title={region.name}>{shortName(region.name)}</span>
          </>
        ) : (
          <span className="crumb-cur">Все районы · {manifest.regions.length}</span>
        )}
        {kind !== 'real' && (
          <span className={`badge ${kind === 'fixture' ? 'warn' : ''}`} data-testid="data-kind" title="Тип набора данных из manifest.kind">
            {kind === 'fixture' ? 'ФИКСТУРА · синтетические данные' : kind === 'demo' ? 'демо-набор' : kind}
          </span>
        )}
      </div>
      <div className="header-actions">
        <button className="btn ghost" onClick={onCopy} data-testid="copy-link">
          <Icon d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
          Скопировать ссылку
        </button>
        <button className={`btn ${tourRunning ? 'ghost' : 'accent'}`} onClick={onTour} data-testid="tour-button">
          {tourRunning ? <Icon d="M7 7h10v10H7z" /> : <Icon d="M8 5v14l11-7z" />}
          {tourRunning ? 'Стоп тура' : 'Демо-тур'}
        </button>
      </div>
    </header>
  );
}

export function Icon({ d, size = 16 }: { d: string; size?: number }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}
