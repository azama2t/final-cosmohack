# Самопроверка согласованности API v3 (2026-09-25T20:33:08)

Проверок: 283; ok: 283; 42.1 с. Изоляция: case_store.PATHS['queries'] -> C:\Users\User\AppData\Local\Temp\selfcheck_miwqspcb\queries.jsonl.

## Запросы

| запрос | query_id | набл. | зон | сцен | пар (принято) | ok | fail | SHA-256 run |
|---|---|---|---|---|---|---|---|---|
| S2 Саргассово море (total_plastic — через selection) | q_3bd89653 | 330 | 0 | 0 | 1320 (0) | 39 | 0 | 1204b4b94c77 |
| Чёрное море, все пары | q_ce99f758 | 33 | 24 | 217 | 596 (0) | 34 | 0 | 576b94eeb883 |
| Северное море, 2016 | q_e138dc88 | 27 | 2 | 9 | 193 (0) | 34 | 0 | 95131a8896a2 |
| ГПМП, трал 5–50 см | q_1fe7ca58 | 83 | 0 | 0 | 1400 (0) | 39 | 0 | 6d5501b2851e |
| Пустой bbox (Южная Атлантика, наблюдений нет) | q_c0959203 | 0 | 0 | 0 | — (—) | 29 | 0 | 07e94b714083 |
| Некорректные даты (задом наперёд) | — | — | — | — | — (—) | 1 | 0 | — |

## Расхождения (для владельцев)

Нет.

## Все проверки

