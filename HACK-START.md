# HACK-START — первые 60 минут после выдачи кейса (черновик L50)

Коротко, по шагам. Подробности и развилки — `TOMORROW.md`, команды инструментов — `docs/TOOLS.md`.
Все флаги ниже сверены с `--help` (25.09). Test MARIDA не читать. Каждое решение — одна строка в `docs/DECISIONS.md`, каждый сабмит — в `docs/TIMELINE.md`.

```powershell
cd C:\Users\User\Documents\GitHub\final-cosmohack
$env:PYTHONPATH = "src"
$env:CUDA_VISIBLE_DEVICES = "-1"    # именно "-1": в PowerShell 5.1 "" удаляет переменную
$PY  = ".venv\Scripts\python.exe"
$ZIP = "D:\org\dataset.zip"         # <- архив или папка организаторов
$I   = "data\ingest\org"            # <- рабочая папка датасета
```

## 0–10 мин. Кейс и правила

- [ ] **Метрика**: формула, класс(ы), micro/macro, пиксели / объекты / чипы / ячейки. **Все пиксели или только размеченные?**
- [ ] **Формат сабмита**: тип файла, имена, dtype, код класса мусора и фона, zip/папка, пример файла.
- [ ] **Данные**: каналы и их порядок, уровень (L1C/L2A), масштаб (×1e-4? offset −1000?), классы масок, есть ли val.
- [ ] **Лимиты**: сабмитов в день, публичный/приватный лидерборд, время/железо инференса.
- [ ] **Баллы**: вес модели, сервиса, презентации; что смотрят в сервисе.
- [ ] **Запреты**: внешние данные (MARIDA/MADOS), предобученные веса (marinedebrisdetector), код/контейнер.

Вопросы организаторам — одним сообщением, список из 10 пунктов в `TOMORROW.md` (раздел 0–10). Первые три: метрика по всем или по размеченным пикселям; какой класс оценивается; можно ли MARIDA/MADOS и открытые веса.

## 10–20 мин. Загрузчик и HTML-отчёт

```powershell
& $PY scripts\tools\ingest.py $ZIP --out $I              # распаковка, отчёт, автоконфиг adapter.yaml
start $I\report\index.html
# если README/отчёт определил неверно — флаги важнее README:
& $PY scripts\tools\ingest.py $ZIP --out $I --target-class 3 --channels B8,B4,B3,B2,B11,B12,B5,B6,B7,B8A,B1 --scale 0.0001 --offset 0
#   ещё: --ignore-values 255  --test-glob "test/**/*.tif"  --format dirs
```

Что проверить в отчёте (сверху вниз):
- **«Извлечено из текста»**: целевой класс, каналы, масштаб совпадают с ТЗ.
- **Значения масок**: «проверено N из M», целевой класс присутствует.
- **«Сомнения»**: `[blocker]` = 0 (иначе `--convert` откажет с кодом 3 — снять флагом или правкой `adapter.yaml`); все «важно» прочитаны; `classes.map`: цель → 1, прочие ненулевые → 7, их 0 → 99.
- **Превью**: RGB выглядит естественно (иначе неверный порядок каналов), медиана B8 на воде ≈ 0.00–0.03.
- **Геопривязка**: есть ли CRS у снимков (нужна для карты, см. 55–60).

## 20–30 мин. Разбор разметки

```powershell
& $PY scripts\tools\ingest.py $ZIP --out $I --convert     # train -> $I\converted\, test -> $I\converted_test\
& $PY scripts\tools\label_forensics.py --manifest (Get-ChildItem $I\converted\*\manifest.csv).FullName --scale 0.0001 --nodata-zero --out reports\tools\org_forensics
```

Читать: дерево глубины 3 или один порог индекса дают F1 ≥ 0.9 → разметка — правило (учить его); F1 ≪ 0.5 → пиксельно много не выжать, решают порог и контекст. Смотреть геометрию разметки: частичная ли она (доля неразмеченного — от неё зависит `--zero-as`).

## 30–40 мин. Адаптер, конвертация, провенанс

