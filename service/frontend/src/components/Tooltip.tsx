import type { DetProps, H3Props, Manifest } from '../types';
import type { HoverInfo } from '../map/layers';
import { fmtArea, fmtDate, fmtNum, fmtPct, fmtPermille, modelLabel } from '../lib/style';

export default function Tooltip({ hover, manifest }: { hover: HoverInfo; manifest: Manifest }) {
  const style = { left: hover.x + 16, top: hover.y + 16 };
  if (hover.kind === 'det') {
    const p = hover.props as DetProps;
    const [a, u] = fmtArea(p.area_m2);
    return (
      <div className="tooltip" style={style} data-testid="tooltip">
        <div className="tt-title accent-text">Пятно · признаки мусора</div>
        <div className="tt-row"><span>Площадь</span><b>{fmtNum(p.area_m2)} м²{u === 'га' ? ` (${a} га)` : ''}</b></div>
        <div className="tt-row"><span>Вероятность ср. / макс.</span><b>{p.mean_prob.toFixed(2)} / {p.max_prob.toFixed(2)}</b></div>
        <div className="tt-row"><span>Дата</span><b>{fmtDate(p.date)}</b></div>
        <div className="tt-row"><span>Модель</span><b>{modelLabel(p.model, manifest.models[p.model]?.name)}</b></div>
        <div className="tt-hint">Клик — карточка с вырезкой снимка</div>
      </div>
    );
  }
  const p = hover.props as H3Props;
  return (
    <div className="tooltip" style={style} data-testid="tooltip">
      <div className="tt-title">Ячейка H3 · res {p.res}</div>
      <div className="tt-row"><span>Индекс</span><b>{p.share_permille === null ? 'нет данных' : `${fmtPermille(p.share_permille)} ‰`}</b></div>
      <div className="tt-row"><span>Помечено / вода, пикс.</span><b>{fmtNum(p.flagged_water_px)} / {fmtNum(p.observed_water_px)}</b></div>
      <div className="tt-row"><span>Наблюдалось ячейки</span><b>{fmtPct(p.observed_frac)}</b></div>
      <div className="tt-row"><span>Пятен</span><b>{p.n_detections}</b></div>
      {p.share_permille === null && <div className="tt-hint">Наблюдалось &lt; 50 % ячейки — не ноль, а «нет данных»</div>}
    </div>
  );
}
