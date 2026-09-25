# Проверка на чистом клоне (L74), 25.09.2026 19:41–20:00

Коммит: `6722a7c` (origin/main). Клон: `out\clean_clone_1941` (сделан `git clone` с GitHub, не архив рабочего дерева). Всё, что ниже, сделано по README клона. Сервис основного репозитория на :8000 не трогали, GPU не использовали (`-Cpu`, `CUDA_VISIBLE_DEVICES=""`).

## Итог

- От начала клона до карты на экране прошло **≈ 7,5 мин** (19:41 → 19:48:41). Из них 76 с на клон и ≈ 3 мин на создание `.venv` и установку пакетов.
- **Числа совпали:** 318 событий, 935 наблюдений, 4436 пар, 29 зон; summary 29 / 0 / 0; F1 MARIDA test 0.8713; test концентрации S2 30.0 против медианы 25.2, S1 370.0 против 371.1.
- **Отпечаток выходов:** в клоне `b97c5cb16ee4fc7a`, в закоммиченном `run_summary.json` `85335b7a5f8aa5de`. Разница целиком объясняется одним файлом, который зависит от MARIDA (`reports/case_run/detector_recomputed.json`). Остальные 23 из 24 выходных таблиц совпадают по sha256. Если подставить в клон sha256 этого файла из HEAD, отпечаток получается ровно `85335b7a5f8aa5de`.
- **Тесты:** тесты кейса из README — 142 passed, 1 skipped (README пишет «112»). Вся папка `tests` — **1 failed**, 346 passed, 76 skipped. Упал `test_case_geometry.py::test_manifest_sha256_matches_files`. Причина — CRLF при checkout, подробности ниже.
- Отклонений от процедуры не было: пакеты поставились в `out\clean_clone_1941\.venv` из `requirements-cpu.txt` за ≈ 3 мин, основной `.venv` для кода клона не использовался.

## Шаги, команды, время

| # | Команда | Время | Результат |
|---|---|---|---|
| 1 | `git clone https://github.com/azama2t/final-cosmohack out\clean_clone_1941` | 76 с | 264 МБ рабочей копии вместе с .git, HEAD 6722a7c |
| 2 | `powershell -ExecutionPolicy Bypass -File run.ps1 -Case all -Offline -Cpu` | 3 мин 17 с (19:43:24–19:46:41) | создан `.venv` через `py -3.12`, пакеты поставлены из requirements-cpu.txt (torch 2.11.0+cpu; `.venv` занимает 1,53 ГБ). Маршрут: exit 0, шаги 6.9 с |
| 3 | `.venv\Scripts\python.exe -m pytest -q tests\test_case_concentration.py tests\test_case_conc_model.py tests\test_case_run_all.py tests\test_case_consistency.py tests\test_api_v3.py` | 22 с | 142 passed, 1 skipped |
| 3b | `.venv\Scripts\python.exe -m pytest -q tests` | 10 мин 53 с | 1 failed, 346 passed, 76 skipped |
| 4 | `.venv\Scripts\python.exe -m service --port 8097` | готов за 1,1 с | API проверено, см. ниже |
| 5 | Playwright 1366×768, `http://127.0.0.1:8097/` (из основного .venv) | загрузка 8,1 с | 0 ошибок консоли, 0 неудачных запросов, 0 ответов ≥ 400. Скриншот: `reports/selfcheck/clean_clone_1941_map.png` |
| 6 | сервис 8097 остановлен | — | порт свободен |

Время шагов маршрута в клоне: 1_selection 0.1 с, 2_pairs 1.2 с (1272 запроса, промахов кэша 0, ошибок 0), 3_quality_detector 0.5 с, 3b_registry 0.7 с, 4_concentration 3.0 с, 5_detector_marida 0.1 с, 6_export 1.4 с, всего 6.9 с (в README 6.2 с).

## Фактические числа клона и сверка с основным

