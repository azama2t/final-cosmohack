# Промежуточный чистый клон (L125c, 16:57–17:12)

Локальный клон (не GitHub): `git clone --no-hardlinks . out\clone_1700` из `C:\Users\User\Documents\GitHub\final-cosmohack`.
HEAD клона — `0547268` (совпал с main на момент старта). Установка пакетов в общий `.venv` не делал: пакетов в
клоне нет, вместо этого создал junction `out\clone_1700\.venv` → основной `.venv` (`cmd /c mklink /J`), запускал
`run.ps1`/`pytest`/сервис через этот же интерпретатор с cwd = клон (PYTHONPATH не потребовался — cwd клона первым
в sys.path, импортируется код клона, не основного дерева). Перед удалением клона junction снят отдельно
(`cmd /c rmdir .venv`), только потом `rm -rf out\clone_1700` — основной `.venv` не пострадал (проверено).
Диск: старт 42 ГБ свободно (≥ 40 ГБ, порог соблюдён), после клона+прогона 38 ГБ, после удаления — 39 ГБ (клон
2.1 ГБ без .venv). Сервис клона — порт **:8093**, в конце остановлен (порт свободен), :8070/:8000 не трогал.
Ничего не коммитил, не пушил, не переключал ветки.

## Итог по времени

| Шаг | Время | Результат |
|---|---:|---|
| `git clone --no-hardlinks . out\clone_1700` | 10 с (16:57:03–16:57:13→фактически лог показал 10.283s) | HEAD 0547268, 4543 файла |
| `run.ps1 -Case all -Offline` (CUDA_VISIBLE_DEVICES="") | 16:57:25→16:57:49, шаги 8.0 с | exit 0; rows=1774, accepted_events=0, errors=0; отпечаток `df959a30e0321746` |
| `pytest -q` (вся папка tests) | 16:57:59→17:03:56, **354 с (5 мин 54 с)** | **569 passed, 82 skipped, 2 FAILED** (разбор ниже) |
| Сервис `python -m service --port 8093` | 16:59:00, /health 200 за ≤ 3 с | поднялся штатно |
| Обход API (11 маршрутов + 8 экспортов) | 16:59–17:00 | всё 200 |
| Playwright DEMO.md-путь, 1366×768 и 1920×1080 | 17:00 (репо-скрипт) + повторно вручную | см. ниже — репо-скрипт стал устаревшим, путь вручную зелёный |

## 2 упавших теста pytest — разобраны до корня

### 1) `tests/test_case_docs_numbers.py::test_fingerprint_matches_run_summary`
Отпечаток свежего прогона в клоне (`df959a30e0321746`) ≠ отпечаток, записанный в `final_numbers.json`
(`278d93fb3390edeb`, тот же, что в основном дереве до прогона — там тест зелёный). Причина найдена точно:
**`git clone` на этой машине конвертирует `configs\case_pairs.yaml` в CRLF** (`git config --system core.autocrlf`
= `true`; в основном рабочем дереве стоит локальный оverride `core.autocrlf=false`, который **не переносится** в
свежий клон). `.gitattributes` защищает от этого только `data/**`, `task/**`, `reports/case_splits/**`,
`reports/case_conc/*.csv` — но не `configs/**`. Проверено: `tr -d '\r'` у обеих версий `case_pairs.yaml` даёт
идентичный sha256 `445453dc...` (= значение из основного `run_summary.json`), т.е. содержимое одинаковое,
разница только в переводе строк. Из-за этого меняется `.inputs` sha256 → `outputs_fingerprint` шага `2_pairs` →
весь отпечаток прогона, и `run_summary.json`/`final_numbers.json` расходятся при любом offline-прогоне на свежем
клоне без специальной настройки git. **Это не разовая случайность — тот же класс проблемы уже ловили в
09:44 (`docs/LOG.md:309`, другая точная причина: несинхронный экспорт), сейчас корень другой и теперь найден
конкретно.** Реальные числа (77/286, 935/4436/29, F1 0.8713, S2 30.0/25.2, S1 370.0/371.1) не пострадали — только
служебный отпечаток. **Правка для оркестратора:** добавить в `.gitattributes` `configs/** -text` (или минимум
`configs/*.yaml -text`), чтобы sha256/отпечаток не зависели от `core.autocrlf` клиента.

