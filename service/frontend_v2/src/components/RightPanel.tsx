import { lazy, Suspense } from 'react';
import type { DateEntry, DetProps, DriftFile, Feature, FC, H3Props, LayerKey, Layers, Manifest, Region, TsRow, Zone, ZonesFile } from '../types';
import type { RoutePlan } from '../lib/route';
import type { IncidentLite } from '../App';
import { shortName } from '../lib/data';
import { fmtDate } from '../lib/style';
import { artifactOf } from '../lib/artifacts';
import { FindingsView, ZonesView, HistoryView, DriftView } from './RegionPanel';
import EvidencePanel from './EvidencePanel';
import ZonePanel from './ZonePanel';
import CheckPanel, { type CheckProps } from './CheckPanel';
import { lazyRetry } from '../lib/reload';

const PlacePanel = lazy(lazyRetry(() => import('./PlacePanel')));

export type RightMode = 'findings' | 'zones' | 'history' | 'drift' | 'det' | 'zone' | 'place' | 'check';

export interface RightProps {
  mode: RightMode | null;
  manifest: Manifest;
  region: Region | null;
  dateEntry: DateEntry | null;
  model: string;
  timeseries: TsRow[] | null;
  detections: FC<DetProps> | null;
  detectionsAll: FC<DetProps> | null;
  otherDet: FC<DetProps> | null;
  otherModel: string | null;
  nArtifacts: number;
  nConfirmed: number | null;
  zones: ZonesFile | null;
  h3: FC<H3Props> | null;
  rgbImg: HTMLImageElement | null;
  threshold: number | null;
  selected: Feature<DetProps> | null;
  zone: Zone | null;
  place: { h3: string; fromZone: boolean } | null;
  paths: Set<string>;
  contextOk: boolean;
  incidents: Map<string, IncidentLite> | null;
  onIncidentChanged: () => void;
  onDate: (d: string) => void;
  onZone: (z: Zone) => void;
  onDetection: (f: Feature<DetProps>) => void;
  onPlace: (h3: string, fromZone?: boolean) => void;
  onCloseDet: () => void;
  onView: (v: 'findings' | 'zones' | 'history' | 'drift') => void;
  onCloseZone: () => void;
  onClosePlace: () => void;
  onWorld: () => void;
  onToast: (t: string) => void;
  routeOn: boolean;
  route: RoutePlan | null;
  onRoute: (on: boolean) => void;
  onCompare: () => void;
  onCheck: () => void;
  onLayer: (k: LayerKey, on?: boolean) => void;
  drift: DriftFile | null;
  layers: Layers;
  flowAvail: string[];
  check: CheckProps;
}

const VIEW_RU: Record<string, string> = { findings: 'Находки', zones: 'Зоны', history: 'История', drift: 'Дрейф' };

export default function RightPanel(p: RightProps) {
  const r = p.region;
  const open = !!p.mode;
  let kicker: React.ReactNode = null;
  let title = '';
  let sub = '';
  let onBack: (() => void) | null = null;
  let backLabel = '';
  if (p.mode === 'check') {
    kicker = 'эксперимент';
    title = 'Проверка прогноза дрейфа';
  } else if (r && p.mode) {
    const name = shortName(r.name);
    if (p.mode in VIEW_RU) {
      title = name;
    } else if (p.mode === 'det' && p.selected) {
      const art = artifactOf(p.selected.properties);
      title = art ? 'Исключено: не находка' : 'Признаки плавающего материала';
      sub = `${name} · ${fmtDate(p.selected.properties.date)}`;
      onBack = p.onCloseDet;
      backLabel = p.zone ? `Зона №${p.zone.rank}` : 'Находки';
    } else if (p.mode === 'zone' && p.zone) {
      title = `Зона обследования №${p.zone.rank}`;
      onBack = p.onCloseZone;
      backLabel = 'Все зоны';
    } else if (p.mode === 'place' && p.place) {
      title = 'История места';
      onBack = p.onClosePlace;
      backLabel = p.place.fromZone && p.zone ? `Зона №${p.zone.rank}` : 'Назад';
    }
  }

  return (
    <aside className={`right ${open ? 'open' : ''}`} data-panel="right" data-testid="right-panel" aria-hidden={!open}>
      {open && (
        <div className="right-inner">
          <div className="rp-head">
            <div className="rp-titles">
              <div className="rp-kicker" style={onBack || kicker ? undefined : { display: 'none' }}>
                {onBack && (
                  <>
                    <button className="back" onClick={onBack} data-testid="panel-back">
                      ← {backLabel}
                    </button>
                    <span className="faint">/</span>
                  </>
                )}
                <span>{kicker}</span>
              </div>
              <div className="rp-title" data-testid="panel-title">
                {title}
              </div>
              {sub && <div className="rp-sub">{sub}</div>}
            </div>
            {(onBack || p.mode === 'check') && (
              <button className="icon-btn" aria-label="Закрыть" data-testid="panel-close" onClick={p.mode === 'check' ? p.check.onClose : onBack!}>
                ×
              </button>
            )}
          </div>
          <div className="rp-body" key={`${p.mode}-${p.selected?.properties.id ?? ''}-${p.zone?.rank ?? ''}-${p.place?.h3 ?? ''}`}>
            {p.mode === 'check' && <CheckPanel {...p.check} manifest={p.manifest} />}
            {p.mode === 'findings' && r && <FindingsView {...p} region={r} />}
            {p.mode === 'zones' && r && <ZonesView {...p} region={r} />}
            {p.mode === 'history' && r && <HistoryView {...p} region={r} />}
            {p.mode === 'drift' && r && <DriftView {...p} region={r} />}
            {p.mode === 'det' && r && p.selected && p.dateEntry && <EvidencePanel {...p} feature={p.selected} region={r} dateEntry={p.dateEntry} />}
            {p.mode === 'zone' && r && p.zone && p.dateEntry && (
              <ZonePanel
                manifest={p.manifest}
                region={r}
                dateEntry={p.dateEntry}
                model={p.model}
                zone={p.zone}
                zones={p.zones?.zones ?? [p.zone]}
                detections={p.detections}
                h3={p.h3}
                rgbImg={p.rgbImg}
                threshold={p.threshold}
                onPlace={(h) => p.onPlace(h, true)}
                onToast={p.onToast}
              />
            )}
            {p.mode === 'place' && r && p.place && (
              <Suspense fallback={<div className="sec note">Загрузка истории места…</div>}>
                <PlacePanel manifest={p.manifest} region={r} model={p.model} h3={p.place.h3} date={p.dateEntry?.date ?? null} onDate={p.onDate} />
              </Suspense>
            )}
          </div>
        </div>
      )}
    </aside>
  );
}