| Что | Клон | Ожидалось (README / HEAD) | Совпало |
|---|---|---|---|
| строк CSV / событий | 935 / 318 | 935 / 318 | да |
| отбор S2 / S1 | 63 / 83 принято | 63 / 83 | да |
| строк пар (find_pairs) | 1774 | 1774 | да |
| маски: принято / отклонено | 12 / 17 (блик 10, облака 6, покрытие 1) | 12 / 17 | да |
| реестр пар | 318 reject; без дрейфа 12 accept | то же | да |
| экспорт: observations / pairs / zones | 935 / 4436 / 29 | 935 / 4436 / 29 | да |
| `/api/v3/meta` summary | n_strips 29, n_confirmed_pairs 0, plastic_scenes 0 | 29 / 0 / 0 | да |
| `/api/v3/observations` | count 935 (768 мс) | 935 | да |
| `/api/v3/pairs` | 4436 | 4436 | да |
| `/api/v3/zones` | 29, layer_kind candidate_strip | 29 | да |
| `/api/v3/scenes` | 236 | — | — |
| `/api/v3/metrics` | `concentration.final_test_summary` есть: S2 ridge_log MAE 30.02 против медианы (n 14); S1 knn5_log 370.02 против 371.12, ΔMAE −1.10 [−60.7; 49.2]; `final_test_status` «посчитан один раз 25.09…» | есть | да (поле лежит в `concentration`, а не на верхнем уровне) |
| экспорт `/api/v3/export` | zones geojson / csv 29; observations csv / geojson 935; pairs csv 4436; `Content-Disposition` с именем файла | — | да |
| детектор MARIDA test F1 | 0.8713, взято из `metrics.json` (MARIDA нет) | 0.871 | да |
| `outputs_fingerprint` | `b97c5cb16ee4fc7a` | `85335b7a5f8aa5de` | **нет**, причина ниже |

## Расхождения и их причины

1. **Отпечаток выходов.** В отпечаток (`run_all.py: write_summary`, sha256 от `outputs_sha256`) входит `reports/case_run/detector_recomputed.json`. Разметки MARIDA в git нет, поэтому шаг 5 пишет в этот файл `recomputed: null` вместо TP/FP/FN семи моделей. Из-за этого меняется sha256 файла, а с ним и весь отпечаток.
   - Проверка: остальные 23 выхода совпадают с `outputs_sha256` из `git show HEAD:reports/case_run/run_summary.json` байт в байт. Если подставить sha256 этого файла из HEAD, пересчёт даёт `85335b7a5f8aa5de`.
   - Итог: на чистом клоне без MARIDA отпечаток из README получить **нельзя**, хотя все таблицы те же.
2. **sha256 входного CSV** в `run_summary.inputs`: в клоне `61ed02d8…`, в HEAD `30ce0a65…`.
   - Причина: в системном `C:\Program Files\Git\etc\gitconfig` стоит `core.autocrlf=true`, а в основном репозитории локально `false`. В клоне `task/macroplastic_marine_samples.csv` выписан с CRLF (`git ls-files --eol`: `i/lf w/crlf`, 936 CRLF). Если заменить CRLF на LF, получается ровно `30ce0a65…`.
   - На разбор CSV это не влияет, выходы те же.
3. **Падающий тест `test_case_geometry.py::test_manifest_sha256_matches_files`.** Та же причина: `data/case/pangaea/PANGAEA_*.tab` выписаны с CRLF (`i/lf w/crlf`). sha256 после замены CRLF→LF совпадают с `manifest.json`: 931833 `9ba6c0b5faa2`, 890782 `c8abc2191131`, 890781 `830636a3714e`. На машине с autocrlf=false тест пройдёт, у нового человека на Windows с Git по умолчанию — упадёт.
4. **Основной репозиторий во время проверки.** Рабочее дерево «грязное»: изменены `configs/case_pairs.yaml`, `data/pairs/*`, `data/pairs/quality/*`, их меняет параллельная работа. В 19:46:29 основной маршрут был перезапущен, его текущий отпечаток `a323da786c1a39dd`. Поэтому клон сверялся с **закоммиченным** `run_summary.json` из HEAD 6722a7c (`85335b7a…`), а не с рабочим деревом основного.
5. Мелочи:
   - В клоне стоит numpy 2.5.3, в `run_summary` HEAD — 2.5.2: `requirements-cpu.txt` без версий, `run.ps1` lock-файл не использует. На числа это не повлияло.
   - Шаг 4 в логе печатает «median MAE 35.9 vs knn5 31.3», а README (раздел 5, «Справочно») — «kNN — 31.7». В `reports/case_conc/baseline.md` оба числа есть, это разные варианты kNN. Лог и README называют разное «kNN».
   - После прогона в клоне изменены 2 файла под git: `reports/case_run/detector_recomputed.json` и `run_summary.json`. У нового человека после первого запуска `git status` не будет чистым.
   - `find_pairs.py:246`: SyntaxWarning invalid escape sequence `'\*'`, печатается в stderr при каждом прогоне. Если захватывать stderr, PowerShell показывает это как NativeCommandError.

