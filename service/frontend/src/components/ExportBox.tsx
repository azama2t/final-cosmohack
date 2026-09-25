import { useEffect, useState } from 'react';
import type { DetProps, FC, ZonesFile } from '../types';
import { apiAvailable, dataUrl, loadH3, modelPath } from '../lib/data';
import { centroid } from '../map/layers';

type ExLayer = 'detections' | 'h3' | 'zones';

interface Props {
  region: string;
  date: string;
  model: string;
  detections: FC<DetProps> | null;
  zones: ZonesFile | null;
  onToast: (t: string) => void;
}

const LABEL: Record<ExLayer, string> = { detections: 'Пятна', h3: 'Сетка H3', zones: 'Зоны' };

export default function ExportBox(p: Props) {
  const [layer, setLayer] = useState<ExLayer>('detections');
  const [api, setApi] = useState(false);
  useEffect(() => {
    apiAvailable().then(setApi);
  }, []);
  const q = `region=${encodeURIComponent(p.region)}&date=${p.date}&model=${p.model}&layer=${layer}`;
  const fname = `${p.region}_${p.date}_${p.model}_${layer}`;

  // Fallback without backend: GeoJSON straight from the data root, CSV built in the browser.
  const staticGeo =
    layer === 'zones' ? dataUrl(modelPath(p.region, p.date, p.model, 'zones.json')) : dataUrl(modelPath(p.region, p.date, p.model, `${layer}.geojson`));

  const csvFallback = async () => {
    let rows: Record<string, unknown>[] = [];
    if (layer === 'detections')
      rows = (p.detections?.features ?? []).map((f) => {
        const [lon, lat] = centroid(f);
        return { ...f.properties, lon: +lon.toFixed(6), lat: +lat.toFixed(6) };
      });
    else if (layer === 'zones') rows = (p.zones?.zones ?? []).map((z) => ({ ...z }));
    else rows = ((await loadH3(p.region, p.date, p.model))?.features ?? []).map((f) => ({ ...f.properties }));
    if (!rows.length) return p.onToast('Нет строк для выгрузки');
    const cols = [...new Set(rows.flatMap((r) => Object.keys(r)))]; // L38: optional fields (artifact) of later rows too
    const esc = (v: unknown) => {
      const s = v === null || v === undefined ? '' : String(v);
      return /[",;\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const csv = [cols.join(','), ...rows.map((r) => cols.map((c) => esc(r[c])).join(','))].join('\n');
    const url = URL.createObjectURL(new Blob(['﻿' + csv], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `${fname}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 2000);
  };

  return (
    <div className="export">
      <div className="section-head">
        <h3>Выгрузка</h3>
        <span className="muted small">{api ? 'через API' : 'из файлов данных'}</span>
      </div>
      <div className="segmented small">
        {(Object.keys(LABEL) as ExLayer[]).map((k) => (
          <button key={k} className={`seg ${layer === k ? 'on' : ''}`} onClick={() => setLayer(k)} data-testid={`export-layer-${k}`}>
            {LABEL[k]}
          </button>
        ))}
      </div>
      <div className="export-btns">
        <a
          className="btn"
          href={api ? `/api/export?${q}&format=geojson` : staticGeo}
          download={`${fname}.${layer === 'zones' && !api ? 'json' : 'geojson'}`}
          data-testid="export-geojson"
        >
          GeoJSON
        </a>
        {api ? (
          <a className="btn" href={`/api/export?${q}&format=csv`} download={`${fname}.csv`} data-testid="export-csv">
            CSV
          </a>
        ) : (
          <button className="btn" onClick={csvFallback} data-testid="export-csv">
            CSV
          </button>
        )}
      </div>
    </div>
  );
}