| запрос | проверка | итог | детали |
|---|---|---|---|
| S2_sargasso | POST /queries 201 | ok | 201 |
| S2_sargasso | GET /queries/{id} = POST | ok |  |
| S2_sargasso | run.http_200 | ok | 200/200 |
| S2_sargasso | run.sha256_repeat | ok | 1204b4b94c770225 vs 1204b4b94c770225 |
| S2_sargasso | summary.counts | ok | n_obs=330 n_zones=0 n_scenes=0 |
| S2_sargasso | summary.by_status | ok | {} |
| S2_sargasso | empty.zones_reason | ok | Нет зон под выбранные фильтры |
| S2_sargasso | obs.ids algo=api | ok | {"only_left": [], "only_right": [], "n_left": 330, "n_right": 330} |
| S2_sargasso | obs.sorted_by_id | ok |  |
| S2_sargasso | obs.numbers algo=api (conc, area) | ok |  |
| S2_sargasso | obs.geometry algo=api [lon,lat] EPSG:4326 | ok |  |
| S2_sargasso | obs.kind/profile/scope algo=api | ok |  |
| S2_sargasso | obs.list endpoint = run | ok | 330 vs 330 |
| S2_sargasso | export.obs http/headers | ok | 200/200 |
| S2_sargasso | export.obs ids csv=geojson=json | ok | {"only_left": [], "only_right": [], "n_left": 330, "n_right": 330} |
| S2_sargasso | export.obs geojson = json (features) | ok |  |
| S2_sargasso | export.obs csv numbers = json | ok |  |
| S2_sargasso | selection(S2_visual_total_plastic) ids ⊆ api(scope=total_plastic) | ok | {"only_left": [], "only_right": [], "n_left": 63, "n_right": 63} |
| S2_sargasso | selection.target = api.concentration_items_km2 | ok |  |
| S2_sargasso | predictions.y_true (route_buf1) = api.concentration | ok | n_pred=63 |
| S2_sargasso | units: selection unit = meta.units.concentration | ok | items/km2 |
| S2_sargasso | zones.ids algo(pair_quality)=api | ok | {"only_left": [], "only_right": [], "n_left": 0, "n_right": 0} |
| S2_sargasso | zones.numbers algo=api (area_km2, detected_area_m2) | ok |  |
| S2_sargasso | zones.statuses algo=api (detection, concentration=null) | ok |  |
| S2_sargasso | zones.geometry: Polygon в EPSG:4326, образец внутри полигона | ok |  |
| S2_sargasso | zones.area_km2_vs_polygon | ok | 0/0 зон: area_km2 (растровая полоса) vs геодез. площадь полигона, макс. 0.0% |
| S2_sargasso | export.zones_query_id | ok | run.zones=0, export csv=0, geojson=0; {"only_left": [], "only_right": [], "n_left": 0, "n_right": 0} |
| S2_sargasso | export.zones csv/geojson numbers,statuses,centroid = json | ok |  |
| S2_sargasso | scenes.n_zones/footprint = zones | ok |  |
| S2_sargasso | zone_vs_pair.status | ok | зона с вердиктом детектора на отклонённой паре/снимке |
| S2_sargasso | obs.linked_scenes_vs_zones | ok |  |
| S2_sargasso | pairs.ids json=export csv=geojson | ok | {"only_left": [], "only_right": [], "n_left": 1320, "n_right": 1320} |
| S2_sargasso | pairs.dt_hours algo(candidates)=api | ok |  |
| S2_sargasso | pairs.drift_shift_km | ok | 0 пар: в candidates.csv есть drift_shift_km, в API null |
| S2_sargasso | pairs.status algo(candidates+pair_quality)=api | ok |  |
| S2_sargasso | pairs.reject_reasons_nonempty | ok | 0 отклонённых пар без кода причины |
| S2_sargasso | export.pairs csv = json (dt, drift, status, reasons) | ok |  |
| S2_sargasso | pairs.geometry EPSG:4326 | ok |  |
| S2_sargasso | export.repeat same bytes | ok |  |
| S4_black_sea | POST /queries 201 | ok | 201 |
| S4_black_sea | GET /queries/{id} = POST | ok |  |
| S4_black_sea | run.http_200 | ok | 200/200 |
| S4_black_sea | run.sha256_repeat | ok | 576b94eeb8836486 vs 576b94eeb8836486 |
| S4_black_sea | summary.counts | ok | n_obs=33 n_zones=24 n_scenes=217 |
| S4_black_sea | summary.by_status | ok | {"insufficient_data": 24} |
| S4_black_sea | obs.ids algo=api | ok | {"only_left": [], "only_right": [], "n_left": 33, "n_right": 33} |
| S4_black_sea | obs.sorted_by_id | ok |  |
| S4_black_sea | obs.numbers algo=api (conc, area) | ok |  |
| S4_black_sea | obs.geometry algo=api [lon,lat] EPSG:4326 | ok |  |
| S4_black_sea | obs.kind/profile/scope algo=api | ok |  |
| S4_black_sea | obs.list endpoint = run | ok | 33 vs 33 |
| S4_black_sea | export.obs http/headers | ok | 200/200 |
| S4_black_sea | export.obs ids csv=geojson=json | ok | {"only_left": [], "only_right": [], "n_left": 33, "n_right": 33} |
| S4_black_sea | export.obs geojson = json (features) | ok |  |
| S4_black_sea | export.obs csv numbers = json | ok |  |
| S4_black_sea | zones.ids algo(pair_quality)=api | ok | {"only_left": [], "only_right": [], "n_left": 24, "n_right": 24} |
| S4_black_sea | zones.numbers algo=api (area_km2, detected_area_m2) | ok |  |
| S4_black_sea | zones.statuses algo=api (detection, concentration=null) | ok |  |
| S4_black_sea | zones.geometry: Polygon в EPSG:4326, образец внутри полигона | ok |  |
| S4_black_sea | zones.area_km2_vs_polygon | ok | 0/24 зон: area_km2 (растровая полоса) vs геодез. площадь полигона, макс. 0.0% |
| S4_black_sea | export.zones_query_id | ok | run.zones=24, export csv=24, geojson=24; {"only_left": [], "only_right": [], "n_left": 24, "n_right": 24} |
| S4_black_sea | export.zones csv/geojson numbers,statuses,centroid = json | ok |  |
| S4_black_sea | scenes.n_zones/footprint = zones | ok |  |
| S4_black_sea | zone_vs_pair.status | ok | зона с вердиктом детектора на отклонённой паре/снимке |
| S4_black_sea | obs.linked_scenes_vs_zones | ok |  |
| S4_black_sea | pairs.ids json=export csv=geojson | ok | {"only_left": [], "only_right": [], "n_left": 596, "n_right": 596} |
| S4_black_sea | pairs.dt_hours algo(candidates)=api | ok |  |
| S4_black_sea | pairs.drift_shift_km | ok | 0 пар: в candidates.csv есть drift_shift_km, в API null |
| S4_black_sea | pairs.status algo(candidates+pair_quality)=api | ok |  |
| S4_black_sea | pairs.reject_reasons_nonempty | ok | 0 отклонённых пар без кода причины |
| S4_black_sea | export.pairs csv = json (dt, drift, status, reasons) | ok |  |
| S4_black_sea | pairs.geometry EPSG:4326 | ok |  |
| S4_black_sea | export.repeat same bytes | ok |  |
| S3_north_sea_2016 | POST /queries 201 | ok | 201 |
| S3_north_sea_2016 | GET /queries/{id} = POST | ok |  |
| S3_north_sea_2016 | run.http_200 | ok | 200/200 |
| S3_north_sea_2016 | run.sha256_repeat | ok | 95131a8896a21616 vs 95131a8896a21616 |
| S3_north_sea_2016 | summary.counts | ok | n_obs=27 n_zones=2 n_scenes=9 |
| S3_north_sea_2016 | summary.by_status | ok | {"insufficient_data": 2} |
| S3_north_sea_2016 | obs.ids algo=api | ok | {"only_left": [], "only_right": [], "n_left": 27, "n_right": 27} |
| S3_north_sea_2016 | obs.sorted_by_id | ok |  |
| S3_north_sea_2016 | obs.numbers algo=api (conc, area) | ok |  |
| S3_north_sea_2016 | obs.geometry algo=api [lon,lat] EPSG:4326 | ok |  |
| S3_north_sea_2016 | obs.kind/profile/scope algo=api | ok |  |
| S3_north_sea_2016 | obs.list endpoint = run | ok | 27 vs 27 |
| S3_north_sea_2016 | export.obs http/headers | ok | 200/200 |
| S3_north_sea_2016 | export.obs ids csv=geojson=json | ok | {"only_left": [], "only_right": [], "n_left": 27, "n_right": 27} |
| S3_north_sea_2016 | export.obs geojson = json (features) | ok |  |
| S3_north_sea_2016 | export.obs csv numbers = json | ok |  |
| S3_north_sea_2016 | zones.ids algo(pair_quality)=api | ok | {"only_left": [], "only_right": [], "n_left": 2, "n_right": 2} |
| S3_north_sea_2016 | zones.numbers algo=api (area_km2, detected_area_m2) | ok |  |
| S3_north_sea_2016 | zones.statuses algo=api (detection, concentration=null) | ok |  |
| S3_north_sea_2016 | zones.geometry: Polygon в EPSG:4326, образец внутри полигона | ok |  |
| S3_north_sea_2016 | zones.area_km2_vs_polygon | ok | 0/2 зон: area_km2 (растровая полоса) vs геодез. площадь полигона, макс. 0.0% |
| S3_north_sea_2016 | export.zones_query_id | ok | run.zones=2, export csv=2, geojson=2; {"only_left": [], "only_right": [], "n_left": 2, "n_right": 2} |
| S3_north_sea_2016 | export.zones csv/geojson numbers,statuses,centroid = json | ok |  |
| S3_north_sea_2016 | scenes.n_zones/footprint = zones | ok |  |
| S3_north_sea_2016 | zone_vs_pair.status | ok | зона с вердиктом детектора на отклонённой паре/снимке |
| S3_north_sea_2016 | obs.linked_scenes_vs_zones | ok |  |
| S3_north_sea_2016 | pairs.ids json=export csv=geojson | ok | {"only_left": [], "only_right": [], "n_left": 193, "n_right": 193} |
| S3_north_sea_2016 | pairs.dt_hours algo(candidates)=api | ok |  |
| S3_north_sea_2016 | pairs.drift_shift_km | ok | 0 пар: в candidates.csv есть drift_shift_km, в API null |
| S3_north_sea_2016 | pairs.status algo(candidates+pair_quality)=api | ok |  |
| S3_north_sea_2016 | pairs.reject_reasons_nonempty | ok | 0 отклонённых пар без кода причины |
| S3_north_sea_2016 | export.pairs csv = json (dt, drift, status, reasons) | ok |  |
| S3_north_sea_2016 | pairs.geometry EPSG:4326 | ok |  |
| S3_north_sea_2016 | export.repeat same bytes | ok |  |
| S1_gpgp_trawl | POST /queries 201 | ok | 201 |
| S1_gpgp_trawl | GET /queries/{id} = POST | ok |  |
| S1_gpgp_trawl | run.http_200 | ok | 200/200 |
| S1_gpgp_trawl | run.sha256_repeat | ok | 6d5501b2851e0ca0 vs 6d5501b2851e0ca0 |
| S1_gpgp_trawl | summary.counts | ok | n_obs=83 n_zones=0 n_scenes=0 |
| S1_gpgp_trawl | summary.by_status | ok | {} |
| S1_gpgp_trawl | empty.zones_reason | ok | Нет зон под выбранные фильтры |
| S1_gpgp_trawl | obs.ids algo=api | ok | {"only_left": [], "only_right": [], "n_left": 83, "n_right": 83} |
| S1_gpgp_trawl | obs.sorted_by_id | ok |  |
| S1_gpgp_trawl | obs.numbers algo=api (conc, area) | ok |  |
| S1_gpgp_trawl | obs.geometry algo=api [lon,lat] EPSG:4326 | ok |  |
| S1_gpgp_trawl | obs.kind/profile/scope algo=api | ok |  |
| S1_gpgp_trawl | obs.list endpoint = run | ok | 83 vs 83 |
| S1_gpgp_trawl | export.obs http/headers | ok | 200/200 |
| S1_gpgp_trawl | export.obs ids csv=geojson=json | ok | {"only_left": [], "only_right": [], "n_left": 83, "n_right": 83} |
| S1_gpgp_trawl | export.obs geojson = json (features) | ok |  |
| S1_gpgp_trawl | export.obs csv numbers = json | ok |  |
| S1_gpgp_trawl | selection(S1_trawl_total_plastic) ids ⊆ api(scope=total_plastic) | ok | {"only_left": [], "only_right": [], "n_left": 83, "n_right": 83} |
| S1_gpgp_trawl | selection.target = api.concentration_items_km2 | ok |  |
| S1_gpgp_trawl | predictions.y_true (route_buf1) = api.concentration | ok | n_pred=83 |
| S1_gpgp_trawl | units: selection unit = meta.units.concentration | ok | items/km2 |
| S1_gpgp_trawl | zones.ids algo(pair_quality)=api | ok | {"only_left": [], "only_right": [], "n_left": 0, "n_right": 0} |
| S1_gpgp_trawl | zones.numbers algo=api (area_km2, detected_area_m2) | ok |  |
| S1_gpgp_trawl | zones.statuses algo=api (detection, concentration=null) | ok |  |
| S1_gpgp_trawl | zones.geometry: Polygon в EPSG:4326, образец внутри полигона | ok |  |
| S1_gpgp_trawl | zones.area_km2_vs_polygon | ok | 0/0 зон: area_km2 (растровая полоса) vs геодез. площадь полигона, макс. 0.0% |
| S1_gpgp_trawl | export.zones_query_id | ok | run.zones=0, export csv=0, geojson=0; {"only_left": [], "only_right": [], "n_left": 0, "n_right": 0} |
| S1_gpgp_trawl | export.zones csv/geojson numbers,statuses,centroid = json | ok |  |
| S1_gpgp_trawl | scenes.n_zones/footprint = zones | ok |  |
| S1_gpgp_trawl | zone_vs_pair.status | ok | зона с вердиктом детектора на отклонённой паре/снимке |
| S1_gpgp_trawl | obs.linked_scenes_vs_zones | ok |  |
| S1_gpgp_trawl | pairs.ids json=export csv=geojson | ok | {"only_left": [], "only_right": [], "n_left": 1400, "n_right": 1400} |
| S1_gpgp_trawl | pairs.dt_hours algo(candidates)=api | ok |  |
| S1_gpgp_trawl | pairs.drift_shift_km | ok | 0 пар: в candidates.csv есть drift_shift_km, в API null |
| S1_gpgp_trawl | pairs.status algo(candidates+pair_quality)=api | ok |  |
| S1_gpgp_trawl | pairs.reject_reasons_nonempty | ok | 0 отклонённых пар без кода причины |
| S1_gpgp_trawl | export.pairs csv = json (dt, drift, status, reasons) | ok |  |
| S1_gpgp_trawl | pairs.geometry EPSG:4326 | ok |  |
| S1_gpgp_trawl | export.repeat same bytes | ok |  |
| empty_bbox | POST /queries 201 | ok | 201 |
| empty_bbox | GET /queries/{id} = POST | ok |  |
| empty_bbox | run.http_200 | ok | 200/200 |
| empty_bbox | run.sha256_repeat | ok | 07e94b71408349b8 vs 07e94b71408349b8 |
| empty_bbox | summary.counts | ok | n_obs=0 n_zones=0 n_scenes=0 |
| empty_bbox | summary.by_status | ok | {} |
| empty_bbox | empty.reason | ok | Нет наблюдений под выбранные фильтры |
| empty_bbox | empty.zones_reason | ok | Нет зон под выбранные фильтры |
| empty_bbox | obs.ids algo=api | ok | {"only_left": [], "only_right": [], "n_left": 0, "n_right": 0} |
| empty_bbox | obs.sorted_by_id | ok |  |
| empty_bbox | obs.numbers algo=api (conc, area) | ok |  |
| empty_bbox | obs.geometry algo=api [lon,lat] EPSG:4326 | ok |  |
| empty_bbox | obs.kind/profile/scope algo=api | ok |  |
| empty_bbox | obs.list endpoint = run | ok | 0 vs 0 |
| empty_bbox | export.obs http/headers | ok | 200/200 |
| empty_bbox | export.obs ids csv=geojson=json | ok | {"only_left": [], "only_right": [], "n_left": 0, "n_right": 0} |
| empty_bbox | export.obs geojson = json (features) | ok |  |
| empty_bbox | export.obs csv numbers = json | ok |  |
| empty_bbox | zones.ids algo(pair_quality)=api | ok | {"only_left": [], "only_right": [], "n_left": 0, "n_right": 0} |
| empty_bbox | zones.numbers algo=api (area_km2, detected_area_m2) | ok |  |
| empty_bbox | zones.statuses algo=api (detection, concentration=null) | ok |  |
| empty_bbox | zones.geometry: Polygon в EPSG:4326, образец внутри полигона | ok |  |
| empty_bbox | zones.area_km2_vs_polygon | ok | 0/0 зон: area_km2 (растровая полоса) vs геодез. площадь полигона, макс. 0.0% |
| empty_bbox | export.zones_query_id | ok | run.zones=0, export csv=0, geojson=0; {"only_left": [], "only_right": [], "n_left": 0, "n_right": 0} |
| empty_bbox | export.zones csv/geojson numbers,statuses,centroid = json | ok |  |
| empty_bbox | scenes.n_zones/footprint = zones | ok |  |
| empty_bbox | zone_vs_pair.status | ok | зона с вердиктом детектора на отклонённой паре/снимке |
| empty_bbox | obs.linked_scenes_vs_zones | ok |  |
| empty_bbox | export.repeat same bytes | ok |  |
| bad_dates | POST даты задом наперёд -> 400 BAD_DATE, не сохранён | ok | 400 {"error":{"code":"BAD_DATE","message":"date_from позже date_to","details":{"date_from":"2016-12-31","date_to":"2016-01-0 |
| metrics | metrics.sha256_repeat | ok |  |
| metrics | detector.main P/R/F1/IoU + CI = case_detector test lgbm | ok | api f1 0.8713 vs 0.8712871287128713 |
| metrics | detector.baseline P/R/F1/IoU + CI = case_detector test rf_argmax | ok | api f1 0.7709 vs 0.7708830548926014 |
| metrics | detector.fdi P/R/F1/IoU + CI = case_detector test fdi_threshold | ok | api f1 0.0056 vs 0.005600756859035005 |
| metrics | detector.main.val_f1 = case_detector val lgbm | ok | api 0.9226 vs 0.9226441631504922 |
| metrics | detector.main.test_f1 = lgbm_final_test = case_detector test | ok | api 0.8713 vs 0.8712871287128713 |
| metrics | detector.main.test_iou = lgbm_final_test | ok | api 0.7719 |
| metrics | detector.baseline = RandomForest (MARIDA) on the same test | ok | api RandomForest (код MARIDA) |
| metrics | concentration baseline/main MAE, coverage, CI = dev_cv.json / case_conc_model.yaml (или metrics.json main_split) | ok | профилей 2 |
| metrics | concentration.unit = meta.units | ok | items/km2 |
| metrics | control_example 12 шт / 0.20 км² = 60 | ok | {"items": 12, "area_km2": 0.2, "expected": 60.0, "computed": 60.0} |
| invalid | GET /api/v3/observations?bbox=1,2,3 | ok | 400 BAD_BBOX: bbox: нужны 4 числа minLon,minLat,maxLon,maxLat |
| invalid | GET /api/v3/observations?bbox=abc,1,2,3 | ok | 400 BAD_BBOX: bbox: нужны 4 числа minLon,minLat,maxLon,maxLat |
| invalid | GET /api/v3/zones?bbox=10,0,5,1 | ok | 400 BAD_BBOX: bbox: minLon ≤ maxLon и minLat ≤ maxLat (переход через 180° не поддерживается) |
| invalid | GET /api/v3/scenes?bbox=0,0,200,1 | ok | 400 BAD_BBOX: bbox: долгота в [-180, 180], широта в [-90, 90] |
| invalid | GET /api/v3/observations?date_from=2016-12-31&date_to=2016-01-01 | ok | 400 BAD_DATE: date_from позже date_to |
| invalid | GET /api/v3/zones?date_from=2016-12-31&date_to=2016-01-01 | ok | 400 BAD_DATE: date_from позже date_to |
| invalid | GET /api/v3/pairs?date_from=2016-13-01 | ok | 400 BAD_DATE: date_from: дата в формате ГГГГ-ММ-ДД |
| invalid | GET /api/v3/export?layer=zones&date_from=2016-12-31&date_to=2016-01-01 | ok | 400 BAD_DATE: date_from позже date_to |
| invalid | GET /api/v3/zones?status=foo | ok | 400 BAD_PARAM: status: неизвестные значения foo |
| invalid | GET /api/v3/zones?detection_status=maybe | ok | 400 BAD_PARAM: detection_status: неизвестные значения maybe |
| invalid | GET /api/v3/zones?concentration_status=guess | ok | 400 BAD_PARAM: concentration_status: неизвестные значения guess |
| invalid | GET /api/v3/observations?profile=XYZ | ok | 400 BAD_PARAM: profile: неизвестные значения XYZ |
| invalid | GET /api/v3/observations?source=S9_MARS | ok | 400 BAD_PARAM: source: неизвестные значения S9_MARS |
| invalid | GET /api/v3/observations?scope=everything | ok | 400 BAD_PARAM: scope: неизвестные значения everything |
| invalid | GET /api/v3/observations?limit=0 | ok | 400 BAD_PARAM: limit: нужно целое в [1, 100000] |
| invalid | GET /api/v3/pairs?status=maybe | ok | 400 BAD_PARAM: status: допустимо accepted / rejected / all |
| invalid | GET /api/v3/pairs?max_dt_hours=-1 | ok | 400 BAD_PARAM: max_dt_hours: нужно число в [0, 1000000.0] |
| invalid | GET /api/v3/export?layer=foo | ok | 400 BAD_PARAM: layer: допустимо observations / pairs / zones / detections |
| invalid | GET /api/v3/export | ok | 400 BAD_PARAM: layer: обязателен, observations / pairs / zones / detections |
| invalid | GET /api/v3/export?layer=zones&format=xml | ok | 400 BAD_PARAM: format: допустимо geojson / csv |
| invalid | GET /api/v3/observations/MPL-99999 | ok | 404 NOT_FOUND: Наблюдение MPL-99999 не найдено |
| invalid | GET /api/v3/zones/Z-nope | ok | 404 NOT_FOUND: Зона Z-nope не найдена |
| invalid | GET /api/v3/scenes/NOPE_SCENE | ok | 404 NOT_FOUND: Снимок NOPE_SCENE не найден в реестре |
| invalid | GET /api/v3/scenes/NOPE_SCENE/quality.png | ok | 404 NO_SCENE: Для снимка NOPE_SCENE нет маски качества |
| invalid | GET /api/v3/scenes/NOPE_SCENE/rgb.png | ok | 404 NO_SCENE: Для снимка NOPE_SCENE нет rgb.png |
| invalid | GET /api/v3/queries/q_nope | ok | 404 NOT_FOUND: Сохранённый запрос q_nope не найден |
| invalid | GET /api/v3/queries/q_nope/run | ok | 404 NOT_FOUND: Сохранённый запрос q_nope не найден |
| invalid | DELETE /api/v3/queries/q_nope | ok | 404 NOT_FOUND: Сохранённый запрос q_nope не найден |
| invalid | GET /api/v3/export?layer=zones&query_id=q_nope | ok | 404 NOT_FOUND: Сохранённый запрос q_nope не найден |
| invalid | GET /api/v3/no_such_endpoint | ok | 404 NOT_FOUND: нет такого эндпоинта: /api/v3/no_such_endpoint |
| invalid | POST /api/v3/queries not json | ok | 400 BAD_PARAM: тело: ожидается JSON {"name": ..., "query": {...}} |
| invalid | POST /api/v3/queries {"query": {}} | ok | 400 BAD_PARAM: name: непустая строка до 200 символов |
| invalid | POST /api/v3/queries {"name": "x", "query": {"date_from": "2016-12-31", "date_to": "2016-01-01"}} | ok | 400 BAD_DATE: date_from позже date_to |
| invalid | POST /api/v3/queries {"name": "x", "query": {"bbox": "1,2,3,4"}} | ok | 400 BAD_BBOX: query.bbox: список [minLon, minLat, maxLon, maxLat] или null |
| invalid | POST /api/v3/queries {"name": "x", "query": {"bbox": [1, 2, 3]}} | ok | 400 BAD_BBOX: bbox: нужны 4 числа minLon,minLat,maxLon,maxLat |
| invalid | POST /api/v3/queries {"name": "x", "query": {"statuses": ["foo"]}} | ok | 400 BAD_PARAM: statuses: неизвестные значения foo |
| invalid | POST /api/v3/queries {"name": "x", "query": {"profiles": ["XYZ"]}} | ok | 400 BAD_PARAM: profiles: неизвестные значения XYZ |
| invalid | POST /api/v3/queries {"name": "x", "query": {"scope": ["total_plastic"]}} | ok | 422 BAD_PARAM: query: неизвестные поля scope |
| empty | GET /api/v3/observations?bbox=-30,-50,-29,-49 | ok | 200: Нет наблюдений под выбранные фильтры |
| empty | GET /api/v3/zones?bbox=-30,-50,-29,-49 | ok | 200: Нет зон под выбранные фильтры |
| empty | GET /api/v3/scenes?date_from=2030-01-01 | ok | 200: Нет снимков под выбранные фильтры |
| empty | GET /api/v3/pairs?sample_id=MPL-99999 | ok | 200: Нет пар под выбранные фильтры |
| empty | GET /api/v3/zones?status=research_estimate | ok | 200: Нет зон под выбранные фильтры |
| empty | GET /api/v3/export?layer=zones&format=geojson&bbox=-30,-50,-29,-49 | ok | 200: Нет зон под выбранные фильтры |
| empty | GET /api/v3/export?layer=observations&format=csv&bbox=-30,-50,-29,-49 | ok | 200: только заголовок CSV |
| empty | GET /api/v3/export?layer=pairs&format=csv&sample_id=MPL-99999 | ok | 200: только заголовок CSV |
| live | live run sha256 repeat | ok | bc6955126005dd15 |
| isolation | service/labels/queries.jsonl не изменён | ok | sha256 e3b0c44298fc1c14 |

## Скорость: TestClient

| эндпоинт | n | холодный, мс | p50, мс | p95, мс | max, мс | цель | ок | байт |
|---|---|---|---|---|---|---|---|---|
| GET /meta | 20 | 5.5 | 4.9 | 5.3 | 5.7 | <300 | да | 6168 |
| GET /observations (все 935) | 20 | 250.8 | 248.6 | 264.4 | 274.9 | <300 | да | 1839110 |
| GET /observations?source=S2 | 20 | 83.6 | 87.1 | 103.3 | 113.3 | <300 | да | 696733 |
| GET /observations/{id} | 20 | 3.2 | 2.6 | 3.3 | 3.8 | <300 | да | 5177 |
| GET /pairs (все) | 20 | 55.3 | 55.0 | 58.2 | 63.3 | <300 | да | 3801950 |
| GET /pairs?status=accepted | 20 | 2.5 | 1.9 | 2.1 | 2.3 | <300 | да | 94 |
| GET /scenes | 20 | 32.3 | 30.6 | 40.9 | 41.2 | <300 | да | 130840 |
| GET /scenes/{id} | 20 | 31.9 | 30.5 | 40.7 | 42.8 | <3000 | да | 31169 |
| GET /scenes/{id}/rgb.png | 20 | 1.7 | 1.5 | 1.7 | 1.7 | <3000 | да | 165 |
| GET /scenes/{id}/quality.png | 20 | 217.0 | 1.4 | 1.6 | 2.0 | <3000 | да | 2443 |
| GET /scenes/{id}/mask.png | 20 | 1.6 | 1.5 | 1.6 | 1.7 | <3000 | да | 166 |
| GET /zones | 20 | 6.5 | 6.2 | 6.5 | 6.6 | <300 | да | 99717 |
| GET /zones/{id} | 20 | 6.7 | 6.7 | 9.6 | 11.0 | <300 | да | 15675 |
| GET /metrics | 20 | 3.9 | 3.1 | 4.0 | 4.0 | <300 | да | 19262 |
| GET /export observations csv | 20 | 86.3 | 89.2 | 102.2 | 103.5 | <300 | да | 959949 |
| GET /export zones geojson | 20 | 7.4 | 7.1 | 9.0 | 9.3 | <300 | да | 105235 |
| GET /export pairs csv | 20 | 52.3 | 52.0 | 65.0 | 101.0 | <300 | да | 1416473 |
| GET /export zones csv query_id | 20 | 33.3 | 33.3 | 37.6 | 40.2 | <300 | да | 14996 |
| GET /queries | 20 | 2.1 | 1.7 | 1.9 | 1.9 | <300 | да | 1457 |
| GET /queries/{id} | 20 | 1.6 | 1.6 | 1.7 | 1.8 | <300 | да | 265 |
| GET /queries/{id}/run | 20 | 42.4 | 43.5 | 53.3 | 75.9 | <300 | да | 258358 |
| POST /queries | 20 | 2.0 | 1.7 | 2.0 | 2.2 | <300 | да | 242 |
| DELETE /queries/{id} | 20 | 6.8 | 6.4 | 7.5 | 10.5 | <300 | да | 0 |
| GET /observations?bbox=1,2,3 (400) | 20 | 2.0 | 1.5 | 1.8 | 1.9 | <300 | да | 126 |

## Скорость: живой экземпляр

http://127.0.0.1:8091 старт 1.1 с. 

| эндпоинт | n | холодный, мс | p50, мс | p95, мс | max, мс | цель | ок | байт |
|---|---|---|---|---|---|---|---|---|
| GET /meta | 20 | 387.8 | 15.5 | 26.0 | 33.6 | <300 | да | 6168 |
| GET /observations (все 935) | 20 | 778.3 | 249.3 | 263.1 | 272.5 | <300 | да | 1839110 |
| GET /observations?source=S2 | 20 | 78.3 | 93.7 | 109.3 | 114.5 | <300 | да | 696733 |
| GET /observations/{id} | 20 | 18.2 | 15.5 | 18.7 | 23.0 | <300 | да | 5177 |
| GET /pairs (все) | 20 | 43.9 | 45.5 | 50.6 | 51.5 | <300 | да | 3801950 |
| GET /pairs?status=accepted | 20 | 22.5 | 1.9 | 15.5 | 16.5 | <300 | да | 94 |
| GET /scenes | 20 | 39.2 | 46.4 | 61.0 | 67.4 | <300 | да | 130840 |
| GET /scenes/{id} | 20 | 42.4 | 46.9 | 59.0 | 59.8 | <3000 | да | 31169 |
| GET /scenes/{id}/rgb.png | 20 | 1.3 | 1.5 | 20.0 | 26.1 | <3000 | да | 165 |
| GET /scenes/{id}/quality.png | 20 | 216.2 | 1.4 | 15.4 | 21.4 | <3000 | да | 2443 |
| GET /scenes/{id}/mask.png | 20 | 1.6 | 1.6 | 2.8 | 20.4 | <3000 | да | 166 |
| GET /zones | 20 | 8.4 | 15.5 | 27.2 | 27.4 | <300 | да | 99717 |
| GET /zones/{id} | 20 | 17.6 | 19.5 | 32.3 | 34.9 | <300 | да | 15675 |
| GET /metrics | 20 | 15.6 | 2.4 | 15.8 | 16.1 | <300 | да | 19262 |
| GET /export observations csv | 20 | 92.0 | 94.5 | 109.2 | 114.2 | <300 | да | 959949 |
| GET /export zones geojson | 20 | 4.6 | 15.4 | 26.5 | 26.8 | <300 | да | 105235 |
| GET /export pairs csv | 20 | 71.5 | 59.4 | 76.0 | 94.1 | <300 | да | 1416473 |
| GET /export zones csv query_id | 20 | 59.8 | 45.9 | 56.0 | 66.1 | <300 | да | 14996 |
| GET /queries | 20 | 2.2 | 1.5 | 15.9 | 24.8 | <300 | да | 255 |
| GET /queries/{id} | 20 | 15.5 | 1.3 | 2.3 | 16.0 | <300 | да | 241 |
| GET /queries/{id}/run | 20 | 55.9 | 49.3 | 63.8 | 88.5 | <300 | да | 258334 |
| POST /queries | 20 | 24.6 | 1.5 | 19.8 | 27.1 | <300 | да | 242 |
| DELETE /queries/{id} | 20 | 29.9 | 20.1 | 31.0 | 33.8 | <300 | да | 0 |
| GET /observations?bbox=1,2,3 (400) | 20 | 1.2 | 1.2 | 16.5 | 24.5 | <300 | да | 126 |

UI: {'status': 'skipped', 'reason': '--ui-url не задан'}