## Правки README для нового человека (строка → как надо)

1. **Раздел 2 (быстрый старт), перед командами.** Добавить: «Перед `git clone` на Windows: `git clone -c core.autocrlf=false https://github.com/azama2t/final-cosmohack`». Иначе CSV и `.tab` выписываются с CRLF: меняется sha256 входов, и падает `test_manifest_sha256_matches_files`.
   - Лучше исправить сам репозиторий: в `.gitattributes` добавить `*.csv -text`, `*.tab -text`, `*.json -text`, `*.yaml -text` (или `* -text`). Это решение orchestrator'а.
2. **Раздел 2, «Повторяемость», строка «Отпечаток текущего прогона — `85335b7a5f8aa5de…`».** Написать так: «с разметкой MARIDA в `data/MARIDA/`. Без неё `detector_recomputed.json` содержит `recomputed: null`, и отпечаток другой (`b97c5cb16ee4fc7a…` на 6722a7c). Остальные 23 из 24 таблиц совпадают по sha256». Вариант получше: исключить `detector_recomputed.json` из `outputs_fingerprint` или считать для него отдельный отпечаток.
3. **Раздел 2, «Чистый клон работает без сети… даёт тот же реестр, 29 зон и ту же выгрузку».** Верно, но дописать: «подложка карты (Esri) грузится из интернета; установка пакетов при первом запуске тоже нужна сеть (≈ 3 мин, `.venv` ≈ 1,5 ГБ из-за torch CPU)».
4. **Раздел 2, строка про `run.ps1` без ключей.** Добавить ключи `-Port <n>` и `-NoBrowser`: они есть в `run.ps1`, в README не упомянуты. Нужны, если порт 8000 занят.
5. **Раздел 2, «Версии».** Для чисел байт в байт: `.venv\Scripts\python.exe -m pip install -r requirements-lock.txt`. Но lock-файл указывает на индекс cu128. Нужен вариант lock для CPU, либо указать, что `run.ps1` ставит версии без закрепления (numpy 2.5.3 вместо 2.5.2).
6. **Раздел 7, строка «тесты кейса … | 112 тестов».** Исправить на «142 теста (+1 пропущен), ≈ 22 с». Добавить: полный `-m pytest -q tests` идёт ≈ 11 мин на CPU (347 passed / 76 skipped).
7. **Раздел 2, про MARIDA.** Уточнить, что без MARIDA лог шага 5 печатает «пересчёт из npz совпал: test=None, val=None». Это значит «не пересчитывалось», а не «совпало». Лучше поправить и саму строку лога в `run_all.py:438`: при `None` выводить «не пересчитано: нет data/MARIDA».
8. **Раздел 5, «Справочно…, kNN — 31.7».** Уточнить, какой kNN имеется в виду: лог маршрута на шаге 4 печатает «knn5 31.3».
9. **Раздел 8, `/api/v3/metrics`.** Упомянуть, что итог отложенного test лежит в `concentration.final_test_summary` и `concentration.final_test_status`.
10. **Раздел 2.** Предупредить, что после первого прогона в git изменены `reports/case_run/run_summary.json` и `detector_recomputed.json`. Это нормально (время, версии, отсутствие MARIDA).

## Артефакты

- Клон: `out\clean_clone_1941` (в `.gitignore`, ничего не коммитилось).
- Лог маршрута: `out\clone1941_run_all.log`. Лог сервиса: `out\svc1941.err`.
- Закоммиченный `run_summary` для сверки: `out\head_rs_1941.json`.
- Скрипт скриншота: `out\shot1941.py`. Скриншот: `reports\selfcheck\clean_clone_1941_map.png`.
