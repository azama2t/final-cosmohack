# FILEMAP — карта проекта (читать первым)

| Путь | Что делает | Кто вызывает |
|---|---|---|
| `docs/CONTRACTS.md` | форматы файлов слоя данных сервиса (manifest, prob, detections, h3, zones, drift, timeseries) | все дорожки |
| `scripts/make_fixtures.py` | валидные фейковые данные по контрактам за секунды | фронт/API/тесты |
| `scripts/gpu_queue.py` | единственная очередь GPU-задач, лог `out/gpu_queue.log` | обучение, MDD-инференс |
| `src/macroplastic/__init__.py` | константы: классы MARIDA, IGNORE_INDEX, MARINE_DEBRIS, слияние 12..15→7 | все модули |
| `src/macroplastic/utils.py` | ROOT, seed_everything, available_cpus (affinity), cuda_usable, логгер, load_yaml | все дорожки |
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
| `scripts/check_mados_overlap.py` | пересечения сцен MARIDA↔MADOS по тайлу+дате | дорожка MADOS |
| `reports/eda/`, `reports/marida_scenes.csv`, `reports/marida_regions.md`, `reports/sources.md` | EDA, таблица 63 сцен, регионы по MD px, источники и лицензии | отчёт, выбор регионов |
| `README.md.tmpl` → `README.md` | шаблон README; README генерирует `scripts/render_docs.py` (правь только .tmpl) | render_docs |
| `reports/report.md.tmpl` → `reports/report.md` | шаблон отчёта | render_docs |
| `run.ps1` | запуск одной командой: venv, сборка фронта, сервис, браузер (`-Port -DataRoot -NoBrowser`), UTF-8 с BOM | пользователь |
| `TOMORROW.md` | первые 60 минут хакатона: команды и развилки | команда |
| `reports/qa.md` | вопросы жюри с ответами | защита |
| `scripts/final_numbers.py` | артефакты → `reports/final_numbers.json` (единственный источник чисел) | render_docs, make_deck |
| `scripts/render_docs.py` | `*.tmpl` + final_numbers → README.md, report.md | вручную |
| `scripts/make_deck.py` | `reports/deck.pptx` (10 слайдов), `--preview` → PNG через LibreOffice | вручную |
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
