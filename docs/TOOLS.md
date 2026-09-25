# TOOLS — первые 60 минут с датасетом организаторов

Четыре инструмента. Все команды — **PowerShell из корня репозитория**, копипастой. Вместо `D:\org\train`
подставьте папку организаторов, вместо `org` — короткое имя. Все инструменты только читают исходные данные.

```powershell
cd C:\Users\User\Documents\GitHub\final-cosmohack
$env:PYTHONPATH = "src"
$env:CUDA_VISIBLE_DEVICES = ""
$ORG = "D:\org\train"          # <- папка организаторов
```

| минуты | шаг | инструмент | результат |
|---|---|---|---|
| 0–10 | ТЗ, правила, метрика, формат сабмита | — | заметки: классы, метрика, формат ответа |
| 10–15 | что в данных | `inspect_dataset.py` | `reports\tools\org_inspect\index.html`, `pairs_guess.csv` |
| 15–25 | как построена разметка | `label_forensics.py` | `reports\tools\org_forensics\forensics.md` |
| 15–25 (параллельно) | пересечения с нашим train | `provenance_check.py` | `reports\tools\org_provenance\provenance.md` |
| 25–40 | привести к нашему формату | `organizer_adapter` | `data\organizer\org\` + `manifest.csv` |
| 40–60 | переобучение L3 (LightGBM) на adapter-выходе, метрика организаторов | L3 | число на val |

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
# после inspect: пары снимок-маска из pairs_guess.csv
.venv\Scripts\python.exe scripts\tools\label_forensics.py --pairs-csv reports\tools\org_inspect\pairs_guess.csv --out reports\tools\org_forensics
# или явно (glob; маски отдельно; значения-игнор; группы по сцене из имени)
.venv\Scripts\python.exe scripts\tools\label_forensics.py --images "$ORG\images\*.tif" --masks "$ORG\masks\*.tif" --ignore 255 --group-regex "(T\d{2}[A-Z]{3}_\d{8})" --out reports\tools\org_forensics
# пре/пост (2 источника; индексы и их разности dNBR и т.п. считаются сами)
.venv\Scripts\python.exe scripts\tools\label_forensics.py --images "$ORG\pre\*.tif" --images "$ORG\post\*.tif" --labels pre,post --masks "$ORG\masks\*.tif" --out reports\tools\org_forensics
# наши пресеты (проверка инструмента)
.venv\Scripts\python.exe scripts\tools\label_forensics.py --preset marida --out reports\tools\marida_forensics
.venv\Scripts\python.exe scripts\tools\label_forensics.py --preset fire --out reports\tools\fire_bs_forensics
```
Полезные опции: `--band-names B2,B3,B4,B8` (если нет descriptions), `--exclude-bands SCL`, `--merge "12,13,14,15:7"`,
`--meta-csv meta.csv --meta-id-col chip_id --meta-group-col event_id` (группы для сплита), `--split-lists a.txt b.txt`
(+`--group-by-split` — фолды = сами списки), `--cap 300`, `--no-lgbm`.

Как читать `forensics.md`:
- «ОДИН ПОРОГ почти воспроизводит маску» (CV F1 ≥ 0.9) → разметка, вероятно, сделана порогом индекса: воспроизводим правило, ML не нужен.
- дерево-3 F1 ≥ 0.9 → разметка — простое правило; правила дерева в конце отчёта.
- LGBM ≫ дерево и AUC ≈ 1 → класс спектрально отделим, но не одним правилом: бустинг L3 подходит.
- AUC высокий, F1 низкий → редкий класс, ложные срабатывания на больших классах: нужен порог по val и контекст (окна).
- геометрия: `1-px доля` высокая — точечная разметка; `rect мед.` ≈ 1 на больших компонентах — прямоугольники/полигоны;
  `recall edge ≪ recall int` — буфер/полигон шире объекта; `border8` ≫ 1 — метки режутся краем тайла.
- все F1 — на полной популяции пикселей (выборка с весами), сплит по группам (сцены/события), никогда по случайным пикселям.

## 3. provenance_check.py — пересечения с нашими обучающими данными (≈10–20 с)

```powershell
.venv\Scripts\python.exe scripts\tools\provenance_check.py --ours data\MARIDA\patches --theirs $ORG --within --out reports\tools\org_provenance
# самопроверка: MARIDA train vs val по спискам сплитов
.venv\Scripts\python.exe scripts\tools\provenance_check.py --ours data\MARIDA\splits\train_X.txt --theirs data\MARIDA\splits\val_X.txt --list-root data\MARIDA\patches --within --out reports\tools\marida_provenance
```
Проверки: сцена (MGRS-тайл + дата из имён/тегов), только тайл, только дата, пересечение bbox в WGS84 (> 5 % меньшего),
SHA-1 файлов, перцептивный хеш и корреляция превью 16×16 (max по 8 поворотам/отражениям). `--within` — дубликаты и
перекрытия внутри данных организаторов (важно для своего train/val). Маски пропускаются (`--exclude`, по умолчанию
`_cl|_conf|mask|label|_gt|_lbl`). Выход: `provenance.md`, `pairs.csv`, `theirs_files.csv`, `summary.json`.

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
.venv\Scripts\python.exe -m pytest tests\test_tools.py -q
```

## 5. ingest: архив → отчёт → автоконфиг → внутренний формат → LightGBM (L18)

`.venv\Scripts\python.exe scripts\tools\ingest.py D:\org\train.zip --out data\ingest\org` (безопасная распаковка, `report\index.html` с разделом «Сомнения», `adapter.yaml`) → то же с `--convert` → `scripts\train_lgbm_ingest.py --data-root data\ingest\org`. Подробно: раздел «Как подать датасет» в `TOMORROW.md`; тесты `tests\test_ingest.py`.
