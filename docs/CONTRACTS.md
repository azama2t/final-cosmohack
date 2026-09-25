# Контракты файлов слоя данных сервиса

Все модули пишут и читают эти форматы. Генератор валидных фейковых файлов: `python scripts/make_fixtures.py --out service/demo_fixtures`.
Корень данных сервиса: `service/data/` (полный, не в git) или `service/demo/` (маленький, в git). Сервис берёт `service/data/`, если там есть `manifest.json`, иначе `service/demo/`.
Все пути в `manifest.json` — относительные от корня данных, разделитель `/`.

## Раскладка

```
<root>/manifest.json
<root>/<region>/timeseries.json
<root>/<region>/<date>/rgb.png            true-color вырезка, ≤ 2048 px по стороне
<root>/<region>/<date>/rgb.json           {"bounds":[w,s,e,n], "width":W, "height":H, "crs":"EPSG:326xx", "scene_id":"...", "cloud_frac":0.03}
<root>/<region>/<date>/drift.json         (опционально, только свежие сцены 2025–2026)
<root>/<region>/<date>/<model>/prob.png   RGBA, палитра, прозрачность при prob < 0.05; те же bounds, что rgb.json
<root>/<region>/<date>/<model>/prob.tif   uint8 0–255 (= prob*255), CRS сцены (UTM), nodata=0 нельзя — отдельная маска не нужна
<root>/<region>/<date>/<model>/detections.geojson
<root>/<region>/<date>/<model>/h3.geojson
<root>/<region>/<date>/<model>/zones.json
```
`<region>` — латиница, snake_case (`honduras`, `durban`). `<date>` — `YYYY-MM-DD`. `<model>` ∈ `mdd` (marinedebrisdetector), `lgbm` (наша LightGBM); будущие — своё короткое имя.

## detections.geojson
FeatureCollection, Polygon/MultiPolygon в WGS84 (EPSG:4326), упрощение до ≤ 2 МБ на файл.
`properties`: `id` (str, `<region>_<date>_<model>_<n>`), `region`, `date`, `area_m2` (float, площадь в UTM), `mean_prob`, `max_prob` (0..1), `model`.

## h3.geojson
FeatureCollection, Polygon = граница ячейки H3 res 8. Только ячейки, пересекающие вырезку.
`properties`: `h3` (str), `res` (8), `date`, `flagged_water_px` (int), `observed_water_px` (int), `observed_frac` (0..1, доля ячейки с наблюдаемой водой), `share_permille` (float или null, если `observed_frac < 0.5`), `n_detections` (int), `threshold` (float), `model`.
`share_permille = 1000 * flagged_water_px / observed_water_px`. observed = пиксели воды без облаков/теней/nodata/суши. flagged = prob ≥ threshold среди observed.

## zones.json
`{"region","date","model","threshold","zones":[{"rank":1,"h3":"...","index":<share_permille>,"area_m2":<площадь пятен в ячейке>,"reason":"строка по-русски","lon":..,"lat":..,"repeat_dates":<int>,"mean_prob":..}]}` — топ ≤ 10 по `flagged_water_px` с учётом повторяемости по датам и уверенности. Название в UI: «приоритет обследования».

## drift.json
`{"region","date","start_time":"ISO","hours":[0,1,...,72],"particles":[{"id":0,"path":[[lon,lat,t_hours],...]}],"forcing":{"currents":"...","wind":"...","wind_drift_factor":0.02,"model":"OpenDrift OceanDrift"},"note":"демонстрационный прогноз, без валидации"}`

## timeseries.json (на регион)
`[{"date","model","total_debris_area_m2","mean_index","n_detections","cloud_frac"}]`, отсортировано по дате. `mean_index` — средний `share_permille` по ячейкам с `observed_frac ≥ 0.5`.

## manifest.json
```json
{
  "version": 1,
  "generated": "ISO time",
  "kind": "real | demo | fixture",
  "index": {"name": "доля наблюдаемой воды с признаками мусора", "unit": "‰", "h3_res": 8,
            "formula": "flagged_water_px / observed_water_px", "note": "индекс по снимку, не масса пластика"},
  "models": {"mdd": {"name": "marinedebrisdetector (UNet++)", "threshold": 0.5, "url": "https://github.com/MarcCoru/marinedebrisdetector", "license": "MIT"},
             "lgbm": {"name": "LightGBM (наша, MARIDA)", "threshold": 0.5, "note": "обучена на ACOLITE rhorc; на L2A — сдвиг домена"}},
  "regions": [{
    "id": "honduras", "name": "Гондурасский залив", "country": "Гондурас", "tile": "16PCC",
    "center": [lon, lat], "bounds": [w, s, e, n], "zoom": 11,
    "summary": {"latest_date": "YYYY-MM-DD", "index_permille": 0.0, "n_detections": 0, "total_debris_area_m2": 0.0},
    "dates": [{"date": "YYYY-MM-DD", "scene_id": "...", "source": "earth-search sentinel-2-l2a", "cloud_frac": 0.03,
               "bounds": [w, s, e, n], "rgb": "honduras/2025-10-03/rgb.png",
               "models": ["mdd", "lgbm"], "drift": "honduras/2025-10-03/drift.json" }]
  }],
  "sources": [{"name": "Copernicus Sentinel-2 L2A (Earth Search, AWS)", "url": "https://earth-search.aws.element84.com/v1", "license": "Copernicus open licence"}]
}
```
Файлы модели для даты: `<region>/<date>/<model>/{prob.png,prob.tif,detections.geojson,h3.geojson,zones.json}`. `drift` — null, если нет.

