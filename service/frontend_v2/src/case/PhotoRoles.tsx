// §48 (L142, Egor's screenshot 05): the photo counter is part of the product, not a separate app.
// One explanatory line everywhere + the entry «Посчитать предметы по фото».
// «Открыть детальный снимок» — ONLY when the zone/observation really has a linked photo (a URL in its data);
// as of 26.09 no zone or observation in the API has one, so the button is not shown anywhere.

export const ROLES_LINE = 'Спутник — находит зоны · Детальное фото — считает отдельные предметы · Поле — независимое измерение шт./км²';

/** a linked detailed photo of a zone/observation, if its data has one (never guessed) */
export function linkedPhoto(p: Record<string, any> | null | undefined): string | null {
  if (!p) return null;
  for (const k of ['detail_photo_url', 'photo_url', 'detail_image_url']) {
    const v = p[k];
    if (typeof v === 'string' && v.trim()) return v;
  }
  return null;
}

/** the three roles in one line (optionally with the entry button) */
export function PhotoRolesLine({ className = '' }: { className?: string }) {
  const [a, b, c] = ROLES_LINE.split(' · ');
  return (
    <div className={`c-roles ${className}`} data-testid="photo-roles-line">
      <span className="c-role sat">{a}</span>
      <span className="c-role-sep">·</span>
      <span className="c-role photo">{b}</span>
      <span className="c-role-sep">·</span>
      <span className="c-role field">{c}</span>
    </div>
  );
}

/**
 * Bottom of «Главное» in a zone card (and in a field card): the roles line + «Посчитать предметы по фото»
 * + «Открыть детальный снимок» only if `props` carry a linked photo.
 */
export default function PhotoRoles({ props }: { props?: Record<string, any> | null }) {
  const photo = linkedPhoto(props);
  return (
    <div className="sec c-photo-entry" data-testid="photo-roles">
      <PhotoRolesLine />
      <div className="c-photo-entry-a">
        <a className="btn sm" href="?mode=photo" data-testid="photo-count-open">
          Посчитать предметы по фото
        </a>
        {photo && (
          <a className="btn sm ghost" href={photo} target="_blank" rel="noreferrer" data-testid="photo-detail-open">
            Открыть детальный снимок
          </a>
        )}
      </div>
      <div className="c-photo-entry-n faint">
        По снимку Sentinel-2 (10 м) отдельные предметы не считаются; шт./км² по фото — только когда задана площадь кадра или GSD.
      </div>
    </div>
  );
}
