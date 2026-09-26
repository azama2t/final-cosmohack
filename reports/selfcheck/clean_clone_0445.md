# Проверка на чистом клоне, третий повтор (26.09.2026 04:28–04:45)

Способ тот же, что в `clean_clone_1941.md` и `clean_clone_0003.md`. Клон сделан с GitHub: `git clone https://github.com/azama2t/final-cosmohack out\clone_0445`. HEAD клона — `38b93b5` (коммит 04:27:23, «детектор v2 — 12 вариантов ×3 seed…»), это тот же коммит, что origin/main. Работал только по README клона. Новый `.venv` создан внутри клона (`-Cpu`, `CUDA_VISIBLE_DEVICES=""`). Основной `.venv` не трогал. Сервисы :8000/:8070/:8071/:8072 не трогал; свой сервис на :8097 в конце остановлен, порт свободен. В клоне и в основном репозитории ничего не коммитил.

## Итог

- От начала клона до карты — **≈ 4 мин**: клон 10 с, `.venv` с пакетами и маршрутом 3 мин 28 с, тесты 19 с, сервис 1,1 с.
- **Размер**: рабочая копия 341 МБ, из них `.git` 170 МБ (pack 169.5 МиБ), 3 066 файлов. Это **≤ 2 ГБ**. Вместе с CPU-`.venv` (1,6 ГБ) — ≈ 1,9 ГБ; с `node_modules` v3 (403 МБ) — 2,0 ГБ. Ни `.venv`, ни `node_modules` в git не входят.
- **Отпечаток выходов совпал**: `fccf808a8817d7c4` в клоне, в README и в `run_summary.json`.
- **Тесты кейса из README** (`tests\test_case_*.py tests\test_api_v3.py`): **240 passed, 2 skipped, 2 failed** за 18 с. В README написано «242 passed, 2 skipped, 0 failed». Падают `test_case_docs_numbers.py::test_md_files_are_fresh` и `::test_deck_is_fresh`. Причина: в коммит `38b93b5` попали пересобранные документы, но не попал изменённый генератор `scripts/make_deck_case.py`. В основном репозитории он лежит как незакоммиченный `M`. На рабочем дереве основного репозитория эти тесты проходят (7 passed). **Это главная поломка.** Исправляется коммитом, подробности ниже.
- **Числа §11 собираются офлайн из git.** Шаг `5b_search_numbers` маршрута запускает `scripts/case/collect_search.py`, и `reports/search/search_numbers.json` в клоне получается байт-в-байт как в git. Все 7 разделов (`search`, `labeled_data`, `adis_pairs`, `baselines`, `quantity`, `oil`, `detector_v2`) в `final_numbers.json` равны `search_numbers.json`.
- **Расхождение по detector_v2.** README, отчёт, речь и QA говорят «10 вариантов». Это взято из устаревшего снимка `experiments_snapshot.json` от 04:06. В `decision.json` и в сообщении коммита — «12 вариантов», в `docs/INDEX.md` и `PIPELINE.md` — «11». В основном рабочем дереве `search_numbers.json` уже даёт `n_variants 13`, но `final_numbers.json` и документы ещё не пересобраны.
- **API клона = final_numbers = README = экспорт** по всем сверенным числам (таблица ниже). Исключение — `/api/v3/oil/meta`: в клоне метрики `null`, `empty_reason: "нет data/case/oil/index.json"`, а README пишет test F1 0.733. Метрики берутся из `reports/oil/*.json`, которые в git есть. Для «эксперимент выключен» это допустимо, но можно улучшить.
- **Фронт v3** из git (`service/static_v3`, 33 файла) открывается при `MACROPLASTIC_UI=v3` без npm: 0 ошибок консоли (есть 14 предупреждений WebGL о производительности), все запросы 200. `npm ci` 18 с (236 пакетов, `npm audit`: 13 уязвимостей), `npm run build` 5 с. Сборка воспроизводима: имена ассетов с хешами совпали с git, разница только в концах строк (CRLF/LF).
- **Внутренний процесс в git**:
  - `docs/archive/PROGRESS_25-09_history.md`;
  - `docs/INDEX.md`: раздел «Правила для исполнителей», ссылки на `PROGRESS.md` и `reports/tasklog/`, «оркестратор»;
  - `docs/LOG.md`;
  - README строка 30 и `reports/report.md` строка 16: «(решение оркестратора)».

  Каталогов `INBOX.md`, `PROGRESS.md`, `reports/tasklog/` в git нет. Секретов нет. Абсолютные пути `C:\Users\...` есть только в 5 журналах и отчётах (6 строк), в коде их нет.