```powershell
& $PY scripts\tools\adapter.py --config $I\adapter.yaml --dry-run            # список пар + загрузка первой, ничего не пишет
# пересечения с НАШИМ train: только train+val MARIDA (test MARIDA не трогать)
& $PY scripts\tools\provenance_check.py --ours data\MARIDA\splits\train_X.txt data\MARIDA\splits\val_X.txt --list-root data\MARIDA\patches --theirs $I\unpacked --within --out reports\tools\org_provenance
```

Общие сцены с MARIDA → оценка «как есть» на их данных завышена, записать в `DECISIONS.md`.

## 40–55 мин. Первый сабмит и обучение на их train

**Коды классов.** Во всех командах ниже `3` и `0` — только пример. Подставить свои:
- `--value-debris` / `--value-bg` у `predict_org` — коды мусора и фона **в их сабмите** (из README/ТЗ; в репетиции 3 мусор = 1, фон = 0).
- `score.py --target` — код мусора **в их масках (GT)**; `--pred-target` — код мусора **в наших масках** (= `--value-debris`, с которым писали предсказания). Совпадают — `--pred-target` не нужен. Пример: GT мусор = 2, сабмит мусор = 1 → `--target 2 --pred-target 1`. Строку `per class` при разных кодах не читать — она сравнивает несовпадающие значения; главная цифра — `target_metrics`.

**Проверка групп CV перед обучением** (одна команда):
```powershell
$M = (Get-ChildItem $I\converted\*\manifest.csv).FullName; $R = Import-Csv $M; "файлов $($R.Count), групп $(($R | Select-Object -ExpandProperty group -Unique).Count)"
```
Групп столько же, сколько файлов (анонимные имена `tile_0001`) → сцен в именах нет, CV по файлам **завышена** (репетиция 3: 0.50 по файлам, 0.38 по географии 5 км, private 0.35). Тогда добавить `--group geo` (кластеры по координатам чипов, радиус `--geo-radius-km`, по умолчанию 5 км); скрипт и сам предупредит строкой «ВНИМАНИЕ: группа = файл». Групп 1–3 → CV почти бессмысленна, записать в `DECISIONS.md`. Первая строка вывода `train_lgbm_ingest` — `groups: by scene | by file | by geography …`: так видно, по чему реально идёт CV.

```powershell
# 1) сабмит «как есть» (формат проверяется раньше качества)
& $PY scripts\tools\predict_org.py --config $I\adapter.yaml --weights weights\lgbm --out out\sub_asis --format png --value-debris 3 --value-bg 0 --zip --device cpu
# 2) CV на их train: оба режима нуля + наша модель как базовая линия (~1.5 мин на CPU в репетиции)
#    анонимные имена (групп = файлов) -> добавить  --group geo
& $PY scripts\train_lgbm_ingest.py --data-root $I --cv 4 --zero-as both --baseline weights\lgbm --out weights_exp\lgbm_ingest\org_cv
#    метрика только по размеченным пикселям -> добавить  --metric-zero ignore
# 3) выбор по их val (главный критерий). НЕТ val -> пункт 3 пропустить, решать по CV (правило ниже)
& $PY scripts\tools\predict_org.py --config $I\adapter.yaml --images <папка val images> --weights weights_exp\lgbm_ingest\org_cv --out out\val_trained --value-debris 3 --prob
& $PY scripts\tools\score.py --pred out\val_trained --gt <папка val masks> --target 3                    # 0 = негатив (все пиксели)
& $PY scripts\tools\score.py --pred out\val_trained --gt <папка val masks> --target 3 --zero-as ignore   # только размеченные
#    код мусора в GT != --value-debris предсказаний:  --target <код GT> --pred-target <наш код>
# 4) сабмит лучшей модели
& $PY scripts\tools\predict_org.py --config $I\adapter.yaml --weights weights_exp\lgbm_ingest\org_cv --out out\sub_trained --format png --value-debris 3 --value-bg 0 --zip
```

- 0 px мусора во всех файлах = ошибка каналов/масштаба/порога (скрипт предупредит).
- **Урок репетиции: при метрике по всем пикселям порог ≈ 0.99–0.999**, а не 0.63 (порог MARIDA по размеченным пикселям). «Как есть» с порогом 0.63 дало 0.058, с порогом из CV — 0.199; обучение на их train (0 = негатив) — 0.317 (`reports/l35_asis_check.md`, `reports/rehearsal2.md`). Порог брать из CV/val (`--threshold`), не подбирать по лидерборду.
- Нет val → по умолчанию «обучено на их train»; «как есть» только если выше на `max(0.01, 2×std)` по фолдам.

