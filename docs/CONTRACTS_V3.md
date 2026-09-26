FRONTEND CONTRACTS v3 — кейс «Макропластик», финал КосмоХакатона (25–26.09.2026)
Команда SAMARKAND. Для независимого (запасного) фронтенда. Бэкенд реализует ровно это.
Версия контракта: 3.1 (25.09 17:40). Изменения — только добавлением полей, не переименованием.
ИЗМЕНЕНИЯ 3.1 (по вопросам Егора и ревью):
  (1) /meta.scene_date_range — всегда реальные даты (из реестра сцен); если сцен нет — null (не "...").
  (2) /meta.quality_classes — ОБЯЗАТЕЛЬНО; фронт рисует легенду маски качества только отсюда.
  (3) Зона: статус обнаружения и статус концентрации — ДВА РАЗНЫХ поля:
      detection_status (detected | not_detected | insufficient_data)
      concentration_status (measured_nearby | research_estimate | unavailable)
      Старое поле "status" остаётся = detection_status (для совместимости).
  (4) Полевой прогноз района (модель по полю) — отдельный объект field_estimate, НЕ concentration.
      concentration у спутниковой зоны = null, пока перенос «снимок → шт./км²» не подтверждён.
  (5) Моки — только в явном режиме разработки (?mock=1 или VITE_MOCK=1). Если API упал в обычном
      режиме — КРУПНАЯ ошибка «Сервис недоступен», никаких выдуманных зон. Реальные полевые
      наблюдения (observations.geojson) показывать можно — это настоящие данные.

====================================================================
0. КОРОТКО: ЧТО ДОЛЖЕН УМЕТЬ ФРОНТ (из постановки + критериев)
====================================================================
Веб-карта обязана показать (Постановка, раздел «веб-карта»):
  [ ] исходный спутниковый снимок (сцена) + маски качества (облака/блики/nodata)
  [ ] полевые наблюдения (точки/трансекты из CSV организаторов)
  [ ] зоны детекции (полигоны) с морским фоном
  [ ] для зоны: концентрация + ЕДИНИЦА (шт./км²) + РАЗМЕРНЫЙ ПРОФИЛЬ + НЕОПРЕДЕЛЁННОСТЬ (интервал)
  [ ] площадь зоны — ОТДЕЛЬНО от концентрации (это разные величины!)
  [ ] измерения (поле) и оценки модели визуально различимы (разная форма/обводка + подпись)
  [ ] легенда
  [ ] фильтры: по дате (диапазон) и по статусу
  [ ] статусы: «Обнаружено», «Не обнаружено», «Недостаточно данных», «Исследовательская оценка»,
      «Концентрация недоступна»
  [ ] экспорт GeoJSON и CSV (того, что сейчас отфильтровано)
  [ ] сохранить запрос и перезапустить сохранённый запрос
  [ ] пустые/некорректные входы — понятное сообщение, а не белый экран
  [ ] (желательно) реестр пар «наблюдение ↔ снимок» с причинами принятия/отказа
Баллы за UI малы (О4 = 5, Т5 = 3 из 105), вывод из критериев: алгоритмы и достоверность важнее оформления.
Значит: честно, понятно, без лишнего текста, без лагов. Не выдумывать цифры.

====================================================================
1. ОБЩИЕ ПРАВИЛА
====================================================================
Base URL:          http://127.0.0.1:8000   (настраиваемо: ?api=http://host:port или .env VITE_API)
Префикс:           /api/v3/
Формат:            JSON, UTF-8. Гео — GeoJSON (RFC 7946), координаты [lon, lat], EPSG:4326.
Даты:              "YYYY-MM-DD" (date), "YYYY-MM-DDTHH:MM:SSZ" (datetime, UTC).
Числа:             null = неизвестно/неприменимо. НИКОГДА не рисовать null как 0.
Единицы:           концентрация — "items/km2" (шт./км²); масса — "g/km2"; площадь — "km2";
                   расстояние — "km"; сдвиг времени — "hours".
CORS:              бэкенд отдаёт Access-Control-Allow-Origin: * (фронт можно держать на другом порту).
Ошибки (всегда такой вид, HTTP 400/404/422/500/503):
  { "error": { "code": "BAD_BBOX", "message": "Человеческий текст на русском", "details": {...} } }
  Коды: BAD_BBOX, BAD_DATE, EMPTY_RESULT(не ошибка, см. ниже), NOT_FOUND, NO_SCENE,
        MODEL_NOT_READY, INTERNAL.
Пустой результат — это НЕ ошибка: HTTP 200 и пустой FeatureCollection / пустой список
  + поле "empty_reason" (строка) — показать её пользователю.
Мок-режим (3.1): ТОЛЬКО явно (?mock=1 / VITE_MOCK=1) + плашка «демо-данные». Без явного
  режима падение API = крупная ошибка «Сервис недоступен», выдуманные зоны не показывать.

====================================================================
2. СЛОВАРИ (enum) — GET /api/v3/meta
====================================================================
Ответ:
{
  "version": "3.0",
  "generated_at": "2026-09-26T12:00:00Z",
  "units": { "concentration": "items/km2", "mass": "g/km2", "area": "km2" },
  "date_range": { "min": "2014-04-03", "max": "2024-06-18" },     // поле
  "scene_date_range": { "min": "2014-04-07", "max": "2024-06-19" }, // снимки из реестра; null если сцен нет
  "quality_classes": [                                              // ОБЯЗАТЕЛЬНО (3.1)
    { "id": "valid",   "label": "Пригодная вода", "color": "#00000000" },
    { "id": "cloud",   "label": "Облако",         "color": "#ffffff99" },
    { "id": "shadow",  "label": "Тень облака",    "color": "#74c0fc99" },
    { "id": "glint",   "label": "Блики",          "color": "#ffd43b99" },
    { "id": "land",    "label": "Суша",           "color": "#495057cc" },
    { "id": "nodata",  "label": "Нет данных",     "color": "#00000066" }
  ],
  "detection_statuses": [
    { "id": "detected",          "label": "Обнаружено",          "color": "#d9480f" },
    { "id": "not_detected",      "label": "Не обнаружено",       "color": "#2b8a3e" },
    { "id": "insufficient_data", "label": "Недостаточно данных", "color": "#868e96" }
  ],
  "concentration_statuses": [
    { "id": "measured_nearby",   "label": "Есть полевое измерение рядом", "color": "#1c7ed6" },
    { "id": "research_estimate", "label": "Исследовательская оценка",     "color": "#7048e8" },
    { "id": "unavailable",       "label": "Концентрация недоступна",      "color": "#adb5bd" }
  ],
  "statuses": [
    { "id": "detected",                  "label": "Обнаружено",               "color": "#d9480f" },
    { "id": "not_detected",              "label": "Не обнаружено",            "color": "#2b8a3e" },
    { "id": "insufficient_data",         "label": "Недостаточно данных",      "color": "#868e96" },
    { "id": "research_estimate",         "label": "Исследовательская оценка", "color": "#7048e8" },
    { "id": "concentration_unavailable", "label": "Концентрация недоступна",  "color": "#adb5bd" }
  ],
  "sources": [
    { "id": "S1_GPGP2018",         "label": "Большое тихоокеанское мусорное пятно, 2018", "n": 350 },
    { "id": "S2_SARGASSO_MSM41",   "label": "Саргассово море, MSM41",                     "n": 330 },
    { "id": "S3_SE_NORTH_SEA",     "label": "Юго-восток Северного моря",                  "n": 222 },
    { "id": "S4_BLACK_SEA_DOORS3", "label": "Чёрное море, DOORS",                         "n": 33 }
  ],
  "measurement_profiles": [          // «размерный профиль» — показывать рядом с каждой концентрацией
    { "id": "S1_trawl_5_to_50", "label": "Трал, 5–50 см",        "size_class": "5-50 cm" },
    { "id": "S1_trawl_GT5_H",   "label": "Трал, >5 см (H)",      "size_class": ">5 cm" },
    { "id": "S1_trawl_GT5_N",   "label": "Трал, >5 см (N)",      "size_class": ">5 cm" },
    { "id": "S1_trawl_GT5_F",   "label": "Трал, >5 см (F)",      "size_class": ">5 cm" },
    { "id": "S1_aerial_GT50",   "label": "Авиасъёмка, >50 см",   "size_class": ">50 cm" },
    { "id": "S2_visual_GT2",    "label": "Визуально с судна, >2 см", "size_class": ">2 cm" },
    { "id": "S3_visual_GT2",    "label": "Визуально с судна, >2 см", "size_class": ">2 cm" },
    { "id": "S4_visual_GT2_5",  "label": "Визуально с судна, >2.5 см", "size_class": ">2.5 cm" }
  ],
  "target_scopes": [
    { "id": "total_plastic",             "label": "Весь пластик" },
    { "id": "plastic_category",          "label": "Категория пластика" },
    { "id": "fisheries_litter_category", "label": "Рыболовный мусор" },
    { "id": "all_litter",                "label": "Весь мусор (не только пластик)" },
    { "id": "object_context",            "label": "Отдельный объект (не плотность)" }
  ],
  "record_types": [
    { "id": "transect_density", "label": "Плотность на трансекте" },
    { "id": "item_observation", "label": "Отдельный объект" }
  ],
  "missions": ["Sentinel-2", "Landsat-8", "Landsat-9"]
}
Фронт берёт подписи и цвета ТОЛЬКО отсюда (не хардкодить; в моке — тот же файл).