## Шаги и время

| # | Команда | Время | Результат |
|---|---|---|---|
| 1 | `git clone https://github.com/azama2t/final-cosmohack out\clone_0445` | 10 с (04:28:12–04:28:22) | HEAD 38b93b5; 341 МБ, `.git` 170 МБ |
| 2 | `powershell -ExecutionPolicy Bypass -File run.ps1 -Case all -Offline -Cpu` | 3 мин 28 с (04:28:46–04:32:14) | exit 0. `.venv` (1,6 ГБ) из `requirements-cpu.txt`. Маршрут 12.3 с: selection 0.1, pairs 2.8 (промахов кэша 0), quality 0.7, registry 1.5, concentration 5.4, detector 0.0, search_numbers 0.2, export 1.5 |
| 3 | `.venv\Scripts\python.exe -m pytest -q tests\test_case_*.py tests\test_api_v3.py` | 19 с | **240 passed, 2 skipped, 2 failed** (docs fresh) |
| 4 | `$env:MACROPLASTIC_UI="v3"; .venv\Scripts\python.exe -m service --port 8097` | 1,1 с до /health | проверки ниже |
| 5 | Playwright 1366×768 `http://127.0.0.1:8097/` | ~3 с | «Морской мусор · студия», глобус, 29 участков; вкладки Участки / Студия / Слои / Экспорт; 0 ошибок консоли. Скриншоты: `clean_clone_0445_v3.png`, `clean_clone_0445_studio.png` |
| 6 | `cd service\frontend_v3; npm ci; npm run build` | 18 с + 5 с | exit 0 / 0; `../static_v3` пересобран, отличается от git только концами строк |

Замечание к логу шага 2: печатается `SyntaxWarning: invalid escape sequence '\*'` из `scripts/case/find_pairs.py:293`. В PowerShell это выглядит как `NativeCommandError`, хотя маршрут отработал с exit 0. **Исправлено в основном репозитории** (см. «Правки»). Строка лога шага 5 теперь честная: «test — не пересчитан (нет data/MARIDA)». Замечание из проверки 00:03 закрыто.

## API клона (:8097) и сверка

