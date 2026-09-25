# Кейс одной командой: CSV → отбор → пары → качество → детектор → зоны → метрики → экспорт

Скрипт `scripts/case/run_all.py` вызывает готовые шаги кейса в фиксированном порядке и сам ничего не пересчитывает по-своему.
Он пишет время каждого шага и итоговый файл `reports/case_run/run_summary.json`. В этом файле: версии пакетов, sha256 входов
и конфигов, числа по каждому шагу, метрики и sha256 всех выходных таблиц.

## Команды

Запуск из корня репозитория, окружение `.venv` (Python 3.12). GPU не нужен: скрипт сам выставляет `CUDA_VISIBLE_DEVICES=""`.

```powershell
# весь маршрут без сети (пары из кэша STAC, качество из готовых meta.json)
powershell -ExecutionPolicy Bypass -File run.ps1 -Case all -Offline
# то же напрямую
.venv\Scripts\python.exe scripts\case\run_all.py all --offline

# по частям
.venv\Scripts\python.exe scripts\case\run_all.py prepare [--offline] [--force]   # шаги 1–3 + реестры
.venv\Scripts\python.exe scripts\case\run_all.py eval                            # шаги 4–5 (пересчёт метрик)
.venv\Scripts\python.exe scripts\case\run_all.py export                          # шаг 6 (GeoJSON + CSV)

# подмножество событий в отдельный каталог (реальные data/ и reports/ не трогаются)
.venv\Scripts\python.exe scripts\case\run_all.py all --offline --workdir out\case_run_smoke --events "S1:0.1,S4:DOORS3:T2"
.venv\Scripts\python.exe scripts\case\run_all.py prepare --offline --workdir out\tmp --limit-events 10

# smoke-тест (5 событий, без сети, ~4 с)
.venv\Scripts\python.exe -m pytest -q tests\test_case_run_all.py
```

Если запустить `run.ps1` без `-Case`, он работает как раньше и поднимает сервис с картой. С `-Case` он проверяет `.venv`,
запускает маршрут и завершается, сервис не стартует. Флаги `-Offline` и `-Force` передаются в скрипт как `--offline` и `--force`.

| Флаг | Что делает |
|---|---|
| `--offline` | сеть не используется. Если запроса STAC нет в кэше, скрипт не падает: в реестр пар пишется строка `error:…`, и по ней событие получает отказ с причиной. Качество пар берётся только из готовых `data/pairs/quality/*/meta.json` |
| `--force` | качество и детектор считаются заново для всех пар (`pair_quality.py --force`). Нужна сеть, снимки S2 и Landsat читаются заново |
| без флагов | пары ищутся с сетью только для запросов, которых нет в кэше. Качество считается только для пар без `meta.json` |
| `--workdir`, `--events`, `--limit-events` | работа на подмножестве событий, все выходы пишутся в каталог. Шаг концентрации в этом режиме пропускается: для кросс-валидации нужен полный профиль |
| `--pairs-cache DIR` | другой каталог кэша STAC. Нужен для отладки, чтобы проверить промахи кэша или замерить холодный запуск |

## Шаги и что где лежит

| Шаг | Код (вызывается как есть) | Выход |
|---|---|---|
| 1 отбор | `src/macroplastic/case/selection.py` + `configs/case_selection.yaml` | `data/case/selection_<профиль>.csv`, `…_rejected.csv`; **реестр записей** `data/case/run/registry_samples.csv`: каждая пара sample_id × профиль, accept/reject и причина |
| 2 пары | `scripts/case/find_pairs.py` + `configs/case_pairs.yaml` (drift), кэш `data/pairs/cache/` | `data/pairs/events.csv`, `candidates.csv/.parquet`, `best_per_event.csv`, `reports/case_pairs/summary.md` |
| 3 качество + детектор | `scripts/case/pair_quality.py` | `data/pairs/quality/<event>/…`, `data/pairs/pair_quality.csv`, `reports/case_pairs/quality.md` |
| 3b реестр пар | `run_all.py` | **`data/case/run/registry_pairs.csv`**: одна строка на каждый event_id из CSV, `status` accept/reject, `stage` (metadata / drift / quality / all), `reason` (все причины этапов через « \| »), `status_without_drift`, лучшая сцена, dt, сдвиг и допуск по дрейфу, решение по качеству, n_det |
| 4 концентрация | `scripts/case/baseline_concentration.py` | `reports/case_conc/{metrics.json, predictions.csv, metrics_by_fold.csv, consistency.csv, baseline.md}`, `data/case/splits/`; вывод скрипта сохраняется в `reports/case_run/concentration_stdout.md` |
| 5 детектор на MARIDA | `run_all.py`: пересчёт из `data/case/detector_preds/{test,val}_preds.npz` и меток `data/MARIDA/patches/*_cl.tif` | `reports/case_run/detector_recomputed.json`: TP/FP/FN/P/R/F1/IoU 7 моделей и сверка с `reports/case_detector/metrics.json`. Снимки test не читаются, модели не запускаются |
| 6 экспорт | API v3 (`service/routes_v3.py`, FastAPI TestClient) | `out/case_export/{observations,pairs,zones}.{geojson,csv}`, примеры запросов `example_*.csv/geojson`, их список `requests.json` |
| итог | `run_all.py` | `reports/case_run/run_summary.json` |