## 55–60 мин. Их данные на карте

```powershell
# вероятности test: predict_org --prob пишет out\pred_test_prob\
& $PY scripts\tools\predict_org.py --config $I\adapter.yaml --weights weights_exp\lgbm_ingest\org_cv --out out\pred_test --prob
# порог той же модели, что дала сабмит (из её meta.json; без --threshold карта возьмёт 0.63 из weights\lgbm -> в разы больше находок)
$T = (Get-Content weights_exp\lgbm_ingest\org_cv\meta.json -Raw | ConvertFrom-Json).threshold; "порог $T"
& $PY scripts\tools\org_to_map.py --chips <папка их GeoTIFF> --pred lgbm=out\pred_test_prob --threshold "lgbm=$T" --config $I\adapter.yaml --out out\org_map
#   с их масками (P/R/F1 на карте):  --labels <папка масок> --label-debris 3
& $PY scripts\validate_service_data.py out\org_map                      # 0 ошибок
& $PY -m service --port 8090 --data-root out\org_map                   # интерфейс v2 по умолчанию
#   откат на v1:  $env:MACROPLASTIC_UI = "v1"; & $PY -m service --port 8090 --data-root out\org_map
```

`"lgbm=$T"` — именно в кавычках: PowerShell подставит `0.999` с точкой (в консоли число показывается как `0,999`, это нормально). Карта при этом рисует находки при том же пороге, что и сабмит (в репетиции 3 без `--threshold` было ≈ 2400 px против 335 px сабмита).

Анонимные имена без дат: районы называются `tile_p1…` (одинаковые имена получают суффикс зоны UTM или номера группы: `tile_p1_utm16n`, `tile_p1_utm51n`), дата-заглушка 1900-01-01 помечена в manifest `date_unknown`, и v2 пишет «дата неизвестна». На демо так и говорить: дата снимка неизвестна. «N чипов без сигнала» в логе `org_to_map` — это чипы, где вероятность 0 везде (чистая вода), не ошибка.

(`org_to_map.py` — дорожка L49, пройдено в репетиции 3 и перепроверено в L52. Если скрипт падает — запасной путь «сцены → `data\live\<region>\<date>` → `scripts\build_service_data.py`» из `TOMORROW.md`, раздел «Развилки».)

Что переключается на их данные:
- **manifest.json** корня `out\org_map` с `kind: "organizer"` (`docs/CONTRACTS.md`); сервис берёт корень из `--data-root` (или `$env:MACROPLASTIC_DATA`, или `run.ps1 -DataRoot out\org_map`).
- **Фронт**: **v2 по умолчанию**; откат — `$env:MACROPLASTIC_UI = "v1"` перед запуском сервиса.
- **Слои по их данным**: находки, зоны обследования, H3, история; инциденты и лента (`/api/incidents`, `/api/feed`) строятся из того же корня автоматически.
- **Не переносится**: дрейф (нужны реальные даты и форсинг), контекст OSM (есть только для наших 18 районов).

## Развилки

- **Нет геопривязки (нет CRS)**: `org_to_map.py` остановится (exit 2). Сабмит не страдает; для карты — показывать наши живые районы, а их данные — галереей/таблицей метрик.
- **Классификация чипов вместо сегментации**: `predict_org.py --prob`, агрегат по чипу (max или доля пикселей ≥ порога), порог по val; см. `TOMORROW.md` → «Если формат…».
- **Другие каналы / меньше каналов**: `reports/l23_channels_speed.md` (модели на RGB, RGB+NIR, 10+20 м); в адаптере `bands.missing: nan`, `train_lgbm_ingest` сам заполнит соседними; «как есть» без каналов — ошибка (или `predict_org --fill-missing`, костыль).
- **Нет NVIDIA**: `powershell -ExecutionPolicy Bypass -File run.ps1 -Cpu` (ставит `requirements-cpu.txt`); весь первый час и так на CPU.
- **Внешние данные запрещены**: только их train; `weights\lgbm` — только для сравнения, не в сабмит.
