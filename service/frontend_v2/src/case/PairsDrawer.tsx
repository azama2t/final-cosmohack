// Registry of pairs «observation ↔ scene» (GET /api/v3/pairs with the current filters): accepted / rejected,
// reason; a row click shows the pair on the map. Export of exactly this table: /api/v3/export?layer=pairs&…
import { useMemo, useState } from 'react';
import Info from '../components/Info';
import type { Meta, Pair } from './api3';
import { dateRu, driftTitle, driftTxt, dtTitle, dtTxt, missionShort, num, pairDecision, pairReasons, sourceShort } from './fmt';

const PAGE = 150;

export default function PairsDrawer({
  meta,
  pairs,
  loading,
  err,
  status,
  onStatus,
  active,
  onPair,
  onClose,
  exportHref,
  showSource,
}: {
  meta: Meta;
  pairs: { count: number; pairs: Pair[]; empty_reason: string | null } | null;
  loading: boolean;
  err: string | null;
  status: string;
  onStatus: (s: string) => void;
  active: string | null;
  onPair: (p: Pair) => void;
  onClose: () => void;
  exportHref: (fmt: 'csv' | 'geojson') => string;
  showSource: boolean;
}) {
  const [n, setN] = useState(PAGE);
  const rows = useMemo(() => {
    const ps = pairs?.pairs ?? [];
    const rank = (p: Pair) => (p.quality_decision ? 0 : p.scene_id ? 1 : 2);
    return [...ps].sort((a, b) => rank(a) - rank(b) || Math.abs(a.dt_hours ?? 1e9) - Math.abs(b.dt_hours ?? 1e9) || a.pair_id.localeCompare(b.pair_id));
  }, [pairs]);
  const nAcc = rows.filter((p) => p.status === 'accepted').length;
  const nScene = rows.filter((p) => p.scene_id).length;
  return (
    <div className="c-drawer" data-testid="pairs-drawer">
      <div className="c-drawer-head">
        <b>Реестр пар</b>
        <span className="faint" data-testid="pairs-count">
          {pairs ? `${num(pairs.count, 0)} · принято ${nAcc} · со снимком ${nScene}` : ''}
        </span>
        <Info label="Как читать">
          Пара = полевая запись × снимок-кандидат. Сначала — пары, прошедшие маски качества, затем снимки по |Δt|, в конце — окна без снимка.
          Δt = снимок − наблюдение, ч; «±12» — время наблюдения неизвестно (только дата). Дрейф / допуск — смещение мусора за Δt (типичный сценарий) и допуск
          совпадения, км; в подсказке ячейки — низкий / типичный / высокий сценарии.
        </Info>
        <div className="seg" role="radiogroup" aria-label="Статус пары">
          {[
            ['all', 'Все'],
            ['accepted', 'Принятые'],
            ['rejected', 'Отклонённые'],
          ].map(([k, l]) => (
            <button key={k} className={status === k ? 'on' : ''} onClick={() => onStatus(k)} data-testid={`pairs-status-${k}`}>
              {l}
            </button>
          ))}
        </div>
        <span className="c-grow" />
        <a className="btn sm ghost" href={exportHref('csv')} download data-testid="pairs-export-csv">
          CSV
        </a>
        <a className="btn sm ghost" href={exportHref('geojson')} download data-testid="pairs-export-geojson">
          GeoJSON
        </a>
        <button className="icon-btn" onClick={onClose} aria-label="Закрыть реестр" data-testid="pairs-close">
          ✕
        </button>
      </div>
      <div className="c-drawer-body">
        {err && <div className="c-err">{err}</div>}
        {!err && loading && !pairs && <div className="note c-pad">Загрузка…</div>}
        {!err && pairs && !rows.length && <div className="note c-pad" data-testid="pairs-empty">{pairs.empty_reason ?? 'Нет пар под выбранные фильтры'}</div>}
        {rows.length > 0 && (
          <table className="c-table">
            <thead>
              <tr>
                <th>Наблюдение</th>
                {showSource && <th className="c-opt">Акватория</th>}
                <th>Дата измерения</th>
                <th>Снимок</th>
                <th className="r">Δt, ч</th>
                <th className="r" title="смещение мусора за Δt / допуск">Дрейф / допуск, км</th>
                <th className="r c-opt">Облачность</th>
                <th>Решение</th>
                <th>Причина</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, n).map((p) => (
                <tr key={p.pair_id} className={active === p.pair_id ? 'on' : ''} onClick={() => onPair(p)} data-testid="pair-row">
                  <td className="mono">{p.sample_id}</td>
                  {showSource && <td className="c-opt">{sourceShort(meta, p.source_id)}</td>}
                  <td>{dateRu(p.obs_datetime)}</td>
                  <td>{p.scene_id ? `${missionShort(p.mission)} · ${dateRu(p.scene_datetime)}` : <span className="faint">нет снимка</span>}</td>
                  <td className="r" title={dtTitle(p)}>
                    {dtTxt(p)}
                  </td>
                  <td className="r" title={driftTitle(p)}>
                    {driftTxt(p)}
                  </td>
                  <td className="r c-opt">{p.scene_cloud_pct === null || p.scene_cloud_pct === undefined ? '—' : `${num(p.scene_cloud_pct, 0)} %`}</td>
                  <td>
                    <span className={`c-dec ${p.status}`}>{pairDecision(p)}</span>
                  </td>
                  <td className="c-why-cell">{pairReasons(meta, p).join('; ') || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {rows.length > n && (
          <div className="c-pad">
            <button className="btn sm" onClick={() => setN((x) => x + PAGE * 4)} data-testid="pairs-more">
              Показать ещё ({rows.length - n})
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