====================================================================
3. ПОЛЕВЫЕ НАБЛЮДЕНИЯ (измерения) — GET /api/v3/observations
====================================================================
Query (все необязательные):
  bbox=minLon,minLat,maxLon,maxLat
  date_from=YYYY-MM-DD & date_to=YYYY-MM-DD
  source=S1_GPGP2018,S3_SE_NORTH_SEA       (через запятую)
  profile=S2_visual_GT2,...
  scope=total_plastic,...
  record_type=transect_density|item_observation
  geometry=point|line   (line — трансекта от start к end, если есть; иначе точка)
  limit=5000
Ответ: GeoJSON FeatureCollection
{
  "type": "FeatureCollection",
  "kind": "measurement",
  "count": 935,
  "empty_reason": null,
  "features": [
    {
      "type": "Feature",
      "id": "MPL-0001",
      "geometry": { "type": "Point", "coordinates": [7.659, 54.084] },
      "properties": {
        "kind": "measurement",                 // ВСЕГДА measurement для этого слоя
        "sample_id": "MPL-0001",
        "event_id": "S3:HE419_MarLitter_transect01",
        "source_id": "S3_SE_NORTH_SEA",
        "region": "South-eastern North Sea (German Bight)",
        "sea_area": "North Sea",
        "record_type": "transect_density",
        "date_utc": "2014-04-03",
        "time_start_utc": "14:39:00", "time_end_utc": "16:00:00",
        "target_scope": "all_litter",
        "measurement_profile": "S3_visual_GT2",
        "size_class": ">2 cm (macro)",
        "concentration_items_km2": 15.9,       // null для item_observation/без плотности
        "concentration_g_km2": null,           // только S1
        "sampled_area_km2": 0.251,
        "transect": { "lat_start": 54.1292, "lon_start": 7.8357, "lat_end": 54.039, "lon_end": 7.4821,
                      "length_km": 25.143, "width_m": 10 },
        "position_role": "published_transect_center",
        "quality_flags": ["all_litter_not_plastic"],   // массив (в CSV через ';')
        "zero_scope": null,                   // если не null — это ИЗМЕРЕННЫЙ НОЛЬ (важно отличать от «нет данных»)
        "missions_calendar_eligible": ["Landsat-8"],
        "litter_item_type": "total floating marine macrolitter",
        "material": "mixed (predominantly plastic)",
        "notes": "...",
        "source_doi": "10.1594/PANGAEA.890782",
        "linked_scenes": ["S2B_...", "..."]   // из реестра пар (раздел 5), может быть []
      }
    }
  ]
}
Отрисовка: круг с белой обводкой, размер ~ log10(concentration+1); item_observation — маленький
ромб/крестик (нет плотности). zero_scope != null — полый кружок «0 шт./км²». Подпись в
тултипе: «Измерение · 15.9 шт./км² · визуально >2 см · 03.04.2014». Флаг all_litter_not_plastic —
пометка «весь мусор, не только пластик».

GET /api/v3/observations/{sample_id} → один Feature (то же) + "pairs": [PairRecord...] (раздел 5).

====================================================================
4. СНИМКИ (сцены) — GET /api/v3/scenes
====================================================================
Query: bbox, date_from, date_to, mission=Sentinel-2,Landsat-8, status=accepted|rejected|all
Ответ:
{
  "count": 42, "empty_reason": null,
  "scenes": [
    {
      "scene_id": "S2A_MSIL2A_20180812T..._T10SEG",
      "mission": "Sentinel-2",                    // Sentinel-2 | Landsat-8 | Landsat-9
      "source": "earth-search" ,                  // откуда взяли (STAC)
      "datetime": "2018-08-12T19:03:21Z",
      "footprint": { "type": "Polygon", "coordinates": [[...]] },
      "bounds": [minLon, minLat, maxLon, maxLat], // для imageOverlay
      "cloud_pct": 12.5,                          // по сцене
      "valid_water_fraction": 0.81,               // доля чистых морских пикселей в зоне интереса
      "preview_url": "/api/v3/scenes/{scene_id}/rgb.png",    // PNG, EPSG:4326, растянут на bounds
      "quality_url": "/api/v3/scenes/{scene_id}/quality.png",// PNG RGBA: маска качества (см. ниже)
      "prob_url":    "/api/v3/scenes/{scene_id}/prob.png",   // вероятность мусора (0..1 → палитра), может быть null
      "tiles": "/api/v3/scenes/{scene_id}/tiles/{z}/{x}/{y}.png",  // может быть null — тогда PNG overlay
      "n_zones": 3,
      "n_linked_samples": 7,
      "status": "accepted",                       // accepted | rejected
      "reject_reasons": []                        // коды раздела 5
    }
  ]
}
GET /api/v3/scenes/{scene_id} → один объект + "zones": FeatureCollection (раздел 6) + "pairs": [...].
Маска качества quality.png — цвета (легенда фронта):
  прозрачный = пригодная вода; #ffffff α0.6 = облако; #74c0fc α0.6 = тень облака;
  #ffd43b α0.6 = блики (sunglint); #495057 α0.8 = суша; #000000 α0.4 = nodata.
  Расшифровка также в GET /api/v3/meta → "quality_classes" (если есть — брать оттуда).

