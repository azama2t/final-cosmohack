import { useMemo, useState } from 'react';
import type { Basemap, LayerKey, Layers, Manifest, Region } from '../types';
import { dataUrl, isFlagged, regionHaze, shortName, summaryDate } from '../lib/data';
import { fmtThr, fmtDate, fmtDateShort, fmtNum, fmtPermille, modelLabel } from '../lib/style';

interface Props {
  manifest: Manifest;
  region: Region | null;
  date: string | null;
  model: string;
  layers: Layers;
  basemap: Basemap;
  hasDrift: boolean;
  collapsed: boolean;
  onCollapse: () => void;
  onRegion: (id: string | null) => void;
  onDate: (d: string) => void;
  onModel: (m: string) => void;
  onLayer: (k: LayerKey, on?: boolean) => void;
  onBasemap: (b: Basemap) => void;
  /** L15: confirmation data exists for this date (both models) */
  confAvail?: boolean;
  onlyConfirmed?: boolean;
  nConfirmed?: number | null;
  nDetections?: number | null;
  onOnlyConfirmed?: (v: boolean) => void;
}

const LAYER_DEFS: { key: LayerKey; label: string; hint: string; testid: string }[] = [
  { key: 'rgb', label: 'Снимок Sentinel-2', hint: 'true-color вырезка', testid: 'rgb' },
  { key: 'detections', label: 'Пятна (находки)', hint: 'prob ≥ порога модели', testid: 'detections' },
  { key: 'prob', label: 'Вероятность', hint: 'сырой выход модели', testid: 'prob' },
  { key: 'h3', label: 'Индекс по сетке H3', hint: '‰ воды с признаками', testid: 'h3' },
  { key: 'zones', label: 'Приоритет обследования', hint: 'топ-10 ячеек', testid: 'zones' },
  { key: 'drift', label: 'Дрейф 0→72 ч', hint: 'демо-прогноз', testid: 'drift' },
];