## reports/final_numbers.json
Все метрики для README/отчёта/деки/UI. Плоский словарь с вложенными секциями по этапам: `{"l3_lgbm": {"val": {"f1_md":..,"iou_md":..,"threshold":..}, "test": {...}}, "l4_unet": {...}, "data": {...}, "regions": {...}}`. Генерируется скриптом `scripts/final_numbers.py`, руками не правится.

## Дополнения (03:35)
- `manifest.regions[].dates[].thumb` (необязательно): `<region>/<date>/thumb.jpg` — превью rgb 256 px по длинной стороне, ≤ 30 КБ; фронт берёт его для списка регионов.
- Палитра `prob.png` (фиксирована, легенда фронта её повторяет): P < 0.05 — прозрачно; 0.05 ≤ P < порог — рампа от `#2b6cb0` (α 40) через `#b794f4` к `#f6ad55` (α 170); P ≥ порог — акцент `#ff6b4a` (α 230).
- `/api/compare` → `{"a":{region,date,model,kpi},"b":{...},"diff":{<kpi>:{"delta": b−a, "ratio": b/a | null}}}`; kpi: total_debris_area_m2, n_detections, mean_index, max_index, cloud_frac, observed_cells, flagged_cells.
- Корень данных сервиса: `--data-root` → `$MACROPLASTIC_DATA` → `service/data` → `service/demo` → `service/demo_fixtures` → «нет данных».
- `manifest.regions[].dates[].quality` = `{"glint_or_haze": bool, "haze": bool, "note": str}` (haze = `water_b8_median > 0.006` или `glint_or_haze` из scene.json; note «дымка/блик — находки могут быть завышены»; то же в `zones.json.quality` и в `reason` зон); `summary.latest_date` и `regions[].default_date` — последняя дата без haze, если такая есть.
- «Уверенные находки» (необязательно; только даты с ≥ 2 моделями): `detections.geojson` `properties.confirmed` (bool — у другой модели есть пиксель P ≥ её порога на наблюдаемой воде в радиусе 2 px = 20 м от объекта) и `confirmed_by` (id модели или null); `manifest.regions[].dates[].n_confirmed` = `{"mdd": k, "lgbm": m}`; `zones.json` `zones[].n_confirmed` (подтверждённые находки, чей пиксель максимума P лежит в ячейке); H3-индекс не меняется; подпись в UI — «согласие моделей, не проверка на месте».

## API: зоны, место, календарь, проверка
Модули: `service/place.py` (зона, место, календарь, вырезки), `service/pdf.py` (справка PDF), `service/review.py` (очередь, метки, дообучение). Ошибки: 404 `{"detail"}` — нет района/даты/модели/файла; 422 `{"detail"}` — плохой параметр. Живые сцены (для вырезок 10 м и ложного цвета): `$MACROPLASTIC_LIVE` или `data/live/<region>/<date>/bands.tif`; без них вырезка берётся из `rgb.png` корня данных.

**Приоритет обследования (формула, `src/macroplastic/grid/zones.py`):** `score = flagged_water_px × mean_prob × (1 + 0.5 × (repeat_dates − 1))`; ранжируются ячейки с `observed_frac ≥ 0.5`; топ ≤ 10. Это ранжирование, не измеренная опасность и не масса.

- `GET /api/zone?region=&h3=[&date=][&model=mdd]` →
  `{region, date, model, threshold, rank (int | null — вне топа), h3, lon, lat, index (‰), area_m2, flagged_water_px, n_detections, n_confirmed?, mean_prob, max_prob, repeat_dates, n_dates, observed_frac, cloud_frac, quality, reason, detections:[{id,date,model,lon,lat,area_m2,mean_prob,max_prob,confirmed,confirmed_by}], why:{formula, formula_text, score, terms:[{name, label, value, weight, contribution}], text}, crop, crop_false_color (url | null), place, pdf}`.
  `terms`: `flagged_water_px` (weight 1), `mean_prob` (weight 1), `repeat_dates` (weight 0.5 = бонус за дату); `contribution` — множитель в произведении, `Π contribution = score`.
