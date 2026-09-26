# INDEX — вход в репозиторий

Кейс: детектирование скоплений плавающего мусора на снимках Sentinel-2 и оценка концентрации шт./км² с диапазоном. Постановка и критерии — `task/postanovka.pdf`, `task/kriterii.pdf`, данные организаторов — `task/macroplastic_marine_samples.csv`.
Все числа в README, отчёте и презентации берутся из `reports/final_numbers.json`; документы собирает `scripts/case/build_docs.py`.

## Что где
| Что | Где |
|---|---|
| README кейса, воспроизведение | `README.md`, `docs/CASE_RUN.md` |
| Разбор постановки; сверка с критериями | `docs/CASE_ANALYSIS.md`; `docs/CRITERIA_CHECK.md` |
| Маршрут одной командой | `scripts/case/run_all.py` (`run.ps1 -Case all -Offline`) |
| Отбор записей, концентрация C = N/A, сплиты | `src/macroplastic/case/{selection,concentration,splits,conc_models}.py`, `configs/case_selection.yaml` |
| Реестр пар «событие ↔ снимок», дрейф | `scripts/case/find_pairs.py`, `configs/case_pairs.yaml`, `data/pairs/`, `reports/case_pairs/summary.md` |
| Маски качества и детектор на парах | `scripts/case/pair_quality.py`, `data/pairs/quality/`, `reports/case_pairs/` |
| Детектор LightGBM, test MARIDA, бейзлайны | `weights/lgbm/`, `reports/case_detector/compare.md`, `reports/detector_v2/unet_baseline.md` |
| **Как мы искали данные: хронология попыток и неудач** | `docs/PIPELINE.md` |
| **Воронка 318 → … → A/B/C/D** | `docs/img/funnel.png` (генератор `docs/img/funnel_fig.py`, числа `docs/img/funnel.json`) |
| Журнал поиска данных (по строкам + сводка) | `docs/SEARCH_LOG.md` |
| Розыск снимков по событиям и ADIS | `reports/search/{s1,s2,s3,s4,adis}.md`, `reports/search/*_candidates.csv`, `reports/search/search_numbers.json` |
| Новые размеченные данные B/D, полевые данные | `reports/extra_data/{plp_floatingobjects,negatives,field}.md`, `reports/extra_data/registry*.csv` |
| Детектор v2 на B + D (не принят) | `reports/detector_v2/experiments.md`, `reports/detector_v2/decision.json` |
| Количество: поле и спутник раздельно | `docs/QUANTITY.md` |
| Нефтяное пятно (эксперимент, выключен) | `docs/OIL.md`, `reports/oil/` |
| Независимая проверка утверждений | `reports/audit/audit.md` |
| Независимая проверка количественной связи (член команды) | `docs/research/gleb_quantitative_link/results/REPORT.md` |
| API v3 и контракт | `service/routes_v3.py`, `service/case_store.py`, `docs/CONTRACTS_V3.md` |
| Интерфейс: v2 — основной; v3 — рядом, не по умолчанию | `service/frontend_v2/` → `service/static_v2/`; `service/frontend_v3/` (`MACROPLASTIC_UI=v3`) |
| Решения; журнал событий; архив | `docs/DECISIONS.md`; `docs/LOG.md`; `docs/archive/` |

## Главные числа (источник — `reports/final_numbers.json`, раздел `case.sections`; на 26.09 05:40)
Детектор MARIDA test F1 0.871 [0.823–0.920] (RF 0.771, U-Net MARIDA 0.501, FDI×NDVI 0.042). CSV: 318 событий → 68 со снимком ±5 сут → 29 по метаданным → 12 прошли маски → 0 синхронных; розыск снимков по событиям CSV — A 0 · C 9 · D 165. ADIS (найден нами): 21 444 отрезка → 2 086 со снимком ±1 сут → 177 (≤ 3 ч, облачн. ≤ 30 %) → 66 пар по месту и времени (A; не калибровочные); на 6 синхронных отрезках с единичными предметами детектор предметы не увидел (оценивается на 3 из 6 — на всех 0) — согласуется с физикой (~10⁻⁷), но не общий предел для всех скоплений. **Калибровочных пар «снимок → шт./км²» — 0.** B/D: 65 съёмок PLP/FO + 14 374 окна Cózar (B), 2 094 рамки судов + 28 сцен облаков (D), утечек 0. Концентрация: test — модели не лучше медианы (S2 30.0 vs 25.2), на карте медиана профиля. Воронка — `docs/img/funnel.png`.

## Уровни доказательности
A — снимок и независимое полевое число связаны по времени, месту, обследованной площади и категории; B — подтверждённая разметка спутникового скопления (без числа предметов); C — кандидат для ручной проверки; D — отвергнутый/отрицательный (пена, блик, облако, судно, водоросли). Детектор учим только на B + проверенных D; подсчёт «снимок → шт./км²» — только на A; C никогда не становится положительной меткой автоматически.

## Направления работы — статус (26.09 05:40)
| направление | статус | где |
|---|---|---|
| Розыск снимков для событий CSV (S1–S4) | закрыт по стоп-правилу: A 0, C 9 | `reports/search/s1.md … s4.md` |
| Новые полевые данные с числами (ADIS, Lebreton) | ADIS: 66 пар по месту и времени, калибровочных 0; Lebreton 2016 — снимков нет | `reports/search/adis.md`, `reports/extra_data/field.md` |
| Новые размеченные данные B/D | B 65 съёмок + 14 374 окна Cózar; D 2 094 судна, 28 сцен облаков; утечек 0 | `reports/extra_data/` |
| Детектор на B + D; U-Net | 13 вариантов с новыми данными (× 3 seed, + контроль r0) — правило не прошёл ни один → weights/lgbm; U-Net test 0.501 | `reports/detector_v2/` |
| Оценка количества | **основной результат — полевой алгоритм** C = N/A (отложенный test: модели не лучше медианы → медиана профиля); спутник — площадь и сценарий; калибровки нет | `docs/QUANTITY.md` |
| Проверка «спектр → доля покрытия» (PLP2019), Ruiz 2020, FML | не подтверждено (ρ(FDI) = 0.09); Ruiz — D; FML — штуки на кадр без площади | `docs/research/gleb_quantitative_link/results/REPORT.md` |
| Пары для количества; методы подсчёта; «снимок → штуки» по ячейкам; счётчик по фото (FML) | в работе | `docs/SEARCH_LOG.md`, `docs/PIPELINE.md` (по мере результатов) |
| Интерфейс и демо | v2 — основной; зона и демо на отложенной сцене — в работе | `service/frontend_v2/` |
| Нефтяное пятно | эксперимент, выключен по умолчанию | `docs/OIL.md` |
| Сверка с критериями; независимая проверка | в работе | `docs/CRITERIA_CHECK.md`, `reports/audit/audit.md` |
