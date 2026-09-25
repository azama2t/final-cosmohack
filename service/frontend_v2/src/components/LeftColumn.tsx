import { useEffect, useMemo, useRef } from 'react';
import type { Manifest } from '../types';
import { KIND_CLASS, feedDate, type FeedEvent } from '../lib/feed';
import { rankRegions, regionReliability, shortName, summaryDate } from '../lib/data';
import { fmtArea, fmtNum, fmtPermille } from '../lib/style';
import { plural } from './RegionPanel';

interface Props {
  manifest: Manifest;
  regionId: string | null;
  feed: { items: FeedEvent[]; source: 'api' | 'client' } | null;
  feedSel: string | null;
  tab: 'regions' | 'feed';
  onTab: (t: 'regions' | 'feed') => void;
  onFeed: (e: FeedEvent) => void;
  onRegion: (id: string) => void;
  collapsed: boolean;
  onCollapse: () => void;
  onWorld: () => void;
}

const SUB: Record<string, string> = {
  new: 'не проверено',
  zone: 'зона обследования',
  excluded: 'исключено системой, не входит в индекс',
  under_review: 'на проверке',
  confirmed: 'решение оператора',
  false_alarm: 'решение оператора',
  resolved: 'закрыто',
  reopened: 'возвращено на проверку',
  haze: 'качество снимка',
};

export default function LeftColumn(p: Props) {
  const body = useRef<HTMLDivElement>(null);
  const { ok, bad } = useMemo(() => rankRegions(p.manifest.regions), [p.manifest]);
  const nDates = p.manifest.regions.reduce((a, r) => a + r.dates.length, 0);

  // keep the selected feed item in view (tour, clicks on the map)
  useEffect(() => {
    if (!p.feedSel || p.tab !== 'feed') return;
    const el = body.current?.querySelector(`[data-key="${CSS.escape(p.feedSel)}"]`) as HTMLElement | null;
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [p.feedSel, p.tab]);

  return (
    <aside className={`left ${p.collapsed ? 'collapsed' : ''}`} data-panel="left" data-testid="left-panel">
      <div className="brand">
        <div>
          <div className="brand-name">
            <span className="brand-dot" aria-hidden />
            Морской мусор
          </div>
          <div className="brand-sub">
            {p.manifest.regions.length} районов · {nDates} снимков{p.manifest.kind && !['real', 'demo', 'fixture'].includes(p.manifest.kind) ? ` · ${p.manifest.kind}` : ''}
          </div>
        </div>
      </div>
      <div style={{ padding: '12px 24px 0' }}>
        <button className={`btn sm ${p.regionId ? '' : 'on'}`} style={{ width: '100%', justifyContent: 'center' }} onClick={p.onWorld} data-testid="world">
          Обзор мира
        </button>
      </div>
      <div className="tabs" role="tablist">
        <button className={`tab ${p.tab === 'regions' ? 'on' : ''}`} onClick={() => p.onTab('regions')} data-testid="tab-regions">
          Районы
        </button>
        <button className={`tab ${p.tab === 'feed' ? 'on' : ''}`} onClick={() => p.onTab('feed')} data-testid="tab-feed">
          Лента находок
        </button>
      </div>
      <div className="left-body" ref={body}>
        {p.tab === 'feed' ? (
          <div data-testid="feed">
            <div className="feed-head">
              <span>{p.feed ? `${p.feed.items.length} событий · по дате снимка` : 'Загрузка…'}</span>
              <span title={p.feed?.source === 'api' ? 'из /api/feed' : 'построено в браузере из данных'}>{p.feed?.source === 'api' ? '' : 'из файлов'}</span>
            </div>
            {p.feed?.items.map((e, i) => (
              <button
                key={e.key}
                data-key={e.key}
                className={`feed-item ${KIND_CLASS[e.kind] ?? ''} ${p.feedSel === e.key ? 'on' : ''}`}
                style={{ animationDelay: `${Math.min(i, 12) * 18}ms` }}
                onClick={() => p.onFeed(e)}
                data-testid={`feed-item-${i}`}
                data-region={e.region}
                data-kind={e.kind}
              >
                <span className="fi-mark" aria-hidden />
                <div className="fi-title">{e.text}</div>
                <div className="fi-sub">
                  снимок {feedDate(e.date)} · {SUB[e.kind] ?? e.status ?? ''}
                  {e.model && e.kind !== 'threat' ? ` · ${e.model === 'mdd' ? 'MDD' : e.model === 'lgbm' ? 'LGBM' : e.model}` : ''}
                </div>
              </button>
            ))}
          </div>
        ) : (
          <div data-testid="region-list">
            <div className="reg-group">
              Справа — индекс, ‰: доля наблюдаемой воды с признаками плавающего материала на последнем надёжном снимке (не масса пластика).
            </div>
            <div className="reg-group" style={{ paddingTop: 0 }}>Надёжный последний снимок</div>
            {ok.map((r) => (
              <RegionRow key={r.id} r={r} on={r.id === p.regionId} onClick={() => p.onRegion(r.id)} />
            ))}
            {bad.length > 0 && <div className="reg-group">Последний снимок с дымкой, бликом или облаками — индекс ненадёжен</div>}
            {bad.map((r) => (
              <RegionRow key={r.id} r={r} bad on={r.id === p.regionId} onClick={() => p.onRegion(r.id)} />
            ))}
          </div>
        )}
      </div>
      <div className="left-foot">
        <span>Индекс по снимку, не масса пластика</span>
      </div>
      <button className="collapse-tab" onClick={p.onCollapse} aria-label={p.collapsed ? 'Показать панель' : 'Скрыть панель'} data-testid="left-collapse">
        {p.collapsed ? '›' : '‹'}
      </button>
    </aside>
  );
}

function RegionRow({ r, on, bad, onClick }: { r: any; on: boolean; bad?: boolean; onClick: () => void }) {
  const d = summaryDate(r);
  const [a, u] = fmtArea(r.summary?.total_debris_area_m2);
  return (
    <button className={`reg-item ${on ? 'on' : ''} ${bad ? 'bad' : ''}`} onClick={onClick} data-testid={`region-${r.id}`} title={bad ? regionReliability(r).why : r.name}>
      <span className="ri-name">{shortName(r.name)}</span>
      <span className="ri-val">
        {fmtPermille(r.summary?.index_permille)} <span className="faint">‰</span>
      </span>
      <span className="ri-sub">
        {r.country ? `${r.country} · ` : ''}
        {d ? d.date.split('-').reverse().join('.') : '—'}
      </span>
      <span className="ri-sub" style={{ textAlign: 'right' }}>
        {fmtNum(r.summary?.n_detections)} {plural(r.summary?.n_detections ?? 0, 'участок', 'участка', 'участков')}
      </span>
    </button>
  );
}