- `GET /api/crop?region=&lon=&lat=[&date=][&size_m=1500][&highlight=1][&bands=rgb|false|swir][&model=][&h3=][&px=480][&src=auto|bands|png]` → `image/png` (квадрат `px`): вырезка `size_m × size_m` вокруг точки; контуры пятен из `detections.geojson` (`#ff6b4a`, все модели даты или только `model`); белый шестиугольник — ячейка `h3`; масштабная линейка; подпись даты и каналов. `bands=false` — B8/B4/B3, `swir` — B11/B8/B4 (только при наличии `bands.tif`, иначе 404 «доступна только RGB-вырезка»). `src=auto` — `bands.tif` (10 м), иначе `rgb.png`.
- `GET /api/place?region=&h3=[&model=]` →
  `{region, region_name, country, h3, res, lon, lat, boundary:[[lon,lat]×6], model, model_name, threshold, summary:{n_dates, n_observed, n_found, first_found, last_found, n_detections_total, max_index}, history:[{date, scene_id, cloud_frac, quality, model, status: found|clean|no_observation|no_image, index, observed_frac, flagged_water_px, mean_prob, n_detections, n_confirmed?, max_prob, zone_rank, detections:[…], other_models:{<m>:{n_detections}}, crop}], formula, formula_text, sources:[{name,url,license}], limitations:[str], pdf}`.
  Статусы: `no_image` — у даты нет слоя модели; `no_observation` — ячейка вне снимка или `observed_frac < 0.5`; `found` — `flagged_water_px > 0` или есть пятно; иначе `clean`.
- `GET /api/place_report.pdf?region=&h3=[&model=][&date=]` → `application/pdf`, A4, 2 стр. (≈ 0.5–1.5 с): заголовок, координаты, KPI, вырезки до 4 дат с подсветкой, формула и «почему», таблица истории, ограничения, источники/лицензии, дата формирования; подпись «индекс по снимку, не масса пластика; согласие моделей ≠ проверка на месте».
- `GET /api/calendar?region=[&model=]` → `[{date, model, scene_id, status: detected|clean|unreliable|no_image, n_detections, n_confirmed?, cloud_frac, observed_frac_water, quality_flags:[glint_or_haze|haze], reason}]` — только даты из manifest (реальные снимки), без интерполяции. Правила по порядку: `no_image` — нет слоя модели; `unreliable` — `cloud_frac > 0.5` или флаг дымки/блика или `observed_frac_water < 0.3` (`observed_frac_water` = доля ячеек H3 с водой, где индекс определён); `detected` — ≥ 1 пятно; иначе `clean`.
- `GET /api/review/queue?region=[&model=][&limit=50][&include_labeled=0]` →
  `{region, region_name, model, n_total, n_labeled, labels:[debris,foam,algae,ship_wake,cloud,other], labels_ru:{…}, rules:{…}, items:[{id, region, date, model, lon, lat, max_prob, mean_prob, area_m2, threshold, confirmed, confirmed_by, reasons:[user_flag|disagreement|near_threshold], reason (рус.), priority, scene_id, crop_rgb, crop_false_color (url | null), label?}]}`. Правила: `near_threshold` — `|max_prob − порог| ≤ 0.15`; `disagreement` — `confirmed = false` при второй модели на дату; `user_flag` — отмечено «ложное». Сортировка: `priority` (флаг +3, расхождение +2, около порога +1…2). Размеченные объекты из очереди убираются.
- `POST /api/review/label` JSON `{id, region, date, model, lon, lat, label ∈ debris|foam|algae|ship_wake|cloud|other, note?}` → сохранённая запись `{kind:"label", id, region, date, model, lon, lat, label, note, provenance:{user:"local", ts (UTC ISO), app_version, source_scene_id, model, max_prob, detection_known}}`. Файл: `$MACROPLASTIC_LABELS` (папка → `labels.jsonl` внутри, или путь `*.jsonl`), по умолчанию `service/labels/labels.jsonl`; дозапись, последняя метка объекта главная.
- `POST /api/review/flag` — то же тело без `label` → запись `kind:"flag_false"` (кнопка «ложное» на карте; объект попадает в начало очереди).
- `GET /api/review/labels` → `{path, items:[запись…]}`.
- `POST /api/review/retrain` → `{id, status:"running", started, n_labels, out_dir, log_tail, result:null}`; 422 `нет меток для дообучения…`, если меток нет, или если задача уже идёт. `GET /api/review/retrain/{id}` → `{id, status: running|done|error, returncode, log_tail, result}`; `result` = `weights_exp/review/<id>/result.json`: `{ok, n_labels, n_label_px, val_f1_before, val_f1_after, gain, rule, accepted, decision, replace_command (строка | null), replace_note, labels:[…], labelled_px_agreement, seconds, caveat}` или `{error}`. Веса в `weights/` не подменяются автоматически.