| Что | README | final_numbers.json | API клона | Экспорт | Совпало |
|---|---|---|---|---|---|
| полосы / годные по маскам / подтверждённые пары | 29 / 12 / 0 | 29 / 12 / 0 | `/meta` summary 29 / 12 / 0, подозрительных 0 / 0 | zones 29 (csv, geojson) | да |
| наблюдения / пары / сцены | 935 / 4436 / — | — | observations, pairs; `/scenes` 235 | 935 / 4436 (csv, geojson) | да |
| MARIDA test F1 LightGBM | 0.871 [0.823–0.920] | 0.871 | `/metrics` 0.8713 [0.8225; 0.9202] | — | да |
| RF / FDI×NDVI | 0.771 / 0.042 | 0.771 / 0.042 | 0.7709 / 0.0424 | — | да |
| test концентрации S1 | 370.0 против 371.1, ΔMAE [−60.7; +49.2] | то же | `final_test_summary` 370.02 / 371.12, [−60.72; 49.24] | — | да |
| test концентрации S2 | 30.0 против 25.2 | то же | `final_test_summary` | — | да (тест docs_numbers) |
| розыск: события CSV A / C | 0 / 9 | search.csv_A 0, csv_C 9 | — | — | да |
| ADIS A / с предметами / пиксели | 66 / 6 / 0 | adis_pairs A 66, A_with_items 6, det_px 0 | — | — | да |
| B новые съёмки / утечки | 65 / 0 | labeled_data b_new_acq 65, leaks_total 0 | — | — | да |
| U-Net test, разность | 0.501, +0.370 | baselines 0.37 | — | — | да |
| нефть MADOS test / OSI | 0.733 / 0.324 | oil test_f1 0.733, osi 0.324 | `/oil/meta` metrics **null** (нет data/case/oil/index.json) | — | README = json; API пусто |
| detector_v2 вариантов | **10** × 3 seed, принято 0 | n_variants 10 (снимок 04:06) | — | — | README = json, но `decision.json` пишет 12 |
| `/studio/scenes` | — | — | 29 сцен, все `pair` (22 S2 evaluated, 7 Landsat not_run); 73 вида (rgb/detection/quality) — все 200 | — | — |

Все четыре эндпоинта из задания отвечают 200: `/api/v3/meta` 0,24 с; `/metrics` 0,39 с; `/studio/scenes` 0,52 с (90 КБ); `/oil/meta` 4 мс.

## Что в клоне работает без внешних данных и как это выглядит

- **Студия.** В клоне только 29 сцен пар кейса. Сцен районов (`data/live`) и розыска (`data/search`) нет: они не в git, поэтому в `/studio/scenes` `by_source_type = {pair: 29}`. Статуса «ещё не рассчитан» у сцен районов в клоне не бывает, потому что самих сцен районов нет. 7 сцен Landsat получают `not_run` с понятной подписью «Детектор на этой сцене не запускался: Landsat: детектор обучен на каналах Sentinel-2…». Выглядит нормально. Вкладка «Студия» без выбранного участка пишет «Сначала выберите участок на карте».
- **Нефть.** `/api/v3/oil/meta` → 200, `experimental: true`, `n_scenes 0`, `empty_reason: "нет data/case/oil/index.json"`. Ответ корректный, это «нет данных». Но метрики MADOS (val/test/OSI) лежат в `reports/oil/{val_runs,test}.json` в git, и их можно было бы отдавать и без индекса сцен.
- **Подложка** Esri грузится из интернета, в этом прогоне все тайлы 200.

## Поломки и предлагаемые правки

1. **Тесты docs fresh падают на HEAD** (2 failed).
   - Команда: `.venv\Scripts\python.exe -m pytest -q tests\test_case_docs_numbers.py -k fresh`.
   - Вывод: `docs\SPEECH.md устарел: пересоберите scripts/case/build_docs.py`. Генератор HEAD даёт «Речь … (6:02)», а в файле «(4:40)». Слайд 7: нет строки «Вывод: пары A подтверждают предел обнаружения…».
   - Причина: в коммите 38b93b5 нет незакоммиченных правок `scripts/make_deck_case.py` (в основном дереве `M`, 38 строк). На рабочем дереве основного репозитория `tests/test_case_docs_numbers.py` проходит: 7 passed.
   - Правка (оркестратор): закоммитить `scripts/make_deck_case.py` вместе с документами и повторить `pytest tests\test_case_*.py tests\test_api_v3.py` на клоне.
