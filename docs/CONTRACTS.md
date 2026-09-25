# Контракты файлов слоя данных сервиса

Все дорожки пишут и читают эти форматы. Генератор валидных фейковых файлов: `python scripts/make_fixtures.py --out service/demo_fixtures`.
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
`<region>` — латиница, snake_case (`honduras`, `durban`). `<date>` — `YYYY-MM-DD`. `<model>` ∈ `mdd` (marinedebrisdetector), `lgbm` (наша модель L3); будущие — своё короткое имя.

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
Все метрики для README/отчёта/деки/UI. Плоский словарь с вложенными секциями по дорожкам: `{"l3_lgbm": {"val": {"f1_md":..,"iou_md":..,"threshold":..}, "test": {...}}, "l4_unet": {...}, "data": {...}, "regions": {...}}`. Генерируется скриптом `scripts/final_numbers.py`, руками не правится.

## Дополнения (03:35)
- `manifest.regions[].dates[].thumb` (необязательно): `<region>/<date>/thumb.jpg` — превью rgb 256 px по длинной стороне, ≤ 30 КБ; фронт берёт его для списка регионов.
- Палитра `prob.png` (фиксирована, легенда фронта её повторяет): P < 0.05 — прозрачно; 0.05 ≤ P < порог — рампа от `#2b6cb0` (α 40) через `#b794f4` к `#f6ad55` (α 170); P ≥ порог — акцент `#ff6b4a` (α 230).
- `/api/compare` → `{"a":{region,date,model,kpi},"b":{...},"diff":{<kpi>:{"delta": b−a, "ratio": b/a | null}}}`; kpi: total_debris_area_m2, n_detections, mean_index, max_index, cloud_frac, observed_cells, flagged_cells.
- Корень данных сервиса: `--data-root` → `$MACROPLASTIC_DATA` → `service/data` → `service/demo` → `service/demo_fixtures` → «нет данных».
- `manifest.regions[].dates[].quality` = `{"glint_or_haze": bool, "haze": bool, "note": str}` (haze = `water_b8_median > 0.006` или `glint_or_haze` из scene.json; note «дымка/блик — находки могут быть завышены»; то же в `zones.json.quality` и в `reason` зон); `summary.latest_date` и `regions[].default_date` — последняя дата без haze, если такая есть.