====================================================================
5. РЕЕСТР ПАР «наблюдение ↔ снимок» — GET /api/v3/pairs
====================================================================
Query: sample_id, scene_id, status=accepted|rejected|all, max_dt_hours, date_from, date_to, source
Ответ:
{
  "count": 120, "empty_reason": null,
  "pairs": [
    {
      "pair_id": "MPL-0001__S2A_..._T10SEG",
      "sample_id": "MPL-0001",
      "event_id": "S3:HE419_MarLitter_transect01",
      "scene_id": "S2A_...",
      "mission": "Sentinel-2",
      "scene_datetime": "2018-08-12T19:03:21Z",
      "obs_datetime": "2018-08-12T17:10:00Z",
      "dt_hours": 1.9,                     // сцена минус наблюдение, со знаком
      "distance_km": 0.0,                  // от точки/трансекты до ближайшего валидного пикселя
      "drift_shift_km": 0.6,               // оценка смещения за dt (если считали), может быть null
      "geometry": { "type": "LineString", "coordinates": [[...],[...]] },  // трансекта или точка
      "cloud_pct_local": 3.2,              // в буфере вокруг наблюдения
      "valid_fraction_local": 0.95,
      "status": "accepted",                // accepted | rejected
      "reject_reasons": [],                // коды ниже
      "split": "train"                     // train | val | test (группировка по event/scene)
    }
  ]
}
Коды отказа (reject_reasons) и подписи:
  NO_SCENE_IN_WINDOW   «Нет снимка в окне ±N ч»
  CLOUD                «Облачность над точкой»
  GLINT                «Солнечные блики»
  NODATA               «Нет данных/край снимка»
  LAND_OR_COAST        «Суша/берег рядом»
  DT_TOO_LARGE         «Слишком большой разрыв по времени»
  POSITION_UNCERTAIN   «Неточная позиция наблюдения»
  NOT_DENSITY          «Отдельный объект, а не плотность»
  SCOPE_MISMATCH       «Другая целевая совокупность (не пластик)»
Полный словарь — GET /api/v3/meta → "reject_reasons": [{id,label}] (фронт берёт оттуда, если есть).

====================================================================
6. ЗОНЫ ДЕТЕКЦИИ + КОНЦЕНТРАЦИЯ (оценки модели) — GET /api/v3/zones
====================================================================
Query: bbox, date_from, date_to, scene_id, status=detected,research_estimate,...
       profile (размерный профиль, для которого считать концентрацию), min_area_km2
Ответ: GeoJSON FeatureCollection
{
  "type": "FeatureCollection",
  "kind": "model_estimate",
  "count": 17, "empty_reason": null,
  "model": { "detector": "lgbm_v3", "concentration": "calib_v1", "trained_at": "2026-09-26T08:00:00Z" },
  "features": [
    {
      "type": "Feature",
      "id": "Z-000123",
      "geometry": { "type": "Polygon", "coordinates": [[...]] },   // может быть MultiPolygon
      "properties": {
        "kind": "model_estimate",             // ВСЕГДА model_estimate для этого слоя
        "zone_id": "Z-000123",
        "scene_id": "S2A_...",
        "mission": "Sentinel-2",
        "datetime": "2018-08-12T19:03:21Z",
        "status": "detected",                 // = detection_status (совместимость)
        "detection_status": "detected",       // detected | not_detected | insufficient_data
        "concentration_status": "unavailable",// measured_nearby | research_estimate | unavailable
        "status_reason": "Детекция уверенная, 3 полевых наблюдения рядом",  // коротко, для тултипа
        "area_km2": 0.84,                     // ПЛОЩАДЬ ЗОНЫ — отдельная величина
        "detected_area_m2": 12400,            // площадь пикселей мусора внутри зоны
        "detector": { "prob_mean": 0.71, "prob_max": 0.96, "n_pixels": 124 },
        "field_estimate": {                   // 3.1: прогноз ПО ПОЛЕВЫМ ДАННЫМ для района/профиля,
          "value": 36.0, "lo": 12.0, "hi": 95.0,// подпись «оценка по полевым данным, не по снимку»
          "unit": "items/km2", "measurement_profile": "S2_visual_GT2", "model": "field_knn_v1"
        },                                    // может быть null
        "concentration": {                    // 3.1: спутниковая оценка; null пока перенос не подтверждён
          "value": 60.0,                      // точечная оценка
          "lo": 22.0, "hi": 140.0,            // интервал неопределённости
          "interval": "p10-p90",              // или "95% CI"
          "unit": "items/km2",
          "measurement_profile": "S2_visual_GT2",   // для КАКОГО размерного класса оценка
          "size_class": ">2 cm",
          "target_scope": "total_plastic",
          "method": "калибровка по полевым данным (гр. сплит по событиям)",
          "basis": "model_estimate"
        },
        "quality": {
          "valid_fraction": 0.93, "cloud_fraction": 0.02, "glint_fraction": 0.0,
          "flags": ["near_ship_wake"]         // возможные ложные: ship_wake, foam, sargassum, cloud_edge
        },
        "support": {                          // связь с полем
          "n_linked_samples": 3,
          "linked_sample_ids": ["MPL-0412", "MPL-0413", "MPL-0420"],
          "nearest_measurement_km": 1.2
        }
      }
    }
  ]
}
GET /api/v3/zones/{zone_id} → Feature + "crop_url" (PNG вырезка снимка вокруг зоны),
  "prob_crop_url", "linked_observations": FeatureCollection (раздел 3), "explain": [строки ≤ 12 слов].

Отрисовка: полигон, заливка по статусу (цвета из meta), ПУНКТИРНАЯ обводка = модель
(у измерений — сплошная). Тултип: «Оценка модели · 60 шт./км² (22–140) · >2 см · площадь 0.84 км²».
Если concentration=null — «Концентрация недоступна» + status_reason. research_estimate — подпись
«исследовательская оценка» курсивом.

====================================================================
7. МЕТРИКИ / ДОСТОВЕРНОСТЬ — GET /api/v3/metrics
====================================================================
{
  "split": { "type": "grouped", "group_by": ["event_id", "scene_id"], "n_train": 0, "n_val": 0, "n_test": 0 },
  "detector":      { "baseline": { "name": "FDI threshold", "f1": 0.0, "iou": 0.0 },
                     "main":     { "name": "LightGBM",     "f1": 0.0, "iou": 0.0 } },
  "concentration": { "baseline": { "name": "median by profile", "mae": 0.0, "mae_log": 0.0, "coverage": 0.0 },
                     "main":     { "name": "...", "mae": 0.0, "mae_log": 0.0, "coverage": 0.0 },
                     "unit": "items/km2" },
  "control_example": { "items": 12, "area_km2": 0.20, "expected": 60.0, "computed": 60.0 }
}
Фронт: маленькая панель «Достоверность» (baseline vs основной, на одном сплите). coverage =
доля истинных значений, попавших в интервал.

====================================================================
8. СОХРАНЁННЫЕ ЗАПРОСЫ
====================================================================
Query-объект (одинаковый везде, в URL и в POST):
{
  "bbox": [minLon, minLat, maxLon, maxLat] | null,
  "date_from": "2018-01-01" | null, "date_to": "2018-12-31" | null,
  "statuses": ["detected", "research_estimate"],
  "sources": ["S1_GPGP2018"],
  "profiles": ["S1_trawl_5_to_50"],
  "layers": ["scene", "quality", "observations", "zones"],
  "scene_id": null
}
POST /api/v3/queries            body: { "name": "ГПМП август 2018", "query": {...} }
   → 201 { "query_id": "q_7f3a", "name": "...", "created_at": "...", "query": {...} }
