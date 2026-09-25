# Проверка на чистом клоне, повтор (26.09.2026 00:03–00:10)

Способ тот же, что в `clean_clone_1941.md`. Клон сделан с GitHub (`git clone https://github.com/azama2t/final-cosmohack out\clean_clone_0003`). HEAD клона — `71c0500` (коммит 00:03:27). В 00:09 `git ls-remote` показывал на origin/main тот же `71c0500`, более нового коммита не было. Работал только по README клона. `.venv` создан внутри клона, `-Cpu`, `CUDA_VISIBLE_DEVICES=""`. Сервис :8000 не трогал. В обоих репозиториях ничего не коммитил.

## Итог

- От начала клона до карты — **≈ 6 мин**: клон 12 с, `.venv` с пакетами и маршрутом 3 мин 14 с, тесты 18 с, сервис 1,1 с, карта 7,5 с.
- **Отпечаток выходов совпал:** `fccf808a8817d7c4` в клоне, `fccf808a8817d7c4` в README и в `run_summary.json` из HEAD.
- **sha256 входного CSV совпал:** `30ce0a65e3880b83`. Концы строк теперь закреплены в `.gitattributes` (`data/** -text`, `task/** -text`, …). В клоне `i/lf w/lf`. Проблема CRLF из проверки 19:41 устранена.
- **Тесты кейса из README** (`tests\test_case_*.py tests\test_api_v3.py`): **225 passed, 2 skipped, 0 failed**, 17 с. Ровно как в README.
- **Числа README совпали:** 318 / 935 / 4436 / 29; summary 29 / 0 / 0; MARIDA test F1 0.8713; test концентрации S2 ridge_log 30.0 против 25.2, ΔMAE [−1.6; 12.9]; S1 knn5_log 370.0 против 371.1, ΔMAE [−60.7; 49.2].
- **Карта** 1366×768: 0 ошибок консоли, 0 неудачных запросов, 0 ответов ≥ 400. Скриншот: `reports/selfcheck/clean_clone_0003_map.png`.
- **Найдена одна ошибка в README:** пример сохранённого запроса для PowerShell не работает как написано. Причина — `$q.id` вместо `$q.query_id`, подробности ниже. Второй пример, через `curl`, в PowerShell 5.1 падает.

## Шаги

| # | Команда | Время | Результат |
|---|---|---|---|
| 1 | `git clone https://github.com/azama2t/final-cosmohack out\clean_clone_0003` | 12 с | 312 МБ вместе с .git, HEAD 71c0500 |
| 2 | `powershell -ExecutionPolicy Bypass -File run.ps1 -Case all -Offline -Cpu` | 3 мин 14 с (00:04:02–00:07:16) | `.venv` создан, пакеты из requirements-cpu.txt; exit 0; шаги 7.5 с: selection 0.1, pairs 1.9 (промахов кэша 0), quality 0.5, registry 0.7, concentration 2.9, detector 0.0, export 1.3 |
| 3 | `.venv\Scripts\python.exe -m pytest -q tests\test_case_*.py tests\test_api_v3.py` | 18 с | 225 passed, 2 skipped |
| 4 | `.venv\Scripts\python.exe -m service --port 8097` | 1,1 с до /health | проверки ниже; сервис остановлен, порт свободен |
| 5 | Playwright 1366×768 `http://127.0.0.1:8097/` (из основного .venv) | 7,5 с | 0 ошибок; в панели «935 наблюдений, 29 полос, 235 снимков»; легенда «29 обследованных участков…; 0…»; есть вкладка «Куда идти» |

Полную папку `tests` в этот раз не запускал: в 19:41 она шла 11 мин. Единственный тест, который тогда падал (`test_case_geometry::test_manifest_sha256_matches_files`), падал из-за CRLF. Эта причина теперь устранена в `.gitattributes`.

## API клона (:8097)

| Запрос | Ответ |
|---|---|
| `/api/v3/meta` summary | n_strips 29, n_confirmed_pairs 0, plastic_scenes 0, n_strips_quality_ok 12, подозрительных в полосах 0 / 0 |
| `/api/v3/observations` | 935 (233 мс) |
| `/api/v3/pairs` | 4436 |
| `/api/v3/zones` | 29 |
| `/api/v3/scenes` | 235 (в 19:41 было 236) |
| `/api/v3/metrics` | `concentration.final_test_summary` есть, на верхнем уровне его нет. S2 30.0 против 25.2, S1 370.0 против 371.1 |
| `/api/v3/export` | zones geojson / csv 29, observations csv / geojson 935, pairs csv / geojson 4436; все 200 |

### Пример сохранённого запроса из README (раздел 8), порт заменён на 8097