2. **detector_v2: 10 / 11 / 12 / 13 вариантов в разных местах.**
   - `reports/detector_v2/experiments_snapshot.json` в HEAD от 04:06: 10 кандидатов. `experiments.json` от 04:22: 18 строк.
   - `decision.json.note`: «Ни один из 12 вариантов». `docs/INDEX.md:37` и `docs/PIPELINE.md:114`: «11 вариантов».
   - README:30/77, `reports/report.md:28/470`, `docs/SPEECH.md:110`, `docs/QA.md:241`: «10 вариантов».
   - В основном рабочем дереве снимок уже обновлён, `search_numbers.json` даёт `n_variants 13`, но `final_numbers.json` (10) и документы не пересобраны.
   - Правка (оркестратор): после завершения L92 выполнить `.venv\Scripts\python.exe scripts\case\collect_search.py --sync`, затем `scripts\final_numbers.py` и `scripts\case\build_docs.py`, и привести `decision.json.note`, INDEX и PIPELINE к одному числу. Надо договориться, что считаем «вариантом»: без reference, r0 и бейзлайнов rf/fdi/unet.
3. **`SyntaxWarning` в `scripts/case/find_pairs.py:293`**: `"\* ½ длины…"`. **Исправлено**: `"\\* ½ длины…"`, значение строки не меняется, отпечаток не затрагивается.
4. **README не описывает фронт v3 / студию.**
   - В README нет `MACROPLASTIC_UI` и `static_v3`. Упоминание «студии» есть только в строке 64.
   - Правка (шаблон `templates/README.md.tmpl`, раздел 2 или 8, 2 строки): «Студия (фронт v3, собран в `service/static_v3`): `$env:MACROPLASTIC_UI='v3'; powershell -ExecutionPolicy Bypass -File run.ps1`; пересборка — `cd service\frontend_v3; npm ci; npm run build` (≈ 25 с)».
   - Проверить, передаёт ли `run.ps1` переменную окружения процессу сервиса. В этой проверке сервис запускался как `python -m service` с переменной в окружении.
5. **Строка README 142** ссылается на старую проверку `clean_clone_1941.md` (7,5 мин). Можно заменить на эту: ≈ 4 мин, маршрут 12.3 с. Строка 143 пишет «7.7 с», а сейчас маршрут идёт 12.3 с, потому что добавились шаги 4 (5.4 с) и 5b.
6. **Следы внутреннего процесса в git** (по желанию, решает оркестратор):
   - `docs/archive/PROGRESS_25-09_history.md`;
   - в `docs/INDEX.md` раздел «Правила для исполнителей» и ссылки на `PROGRESS.md` / `reports/tasklog/`;
   - «(решение оркестратора)» в источнике раздела 10: README:30, `reports/report.md:16`. Строка задаётся в `scripts/case/collect_search.py:446` («решение оркестратора» → например, «решение команды»).
7. **`/api/v3/oil/meta` без индекса сцен** отдаёт `metrics.val/test = null`, хотя `reports/oil/test.json` в git есть. Правка в `service/routes_v3_oil.py` мне запрещена; предложение — брать метрики из `reports/oil/*.json`, если нет `data/case/oil/index.json`.
8. **Абсолютные пути** (не критично): `reports/case_detector/run.log:20`, `reports/case_pairs/experiment_run.log:7`, `reports/extra_data/content_overlap.csv:121`, `reports/selfcheck/consistency_latest.{json,md}`.

## Правки, сделанные в основном репозитории

- `scripts/case/find_pairs.py:293`: `"\* ½` → `"\\* ½` (1 строка; убран `SyntaxWarning`, строка по значению та же).

## Артефакты

- Клон: `out\clone_0445` (с `.venv` и `service\frontend_v3\node_modules`; `service\static_v3` пересобран — отличается от git только концами строк).
- Логи: `out\clone0445_run_all.log`, `out\clone0445_times.txt`, `out\npm0445_ci.log`, `out\npm0445_build.log`, `out\npm0445_times.txt`, `out\svc0445.err`.
- Сверка: `out\l103\cmp.py`, ответы API — `out\l103\api\{meta,metrics,studio_scenes,oil_meta}.json`.
- Скриншоты: `reports\selfcheck\clean_clone_0445_v3.png`, `reports\selfcheck\clean_clone_0445_studio.png`.
