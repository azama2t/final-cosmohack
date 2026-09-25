# FILEMAP — карта проекта (читать первым)

| Путь | Что делает | Кто вызывает |
|---|---|---|
| `docs/CONTRACTS.md` | форматы файлов слоя данных сервиса (manifest, prob, detections, h3, zones, drift, timeseries) | все модули |
| `scripts/make_fixtures.py` | валидные фейковые данные по контрактам за секунды | фронт/API/тесты |
| `scripts/gpu_queue.py` | единственная очередь GPU-задач, лог `out/gpu_queue.log` | обучение, MDD-инференс |
| `src/macroplastic/__init__.py` | константы: классы MARIDA, IGNORE_INDEX, MARINE_DEBRIS, слияние 12..15→7 | все модули |
| `src/macroplastic/utils.py` | ROOT, seed_everything, available_cpus (affinity), cuda_usable, логгер, load_yaml | все модули |
| `src/macroplastic/io.py` | чтение/запись GeoTIFF (C,H,W)+profile, наборы каналов, win_path, read_marida_patch | данные, модели, inference |
| `src/macroplastic/metrics.py` | конфьюжн-матрица по пулу пикселей (ignore=0), P/R/F1/IoU, mIoU, бинарные F1/IoU Marine Debris | обучение, final_numbers |
| `src/macroplastic/splits.py` | официальные сплиты MARIDA, scene_of/parse_patch, группы по сцене | данные, обучение |
| `src/macroplastic/indices.py` | FDI, FAI, NDVI, NDWI, NDMI, SI, BSI, NRD | признаки, fdi_rule |
| `src/macroplastic/models/registry.py` | реестр предикторов (lgbm, mdd, запасной fdi_rule) с ленивой загрузкой | inference.py, живые сцены |
| `configs/channels.yaml` | порядок каналов наборов marida / s2_l2a_12 / mdd_input | io |
| `inference.py` | CLI: папка GeoTIFF → `<name>_prob.tif` (uint8) и `<name>_mask.tif`; коды выхода 0/1/2 | пользователь, QA |
| `tests/test_core.py`, `tests/conftest.py` | тесты ядра (13), фикстура MARIDA (skip без данных) | pytest |
| `pyproject.toml`, `requirements.txt` | настройки pytest; список пакетов | установка |
| `service/__main__.py` | `python -m service [--port --data-root]` → uvicorn | пользователь |
| `service/app.py` | FastAPI: /health, /api/*, /data/*, /assets/*, SPA-фоллбэк, GZip | фронт |
| `service/core.py` | выбор корня данных, кеш JSON, KPI/compare/diff, экспорт CSV/GeoJSON, safe_path | app.py |
| `tests/test_api.py` | тесты API на временных фикстурах | pytest |
| `scripts/serve.ps1` | однострочный запуск сервиса | пользователь |
| `src/macroplastic/data/marida.py` | загрузчик MARIDA: патчи по сплитам, (img, cl, conf, profile), CLASS_NAMES, merge_water, iter_split | обучение, EDA |
| `scripts/eda_marida.py` | EDA MARIDA → `reports/eda/` (eda.md + 8 PNG), `reports/marida_scenes.csv`, `reports/marida_regions.md` | вручную |
| `scripts/check_mados_overlap.py` | пересечения сцен MARIDA↔MADOS по тайлу+дате | эксперименты с MADOS |
| `reports/eda/`, `reports/marida_scenes.csv`, `reports/marida_regions.md`, `reports/sources.md` | EDA, таблица 63 сцен, регионы по MD px, источники и лицензии | отчёт, выбор регионов |
| `README.md.tmpl` → `README.md` | шаблон README (кейс первым, подготовительный этап — в `docs/PREP.md`); README генерирует `scripts/render_docs.py` (правь только .tmpl) | render_docs |
| `reports/report.md.tmpl` → `reports/report.md` | шаблон отчёта: кейс, затем часть «Подготовка» | render_docs |
| `run.ps1` | запуск одной командой: venv, сборка фронта, сервис, браузер (`-Port -DataRoot -NoBrowser`), UTF-8 с BOM | пользователь |
| `docs/prep/TOMORROW.md` | первые 60 минут хакатона: команды и развилки | команда |
| `docs/QA.md` | 30 вопросов жюри по кейсу с ответами и ссылками на доказательства (генерирует `scripts/make_deck_case.py`) | защита |
| `scripts/final_numbers.py` | артефакты → `reports/final_numbers.json` (единственный источник чисел; блок `case` — числа кейса из `reports/case_*`, `configs/case_*`, `run_summary.json`) | render_docs, make_deck_case |
| `scripts/render_docs.py` | `*.tmpl` + final_numbers → README.md, report.md, docs/PREP.md; пустые значения печатает списком | вручную |
| `scripts/make_deck_case.py` | `reports/case_deck.pptx` (12 слайдов кейса), `docs/SPEECH.md`, `docs/DEMO.md`, `docs/QA.md`, скриншоты `reports/case_deck_img/`; `--check` — все числа найдены, `--preview` → PNG через LibreOffice | вручную |
| `src/macroplastic/features/pixel.py` | пиксельные признаки: каналы, индексы, оконные mean/std 3/7/15, контраст с локальной медианой; блочный режим для больших тайлов | train_lgbm, lgbm_predict |
| `src/macroplastic/models/lgbm_predict.py` | LGBMPredictor / load_predictor (реестр 'lgbm'): P(MD) по тайлу любого размера, опция harmonize="water_median" для L2A | inference, живые сцены |
| `scripts/train_lgbm.py`, `configs/lgbm.yaml` | обучение LightGBM (кэш признаков, выборка, порог по val, meta.json), эксперименты через `--set` | вручную |
| `weights/lgbm/` | итоговая модель по val MARIDA (бинарная + оконные признаки), порог 0.37 | inference.py |
| `weights/lgbm_live/` | вариант с L2A-аугментацией для живых сцен L2A (с гармонизацией по медиане воды) | run_lgbm_live |
| `reports/l3_lgbm.md`, `reports/experiments_l3.md` | отчёт по модели и журнал гипотез | отчёт |
| `service/frontend/` | SPA карты (Vite+React+TS, MapLibre+deck.gl, ECharts): `npm run dev` / `npm run build` → `service/static/` | пользователь |
| `service/frontend/vite.config.ts` | dev-плагин: `/data/*` из `DATA_ROOT` или `service/demo_fixtures`; `/api`, `/health` → :8000 | npm run dev |
| `service/frontend/src/App.tsx` | состояние, загрузка сцены, URL, демо-тур, хуки `window.__mapReady/__fps/__app` | — |
| `service/frontend/src/map/` | MapView (карта, подложки, маркеры), layers.ts (deck-слои, lazy), controller.ts (flyTo, часы дрейфа) | App |
| `service/frontend/src/components/` | панели, легенда, карточка находки, плеер дрейфа, сравнение, выгрузка, график | App |
| `service/frontend/src/lib/` | загрузка /data и API, метрики сравнения, палитра/формат, состояние в URL | App |
| `service/static/` | собранный фронт (не править руками; в git для чистого клона) | FastAPI |
| `scripts/screenshots.py` | Playwright: 10 кадров UI, load/fps/ошибки консоли → `reports/ui_perf.md`, `--video` — демо-тур; `--gl gpu` для реального fps | ревью UI |
| `reports/ui_perf.md` | журнал замеров UI | отчёт |
| `scripts/tools/inspect_dataset.py` | обзор незнакомого датасета: группы имён, каналы/dtype/CRS/nodata, статистика, маски, баланс, пары снимок↔маска → HTML + summary.json | первые минуты хакатона |
| `scripts/tools/label_forensics.py` | выводимость маски из спектра: деревья 3/4/5, LightGBM, пороги индексов, геометрия разметки; групповой сплит | первые минуты хакатона |
| `scripts/tools/provenance_check.py` | пересечения наших данных с данными организатора: тайл+дата, bbox, SHA-1, pHash превью | первые минуты хакатона |
| `src/macroplastic/organizer_adapter/` | YAML-конфиг → (C,H,W) отражательная способность + маска в схеме MARIDA; `python -m macroplastic.organizer_adapter` | переобучение на данных организаторов |
| `configs/adapter_marida.yaml`, `configs/adapter_example.yaml` | конфиг MARIDA (самопроверка) и шаблон для организаторов | адаптер |
| `tests/test_tools.py` | 7 тестов инструментов | pytest |
| `docs/TOOLS.md` | план первых 60 минут и команды всех инструментов | команда |
| `reports/tools/` | прогоны инструментов на MARIDA и на пожарном датасете прошлого хакатона | отчёт |
| `src/macroplastic/live/stac.py` | STAC-поиск (Earth Search / Planetary Computer), scale/offset с проверкой по данным, вырезки 25 км в UTM, water_mask, метрики блика; пишет `data/live/<region>/<date>/` | fetch_live |
| `src/macroplastic/live/mdd.py` | marinedebrisdetector без lightning (класс UNet из клона `data_cache/marinedebrisdetector`), тайловый fp32-инференс → `prob_mdd.tif/json` | run_mdd |
| `scripts/fetch_live.py` | выбор и загрузка живой сцены L2A для региона (облачность вырезки, ранжирование по блику, политика источника) | вручную |
| `scripts/run_mdd.py` | MDD по папкам сцен (через gpu_queue) | вручную |
| `scripts/run_lgbm_live.py` | наша модель (`weights/lgbm_live`, гармонизация water_median) по папкам сцен, CPU | вручную |
| `scripts/list_live.py`, `scripts/diagnose_live.py`, `scripts/rebuild_live_masks.py` | таблица сцен; где сидят срабатывания (берег/мутность/SCL/блик); пересчёт масок воды | диагностика |
| `tests/test_live_harmonize.py` | сетевой тест гармонизации масштаба/смещения S2 L2A до/после 2022 | pytest |
| `src/macroplastic/models/unet/` | предиктор UNet (smp resnet34, 19 каналов), тайлинг с перекрытием; в реестре как 'unet' (в финал не принят) | эксперименты |
| `scripts/train_unet.py`, `configs/unet.yaml` | UNet: cache / train (GPU-очередь) / evalpred / stack | эксперименты |
| `reports/l4_unet.{md,json}`, `reports/experiments_l4.md` | итог UNet и стека (отклонено) | отчёт |
| `src/macroplastic/data/mados.py` | загрузчик MADOS в формат MARIDA (11 каналов, 10 м, схема классов MARIDA) | train_lgbm_mados |
| `scripts/train_lgbm_mados.py` | дедупликация MADOS↔MARIDA по содержимому (overlap), обучение LightGBM на MARIDA/MADOS/обоих | вручную |
| `reports/mados_overlap.csv`, `reports/mados_overlap_with_marida_test.csv` | пересечения сцен MADOS с MARIDA train/val/test | отчёт |
| `reports/l13_mados.{md,json}`, `reports/experiments_l13.md` | итог MARIDA+MADOS (принято) | отчёт |
| `weights/lgbm/` | **итоговая модель: LightGBM на MARIDA train + MADOS** (без сцен val MARIDA), порог 0.63; прежняя (только MARIDA) — `weights_exp/lgbm/l3_final_backup/` | inference.py |
| `scripts/relabel_live_regions.py` | обновляет region_name (рус.), region_name_en, country, marida_dates в существующих scene.json из `stac.REGIONS` | после правки списка регионов |
| `src/macroplastic/grid/` | prob → пятна (vectorize), индекс H3 res 8 (h3index), зоны «приоритет обследования» (zones), таймсерии | build_service_data |
| `scripts/build_service_data.py` | `data/live` → `service/data` по CONTRACTS (rgb/prob png, detections, h3, zones, timeseries, manifest, drift); `--thresholds`, `--regions` | вручную после новых сцен |
| `scripts/make_demo.py` | `service/data` → `service/demo` (≤ 20 МБ, 2 района с дрейфом) | перед коммитом демо |
| `scripts/validate_service_data.py` | проверка любого корня данных по CONTRACTS (exit 1 при ошибках) | QA |
| `scripts/make_live_fixture.py`, `tests/test_grid.py` | синтетические сцены для разработки; тесты сетки | pytest |
| `src/macroplastic/drift/forcing.py` | вырезка форсинга HYCOM ESPC-D-V02 + NCEP GFS (запасные Open-Meteo, константа) в `data_cache/forcing/*.nc` | run_drift |
| `src/macroplastic/drift/run.py` | старты по детекциям MDD (пропорционально площади), OpenDrift OceanDrift 72 ч, ансамбль ветра 0.01/0.03, drift.json + превью | run_drift |
| `scripts/run_drift.py`, `tests/test_drift.py`, `reports/drift.md`, `reports/drift_*.png` | CLI дрейфа (`--region/--date`, `--all-fresh`); офлайн-тест контракта; метод и ограничения; превью треков | вручную / отчёт |
| `scripts/model_agreement.py`, `reports/model_agreement.{md,json}`, `reports/figures/` | согласие MDD и LightGBM на живых сценах, калибровка LightGBM на val, карты согласия | отчёт |
| `src/macroplastic/grid/confirm.py` | «уверенная находка»: объект одной модели, подтверждённый пикселем другой модели ≥ порога в радиусе 20 м (поля confirmed/confirmed_by, n_confirmed) | build_service_data |
| `scripts/experiments/l16_*.py`, `reports/l16_metric_audit.{md,json}` | независимая перепроверка метрики, «то же место», leave-region-out, что помогает на новом регионе | отчёт |
| `service/app.py` (+ модули place/review/pdf) | эндпоинты `/api/zone`, `/api/crop`, `/api/place`, `/api/place_report.pdf` (2 стр., ~1 с, DejaVu Sans), `/api/calendar`, `/api/review/{queue,label,labels,flag,retrain}` | фронт |
| `scripts/retrain_with_labels.py` | дообучение LightGBM с метками проверки человеком → F1 val до/после → решение по правилу; веса не подменяет | /api/review/retrain |
| `tests/test_api_review.py` | тесты новых эндпоинтов | pytest |
| `src/macroplastic/ingest/` | `python -m macroplastic.ingest <архив|папка>`: безопасная распаковка, HTML-отчёт с «Сомнениями», распознавание 3 форматов, `--convert` во внутренний формат | первые минуты хакатона |
| `scripts/train_lgbm_ingest.py`, `scripts/make_ingest_testsets.py`, `tests/test_ingest.py` | обучение на результате ingest; 3 искусственных набора; тесты (в т.ч. zip-slip) | хакатон / pytest |
| `scripts/measure_speed.py`, `reports/speed.{md,json}`, `tests/test_speed_equivalence.py` | замер холодного старта inference.py на 300 чипах (CPU/GPU, по этапам); проверка, что ускорение не меняет результат | отчёт / pytest |
| `scripts/tools/adapter.py`, `scripts/tools/ingest.py` | обёртки для запуска адаптера и загрузчика из корня без PYTHONPATH | хакатон |
| `docs/img/` | кадры интерфейса для README | README |
| `reports/final_numbers.json`, `reports/case_deck.pptx` | единый источник чисел; дека кейса (генерируются скриптами) | README, отчёт, выступление |
| `service/frontend/src/components/{ZoneCard,PlaceCard,ObsCalendar,ReviewView}.tsx`, `src/lib/{api,priority,crop}.ts` | карточка зоны с формулой приоритета, карточка места + PDF, календарь реальных наблюдений, вкладка «Проверка» (клавиши 1–6, дообучение); доступность API — по `/openapi.json` | фронт |
| `tests/test_robustness.py`, `scripts/robustness_report.py`, `reports/robustness.md` | устойчивость инференса: пустые/NaN чипы, облака, блик, шум, размеры, dtype, каналы, битые файлы, пути | pytest / отчёт |
| `scripts/offline_check.py`, `reports/offline_check.md` | проверка карты без интернета (4 режима подложки) | перед показом |
| `docs/DEMO.md` | сценарий демо на карте кейса (2 мин) и план Б без сети | выступление |
| `scripts/experiments/l23_*.py`, `reports/l23_channels_speed.{md,json}` | модели на подмножествах каналов (RGB, RGB+NIR, 10+20 м) и лёгкая быстрая модель | отчёт / завтра |
| `docs/CRITERIA.md` | самопроверка по рубрике: критерий → максимум → доказательство → самооценка → дыры | приёмка |
| `scripts/baselines.py`, `reports/baselines.json` | бейзлайны на val: пороги индексов, RandomForest как в статье MARIDA | отчёт |
| `reports/experiments.md` | сводная таблица всех экспериментов (бейзлайны → итоговая модель → отклонённые) | отчёт, защита |
| `docs/SPEECH.md` | речь на 4 минуты по слайдам кейса с таймингом, числа наизусть (генерирует `scripts/make_deck_case.py`) | выступление |
| `docs/prep/` | архив подготовительного этапа: прежние дека, речь, демо, вопросы и их генератор `make_deck.py` (индекс ‰ по живым снимкам) | история |

| `src/macroplastic/grid/cloudmask.py`, `reports/cloud_edge.md`, `reports/figures/cloud_edge_*.png` | защита от облаков и теней, пропущенных SCL: спектральная маска (B2 ≥ 0.06 и B11 ≥ 0.03), буфер 5 px, проверка тени по NIR | build_service_data |
| `reports/l31_midsize.md` | средняя модель под CPU (не принята): F1, LRO, скорость по потокам | отчёт |
| `scripts/tools/predict_org.py` | предсказание на test организаторов с той же предобработкой, что обучение (адаптер) → маски в их формате + zip | первый сабмит |
| `scripts/tools/score.py` | локальная метрика по маскам (F1/IoU по классу, macro, ignore) | их val |
| `scripts/rehearsal/`, `reports/rehearsal.md`, `reports/rehearsal2.md` | генеральная репетиция первого часа на «чужом» датасете и её повтор | подготовка |
| `scripts/experiments/l33_*.py`, `reports/l33_scene_relative.{md,json}` | признаки относительно воды сцены и крупные окна (отклонено) | отчёт |
| `reports/l35_asis_check.md` | разбор «как есть» на чужом датасете: метрика по частичной разметке, схема классов, порог | завтра |
| `src/macroplastic/grid/artifacts.py`, `reports/artifacts.md`, `reports/figures/artifacts_*.png` | фильтр линейных артефактов (seam/wake/ship) с числами до/после | build_service_data |
| `scripts/final_test.py` | однократная оценка на test MARIDA после заморозки (порог из val, CI по сценам) → `reports/lgbm_final_test.json`; повторный запуск запрещён самим скриптом | приёмка |
| `service/frontend_v2/`, `service/static_v2/` | интерфейс v2 (по умолчанию): один список районов, панель контекста сдвигает карту, крупные действия, карточка доказательств, лента с датой снимка; откат — `MACROPLASTIC_UI=v1` | FastAPI |
| `scripts/screenshots_v2.py`, `scripts/contrast_check.py` | смоук-сценарий v2 (кадры, fps, `--offline`); проверка контраста WCAG | ревью |
| `scripts/tools/org_to_map.py`, `tests/test_org_to_map.py` | данные организаторов (георефер. чипы + предсказания) → мозаика сцен → корень данных карты (kind organizer) | хакатон |
| `service/routes_incidents.py`, `service/routes_drift_check.py`, `service/routes_context.py` | API: инциденты/лента; проверка прогноза дрейфа (эксперимент) и поля течений/ветра; объекты OSM и пересечение с демо-дрейфом | фронт |
| `docs/prep/HACK-START.md` | первые 60 минут после выдачи кейса | команда |

## Кейс «Детектирование и оценка концентрации макропластика» (шт./км²)

| Путь | Что делает | Кто вызывает |
|---|---|---|
| `task/postanovka.pdf`, `task/kriterii.pdf`, `task/macroplastic_marine_samples.csv`, `task/README_macroplastic_dataset.md` | постановка, критерии, реестр полевых наблюдений (935 строк, 318 событий) и его описание | все шаги кейса |
| `docs/CASE_ANALYSIS.md` | разбор постановки и критериев, EDA реестра, спутниковое покрытие, утечки, разрешение против размера предметов | README, отчёт |
| `docs/CASE_RUN.md` | маршрут кейса одной командой: шаги и выходы, время с кэшем и без, ресурсы, повторяемость | README |
| `docs/CONTRACTS_V3.md` (+ `CONTRACTS_V3_1.txt`) | контракт API v3.1: наблюдения, пары, сцены, зоны, метрики, экспорт, сохранённые запросы, коды ошибок | API, фронт |
| `docs/PREP.md.tmpl` → `docs/PREP.md` | прежний README подготовительного этапа без сокращений (генерирует `render_docs.py`) | README |
| `run.ps1 -Case prepare\|eval\|export\|all [-Offline] [-Force]` | запуск маршрута кейса без старта сервиса | пользователь |
| `configs/case_selection.yaml` | профили целевой величины (S2 визуальный > 2 см — основной, S1 трал 5–50 см), правила команды с причинами отказа, основной сплит (участки маршрута + буфер 1 сут), отложенный test (правило, sha256 составов, срок) | selection, splits, модели |
| `configs/case_pairs.yaml` | пороги полосы и масок качества (покрытие, облака, суша, вода, блик), детектор на парах, сценарии дрейфа и допуск синхронности со ссылками на литературу | find_pairs, pair_quality |
| `configs/case_conc_model.yaml` | протокол моделей концентрации, записанный до CV (кандидаты, гиперпараметры, правило выбора, интервал), и выбранная модель | conc_model_cv, final_test_conc, API |
| `src/macroplastic/case/__init__.py` | пакет кейса | — |
| `src/macroplastic/case/selection.py` | отбор записей по конфигу с причиной для каждой строки, поля-утечки (`LEAK_EXPLICIT`, `ALLOWED_FEATURES`), сверка C = N/A | run_all, baseline, модели |
| `src/macroplastic/case/concentration.py` | C = N/A, перевод единиц, площадь полосы, точный интервал Пуассона (Гарвуд), статусы, агрегация ΣN/ΣA, MAE/RMSE/medAE/log1p-MAE | модели, API (контрольный пример) |
| `src/macroplastic/case/splits.py` | группы event / scene / daycell / st / cruiseday / route, буфер по дням, проверка пересечений, отложенный test и sha256 состава | baseline, модели, тесты |
| `src/macroplastic/case/conc_models.py` | 9 моделей концентрации, признаки без утечек, вложенная CV по участкам маршрута, интервал по лог-остаткам, бутстреп по дням рейса, правило выбора | conc_model_cv, final_test_conc, API (`field_estimate`) |
| `scripts/case/eda_samples.py` | EDA реестра без сети → `reports/case_eda/` | вручную |
| `scripts/case/find_pairs.py` | поиск сцен Sentinel-2 / Landsat в STAC вокруг каждого события, отбор по метаданным, дрейф и допуск синхронности, кэш ответов → `data/pairs/{events,candidates,best_per_event}.csv`, `reports/case_pairs/summary.md` | run_all |
| `scripts/case/pair_quality.py` | вырезки вокруг полосы наблюдения, маски качества, решение по паре, детектор LightGBM на S2 → `data/pairs/quality/<событие>/`, `data/pairs/pair_quality.csv`, `reports/case_pairs/quality.md` | run_all |
| `scripts/case/baseline_concentration.py` | бейзлайны концентрации (медиана, среднее, kNN) на 6 схемах разбиения, бутстреп → `reports/case_conc/{metrics.json, predictions.csv, metrics_by_fold.csv, consistency.csv, baseline.md}`, составы сплитов | run_all |
| `scripts/case/freeze_final_test.py` | фиксация отложенного test концентрации до моделей; повторный запуск только сверяет sha256 | вручную (один раз) |
| `scripts/case/conc_model_cv.py` | CV кандидатов против медианы на dev → `reports/case_conc/dev_cv.{md,json}`, `dev_predictions.csv`, `weights/case_conc/*.json`, `selected` в конфиге | вручную |
| `scripts/case/final_test_conc.py` | однократная оценка на отложенном test (отказ до срока и при существующем результате) → `reports/case_conc/final_test.json`, `final_test_predictions.csv` | приёмка |
| `scripts/case/detector_compare.py` | основной детектор и 6 бейзлайнов на val и test MARIDA, ДИ по сценам, ложные срабатывания по классам фона, примеры → `reports/case_detector/*`, `data/case/detector_preds/*` | вручную |
| `scripts/case/pairs_experiment.py` | признаки снимка в полосе (P детектора, FDI) против полевой плотности всего мусора: Спирмен, бутстреп групп, нулевая модель, мощность → `reports/case_pairs/experiment.*` | вручную |
| `scripts/case/run_all.py` | маршрут кейса: отбор → пары → маски и детектор → реестры → концентрация → пересчёт метрик детектора → экспорт API v3; `run_summary.json` с версиями и sha256 | `run.ps1 -Case` |
| `scripts/case/consistency_check.py` | самопроверка: алгоритм = JSON = CSV = GeoJSON по id, повтор сохранённого запроса по SHA-256, некорректные входы, p50/p95 → `reports/selfcheck/consistency_<дата>.{md,json}` | QA |
| `scripts/screenshots_case.py` | кадры и смоук-проверки режима «Кейс» во фронте v2 | ревью UI |
| `service/case_store.py` | слой данных API v3: наблюдения, реестр пар, сцены, зоны (два статуса), метрики, `field_estimate`, сохранённые запросы, CSV/GeoJSON | routes_v3 |
| `service/routes_v3.py` | роутер `/api/v3/*`: meta, observations, pairs, scenes (+ PNG), zones, metrics, export, queries; единый формат ошибок, CORS | фронт кейса |
| `service/frontend_v2/src/case/` | интерфейс кейса (режим по умолчанию): карта, карточки наблюдения и зоны, реестр пар, метрики, выгрузка, сохранённые запросы | FastAPI (`service/static_v2`) |
| `data/pairs/` (`cache/`, `events.csv`, `candidates.{csv,parquet}`, `best_per_event.csv`, `pair_quality.csv`, `quality/`) | кэш STAC и реестр кандидатов пар, маски качества и вырезки по парам (в git — для работы без сети) | run_all, API |
| `data/case/` (`selection_*.csv`, `run/registry_{samples,pairs}.csv`, `splits/`, `detector_preds/*.npz`) | принятые и отклонённые записи, реестры записей и пар, составы сплитов, предсказания детекторов на MARIDA | run_all, API, тесты |
| `weights/case_conc/<профиль>.json` | выбранная модель концентрации, обученная на dev, и квантили интервала | API, final_test_conc |
| `reports/case_eda/` | таблицы и графики EDA реестра | отчёт |
| `reports/case_pairs/` | `summary.md` (кандидаты, дрейф, причины), `quality.md` (маски), `experiment.{md,json,png}` (связь снимка с полем) | README, отчёт |
| `reports/case_detector/` | `compare.md`, `metrics.json`, `per_patch.csv`, `fn_by_scene.csv`, `fp_by_class.{csv,png}`, `examples/` | README, отчёт, API |
| `reports/case_conc/` | бейзлайны на 6 схемах (`baseline.md`, `metrics.json`, `predictions.csv`), модели на dev (`dev_cv.{md,json}`, `dev_predictions.csv`), сверка C = N/A (`consistency.csv`); после приёмки — `final_test.json` | README, отчёт, API |
| `reports/case_splits/` | составы выборок с sample_id / event_id / scene_id / фолдом, отложенный test, принятые и отклонённые записи | проверка утечек, тесты |
| `reports/case_run/` | `run_summary.json` (версии, sha256 входов и выходов, время шагов), `detector_recomputed.json`, `concentration_stdout.md` | README, final_numbers |
| `reports/selfcheck/` | отчёты самопроверки согласованности и скорости API | QA |
| `out/case_export/` | выгрузки API v3 (наблюдения, пары, зоны; примеры запросов; `requests.json`), не в git | run_all |
| `tests/test_case_concentration.py`, `tests/test_case_conc_model.py`, `tests/test_case_run_all.py`, `tests/test_case_consistency.py`, `tests/test_api_v3.py` | контрольные примеры 60 и 15, пересечения групп и дней рейса, утечки, отложенный test, реестры, согласованность API и экспорта, контракт v3 | pytest |