GET  /api/v3/queries            → { "queries": [ {query_id, name, created_at, query} ] }
GET  /api/v3/queries/{id}/run   → { "query_id", "ran_at", "query", "observations": FC, "zones": FC,
                                    "scenes": [...], "summary": { "n_obs", "n_zones", "by_status": {...} } }
DELETE /api/v3/queries/{id}     → 204
Фронт ДОЛЖЕН также: сериализовать текущий query в URL (?q=<base64url JSON>) — ссылка = повтор
запроса без бэкенда; и держать список сохранённых в localStorage (в try/catch) как запасной вариант.

====================================================================
9. ЭКСПОРТ
====================================================================
GET /api/v3/export?layer=zones|observations|pairs&format=geojson|csv&<те же фильтры>|query_id=q_7f3a
  → файл (Content-Disposition: attachment; filename="zones_2026-09-26.geojson")
CSV зон — колонки (порядок фиксирован):
  zone_id,scene_id,mission,datetime,status,area_km2,detected_area_m2,concentration_items_km2,
  conc_lo,conc_hi,interval,unit,measurement_profile,size_class,target_scope,prob_mean,
  valid_fraction,cloud_fraction,flags,n_linked_samples,linked_sample_ids,centroid_lon,centroid_lat,kind
CSV наблюдений — колонки исходного CSV организаторов (как есть) + kind=measurement + linked_scenes.
CSV пар — все поля раздела 5 (geometry → WKT).
Фронтовый запасной экспорт: если /export недоступен — собрать GeoJSON/CSV из того, что уже
загружено на клиенте (Blob + download), с теми же колонками.

====================================================================
10. МОК-ДАННЫЕ (чтобы начать без бэкенда)
====================================================================
В архиве FRONTEND_MOCKS.zip:
  meta.json                 — раздел 2 (реальные словари)
  observations.geojson      — ВСЕ 935 реальных строк CSV организаторов в формате раздела 3
                              (без linked_scenes). Это настоящие данные, их можно показывать.
  zones.mock.geojson        — ВЫДУМАННЫЕ зоны рядом с реальными точками (по 1–3 на событие,
                              все 5 статусов). Только для вёрстки! На защите — только реальные.
  pairs.mock.json, scenes.mock.json, metrics.mock.json — структура разделов 4, 5, 7, цифры фейковые.
Исходник: case/data/macroplastic_marine_samples.csv (+ README_macroplastic_dataset.md).

====================================================================
11. UX-ТРЕБОВАНИЯ КОМАНДЫ (из прошлых ревью)
====================================================================
- Подложка по умолчанию: спутник (+ глобус, если делаешь 3D). Если тайлы не грузятся —
  автоматически офлайн-подложка (простая заливка суша/вода из локального GeoJSON), без ошибок.
- Выбор пользователя (подложка, слои, фильтры, позиция карты) сохраняется: URL + localStorage (try/catch).
- Первый экран — не больше ~15 слов текста. Цифры и подписи — по наведению/клику.
- ≥ 55 fps при панорамировании: GeoJSON > 2000 фич — через WebGL-слой (deck.gl / MapLibre circle layer),
  не DOM-маркеры.
- Никаких «сгенерированных» деталей (не писать «бутылка», если модель этого не знает).
- Пустой результат фильтра → «Нет наблюдений за выбранные даты» + кнопка «Сбросить фильтры».
- Ошибка API → плашка с error.message, карта остаётся рабочей.
- Легенда всегда видна (компактно): цвета статусов, «сплошная = измерение, пунктир = модель»,
  шкала концентрации с единицей шт./км².
- Стек на твой выбор; советуем React + MapLibre GL (+ deck.gl). Без внешних платных ключей.
- Запуск: `npm i && npm run dev`; сборка в dist/, которую бэкенд может отдать статикой (/app).

====================================================================
12. ГДЕ ЛЕЖИТ И КАК СИНХРОНИЗИРУЕМСЯ
====================================================================
- Репо: github.com/azama2t/final-cosmohack (private). Твой фронт — папка frontend_alt/ (отдельно,
  наш не трогаем, наш — service/frontend_v2). Коммиты только в свою папку, push в main, без force.
- Контракт в репо: docs/CONTRACTS_V3.md (этот текст). Если нужно поле — пиши, добавим (не переименуем).
- Проверка бэка: GET /health → {"ok": true}; GET /api/v3/meta.

====================================================================
3.2 (ДОБАВЛЕНИЯ, 25.09 19:30) — только новые поля, старые не переименованы
====================================================================
Смысл слоёв (решение команды 25.09):
  observations — «Полевые измерения» (настоящие шт./км²);
  zones        — «Проверенные снимки-кандидаты»: полосы обследования на снимке вокруг полевого измерения.
                 Это НЕ зоны скопления мусора;
  detections   — «Подозрительные пиксели детектора»: контуры найденных пикселей, не полосы.
Класс MARIDA Marine Debris = любой плавающий мусор, не только пластик.

GET /api/v3/meta — новые поля:
  "summary":  { "n_strips": 29, "n_confirmed_pairs": 0, "plastic_scenes": 0,
                "text": "29 обследованных участков со снимками-кандидатами; 0 подтверждённых пар; для пластика снимков нет" }
  "detector": { "model": "...", "class": "MARIDA Marine Debris",
                "note": "класс MARIDA Marine Debris = любой плавающий мусор, не только пластик" }
  "layers":   [ {id, kind, label} × 3 ]   // подписи слоёв для легенды
  "reject_reasons": + DRIFT_TOO_LARGE «Дрейф за разрыв времени больше допуска (несинхронно)»,
                    + TIME_UNKNOWN «Время наблюдения неизвестно (окно по суткам)»,
                    + MISSION_NOT_TARGET, PROCESSING_ERROR
  "quality_classes[]": + "codes" (коды quality.tif), "present" (встречается ли класс в масках), "note"

GET /api/v3/observations — новые properties:
  "source_short", "source_license"
  "n_items", "ci95_lo", "ci95_hi", "ci95_method", "ci95_reason"
      // эталон C = N/A и его 95 % ДИ Пуассона; null + ci95_reason, если N или A нет
  "model_estimate": { value, lo, hi, unit, model, profile_config, fold, interval_coverage_cv,
                      note: "прогноз по CV вне обучающего участка",
                      status: "исследовательская модель, на test не лучше медианы" } | null
  "linked_scenes_unsynced": [scene_id]    // снимки, на которых образец проверен, но пара отклонена
  "target_scope_label", "is_plastic_scope"

GET /api/v3/pairs — новые поля пары:
  "tolerance_km", "drift_scenarios_km": {low, typical, high}, "dt_drift_hours",
  "status_without_drift", "dt_uncertainty_h" (12, если time_known=false), "time_note"
  ("drift_shift_km" теперь заполнен из реестра)