export default function LeftPanel(p: Props) {
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<'index' | 'name'>('index');
  const regions = useMemo(() => {
    const s = q.trim().toLowerCase();
    const list = [...p.manifest.regions].sort((a, b) =>
      sort === 'name'
        ? shortName(a.name).localeCompare(shortName(b.name), 'ru')
        : (b.summary?.index_permille ?? -1) - (a.summary?.index_permille ?? -1),
    );
    if (!s) return list;
    return list.filter((r) => `${r.name} ${r.country ?? ''} ${r.id} ${r.tile ?? ''}`.toLowerCase().includes(s));
  }, [q, sort, p.manifest.regions]);

  return (
    <aside className={`panel panel-left glass ${p.collapsed ? 'collapsed' : ''}`} data-testid="left-panel">
      <button
        className="collapse-btn left"
        onClick={p.onCollapse}
        data-testid="collapse-left"
        title={p.collapsed ? 'Показать панель' : 'Свернуть панель'}
      >
        {p.collapsed ? '›' : '‹'}
      </button>
      {!p.collapsed && (
        <div className="panel-scroll">
          <section className="section">
            <div className="section-head">
              <h2>
                Районы <span className="muted small">{p.manifest.regions.length}</span>
              </h2>
              <div className="segmented small sort-seg" role="tablist" aria-label="Сортировка районов">
                <button className={`seg ${sort === 'index' ? 'on' : ''}`} onClick={() => setSort('index')} data-testid="region-sort-index" title="Сортировать по индексу (сначала выше)">
                  <span className="wide-only">по </span>индексу
                </button>
                <button className={`seg ${sort === 'name' ? 'on' : ''}`} onClick={() => setSort('name')} data-testid="region-sort-name" title="Сортировать по имени (А–Я)">
                  <span className="wide-only">по </span>имени
                </button>
              </div>
            </div>
            <input
              className="search"
              placeholder="Поиск района, страны, тайла…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              data-testid="region-search"
            />
            <div className="region-list">
              {regions.map((r) => {
                const shown = summaryDate(r);
                const active = r.id === p.region?.id;
                const hazeHint = regionHaze(r);
                const n = r.summary?.n_detections ?? 0;
                return (
                  <button
                    key={r.id}
                    className={`region-item ${active ? 'active' : ''}`}
                    onClick={() => p.onRegion(r.id)}
                    data-testid={`region-item-${r.id}`}
                    title={`${r.name}${r.country ? ' · ' + r.country : ''}${hazeHint ? ' — ' + hazeHint : ''}`}
                  >
                    <span className="ri-thumb">
                      {shown && <img src={dataUrl(shown.thumb ?? shown.rgb)} alt="" loading="lazy" decoding="async" />}
                      {hazeHint && (
                        <span className="ri-haze-dot" data-testid={`region-haze-${r.id}`} aria-label={hazeHint}>
                          !
                        </span>
                      )}
                    </span>
                    <span className="ri-body">
                      <span className="ri-name">{shortName(r.name)}</span>
                      <span className="ri-row2">
                        <span className="ri-sub">
                          <i className="dot-accent" /> {fmtNum(n)} {plural(n, 'пятно', 'пятна', 'пятен')}
                          {shown && <span className="ri-date"> · {fmtDateShort(shown.date)}</span>}
                        </span>
                        <span className="ri-index">
                          {fmtPermille(r.summary?.index_permille)}
                          <small> ‰</small>
                        </span>
                      </span>
                    </span>
                  </button>
                );
              })}
              {!regions.length && <div className="muted small pad">Ничего не найдено</div>}
            </div>
          </section>

          {p.region && (
            <>
              <section className="section">
                <div className="section-head">
                  <h2>Дата снимка</h2>
                  <span className="muted small">{p.date ? fmtDate(p.date) : ''}</span>
                </div>
                <Timeline
                  dates={p.region.dates.map((d) => d.date)}
                  flagged={p.region.dates.map((d) => isFlagged(d))}
                  value={p.date}
                  onChange={p.onDate}
                />
              </section>

              <section className="section">
                <div className="section-head">
                  <h2>Модель</h2>
                </div>
                <div className="segmented" role="tablist">
                  {Object.entries(p.manifest.models).map(([id, m]) => {
                    const available = p.region!.dates.find((d) => d.date === p.date)?.models.includes(id) ?? false;
                    return (
                      <button
                        key={id}
                        className={`seg ${p.model === id ? 'on' : ''}`}
                        disabled={!available}
                        onClick={() => p.onModel(id)}
                        data-testid={`model-toggle-${id}`}
                        title={`${m.name}${m.note ? ' — ' + m.note : ''}`}
                      >
                        {modelLabel(id, m.name)}
                      </button>
                    );
                  })}
                </div>
                <div className="model-note muted small">
                  {p.manifest.models[p.model]?.name} · порог {fmtThr(p.manifest.models[p.model]?.threshold)}
                  {p.manifest.models[p.model]?.note ? ` · ${p.manifest.models[p.model]?.note}` : ''}
                </div>
                <div className={`layer-row conf-row ${p.confAvail ? '' : 'disabled'}`}>
                  <button
                    className={`switch ${p.onlyConfirmed && p.confAvail ? 'on' : ''}`}
                    role="switch"
                    aria-checked={!!(p.onlyConfirmed && p.confAvail)}
                    disabled={!p.confAvail}
                    onClick={() => p.onOnlyConfirmed?.(!p.onlyConfirmed)}
                    data-testid="confirmed-only-toggle"
                  >
                    <span className="knob" />
                  </button>
                  <span
                    className="layer-label"
                    onClick={() => p.confAvail && p.onOnlyConfirmed?.(!p.onlyConfirmed)}
                    title="Объект одной модели, рядом с которым (≤ 20 м) вторая модель тоже видит признаки. Согласие моделей, не проверка на месте."
                  >
                    Только подтверждённые обеими моделями
                    <small>
                      {!p.confAvail
                        ? 'для этой даты нет второй модели'
                        : p.nConfirmed !== null && p.nConfirmed !== undefined
                          ? `вторая модель в радиусе 20 м · ${fmtNum(p.nConfirmed)} из ${fmtNum(p.nDetections ?? 0)}`
                          : 'вторая модель в радиусе 20 м'}
                    </small>
                  </span>
                </div>
              </section>

              <section className="section">
                <div className="section-head">
                  <h2>Слои</h2>
                </div>
                <div className="layer-list">
                  {LAYER_DEFS.map((d) => {
                    const disabled = d.key === 'drift' && !p.hasDrift;
                    return (
                      <div key={d.key} className={`layer-row ${disabled ? 'disabled' : ''}`}>
                        <button
                          className={`switch ${p.layers[d.key] ? 'on' : ''} ${d.key === 'detections' ? 'accent' : ''}`}
                          role="switch"
                          aria-checked={p.layers[d.key]}
                          disabled={disabled}
                          onClick={() => p.onLayer(d.key)}
                          data-testid={`layer-toggle-${d.testid}`}
                        >
                          <span className="knob" />
                        </button>
                        <span className="layer-label" onClick={() => !disabled && p.onLayer(d.key)}>
                          {d.label}
                          <small>{disabled ? 'нет прогноза для этой даты' : d.hint}</small>
                        </span>
                        {d.key === 'h3' && (
                          <button
                            className={`chip ${p.layers.h3_3d ? 'on' : ''}`}
                            onClick={() => p.onLayer('h3_3d')}
                            data-testid="layer-toggle-h3-3d"
                            title="3D-столбики с наклоном камеры"
                          >
                            3D
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>
            </>
          )}

          <section className="section">
            <div className="section-head">
              <h2>Подложка</h2>
            </div>
            <div className="segmented">
              {(
                [
                  ['dark', 'Тёмная'],
                  ['satellite', 'Спутник'],
                  ['none', 'Без'],
                ] as [Basemap, string][]
              ).map(([id, label]) => (
                <button
                  key={id}
                  className={`seg ${p.basemap === id ? 'on' : ''}`}
                  onClick={() => p.onBasemap(id)}
                  data-testid={`basemap-${id}`}
                >
                  {label}
                </button>
              ))}
            </div>
          </section>
        </div>
      )}
    </aside>
  );
}

function Timeline({
  dates,
  flagged,
  value,
  onChange,
}: {
  dates: string[];
  flagged: boolean[];
  value: string | null;
  onChange: (d: string) => void;
}) {
  const t = dates.map((d) => Date.parse(d));
  const min = Math.min(...t),
    max = Math.max(...t);
  const pos = (i: number) => (dates.length === 1 ? 50 : 6 + ((t[i] - min) / (max - min || 1)) * 88);
  const cur = value ? dates.indexOf(value) : -1;
  // labels: selected date always; others only if they do not collide (≥ 24 % apart)
  const showLabel: boolean[] = [];
  let lastPos = -1e9;
  const order = dates.map((_, i) => i).sort((a, b) => (a === cur ? -1 : b === cur ? 1 : a - b));
  const shownPos: number[] = [];
  for (const i of order) {
    const ok = i === cur || shownPos.every((p) => Math.abs(p - pos(i)) >= 24);
    showLabel[i] = ok;
    if (ok) shownPos.push(pos(i));
  }
  void lastPos;
  return (
    <div className="timeline" data-testid="timeline">
      <div className="tl-track" />
      {cur >= 0 && <div className="tl-fill" style={{ width: `${pos(cur)}%` }} />}
      {dates.map((d, i) => (
        <button
          key={d}
          className={`tl-dot ${d === value ? 'on' : ''} ${flagged[i] ? 'q-warn' : ''}`}
          style={{ left: `${pos(i)}%` }}
          onClick={() => onChange(d)}
          data-testid={`date-dot-${d}`}
          title={flagged[i] ? `${fmtDate(d)} · дымка/блик — находки могут быть завышены` : fmtDate(d)}
        >
          {showLabel[i] && (
            <span className="tl-label">{fmtDateShort(d)}</span>
          )}
        </button>
      ))}
    </div>
  );
}

function plural(n: number, one: string, few: string, many: string) {
  const m10 = n % 10,
    m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
  return many;
}
