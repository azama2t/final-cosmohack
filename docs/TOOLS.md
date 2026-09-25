# TOOLS — первые 60 минут с датасетом организаторов

Инструменты первого часа (основной порядок действий — `TOMORROW.md`: ingest → convert → predict_org → train_lgbm_ingest --cv →
predict_org → score). Все команды — **PowerShell из корня репозитория**, копипастой. Вместо `D:\org\train`
подставьте папку организаторов, вместо `org` — короткое имя. Все инструменты только читают исходные данные.

```powershell
cd <корень репозитория>
$env:PYTHONPATH = "src"
$env:CUDA_VISIBLE_DEVICES = "-1"   # "-1", не "": в PowerShell 5.1 "" удаляет переменную -> lgbm уходит на GPU
$I = "data\ingest\org"             # <- папка ingest (adapter.yaml, converted\, converted_test\)
$ORG = "D:\org\train"          # <- папка организаторов
```

| минуты | шаг | инструмент | результат |
|---|---|---|---|
| 0–10 | ТЗ, правила, метрика, формат сабмита | — | заметки: классы, метрика, формат ответа |
| 10–15 | что в данных | `inspect_dataset.py` | `reports\tools\org_inspect\index.html`, `pairs_guess.csv` |
| 15–25 | как построена разметка | `label_forensics.py` | `reports\tools\org_forensics\forensics.md` |
| 15–25 (параллельно) | пересечения с нашим train | `provenance_check.py` | `reports\tools\org_provenance\provenance.md` |
| 25–40 | привести к нашему формату | `organizer_adapter` | `data\organizer\org\` + `manifest.csv` |
| 40–60 | переобучение LightGBM на adapter-выходе, метрика организаторов | `train_lgbm_ingest.py` | число на val |

## 1. inspect_dataset.py — что в данных (≈20 с на MARIDA, 15 с на пожарном датасете)

```powershell
.venv\Scripts\python.exe scripts\tools\inspect_dataset.py --data-dir $ORG --out reports\tools\org_inspect
start reports\tools\org_inspect\index.html
```
Опции: `--max-files 20000` (заголовки), `--sample-per-group 40` (статистика пикселей), `--max-mask-files 4000` (баланс классов).

Смотреть: группы файлов по шаблону имени (`S2_{date}_{tile}_{n}_cl.tif`), каналы/dtype/nodata/CRS/разрешение по группам,
`descriptions` (имена каналов — сразу в конфиг адаптера), подсказки по шкале (DN ×1e-4? смещение −1000 в L2A ≥ 04.00?
SCL?), какая группа — маски (мало целых значений) и баланс классов, семейства «снимок↔маска» (`pairs_guess.csv`).

## 2. label_forensics.py — насколько маска выводима из спектра (≈1.5–2.5 мин)

```powershell
# рекомендуется: ВСЕ пары из манифеста ingest (сырые файлы, группы = сцены; каналы/scale/offset из adapter.yaml)
.venv\Scripts\python.exe scripts\tools\label_forensics.py --manifest (Get-ChildItem $I\converted\*\manifest.csv).FullName --out reports\tools\org_forensics
# после inspect: пары из pairs_guess.csv (по полному имени внутри сцены; колонка group = сцена)
.venv\Scripts\python.exe scripts\tools\label_forensics.py --pairs-csv reports\tools\org_inspect\pairs_guess.csv --scale 0.0001 --nodata-zero --out reports\tools\org_forensics
# или явно (glob; маски отдельно; значения-игнор; группы по сцене из имени)
.venv\Scripts\python.exe scripts\tools\label_forensics.py --images "$ORG\images\*.tif" --masks "$ORG\masks\*.tif" --ignore 255 --group-regex "(T\d{2}[A-Z]{3}_\d{8})" --out reports\tools\org_forensics
# пре/пост (2 источника; индексы и их разности dNBR и т.п. считаются сами)
.venv\Scripts\python.exe scripts\tools\label_forensics.py --images "$ORG\pre\*.tif" --images "$ORG\post\*.tif" --labels pre,post --masks "$ORG\masks\*.tif" --out reports\tools\org_forensics
# наши пресеты (проверка инструмента)
.venv\Scripts\python.exe scripts\tools\label_forensics.py --preset marida --out reports\tools\marida_forensics
.venv\Scripts\python.exe scripts\tools\label_forensics.py --preset fire --out reports\tools\fire_bs_forensics
```
По умолчанию берутся ВСЕ найденные пары; выше `--max-files` (3000) — случайная выборка с явным «ВЫБОРКА: N из M».
Ключ пары по умолчанию — полное имя без суффикса маски (`r05_001_mask` → `r05_001`): одинаковые номера в разных сценах
больше не склеиваются (в репетиции 1: молча 30 из 207 пар); совпадения id печатаются как «ВНИМАНИЕ».
Полезные опции: `--scale 0.0001 --offset 0` (пороги в отражении, а не в DN), `--band-names B2,B3,B4,B8` (если нет descriptions), `--exclude-bands SCL`, `--merge "12,13,14,15:7"`,
`--meta-csv meta.csv --meta-id-col chip_id --meta-group-col event_id` (группы для сплита), `--split-lists a.txt b.txt`
(+`--group-by-split` — фолды = сами списки), `--cap 300`, `--no-lgbm`.

Как читать `forensics.md`:
- «ОДИН ПОРОГ почти воспроизводит маску» (CV F1 ≥ 0.9) → разметка, вероятно, сделана порогом индекса: воспроизводим правило, ML не нужен.
- дерево-3 F1 ≥ 0.9 → разметка — простое правило; правила дерева в конце отчёта.
- LGBM ≫ дерево и AUC ≈ 1 → класс спектрально отделим, но не одним правилом: наш бустинг LightGBM подходит.
- AUC высокий, F1 низкий → редкий класс, ложные срабатывания на больших классах: нужен порог по val и контекст (окна).
- геометрия: `1-px доля` высокая — точечная разметка; `rect мед.` ≈ 1 на больших компонентах — прямоугольники/полигоны;
  `recall edge ≪ recall int` — буфер/полигон шире объекта; `border8` ≫ 1 — метки режутся краем тайла.
- все F1 — на полной популяции пикселей (выборка с весами), сплит по группам (сцены/события), никогда по случайным пикселям.

## 3. provenance_check.py — пересечения с нашими обучающими данными (≈10–20 с)

```powershell
# ТОЛЬКО train+val MARIDA по спискам сплитов (test MARIDA не читаем)
.venv\Scripts\python.exe scripts\tools\provenance_check.py --ours data\MARIDA\splits\train_X.txt data\MARIDA\splits\val_X.txt --list-root data\MARIDA\patches --theirs $ORG --within --out reports\tools\org_provenance
# самопроверка: MARIDA train vs val по спискам сплитов
.venv\Scripts\python.exe scripts\tools\provenance_check.py --ours data\MARIDA\splits\train_X.txt --theirs data\MARIDA\splits\val_X.txt --list-root data\MARIDA\patches --within --out reports\tools\marida_provenance
```
Проверки: сцена (MGRS-тайл + дата из имён/тегов), только тайл, только дата, пересечение bbox в WGS84 (> 5 % меньшего),
SHA-1 файлов, перцептивный хеш и корреляция превью 16×16 (max по 8 поворотам/отражениям). `--within` — дубликаты и
перекрытия внутри данных организаторов (важно для своего train/val). Маски пропускаются (`--exclude`, по умолчанию файлы
в папках `masks/labels/gt/annotations` и имена с `_cl|_conf|mask|label|_gt|_lbl`; в репетиции 1 PNG из `masks/` давали 39 ложных совпадений). Выход: `provenance.md`, `pairs.csv`, `theirs_files.csv`, `summary.json`.

Решение: общие сцены/тайлы с MARIDA → в отчёте про метрику на данных организаторов это оговорить или обучить без них;
перекрытия внутри theirs → групповой сплит по ним.

## 4. organizer_adapter — к нашему формату (минуты на конфиг, ≈45 с на 1381 патч)

```powershell
copy configs\adapter_example.yaml configs\adapter_org.yaml
notepad configs\adapter_org.yaml     # root, image_glob, mask_glob/id_regex, bands, scale/offset, resolution, classes.map
.venv\Scripts\python.exe scripts\tools\adapter.py --config configs\adapter_org.yaml --dry-run
.venv\Scripts\python.exe scripts\tools\adapter.py --config configs\adapter_org.yaml --out data\organizer --limit 20
.venv\Scripts\python.exe scripts\tools\adapter.py --config configs\adapter_org.yaml --out data\organizer
# самопроверка на MARIDA: вывод адаптера == прямое чтение marida.load_patch
.venv\Scripts\python.exe scripts\tools\adapter.py --config configs\adapter_marida.yaml --out data\organizer --verify-marida 60
```
Внутренний формат: `data\organizer\<name>\images\<id>.tif` — (C,H,W) float32 отражательная способность, NaN = nodata,
descriptions = наши имена каналов (B1…B12, B8A); `masks\<id>.tif` — uint8 в нашей схеме (1 = Marine Debris,
7 = Marine Water, …, 0 = не размечено/игнор); `manifest.csv` — id, split, пути, размер, CRS, разрешение, bbox WGS84,
доля NaN, число размеченных и MD-пикселей, исходные файлы. В коде без записи на диск:
`from macroplastic.organizer_adapter import load_config, iter_samples`.

Ловушки конфига: регэкспы в YAML — **только в одинарных кавычках** (`'(S2_[^/]+?)\.tif$'`; в двойных `\d` — ошибка
YAML); `bands.source: descriptions` работает, только если в GeoTIFF есть имена каналов, иначе список по порядку;
L2A после 25.01.2022 в сыром DN — `offset: -0.1` (DN×1e-4 − 0.1), проверить по воде (B8 ≈ 0.00–0.03) в `--dry-run`;
маски в PNG с цветами — `classes.rgb_palette`; каналы отдельными файлами (jp2 из SAFE) — `layout.band_files`.

## Тесты

```powershell
.venv\Scripts\python.exe -m pytest tests\test_tools.py tests\test_ingest.py -q
```

## 5. ingest: архив → отчёт → автоконфиг → внутренний формат

```powershell
.venv\Scripts\python.exe scripts\tools\ingest.py D:\org\dataset.zip --out $I            # отчёт + adapter.yaml, README в консоли
.venv\Scripts\python.exe scripts\tools\ingest.py D:\org\dataset.zip --out $I --target-class 3 --channels B8,B4,B3,B2,B11,B12,B5,B6,B7,B8A,B1 --scale 0.0001 --offset 0 --ignore-values 255
.venv\Scripts\python.exe scripts\tools\ingest.py D:\org\dataset.zip --out $I --convert  # train -> converted\, test -> converted_test\ (--split train|test|all)
```
- Приоритет: флаг > README организаторов (README*/*.txt/*.md, текст в начале отчёта и в консоли; разбираются легенда
  классов «3 - marine debris», порядок каналов, «x 10000») > угадывание.
- Значения классов — по ВСЕМ маскам («проверено N из M»; выше `--max-masks` 20000 — стратифицированная выборка по сценам).
- Имена `r05_001` (префикс сцены + номер) — одна группа, а не группа на сцену.
- Цель не названа при нескольких ненулевых классах, число каналов не совпадает, масштаб не ясен → БЛОКЕР:
  `ingest_blockers:` в `adapter.yaml`, `--convert` возвращает 3 (снять флагом, правкой YAML или `--force`).
- Маппинг: цель → 1, прочие ненулевые → 7, их 0 → 99 («фон/не размечено», решает `train_lgbm_ingest --zero-as`).
- `layout.test_glob` — снимки без масок (test): конвертируются отдельно, `predict_org.py` читает их напрямую.

## 6. train_lgbm_ingest.py — обучение и развилка «как есть vs их train» (≈1.5 мин на 207 чипов 240×240, CPU)

```powershell
.venv\Scripts\python.exe scripts\train_lgbm_ingest.py --data-root $I --cv 4 --zero-as both --baseline weights\lgbm --out weights_exp\lgbm_ingest\org_cv
```
Групповой K-fold по сценам; OOF-вероятности по ВСЕМ пикселям их train (их метрика; `--metric-zero ignore` — только
размеченные); оба режима нуля (`negative`/`ignore`); `--baseline` (наша модель как есть) на тех же пикселях; порог — лучший
OOF (сетка до 0.999); финальная модель — на всём train. Выход: `model.txt`, `meta.json` (threshold, zero_as, band_fill),
`cv.json`. Дообучение: `--init-model weights\lgbm --set lgbm.num_boost_round=100`. Без `--cv` — прежний режим (один сплит).

## 7. predict_org.py — сабмит в формате организаторов (≈17 с на 147 чипов, CPU)

```powershell
.venv\Scripts\python.exe scripts\tools\predict_org.py --config $I\adapter.yaml --weights weights_exp\lgbm_ingest\org_cv --out out\sub --format png --value-debris 3 --value-bg 0 --zip
```
Та же предобработка, что при обучении (адаптер: порядок каналов, scale/offset, nodata). `--images <папка|glob>` (по умолчанию
`layout.test_glob`), `--format png|tif` (tif — с геопривязкой источника), `--threshold` (по умолчанию из meta.json), `--prob`,
`--value-nodata`, `--device cpu` (по умолчанию). Нет нужных модели каналов → ошибка с кодом 2 (или `--fill-missing`);
0 px во всех масках → предупреждение. Проверено: на 40 чипах val MARIDA маски и вероятности побитово равны `inference.py`.

## 8. score.py — локальная метрика (их val или отложенная часть train)

```powershell
.venv\Scripts\python.exe scripts\tools\score.py --pred out\val_pred --gt <папка масок val> --target 3 [--ignore 255] [--zero-as ignore] [--json out\score.json]
```
F1/IoU/P/R класса по пулу пикселей всех файлов, F1/IoU по каждому классу, macro-F1 и mIoU, худшие файлы, пропущенные/лишние
файлы и несовпадение размеров (проверка формата). На репетиции совпадает с `scripts\rehearsal\score_private.py` (TP/FP/FN).

## 9. screenshots.py — кадры UI и замер скорости карты

```powershell
.venv\Scripts\python.exe scripts\screenshots.py --base-url http://127.0.0.1:8000 --out reports\screens\check --gl gpu
```

10 кадров PNG и `result.json` (load, fps, ошибки консоли) в папке `--out`. По умолчанию отслеживаемый `reports\ui_perf.md` не меняется; строка замера дописывается туда только с флагом `--log-perf`. `--video` — запись демо-тура, `--gl gpu` — WebGL на видеокарте (по умолчанию swiftshader).