GET /api/v3/zones — новые поля FeatureCollection: "layer_kind": "candidate_strip", "label".
Новые properties зоны (старые "status" и "detection_status" остаются):
  "layer_kind": "candidate_strip", "layer_label": "Снимок-кандидат: полоса обследования"
  "detection_status": "detected" | "not_detected" — ТОЛЬКО при принятой (синхронной) паре.
                      Если пара отклонена → "insufficient_data" + "detection_reason":
                      «связь снимка с полевым измерением не подтверждена: <причина>»
  "detector_verdict": результат детектора по полосе как есть (detected | not_detected | insufficient_data)
  "suspicious_pixels": { n_objects, area_m2, prob_max,
                         note: "подозрительные пиксели на снимке-кандидате, без полевого подтверждения" } | null
  "pair_status", "pair_reject_reasons", "pair_sync" (synchronous | unsynchronized), "pair_drift_shift_km",
  "pair_tolerance_km", "pair_time_known", "pair_dt_uncertainty_h",
  "registry": {status, stage, status_without_drift}
  "area_km2" = геодезическая площадь полигона geometry; "strip_area_raster_km2" — площадь полосы по пикселям
  "field_estimate.model" = "median_train", если основная модель на отложенном test не лучше медианы
      (подпись "label": «оценка по полевым данным, не по снимку: медиана профиля; модели по координатам/сезону
      на отложенном test не лучше медианы»)
  "concentration" = null всегда (переноса «снимок → шт./км²» нет), "concentration_status" = "unavailable"
Фильтры /zones: + source, scope, detection_status, concentration_status.
GET /api/v3/zones/{id}: + "detections" (FeatureCollection слоя detections).

Слой detections: GET /api/v3/export?layer=detections&format=geojson|csv (те же фильтры, что у зон, или query_id).
  Feature.properties: kind="detection", label, det_id, zone_id, scene_id, datetime, n_pixels, area_m2,
                      prob_max, prob_mean, threshold, in_strip
  CSV: det_id,zone_id,scene_id,datetime,n_pixels,area_m2,prob_max,prob_mean,in_strip,threshold,centroid_lon,centroid_lat,kind

CSV зон: после колонок раздела 9 дописаны detection_status, concentration_status, field_estimate_items_km2,
  layer_kind, detector_verdict, detection_reason, suspicious_n_objects, suspicious_area_m2, pair_status,
  pair_reject_reasons, strip_area_raster_km2.
CSV пар: дописаны tolerance_km, dt_drift_hours, status_without_drift, dt_uncertainty_h.

GET /api/v3/metrics:
  detector: { main, baseline, fdi, rows[] } — на одном test MARIDA: precision, recall, f1, iou, ci95_* для каждой строки;
            main = LightGBM, baseline = RandomForest (код MARIDA), fdi = FDI-порог.
  concentration: + "final_test" (файл как есть), "final_test_summary"[profile] = { n_test, main{mae,rmse,mae_log,
            coverage}, baseline{…}, delta_mae, delta_mae_ci95, main_better_significant, decision },
            "final_test_status": «посчитан один раз 25.09» | «будет посчитан один раз в приёмке».
Сохранённый запрос: + "scopes": [target_scope]. Неизвестные поля в query или в теле → 422 BAD_PARAM,
  details.unknown и details.allowed.

3.3 (ДОБАВЛЕНИЯ, 25.09 20:40) — «медиана на карте»
GET /api/v3/observations, properties:
  "field_estimate": { value, lo, hi, unit:"items/km2", measurement_profile, profile_config, model:"median_train",
                      interval, interval_nominal: 0.9, interval_coverage_test, interval_coverage_cv, n_test,
                      basis:"field_model",
                      note:"оценка по полевым данным, не по снимку; модели по координатам/сезону на отложенном test не лучше медианы" }
      // у КАЖДОЙ записи профилей S2_visual_total_plastic / S1_trawl_total_plastic (dev, test, буфер); у остальных null
  "research_estimate": { …как model_estimate…, note:"исследовательская модель, на отложенном test не лучше медианы; …" } | null
  "model_estimate": алиас research_estimate (оставлен для совместимости)
CSV наблюдений: + field_estimate_items_km2, field_estimate_lo, field_estimate_hi, field_estimate_model,
  field_estimate_coverage_test, research_estimate_items_km2, research_estimate_lo, research_estimate_hi,
  research_estimate_model, research_estimate_fold (в конце).
GET /api/v3/metrics: concentration.profiles[p].map_field_estimate =
  { model, value, lo, hi, interval, interval_coverage_test, interval_coverage_cv, n_test, note }.

3.6 (ДОБАВЛЕНИЯ, 25.09 23:00)
GET /api/v3/zones, properties зоны:
  "quality_decision": "accept" | "reject"   // решение масок качества полосы (data/pairs/pair_quality.csv decision; error → reject)
  "quality_reject_reason": "cloud" | "glint" | "insufficient_coverage" | … | "error" | null (null при accept)
  "quality_reject_label": русская подпись причины | null
  "quality_reason": как в pair_quality.csv (оставлено)
  "suspicious_pixels.quality_rejected": true, если полоса отклонена масками качества (детекции вероятно ложные)
CSV зон: + quality_decision, quality_reject_reason (в конце).
GET /api/v3/export?layer=detections: + properties.quality_rejected.
GET /api/v3/meta.summary: + n_strips_quality_ok, n_suspicious_in_quality_ok_strips, n_suspicious_in_quality_rejected_strips.
GET /api/v3/metrics.concentration: + selected {profile: "median_train"}, profiles[p].selected, profiles[p].selected_reason;
  field_estimate.scenarios {p25, p50, p75, n, unit, note} — квартили обучающего профиля.
3.6 (продолжение, 25.09 23:50) — строгие параметры (жюри-4, Т5)
  Неизвестный query-параметр у любого GET /api/v3/* → 400 BAD_PARAM,
    details {unknown: [...], allowed: [...], aliases: {...}}.
  Допустимые имена:
    /observations: bbox, date_from, date_to, source, profile, scope, record_type, geometry, limit, offset
    /pairs:        sample_id, scene_id, status, max_dt_hours, date_from, date_to, source, limit, offset
    /scenes:       bbox, date_from, date_to, mission, status, limit, offset
    /zones:        bbox, date_from, date_to, scene_id, status, profile, min_area_km2, source, scope,
                   detection_status, concentration_status, limit, offset
    /observations/{id}: geometry
    /meta, /metrics, /queries, /queries/{id}, /queries/{id}/run, /zones/{id}, /scenes/{id}[/*.png]: без параметров
    /export: layer, format + фильтры своего слоя без limit/offset; при query_id — только layer, format, query_id, geometry
  Явные алиасы (имена полей сохранённого запроса): sources→source, profiles→profile, scopes→scope,
    statuses→status, missions→mission. Алиас и основное имя одновременно → 400.
  limit/offset работают у /observations, /pairs, /scenes, /zones. Ответ: count (на странице), total (всего), offset, limit.
  export?query_id= с пустым значением → 400 BAD_PARAM (раньше выгружалось всё).

3.8 СТУДИЯ (ДОБАВЛЕНИЯ, 26.09 01:00, L95) — новые эндпоинты, старые не менялись
Код: service/routes_v3_studio.py (маршруты встраиваются в router routes_v3 перед его catch-all). Тесты: tests/test_api_studio.py.
Типы и загрузчик для фронта v3: service/frontend_v3/src/views/views.ts.
Общие правила как в разделе 1 и 3.6: JSON «application/json; charset=utf-8», ошибки {"error": {code, message, details}},
неизвестный параметр → 400 BAD_PARAM {unknown, allowed, aliases}; алиасы sources→source, levels→level, views→view.

GET /api/v3/studio/scenes?bbox=&date_from=&date_to=&source=&level=&view=&limit=&offset=
  Все сцены С РЕАЛЬНЫМИ ФАЙЛАМИ (каналы, маски, вероятности) в области/датах. Источники (source_type):
    pair   — снимки пар кейса data/pairs/quality/<event>/ (+ FDI data/pairs/experiment/<event>/fdi.npy)
    live   — сцены районов data/live/<region>/<date>/ (bands.tif 12 каналов L2A, scl.tif, prob_lgbm/prob_mdd.tif);
             даты service/data/<region>/<date>/ без сырья — только «Снимок» из готового rgb.png (EPSG:4326)
    drift  — data/drift_check/<region>/<date>/ (формат live)
    search — розыск data/search/<src>/**/<dir>/ с meta.json|scene.json и хотя бы одним растром (cache/src/code пропускаются)
  source: список pair,live,drift,search; level: A,B,C,D,none; view: сцены, у которых есть ВСЕ перечисленные виды.
  Ответ: {version:"3.8", count, total, offset, limit, empty_reason, kinds:[{id,label,overlay}], source_types, levels,
          by_source_type:{pair:n,…}, scenes:[Scene]} — сортировка по datetime, затем id.
  Scene: id ("pair.<dir>" | "live.<region>.<date>" | "drift.<region>.<date>" | "search.<src>.<путь через ~>"),
    source_type, source_label, title, datetime (ISO UTC), date, mission, platform ("Sentinel-2A"…), catalog
    ("earth-search"…), collection ("sentinel-2-l2a"…), product_id, tile, region, event_id,
    level ("A"|"B"|"C"|"D"|null), level_match ("event_id+scene_id"|"scene_id"|null),
    coordinates [[lon,lat]×4: tl,tr,br,bl — углы сетки растра, порядок image-source MapLibre], bounds [w,s,e,n], crs,
    size_px [w,h], pixel_m, views {rgb,spectral,detection,quality: View}, available_views [kind…],
    quality {decision, reason, valid_water_frac, cloud_frac, …}|null, detector {threshold, n_det|n_above_threshold, …}|null,
    field {sample_ids, field_items_km2, obs_datetime, dt_hours}|null, links {v3_scene, dir, detections_lgbm, …},
    footprint_note ("вырезка вокруг наблюдения, не вся сцена" и т.п.).
  View: {available, label ("Снимок"|"Спектральный"|"Детекция"|"Качество"), overlay (true у detection/quality — PNG
    с прозрачностью поверх «Снимка»), url|null, source (из каких файлов/каналов), variants|null, reason|null (почему нет)}.
  В списке нет View.legend и level_records — они в GET /scenes/{id}.
  Фронт показывает в переключателе ТОЛЬКО available_views (пустых переключателей нет).