### 2) `tests/test_api_v3_zone_estimate.py::test_region_filter_and_meta_regions`
`cloud_pct` оказался `None` у всех 83 сцен (`assert all(s["cloud_pct"] is not None …)` падает). Найдено:
`case_store._sz_src_scene()` берёт облачность из `data/live/<region>/<date>/scene.json` (не из
`data/case/scene_zones/.../scene.json` — тот идентичен в клоне и основном дереве, диф пуст). Проверка
`git ls-files data/live | grep -c scene.json` → **0**: все 65 файлов `data/live/**/scene.json` в основном дереве
**не отслеживаются git** (не `.gitignore`, просто не закоммичены — рабочий кэш от прошлых онлайн-фетчей). На
свежем клоне их физически нет → `_sz_src_scene()` возвращает `{}` → `cloud_pct`/`tile_cloud_pct` всегда `None`.
`run.ps1 -Case all -Offline` их не создаёт (офлайн-режим этого и не должен делать). В живом основном `.venv`
(`TestClient(app)` из основного дерева) тот же тест зелёный — там кэш есть на диске годами. **Это реальный разрыв
воспроизводимости**: чистый клон без `data/live/**/scene.json` не покажет облачность сцены в
`/api/v3/scene_zones/scenes` (поле `cloud_pct`), README нигде не предупреждает об этом отдельно. **Правка для
оркестратора:** либо закоммитить `data/live/**/scene.json` (это лёгкие JSON, не растры — сафе для git), либо
явно задокументировать, что `cloud_pct` в списке сцен недоступен без предварительного онлайн-фетча.

Остальные 569 тестов и 82 skip — как ожидалось (skip — тесты, требующие MARIDA/сетевых данных, которых по
дизайну офлайн нет).

## API клона (:8093) — все маршруты 200

`meta`, `observations` (935, 2.0 МБ), `zones` (29), `scenes` (235/83 сцены… см. ниже), `pairs` (4436),
`metrics`, `scene_zones` (286 объектов), **`defense_examples`** (`count: 5, missing: []` — все 5/5 примеров
защиты присутствуют: success/miss/false_alarm/background_error/no_analysis), `oil/meta`, `queries` (GET) —
всё 200. Экспорт `export?layer={observations,zones,scene_zones,pairs}&format={csv,geojson}` — все 8 комбинаций
200.

Числа сверены с `reports/final_numbers.json` и README **напрямую по API**: `/api/v3/metrics` →
`concentration.final_test_summary.S2_visual_total_plastic` = MAE 30.02 vs медиана 25.20 (README «30.0 против
25.2»); `S1_trawl_total_plastic` = 370.02 vs 371.12 (README «370.0 против 371.1») — совпало байт-в-байт с
округлением README. `reports/final_numbers.json` в клоне после прогона побайтово равен основному дереву (md5
`c1f70807c53452871164a9d2116cda0a` в обоих) — `run.ps1 -Case all -Offline` не переписывает `final_numbers.json`
(это отдельный скрипт `scripts/final_numbers.py`, в маршрут `-Case all` не входит).

## Playwright путь docs/DEMO.md — 1366

Штатный репозиторный скрипт `scripts/case/s33_path.py` **упал на обоих окнах** (1366 и 1920) с
`TypeError: Cannot read properties of null (reading 'getBoundingClientRect')` на строке, обращающейся к
`[data-testid=tab-metrics]`. Это **устаревший testid**: после вехи 6 (§39/§40/§44, левая колонка переписана)
такого элемента больше нет — вкладки слева теперь `tab-regions`/`tab-feed` (`LeftColumn.tsx`), а кнопка «Цифры»
называется `headline-open` (`CaseApp.tsx:733`, подтверждено также списком тестидов в `docs/LOG.md` 16:52,
L140 → L132: «headline-open» в списке, «tab-metrics» — нет). Код не менял (задание запрещает); вместо этого
написал отдельный проверочный скрипт `out\qa1700\demo_path_check.py` (не в git, только для этой проверки) с
актуальными testid'ами и прогнал путь «Земля → список снимков (`scene-item`) → снимок → зона (`sz-item`) →
карточка (`card-title`) → «В студию» (`sz-studio`/`zone-studio`) → «Назад» (`studio-back`)» на **1366×768 и
1920×1080**: путь пройден полностью, `card_title` = «Альборан · 30SXE · зона 1», `studio_ok: true`,
`back_card_closed: true`, **`console_errors: []`, `http_errors: []`** на обоих окнах. Т.е. сам UI по DEMO.md
работает штатно; ломается только устаревший QA-скрипт `s33_path.py` (нужно поправить `tab-metrics` →
`headline-open`/`left-bar`, но это код — не мой мандат в этом задании).