Детектор на MARIDA не переобучается и не перезапускается. Если нет npz или меток MARIDA, берутся числа из `metrics.json`,
а в сводку пишется его sha256. Полный прогон сравнения детекторов делает `scripts/case/detector_compare.py`
(около 130 с, 12 процессов, читает MARIDA test). В `run_all` он не входит, чтобы test не читался повторно.

## Время на этой машине

Машина: Windows 10, 20 логических CPU, 34 ГБ ОЗУ, GPU не используется.

| Шаг | С кэшем / готовыми meta.json | Без кэша (холодный запуск) |
|---|---:|---:|
| 1 отбор | 0.1 с | 0.1 с |
| 2 пары (318 событий × 4 коллекции = 1272 запроса STAC) | 0.9–1.0 с | ≈ 2–3 мин: замер 2.6 с на 5 событиях (24 запроса); L59 для полного прогона дал ≈ 2 мин |
| 3 качество + детектор (29 пар) | 0.1–0.2 с | ≈ 4–5 мин: сумма `elapsed_s` в meta.json 222 с (1–17 с на пару) плюс загрузка модели. С `--force` здесь не перезамерялось |
| 3b реестр пар | 0.7 с | 0.7 с |
| 4 концентрация (2 профиля, 6 схем сплита, бутстреп 2000) | 2.3–3.3 с | то же |
| 5 метрики детектора из npz (359 + val патчей) | 1.0–2.0 с | то же |
| 6 экспорт API v3 (8 файлов) | 0.8–1.2 с | то же |
| **всего `all --offline`** | **6–8 с** (плюс ≈ 1.5 с на запуск Python) | ≈ 7–9 мин с сетью |

## Ресурсы

- Диск: кэш STAC `data/pairs/cache` 2.5 МБ, вырезки качества `data/pairs/quality` 31 МБ, предсказания детектора
  `data/case/detector_preds` 57 МБ, MARIDA 4.5 ГБ (для шага 5 нужны только метки `*_cl.tif`), экспорт `out/case_export` ≈ 7 МБ.
- Память: меньше 2 ГБ, пик на шаге 5 (маски 7 моделей в packbits).
- Сеть нужна только при промахах кэша STAC и для `--force` или новых пар в шаге 3 (Planetary Computer, Earth Search).
  С `--offline` сеть не нужна.
- Пакеты уже есть в `requirements*.txt`: pandas, numpy, scikit-learn, lightgbm, rasterio, shapely, pyproj, requests,
  fastapi, httpx (для TestClient), pyyaml.

## Повторяемость

При тех же входах два подряд прогона `all --offline` дают одинаковые sha256 всех 24 выходных таблиц. Отпечаток —
`outputs_fingerprint` в `run_summary.json`. Выходы шагов 1–4 побайтно совпали с файлами до запуска, которые построили
исполнители L58–L61. Время в `run_summary.json` в таблицы не попадает.

Ограничение повторяемости: в реестре кандидатов есть 301 строка с одинаковыми `(событие, коллекция, endpoint, dt_hours)`.
Это соседние тайлы одной съёмки и повторная обработка одной сцены. Порядок таких строк, а значит и выбор «лучшей» сцены
среди равных, определяется порядком выдачи STAC. Из кэша этот порядок фиксирован. При холодном запуске сервер может
вернуть равные сцены в другом порядке. На 5 событиях, пересчитанных по сети, результат совпал с кэшем побайтно.
