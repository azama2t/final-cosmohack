import { useEffect, useMemo, useRef, useState } from 'react';
import type { Manifest } from '../types';
import { KIND_CLASS, feedDate, type FeedEvent } from '../lib/feed';
import { rankRegions, regionReliability, shortName, summaryDate } from '../lib/data';
import { fmtM2, fmtNum, fmtPermille, isUnknownDate } from '../lib/style';
import { plural } from './RegionPanel';
import Info from './Info';

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
  excluded: 'исключено системой',
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
  // regions with findings first; «без находок» and «снимок ненадёжен» are folded groups (shorter first screen)
  const withF = ok.filter((r) => (r.summary?.n_detections ?? 0) > 0);
  const noF = ok.filter((r) => !((r.summary?.n_detections ?? 0) > 0));
  const [openNo, setOpenNo] = useState(false);
  const [openBad, setOpenBad] = useState(false);
  useEffect(() => {
    if (noF.some((r) => r.id === p.regionId)) setOpenNo(true);
    if (bad.some((r) => r.id === p.regionId)) setOpenBad(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.regionId]);
  const nameOf = (id: string, fallback: string) => shortName(p.manifest.regions.find((r) => r.id === id)?.name ?? fallback);
  const feedTitle = (e: FeedEvent) => {
    const nm = nameOf(e.region, e.regionName);
    if (e.kind === 'haze') return `${nm} · дымка или блик`;
    if (e.kind === 'threat') return e.text;
    return e.area_m2 ? `${nm} · ${fmtM2(e.area_m2)}` : nm;
  };

  // keep the selected feed item in view (tour, clicks on the map)
  useEffect(() => {
    if (!p.feedSel || p.tab !== 'feed') return;
    const el = body.current?.querySelector(`[data-key="${CSS.escape(p.feedSel)}"]`) as HTMLElement | null;
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [p.feedSel, p.tab]);

  return (
    <aside className={`left ${p.collapsed ? 'collapsed' : ''}`} data-panel="left" data-testid="left-panel">
      <div className="modebar">
        <div className="seg" role="tablist" aria-label="Режим">
          <button onClick={() => (location.href = location.pathname)} data-testid="mode-case">
            Кейс
          </button>
          <button className="on" aria-selected data-testid="mode-live">
            Обзор районов (прежний режим)
          </button>
        </div>
      </div>
      <div className="c-legacy-note" role="note" data-testid="legacy-note">
        Прежний режим без фильтров судов, пены и ветра; числа находок не совпадают со слоем «Спутниковые зоны» во вкладке «Кейс» — для оценки
        используйте «Кейс».
      </div>
      <div className="brand">
        <div className="brand-name">
          <span className="brand-dot" aria-hidden />
          Морской мусор
          <Info label="О карте" testid="info-index">
            Находки — участки, где модель видит признаки плавающего материала на снимке; это приоритет проверки, не подтверждённый мусор.
            Индекс района, ‰ — доля наблюдаемой воды с такими признаками на последнем надёжном снимке (в подсказке у района), не масса
            пластика. {p.manifest.regions.length} районов, {nDates} снимков.
          </Info>
        </div>
        <button className={`btn icon sm ${p.regionId ? '' : 'on'}`} onClick={p.onWorld} data-testid="world" title="Обзор мира" aria-label="Обзор мира">
          <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" aria-hidden>
            <circle cx="8" cy="8" r="6.2" />
            <path d="M1.8 8h12.4M8 1.8c1.8 1.9 2.6 4 2.6 6.2S9.8 12.3 8 14.2M8 1.8C6.2 3.7 5.4 5.8 5.4 8s.8 4.3 2.6 6.2" />
          </svg>
        </button>
      </div>
      <div className="tabs" role="tablist">
        <button className={`tab ${p.tab === 'regions' ? 'on' : ''}`} onClick={() => p.onTab('regions')} data-testid="tab-regions">
          Районы
        </button>
        <button className={`tab ${p.tab === 'feed' ? 'on' : ''}`} onClick={() => p.onTab('feed')} data-testid="tab-feed">
          Лента
        </button>
      </div>
      <div className="left-body" ref={body}>
        {p.tab === 'feed' ? (
          <div data-testid="feed">
            <div className="feed-head">
              <span>{p.feed ? `${p.feed.items.length} событий` : 'Загрузка…'}</span>
            </div>
            {p.feed?.items.map((e, i) => (
              <button
                key={e.key}
                data-key={e.key}
                className={`feed-item ${KIND_CLASS[e.kind] ?? ''} ${p.feedSel === e.key ? 'on' : ''}`}
                onClick={() => p.onFeed(e)}
                data-testid={`feed-item-${i}`}
                data-region={e.region}
                data-kind={e.kind}
              >
                <span className="fi-mark" aria-hidden />
                <div className="fi-title">{feedTitle(e)}</div>
                <div className="fi-sub">
                  {feedDate(e.date)} · {SUB[e.kind] ?? e.status ?? ''}
                </div>
              </button>
            ))}
          </div>
        ) : (
          <div data-testid="region-list">
            {withF.map((r) => (
              <RegionRow key={r.id} r={r} on={r.id === p.regionId} onClick={() => p.onRegion(r.id)} />
            ))}
            {noF.length > 0 && (
              <button className="reg-fold" onClick={() => setOpenNo((v) => !v)} aria-expanded={openNo} data-testid="fold-nofind">
                <span>{openNo ? '▾' : '▸'}</span> Без находок ({noF.length})
              </button>
            )}
            {openNo && noF.map((r) => <RegionRow key={r.id} r={r} on={r.id === p.regionId} onClick={() => p.onRegion(r.id)} />)}
            {bad.length > 0 && (
              <button
                className="reg-fold"
                onClick={() => setOpenBad((v) => !v)}
                aria-expanded={openBad}
                data-testid="fold-bad"
                title="Последний снимок с дымкой, бликом или облаками — находки могут быть ложными"
              >
                <span>{openBad ? '▾' : '▸'}</span> Ненадёжные ({bad.length})
              </button>
            )}
            {openBad && bad.map((r) => <RegionRow key={r.id} r={r} bad on={r.id === p.regionId} onClick={() => p.onRegion(r.id)} />)}
          </div>
        )}
      </div>
      <button className="collapse-tab" onClick={p.onCollapse} aria-label={p.collapsed ? 'Показать панель' : 'Скрыть панель'} data-testid="left-collapse">
        {p.collapsed ? '›' : '‹'}
      </button>
    </aside>
  );
}

function RegionRow({ r, on, bad, onClick }: { r: any; on: boolean; bad?: boolean; onClick: () => void }) {
  const d = summaryDate(r);
  const n = r.summary?.n_detections ?? 0;
  const tip = `${r.name}${r.country ? `, ${r.country}` : ''} · индекс ${fmtPermille(r.summary?.index_permille)} ‰ (доля воды с признаками, не масса)${bad ? ` · ${regionReliability(r).why}` : ''}`;
  return (
    <button className={`reg-item ${on ? 'on' : ''} ${bad ? 'bad' : ''}`} onClick={onClick} data-testid={`region-${r.id}`} title={tip}>
      <span className="ri-name">{shortName(r.name)}</span>
      <span className={`ri-val ${n ? '' : 'faint'}`}>
        {n ? `${fmtNum(n)} ${plural(n, 'кандидат', 'кандидата', 'кандидатов')} (без фильтров)` : 'нет'}
      </span>
      <span className="ri-sub">{d ? (isUnknownDate(d.date) ? 'дата неизвестна' : d.date.split('-').reverse().join('.')) : '—'}</span>
    </button>
  );
}