1. **Вариант для PowerShell 5.1**, скопирован как есть:
   - `$q = Invoke-RestMethod -Method Post … queries` → 201, запрос сохраняется.
   - `Invoke-RestMethod ".../queries/$($q.id)/run"` → **404** `нет такого эндпоинта: /api/v3/queries//run`. В ответе POST поле называется `query_id`, а не `id`: `{"query_id":"q_…","name":"Чёрное море","created_at":…,"query":{…}}`.
   - С `$q.query_id` запуск возвращает 200: observations 33, zones 24, scenes 217. Два запуска подряд без `ran_at` дают одинаковый результат (проверено).
   - `Invoke-WebRequest ".../export?layer=zones&format=geojson" -OutFile zones.geojson` → 29 объектов. Файл кладётся в текущую папку; в клоне он остался как неотслеживаемый `zones.geojson`.
   - Мелочь: `Invoke-RestMethod` в PS 5.1 показывает кириллицу кракозябрами (`Ð§ÑÑÐ½Ð¾Ðµ…`). Сервис отдаёт `Content-Type: application/json` без `charset=utf-8`, и PS 5.1 декодирует ответ как Latin-1. Сохранённое имя при этом верное: в сыром UTF-8 — «Чёрное море».
2. **Вариант через `curl`** (строка 417), в PowerShell 5.1, где `curl.exe` — это `C:\Windows\system32\curl.exe`:
   - POST с `'{\"name\":\"Чёрное море\",…}'` → **exit 3**, запрос не отправляется. PS 5.1 портит аргумент, в котором есть и `\"`, и пробел.
   - Тот же POST с именем без пробела (`Чёрное_море`) → 201, кириллица сохраняется верно.
   - В Git Bash (`/mingw64/bin/curl`) с `-d '{"name":"Чёрное море",…}'` → 400 BAD_PARAM: тело приходит не в UTF-8.
   - `curl -OJ ".../export?layer=zones&format=geojson&detection_status=detected"` → 200, `zones_2026-09-26.geojson`. Повторный запуск в той же папке даёт exit 23: `-J` не перезаписывает существующий файл.

## Сверка с README и основным

| Что | Клон | README / HEAD | Совпало |
|---|---|---|---|
| outputs_fingerprint | fccf808a8817d7c4 | fccf808a8817d7c4 | да |
| sha256 CSV | 30ce0a65e3880b83 | 30ce0a65e3880b83 | да |
| события / наблюдения / пары / зоны | 318 / 935 / 4436 / 29 | то же | да |
| отбор S2 / S1 | 63 / 83 | то же | да |
| маски 12 / 17, реестр 318 reject | то же | то же | да |
| тесты кейса | 225 passed, 2 skipped | 225 passed, 2 skipped | да |
| MARIDA test F1 | 0.8713 (из metrics.json, MARIDA нет) | 0.871 | да |
| test концентрации | S2 30.0 / 25.2; S1 370.0 / 371.1 | то же | да |
| numpy | 2.5.3 | README предупреждает, что может поставиться новее | ожидаемо |

Что осталось после проверки 19:41:
- В логе шага 5 по-прежнему печатается «пересчёт из npz совпал: test=None, val=None».
- В README (раздел 5, «Справочно») по-прежнему «kNN — 31.7», а лог шага 4 печатает «knn5 31.3».
- После прогона в клоне изменены `run_summary.json` и `detector_recomputed.json`. README теперь это объясняет, так что это не ошибка.

## Правки README

1. **Строка 427**: `Invoke-RestMethod "http://127.0.0.1:8000/api/v3/queries/$($q.id)/run"` → `Invoke-RestMethod "http://127.0.0.1:8000/api/v3/queries/$($q.query_id)/run"`.
2. **Строки 416–419**, блок с `curl`. Сейчас он помечен ```` ```powershell ````, но в PowerShell 5.1 не работает (exit 3 из-за пробела в имени). Нужно одно из двух:
   - пометить его как bash / cmd и написать «`curl.exe` в cmd или Linux / macOS»;
   - либо дать имя без пробела.
3. В то же место стоит дописать: `curl -OJ` не перезаписывает существующий файл (exit 23).
4. По желанию, в сервисе: отдавать `application/json; charset=utf-8`. Тогда `Invoke-RestMethod` в PS 5.1 покажет кириллицу нормально.
5. По желанию: в строке лога `run_all.py` при `None` писать «не пересчитано: нет data/MARIDA». Уточнить «kNN — 31.7» против «knn5 31.3».

## Артефакты

- Клон: `out\clean_clone_0003`.
- Лог маршрута: `out\clone0003_run_all.log`. Лог сервиса: `out\svc0003.err`.
- Скрипт скриншота: `out\shot0003.py`. Выгрузка curl: `out\curl0003\`.
- Скриншот: `reports\selfcheck\clean_clone_0003_map.png`.
