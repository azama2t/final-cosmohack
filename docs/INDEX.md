# INDEX — вход в репозиторий (читать первым)

Кейс: детектирование скоплений плавающего мусора (Sentinel-2) и оценка концентрации шт./км² с диапазоном. Постановка и критерии — `task/`, текст в UTF-8 — `out/case/*.txt` (не в git; `pdftotext -enc UTF-8 -layout`).
Главные числа — только из `reports/final_numbers.json` (раздел `case.sections`); README/отчёт/дека собираются `scripts/case/build_docs.py`.

## Что где
| Что | Где |
|---|---|
| README кейса, воспроизведение | `README.md` (из `templates/README.md.tmpl`), `docs/CASE_RUN.md` |
| Разбор постановки и критериев | `docs/CASE_ANALYSIS.md` |
| Маршрут одной командой | `scripts/case/run_all.py` (`run.ps1 -Case all -Offline`) |
| Отбор записей, концентрация C=N/A, сплиты | `src/macroplastic/case/{selection,concentration,splits,conc_models}.py`, `configs/case_selection.yaml` |
| Реестр пар «событие ↔ снимок», дрейф | `scripts/case/find_pairs.py`, `configs/case_pairs.yaml`, `data/pairs/`, `reports/case_pairs/summary.md` |
| Маски качества + детектор на парах | `scripts/case/pair_quality.py`, `data/pairs/quality/<event>/`, `reports/case_pairs/{quality,detector_current,visual_review}.md` |
| Геометрия трансект (PANGAEA) | `src/macroplastic/case/geometry.py`, `data/case/geometry/`, `reports/case_geometry/summary.md` |
| Детектор LightGBM, test MARIDA | `weights/lgbm/`, `reports/case_detector/compare.md`, `reports/lgbm_final_test.json` |
| API v3 + контракт для запасного фронта | `service/routes_v3.py`, `service/case_store.py`, `docs/CONTRACTS_V3.md` (менять только добавлением полей) |
| Фронт (текущий, откат) | `service/frontend_v2/` → `service/static_v2/` |
| Решения | `docs/DECISIONS.md` |
| Журнал событий (дописывать в конец) | `docs/LOG.md` |
| Журнал поиска данных | `docs/SEARCH_LOG.md` |

## Главные числа (на 26.09 00:50; источник — final_numbers.json)
Детектор MARIDA test F1 0.871 [0.823–0.920] (RF 0.771, FDI×NDVI 0.042). Пары: 318 событий → 29 по метаданным → 12 прошли маски → 0 синхронных при типичном дрейфе; для пластика (S1, S2) снимков нет. Концентрация: отложенный test — модели не лучше медианы (S2 30.0 vs 25.2), на карте медиана профиля. Детектор на 22 сценах пар без гармонизации — 5 объектов, 0 в полосах.

## Уровни доказательности (§11)
A — снимок и независимое полевое число связаны по времени, месту, обследованной площади и категории; B — подтверждённая разметка спутникового скопления (без числа предметов); C — кандидат для ручной проверки; D — отвергнутый/отрицательный (пена, блик, облако, судно, водоросли). Детектор учим только на B + проверенных D; подсчёт «снимок → шт./км²» — только на A; C никогда не становится положительной меткой автоматически.

## Направления §11 (статус — в PROGRESS.md, раздел «РАССЛЕДОВАНИЕ»)
1 розыск снимков для событий CSV · 2 новые размеченные спутниковые данные · 3 новые полевые данные · 4 эксперименты с детектором · 5 оценка количества · 6 фронт и студия · 7 журнал поиска и пайплайн · 8 дека/речь/демо.

## Правила для исполнителей
Git и установка пакетов — только оркестратор. Свой каталог/файлы и отчёт `reports/tasklog/<id>.md`, начинающийся с блока «ИТОГ (≤ 10 строк)». Каждое событие — одна строка в конец `docs/LOG.md`: `время | направление | что сделано | результат/число | файл`. GPU — только через `scripts/gpu_queue.py`. Не использовать CDS/ERA5 через CDS (ветер ERA5 — через Open-Meteo archive API), не писать вне репозитория.
