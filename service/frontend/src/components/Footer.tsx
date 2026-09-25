import type { Basemap, Manifest } from '../types';
import { ATTRIBUTION } from '../map/controller';

export default function Footer({ manifest, basemap }: { manifest: Manifest; basemap: Basemap }) {
  const full = manifest.sources.map((s) => `${s.name}${s.license && s.license !== '—' ? ` (${s.license})` : ''}${s.url ? ` — ${s.url}` : ''}`).join('\n');
  return (
    <footer className="footer" data-testid="attribution">
      <span className="footer-data" title={full}>
        Данные:{' '}
        {manifest.sources.map((s, i) => (
          <span key={i}>
            {i > 0 && ' · '}
            {s.url ? (
              <a href={s.url} target="_blank" rel="noreferrer" title={s.license ?? ''}>
                {s.name}
              </a>
            ) : (
              <span title={s.license ?? ''}>{s.name}</span>
            )}
          </span>
        ))}
      </span>
      <span className="footer-sep">|</span>
      <span className="footer-base">{ATTRIBUTION[basemap]} · MapLibre · deck.gl</span>
    </footer>
  );
}