GET /api/v3/studio/scenes/{id} — Scene + level_records [{level, event_id, scene_id, reason, file}] + View.legend
  (detection: {type:"probability", threshold, items}; quality: {type:"classes", palette:"meta.quality_classes", items})
  + timeline [{id, datetime, source_type, platform, level, available_views}] — все сцены того же event_id (или региона).
  Параметров нет. Нет сцены → 404 NOT_FOUND.

GET /api/v3/studio/scenes/{id}/view/{kind}.png?px=&variant=
  kind: rgb | spectral | detection | quality (другое → 400 BAD_PARAM). px: 64..4096, по умолчанию 1024 — максимум
  стороны, без увеличения. variant: spectral — fdi (по умолчанию) | swir (только при bands.tif); detection — lgbm
  (по умолчанию) | mdd (если есть prob_mdd.tif); недопустимый для сцены → 400 BAD_PARAM {allowed}.
  Все виды сцены — в одной сетке (coordinates сцены).
    rgb       — bands.tif B4,B3,B2, 0..0.16, гамма 1/1.8 (NaN → прозрачно); у пар — rgb.png pair_quality.py.
    spectral  — FDI (Biermann 2020, src/macroplastic/indices.fdi: B6, B8, B11), палитра magma, растяжка p2..p99.5 по
                пригодной воде (quality код 1 / SCL 6); swir — B11,B8,B4, p2..p98 по каналу. У пар — fdi.npy тех же вырезок.
    detection — вероятность детектора: P ≥ порога → #ff2d55f2; 0.2 ≤ P < порога → жёлтый полупрозрачный; иначе
                прозрачно. При уменьшении — максимум по блоку (одиночные пиксели не теряются). Порог: meta.detector.
                threshold (пары) / prob_lgbm.json threshold (районы); нет порога → 0.5 и "(default)" в X-View-Scale.
    quality   — классы качества в палитре /meta.quality_classes: quality.tif (пары, розыск) или SCL (районы:
                6 вода, 4/5 суша, 3/8/9/10 облако, 0 нет данных, прочее — «нет данных / непригодно»; бликов нет).
  Ответ 200 image/png; заголовки X-Evidence-Level (A–D|none), X-Scene-Datetime, X-View-Kind, X-View-Variant,
  X-View-Size ("WxH"), X-View-Scale, X-Detection-Pixels (detection), X-Cache (render|mem|disk), ETag
  (If-None-Match → 304), Cache-Control: public, max-age=3600; CORS с Access-Control-Expose-Headers для X-*.
  Нет файла для вида → 404 {"error": {"code": "NO_VIEW", "message", "details": {scene_id, kind, reason,
  available_views}}}. Кэш PNG: память (96) + диск out/studio_cache/<id>/ (MACROPLASTIC_STUDIO_CACHE), ключ включает
  mtime/размер исходных файлов.
  Уровень доказательности — ТОЛЬКО из data/search/*/candidates.csv (поле level; сначала event_id+scene_id, затем
  scene_id; при нескольких записях — сильнейший, все записи в level_records). Нет записи → null («не оценивался»).

3.9 НЕФТЬ (ДОБАВЛЕНИЯ, 26.09 02:00, L101, INBOX §14) — новые эндпоинты, старые не менялись. ЭКСПЕРИМЕНТАЛЬНЫЙ слой
Код: service/routes_v3_oil.py (маршруты встраиваются в router routes_v3 перед его catch-all, как 3.8). Тесты: tests/test_oil.py.
Данные — только с диска: data/case/oil/index.json + data/case/oil/<scene_key>.geojson (scripts/oil/train_oil.py infer;
scene_key = live.<region>.<date> | pair.<event_dir>, те же id, что у студии без префикса типа). Описание — docs/OIL.md.
Общие правила как в 1, 3.6, 3.8: charset=utf-8, ошибки {"error": {code, message, details}}, неизвестный параметр → 400
BAD_PARAM {unknown, allowed}; bbox/даты — как в 3.6 (400 BAD_BBOX / BAD_DATE); нет данных → 200 + пусто + empty_reason; CORS *.
Единица — ПЛОЩАДЬ пятна, км² (+ доля пикселей наблюдаемой воды сцены); «площадь, не объём/масса»; штуки к нефти НЕ применяются.

GET /api/v3/oil/meta  (параметров нет)
  {class:"oil_spill", class_label:"Нефтяное пятно", experimental:true, unit:"km²", unit_note, counts_applicable:false,
   color:{fill, fill_opacity, line, line_width, note}, model, selected_run, threshold, min_px, harmonize,
   gates:{min_valid_water_frac, max_cloud_frac, max_scene_frac, note},
   metrics:{dataset, val, val_ci95, test, test_ci95, baseline_osi}, n_scenes, generated_at, domain_note, limitations[], doc}

GET /api/v3/oil/scenes?bbox=&date_from=&date_to=&region=&kind=&scene_id=&limit=&offset=
  kind: live,pair; scene_id: список scene_id ИЛИ scene_key. Ответ {items, total, limit, offset, class, unit_note, empty_reason};
  item = {scene_key, scene_id, date, region, kind, source, class, experimental, status, status_reason, oil_km2,
          oil_frac_water, n_spills, water_km2, valid_water_frac, bbox[4] (EPSG:4326), unit_note}.
  status: ok | no_estimate_low_water (пригодной воды < 50 % нессушной части кадра) | skipped_cloudy (облака+тени > 0.2)
          | suspect_scene_wide (> 5 % воды отмечено — дымка/блик, не карта пятен). При status ≠ ok: oil_km2,
          oil_frac_water, n_spills = null — «нет оценки», НЕ 0; фронт показывает status_reason. 0 бывает только при ok.

GET /api/v3/oil/spills?bbox=&date_from=&date_to=&region=&kind=&scene_id=&min_area_km2=&limit=&offset=
  application/geo+json; charset=utf-8. FeatureCollection {features, total, count, class, class_label, experimental,
  total_area_km2 (по странице), unit_note, color, empty_reason}. Feature: Polygon | MultiPolygon, EPSG:4326, id "oil.<hash>";
  properties {id, class:"oil_spill", class_label, scene_id, scene_key, date, region, source, experimental(bool),
  area_km2, n_px, scene_frac (доля пикселей наблюдаемой воды сцены), prob_mean, lon, lat (центр), model, unit_note}.
  bbox — пересечение рамки полигона; сортировка: площадь по убыванию, затем id. limit по умолчанию 1000 (все сцены
  сразу ≈ 13 000 полигонов / 29 МБ — запрашивайте по scene_id или bbox), максимум 100000; offset — как в 3.6.

GET /api/v3/oil/export?format=geojson|csv&<фильтры spills>
  Отдельный класс: Content-Disposition: attachment; filename="oil_spill.<fmt>"; limit по умолчанию 100000 (всё). geojson — как /oil/spills;
  csv (text/csv; charset=utf-8) — колонки id, class, class_label, scene_id, scene_key, date, region, source, experimental,
  area_km2, n_px, scene_frac, prob_mean, lon, lat, model, unit_note. Неверный format → 400 BAD_PARAM.
Цвет слоя (предложение для фронта v3, не пересекается с мусором #ff2d55/оранжевым/жёлтым и дрейфом #9ad0ff):
  заливка #c026d3 (фуксия) 0.45, контур #f0abfc 1.5 px; подпись «Нефтяное пятно · эксперимент · площадь, не объём/масса».
3.8.1 СТУДИЯ: ДЕТЕКТОР ТОЛЬКО В ТЕКУЩЕМ РЕЖИМЕ (26.09 02:00, L95)
  Scene.detector (всегда объект): {run, weights:"lgbm"|null, harmonization:false|null, threshold (0.63 — weights/lgbm,
    как у пар, configs/case_pairs.yaml harmonize: none), pixels, objects (вся вырезка, после фильтров облака/тени),
    scope:"вся вырезка", strip {objects, pixels}|null (пары: в полосе наблюдения), source, label (готовая подпись
    по-русски: «Детектор (текущий режим: веса lgbm, без гармонизации, порог 0,63): N объектов, M пикселей на всей
    вырезке[; в полосе наблюдения — K объектов]» или «Детектор на этой сцене не запускался: <причина>»),
    legacy {weights:"lgbm_live", harmonization:"water_median", threshold, note:"прежний режим …, отвергнут"}|null}.
  Прежние поля detector (n_det, n_det_crop, prob_max, n_above_threshold, harmonize, note) УДАЛЕНЫ (раздел 3.8 был
    черновым 2 ч, потребитель один — фронт v3). Вид «Детекция» строится только из текущего режима:
    пары/розыск — prob.tif прогонов с harmonize none (meta.detector.harmonize_offset = null); районы/дрейф —
    out/studio_cache/detector_current/<live|drift>/<region>/<date>/ (scripts/case/studio_detector_current.py;
    правило маски = pair_quality.py). prob_lgbm.tif/prob_mdd.tif data/live не показываются никогда; нет расчёта
    текущего режима → вида нет, reason «…ещё не рассчитан; прежний режим не показывается». variant у detection убран.
    X-Detection-Pixels = detector.pixels. Красный — пиксели объектов детектора; жёлтый — 0,2 ≤ P < порога на воде.
  Районы/дрейф с расчётом текущего режима: «Качество» — маска как у пар (облака SCL + спектральный тест, блики).
  quality: + decision_label, reason_label (по-русски). level_records[]: reason — по-русски (текст реестра, если он
    кириллицей, иначе определение уровня), reason_raw — текст реестра как есть. field.sampling_method удалено.
3.8.2 СТУДИЯ: ОСВЕЩЁННОСТЬ, ПОЛЕВОЙ СЧЁТ, RGB ПО ВОДЕ (26.09 03:10, L95)
  Scene.illumination {sun_zenith_deg (расчёт по времени и центру сцены, NOAA), water_b3_median (медиана B3 пригодной
    воды; считается только если может изменить вывод), low_sun, weak_signal, rule}.
  Детектор «не оценивается» при зените Солнца ≥ 58° или B3 воды < 0.003: detector.status = "not_evaluated",
    pixels/objects/strip = null, числа — в detector.raw {pixels, objects, strip, note: «шум, не мусор»}, label
    «Детектор не оценивается: низкое солнце (зенит 61°) — …»; вида «Детекция» нет (reason тот же).
    detector.status: "evaluated" | "not_evaluated" | "not_run".
  Числа детектора = реестр розыска: detector.strip {objects, pixels} = candidates.csv n_det_strip / det_pixels
    (ADIS: 157/157 совпадают); detector.objects/pixels — вся вырезка (scope «вся вырезка»).
  Scene.field для сцен с уровнем из реестра с полевыми числами (ADIS: field_count_by_size, survey_area_km2,
    dhat_5cm_km2; S3: field_items_km2): + count, count_by_size {"> 5 см": n, …}, size_class, survey_area_km2,
    items_km2, field_source, label («Полевой счёт: 1 предмет > 5 см на 0,56 км² обзора; оценка 3,5 шт./км² (…)»).
  «Снимок»: variants ["water", "natural"]; water (по умолчанию) — растяжка 0..p98 по пикселям пригодной воды (все три
    канала одной шкалой, гамма 1/1,4; суша может пересвечиваться), natural — прежние 0..0,16, гамма 1/1,8.

3.10 ФОТО (ДОБАВЛЕНИЯ, 26.09 05:15, L109, INBOX §15 «Агент 5») — новые эндпоинты, старые не менялись. ОТДЕЛЬНЫЙ МОДУЛЬ, не спутник
Код: service/routes_v3_photo.py (маршруты встраиваются в router routes_v3 перед его GET catch-all, как 3.8/3.9);
модель src/macroplastic/photo_count/; веса weights_exp/photo_count/ (model_card.json + .pth; вне git). Тесты: tests/test_photo_count.py.
Описание, данные, лицензии, метрики — docs/PHOTO_COUNT.md. Общие правила как в 1, 3.6: charset=utf-8, ошибки {"error": {code, message,
details}}, неизвестный параметр → 400 BAD_PARAM {unknown, allowed}; CORS *.
Единица — ШТУКИ НА КАДР. Шт./км² — только если клиент передал известную площадь воды в кадре (frame_area_m2); подпись
«на площади кадра, не спутник». Тип съёмки — камера у воды (надводный аппарат, вид от первого лица); дроны и спутник — не проверено.

GET /api/v3/photo/meta  (параметров нет)
  {module:"photo_count", task_note, survey, available:bool, unit:"штук на кадр", density_note:"на площади кадра, не спутник",
   limitations[], upload:{method, path, body, max_bytes, params[]},
   model:null | {version, architecture, threshold, threshold_rule, dataset, source, weights_license,
                 metrics:{test_official?:{n_images, ap50, ap50_ci95, count_mae, count_mae_ci95, count_exact, count_exact_ci95},
                          test_grouped?:{…те же поля…, note}}, caveat}}

POST /api/v3/photo/count?threshold=&frame_area_m2=
  Тело: байты изображения (Content-Type image/jpeg|png|webp|application/octet-stream) ИЛИ multipart/form-data с полем file
  (python-multipart не нужен). ≤ 25 МБ, сторона ≤ 8000 px; EXIF-поворот учитывается.
  threshold ∈ [0, 1] (по умолчанию — порог модели, выбранный на val FML); frame_area_m2 > 0 (м², необязательно).
  200 {count:int, unit:"штук на кадр", threshold, threshold_default, model_version, image:{width, height},
       boxes:[{x1, y1, x2, y2 (px исходного фото после EXIF-поворота), score, label:"мусор"}],
       density: null | {items_per_km2, frame_area_m2, note:"на площади кадра, не спутник"}, density_reason: null | str,
       survey, limitations[], device:"cuda"|"cpu", elapsed_ms}
  Ошибки: 400 BAD_BODY (пусто / multipart без файла), 400 BAD_PARAM, 413 TOO_LARGE, 415 BAD_IMAGE,
          503 MODEL_UNAVAILABLE (нет weights_exp/photo_count/model_card.json или весов).
  Фронт v2: режим ?mode=photo (service/frontend_v2/src/photo/PhotoApp.tsx), кнопка «Фото» в переключателе режимов;
  клиент шлёт threshold=0.05 и фильтрует рамки ползунком порога локально.

3.10 СПУТНИКОВЫЕ ЗОНЫ (ДОБАВЛЕНИЯ, 26.09 05:30, L111, INBOX §15) — новый слой; /zones (полосы) не менялся, кроме kind
Код: service/case_store.py (раздел «satellite scene zones»), service/routes_v3.py; данные строит scripts/case/scene_zones.py
→ data/case/scene_zones/{index.json, <scene_key>/{zones.geojson, detections.geojson, scene.json, rgb.jpg, quality.png, crops/*.jpg}}.
Тесты: tests/test_api_v3_scene_zones.py. Сцены: отложенная сцена Cózar 2024 (30SXE 11.03.2021, reports/case_demo/heldout_scene.md),
районы data/live и data/drift_check; детектор в текущем режиме (weights/lgbm, без гармонизации, порог 0.63); сцены, где детектор
«не оценивается» (src/macroplastic/case/illumination.py), зон не дают.
ИЗМЕНЕНИЕ значения (не поля): у полос /zones properties.kind и FeatureCollection.kind = "candidate_strip" (было "model_estimate";
концентрация у полос всегда null — это не оценка модели). CSV зон: колонка kind = candidate_strip.
GET /api/v3/scene_zones?bbox=&date_from=&date_to=&status=&detection_status=&concentration_status=&scene_kind=demo,live,drift&scene_key=&limit=&offset=
  FeatureCollection {kind:"detection_zone", layer_kind:"scene_zone", count, total, offset, limit, empty_reason, model{weights, sha256,
  trained_at (дата файла model.txt), threshold, harmonize}, rules, status_note, blocks_note, examples[], features}.
  Feature.properties: kind "detection_zone", zone_id "SZ-<scene_key>-NNN" (-000 = вся вырезка, «не обнаружено»), scene_key, scene_kind,
    scene_kind_label, scene_id, title, datetime, detection_status (detected | not_detected | insufficient_data), status (= detection_status),
    verification: level_B_cozar (контур пересекает нить каталога Cózar 2024 — независимая разметка людьми) | unverified | false_alarm_signs,
    detection_label (по-русски: «обнаружено детектором; совпадает с нитью Cózar 2024 (уровень B)» | «срабатывание детектора, не проверено»
    | «недостаточно данных: признаки ложного срабатывания» | «не обнаружено …»), detection_reason, flags [foam, glint, ship, seam, coast,
    shallow], training_scene (MARIDA/MADOS той же съёмки) + training_scene_note, n_cozar_filaments, area_km2 (= measured.zone_area_km2),
    detected_area_m2, concentration = null всегда, concentration_status: research_estimate ТОЛЬКО при verification = level_B_cozar,
    иначе unavailable; concentration_reason; crop_url (снимок | он же с пикселями детектора), crop_note;
    measured {zone_area_km2, suspicious_area_m2, n_pixels, n_objects, water_km2, lwd_m2_km2 (м² на км² пригодной воды, как LWD
      Cózar 2024), quality {valid_water_fraction, cloud_fraction, glint_fraction}, model};
    probable {prob_max, prob_mean, status, signs {foam, glint, ship, seam, coast, shallow: {flag, rule, значения}, note}, n_cozar_filaments,
      cozar_note, context};
    scenario {shown, label «Условный диапазон, ЕСЛИ это мусорная полоса», lo 1e4, typical_lo 1e6, typical_hi 1e7, hi 1e8 (шт./км²),
      size_class, applies_to, assumption, not_what («не доверительный интервал, не измерение и не результат модели»), basis [{value, quote,
      where}] (Cózar et al. 2021, Front. Mar. Sci. 8:571796), source} | {shown:false, label, reason};
    field_nearby {items [{source ADIS, segment_id, ship, date, days_from_scene, distance_km, n_items, area_km2, size_class "> 5 см",
      c_items_km2 = N/A, ci95_lo, ci95_hi (Пуассон)}], nearest_organizer_sample {sample_id, source_id, distance_km}, note «Измерение ≠ оценка…»}.
GET /api/v3/scene_zones/{zone_id} → Feature + detections (FeatureCollection объектов детектора зоны: det_id, n_pixels, area_m2, prob_max,
  prob_mean, threshold, artifact) + scene (запись сцены) + examples [{kind success|false_alarm, label, zone_id, crop_url, title, note}].
GET /api/v3/scene_zones/scenes → {count, scenes [форма /scenes + scene_key, scene_kind, evaluable, not_evaluated_reason, sun_zenith_deg,
  wind10m_ms, water_km2, lwd_m2_km2, by_status, model]}; /scene_zones/scenes/{key}/rgb.jpg | quality.png (EPSG:4326 по bounds);
  /scene_zones/scenes/{key}/crops/{zone_id}.jpg.
GET /api/v3/export?layer=scene_zones&format=geojson|csv&<фильтры scene_zones> | query_id=… — CSV колонки cs.SZ_COLS (zone_id … kind);
  сценарий в CSV только при scenario_shown = true.
GET /api/v3/queries/{id}/run: + scene_zones (FeatureCollection), summary.n_scene_zones (фильтры: bbox, даты, statuses; при фильтре по
  source/profile/scope спутниковые зоны не выдаются — у них нет полевого источника).
GET /api/v3/meta: + detector.version {weights, sha256, sha256_short, trained_at, threshold, harmonize}, + quantity_levels
  {place_time_pairs «пары по месту и времени» n=66, visible_signal, calibration_pairs n=0}, + scene_zone_statuses, + layers[] scene_zones.
GET /api/v3/zones: model + weights_sha256, trained_at = дата файла weights/lgbm/model.txt; объекты детектора полос (detections) +
  type_label / visual_class / false_alarm по визуальной разметке (data/case/pairs_visual_labels.csv), если она есть для вырезки.