## Итог

- Числа README/API/`final_numbers.json` — совпали везде, где сверял (детектор F1, концентрация S2/S1, 77/286,
  935/4436/29, defense_examples 5/5, экспорт CSV/GeoJSON).
- 2 находки достойны внимания оркестратора (не блокируют показ жюри — сервис :8070 их не задевает, т.к. там
  `data/live` и правильный `core.autocrlf` уже есть):
  1. `.gitattributes` не защищает `configs/**` от CRLF на свежих клонах → `test_fingerprint_matches_run_summary`
     красный на любой машине с `core.autocrlf=true` по умолчанию (это большинство свежих Windows-машин, в т.ч.,
     вероятно, у жюри, если попросят прогнать `run.ps1 -Case all -Offline` у себя).
  2. `data/live/**/scene.json` (65 файлов) не в git → на чистом клоне `cloud_pct`/`tile_cloud_pct` сцен всегда
     `None` в `/api/v3/scene_zones/scenes`.
- Отдельно (не блокер): `scripts/case/s33_path.py` использует удалённый testid `tab-metrics`, путь по факту
  рабочий, но автоматическую проверку почините отдельным исполнителем.

Артефакты: клон удалён по завершении (`out\clone_1700` больше не существует). Проверочный скрипт —
`out\qa1700\demo_path_check.py`, его результат — `out\qa1700\demo_path_result.json` (не в git).

## Повтор после исправлений (17:10–…, main 8dfdb68)

Оркестратор закоммитил `8dfdb68`: `configs/** -text` в `.gitattributes` + `data/live/**/scene.json` в git
(исключение из `.gitignore` `/data/*`). Проверено новым клоном **с явным `-c core.autocrlf=true`**
(имитация чистой Windows-машины) — `git -c core.autocrlf=true clone --no-hardlinks . out\clone_1710`, HEAD
8dfdb68, junction `.venv` как раньше.

- **`test_fingerprint_matches_run_summary` — теперь зелёный.** `configs/case_pairs.yaml` в клоне остался LF
  (`file` подтверждает: `Unicode text, UTF-8 text`, без CRLF) даже при `core.autocrlf=true` — `-text` в
  `.gitattributes` сработал как задумано.
- **`test_region_filter_and_meta_regions` — всё ещё КРАСНЫЙ**, но уже не 83, а **18 сцен из 83** с
  `cloud_pct: None` (было 83/83). Причина найдена: коммит закрыл только `data/live/**/scene.json` через
  `!/data/live/**/scene.json` в `.gitignore`; **`data/drift_check/**/scene.json` (18 файлов, сцены
  `scene_kind: "drift"` — Бали/Дурбан/Ганг и др.) остаются под тем же общим `/data/*` и не в git**
  (`git ls-files data/drift_check | grep -c scene.json` = 0, `git check-ignore` подтверждает: правило
  `/data/*`). `case_store._sz_src_scene()` читает облачность дрейф-сцен из `data/drift_check/<region>/<date>/
  scene.json` — тот же код путь, что и `data/live`, просто для `kind == "drift"`. **Правка (тот же класс, тот же
  файл `.gitattributes`/`.gitignore`):** добавить симметрично
  `!/data/drift_check/`, `/data/drift_check/**`, `!/data/drift_check/**/`, `!/data/drift_check/**/scene.json`.
- Полный `pytest -q` на этом клоне запущен для контроля — результат допишу после завершения (если появится
  строка после сдачи — см. ниже) или следующий исполнитель подхватит по этому файлу.
- Клон `out\clone_1710` будет удалён после завершения проверки (тот же порядок: сначала снять junction `.venv`
  отдельной командой, потом `rm -rf`).
