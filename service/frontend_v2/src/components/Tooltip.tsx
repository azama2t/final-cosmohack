import type { Manifest } from '../types';
import type { HoverInfo } from '../map/layers';
import { artifactOf, artifactRu } from '../lib/artifacts';
import { fmtArea, fmtPct, fmtPermille, modelLabel } from '../lib/style';

export default function Tooltip({ hover, manifest }: { hover: HoverInfo; manifest: Manifest }) {
  const p = hover.props;
  let title = '';
  let sub = '';
  if (hover.kind === 'det') {
    const art = artifactOf(p);
    const [a, u] = fmtArea(p.area_m2);
    title = art ? `Исключено: ${artifactRu(art)}` : `Признаки материала · ${a} ${u}`;
    sub = `уверенность ${Number(p.mean_prob).toFixed(2)} · ${modelLabel(p.model, manifest.models[p.model]?.name)}${p.confirmed ? ' · вторая модель согласна (сигнал)' : ''}`;
  } else if (hover.kind === 'h3') {
    title = p.share_permille === null ? 'Ячейка H3 · мало наблюдаемой воды' : `Индекс ${fmtPermille(p.share_permille)} ‰`;
    sub = `наблюдалось ${fmtPct(p.observed_frac)} · пятен ${p.n_detections} · клик — история места`;
  } else if (hover.kind === 'osm') {
    title = `${p.kind_ru ?? p.kind}${p.name ? ` «${p.name}»` : ''}`;
    sub = '© OpenStreetMap contributors';
  } else if (hover.kind === 'source') {
    title = 'Вероятный постоянный источник';
    sub = `${p.n_dates} даты с находками${p.nearest_source ? ` · ${p.nearest_source.kind_ru}${p.nearest_source.name ? ` «${p.nearest_source.name}»` : ''}` : ''} · требует проверки`;
  }
  const W = window.innerWidth;
  const left = hover.x + 16 + 280 > W ? hover.x - 296 : hover.x + 16;
  return (
    <div className="tip" style={{ left, top: hover.y + 12 }} data-testid="tooltip">
      <div className="tt">{title}</div>
      <div className="ts">{sub}</div>
    </div>
  );
}
