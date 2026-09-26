# Новые размеченные S2-данные: PLP и FloatingObjects (L89, §11 п.2)

Дата: 26.09.2026, 00:54–03:20. Исполнитель L89. Скрипты: `scripts/extra_data/`. Данные: `data/extra/` (не в git). Реестр: `data/extra/registry.csv` и копия `reports/extra_data/registry.csv`.

## Коротко
| источник | сцен (съёмок) | B (метки → px) | D (метки → px) | тип | лицензия |
|---|---|---|---|---|---|
| PLP2019 (Zenodo 3752719) | 5 | 48 px с долей пластика > 0 | 15 px с 0 % | искусственная мишень, доля покрытия по дрону | CC-BY-4.0 |
| PLP2021 (зеркало PLP.zip) | 21 | 21 HDPE (209 px) + 15 дерево (160 px) | — | искусственная мишень | не указана (зеркало в MIT-репозитории) |
| PLP2022 (зеркало PLP.zip) | 12 | 24 HDPE + 12 PVC (78 px, все меньше пикселя) | — | искусственная мишень | не указана |
| FloatingObjects (Mifdal 2021) | 26 | 3 378 линий → 373 033 px | — | естественные скопления (смешанный плавающий материал) | Apache-2.0 (репозиторий), ссылка на статью |
| RefinedFloatingObjects (Rußwurm 2023) | 6 (5 используются) | 1 774 точки → 1 395 px | 2 916 точек → 1 181 px | естественные скопления / проверенные отрицательные | MIT (репозиторий), ссылка на статью |

Итого новых уникальных съёмок S2: 38 PLP + 27 FO/Refined (26 + marmara; durban исключена) = **65**, все с меткой уровня B. Уровень D есть в 10 из них (5 PLP2019, 5 Refined). Плюс **320 202 пикселя U** (непроверенная вода вдали от меток, SCL = 6). Это не D, для оценки ложных срабатываний он годится только как «фон».

Файлы признаков: тот же `src/macroplastic/features/pixel.py`, уровень `win`, 48 признаков, те же имена.
- `data/extra/features_plp.npz` — 15 342 px. Поля: X, y (1 пластик, 2 дерево, 0 фон/0 %), frac (доля пикселя под мишенью), level (B/D/U), kind, material, group → scenes, date, xy (EPSG:32635), names, readme.
- `data/extra/features_floatingobjects.npz` — 680 979 px. Поля: X, y, level, kind (fo_line / refined_pos / refined_neg / background_unverified), group → scenes (регион = одна съёмка), crop → crops, scl, xy, epsg, tile, item_id, names, readme.

## Что скачано и как (≤ 2 ГБ)
| что | МБ | как |
|---|---|---|
| PLP.zip (PLP 2021/22: S2 133×134 px, 12 каналов, + shp мишеней; PlanetScope не используем) | 39.7 | целиком |
| PLP2019_dataset.rar (ACOLITE L2W netCDF 42×24 px, точки % покрытия, фото с дрона) | 68.9 | целиком; распакован системным tar.exe |
| FloatingObjects: оглавление 18.9 ГБ zip + shp + маски | 34 | HTTP Range (`remote_zip.py`), сцены НЕ качались |
| RefinedFloatingObjects: оглавление 11.9 ГБ zip + shp точек | 101 | HTTP Range (из-за перебора блоков; исправлено) |
| Пиксели FO/Refined: 86 чтений, 154 ячейки по 10.24 км, 27 регионов | ≈ 1 398 (оценка по размеру блоков COG) | Earth Search L2A той же съёмки (тайл + дата) через `src/macroplastic/live/stac.py` |
| Проверки (panama, PLP против L2A) | ≈ 50 | |
| **Всего** | **≈ 1.69 ГБ** | На диске 1.6 ГБ: plp 112 МБ (архивы удалены, sha256 в `data/extra/plp/archives.sha256`), crops 1.4 ГБ, npz 104 МБ |

Почему пиксели FO берутся из Earth Search, а не из архива: архив сцен весит 18.9 + 11.9 ГБ в deflate-zip, и читать из него кусками нельзя. Сцена та же (тайл и дата из поля `image` shapefile или из поиска STAC по центру меток, с проверкой покрытия). Обработка — наш live-стандарт (Sen2Cor L2A, исходный baseline `_0`, правила scale/offset из `stac.py`). Такие признаки ровно того же вида, что на инференсе. Метки нарисованы по снимкам GEE, геопривязка сошлась: медиана FDI−фон (FDI_dmed15) на пикселях линий положительна во всех 26 регионах, на точках Refined pos 0.027 против 0.0002 у neg.

**Проверка источника пикселей PLP.** GeoTIFF PLP 2021 и Earth Search L2A той же даты (20210711) совпадают бит в бит по всем 12 каналам: корреляция 1.000, наклон 1.000, медианы равны. PLP 2022 совпадает с L2A Planetary Computer (корреляция 0.998–1.000, сдвиг +0.0005…0.0008: у Earth Search после 2022 тёмная вода обрезана на DN=1, см. `fetch_live.pick_item`). Значит, PLP 2021/22 — тот же тип данных, что наш live. PLP2019 — ACOLITE rhos: ближе к MARIDA (rhorc), но не то же самое.

## Формат и смысл меток
- **PLP 2021**: HDPE-мишень (круг r = 14 м, ≈ 6 px) и деревянная мишень (r = 14 м), 21 дата 11.06–24.09.2021. Примечания в данных: 15.08 «under water?», 20.08 «partly underwater» (сохранены в `notes`). y=2 для дерева: это натуральный материал, а не пластик.
- **PLP 2022**: PVC 5×5 м и 2 круга HDPE r = 3.5 м (≈ 38 м², меньше пикселя 100 м²), 12 дат. Три даты помечены «difficult to see». Дубль 26.06.2022 в shp удалён. Ни одного пикселя с долей ≥ 0.5, то есть это смешанные пиксели.
- **PLP2019**: доля пакетов+бутылок/тростника/моря в каждом 10-м пикселе (по фото с дрона), 5 дат. Максимальная доля пластика 0.43. Два пикселя 18.05 лежат вне AOI netCDF и отброшены. Строки netCDF идут с юга на север (lat[0,0] — южный край), массив перевёрнут, проверено по lat/lon.
- **FloatingObjects**: ломаные, проведённые по объектам. Растеризация all_touched, как в marinedebrisdetector: центральная линия шириной 1–2 px, край объекта не размечен. Поэтому метки шумные: медиана FDI-контраста линий 0.004, у точек Refined 0.027.
- **Refined**: точки по одному пикселю, type 1 = marine debris, type 0 = размеченный отрицательный (вода, суда, следы, берег; подтип не указан). В исходной работе это val/test — самая чистая часть.

## Пересечения с нашими данными (без дублей и утечки)
Метод 1, тайл + дата + bbox (`build_registry.py` → `reports/extra_data/overlap.csv`). Проверены MARIDA (все 63 сцены всех сплитов), data/live (все сцены), data/pairs (best_per_event).
- **SAME_ACQUISITION: 1 сцена** — Refined `durban_20190424` = MARIDA train `S2_24-4-19_36JUN` = наш live `durban/2019-04-24`. Исключена, пиксели не читались.
- То же место, другая дата (утечки пикселей нет):
  - `mandaluyong_20180314` (51PTS) — это место MARIDA **test** (S2_17-7-16, S2_18-5-19) и live manila. Если судья — MARIDA test, на этом регионе не учить (866 px).
  - accra, lagos ×2, kolkata (ganges), riodejaneiro (guanabara) — места live-сцен 2025–26; для обучения это нормально, для демо их надо отметить.
- С pairs (Северное и Чёрное море, GPGP) пересечений нет.

Метод 2, содержимое (для MADOS, у которого нет ни тайла, ни даты; `mados_content_check.py` → `reports/extra_data/content_overlap.csv`). 48-битный LBP (B8, B4, окно 5×5) из L13, индекс 60.3 млн ключей по 2 803 кропам MADOS и 1 381 патчу MARIDA. Каждый пиксель новых изображений голосует за пару «изображение, сдвиг». Результат: **0 совпадений из 119** (33 PLP + 86 кропов FO), максимум 6 голосов. Положительный контроль: live durban 2019-04-24 (Sen2Cor) против MARIDA 36JUN (ACOLITE) — **7 579 голосов**. Порог совпадения — 30. Хеши файлов PLP против патчей MARIDA (`provenance_check.py`): 0 совпадений по тайлу+дате, bbox, SHA-1, pHash и корреляции превью.

Итог: кроме durban, дублей и утечек пикселей нет. Искусственные мишени PLP и естественные скопления FO/Refined лежат в разных файлах и различаются полем `kind` в реестре.

## Флаги качества (измерено, в `registry.csv` → `notes` / `use_for_training`)
| регион | что видно | рекомендация |
|---|---|---|
| tangshan_20180130 | январь, Бохайский залив. Линии: B3 0.088 против 0.056 у фона, B11 0.004, FDI-контраст 0.0018. Похоже на лёд или шугу. Это 107 311 px (29 % всех пикселей линий) | review first; не брать без просмотра |
| toledo_20191221 | декабрь, оз. Эри, пресная вода, FDI-контраст 0.0002 ≈ фон | review first |
| tunisia_20180715 | 0 % пикселей линий — вода по SCL; B8 0.08, B11 0.07 (мелководье или осушка) | review first |
| mandaluyong_20180314 | место MARIDA test | не учить, если судья — MARIDA test |

## Текущий детектор на новых данных (zero-shot, только замер)
`weights/lgbm`, порог 0.63 из meta.json (`eval_current_detector.py` → `reports/extra_data/current_detector_zero_shot.csv`). Доля пикселей с p ≥ 0.63:
| набор | px | доля ≥ порога | медиана p | p90 |
|---|---|---|---|---|
| Refined B (точный мусор) | 1 395 | **0.028** | 0.0007 | 0.21 |
| Refined D | 1 181 | 0.000 | 0.0000 | 0.00 |
| FO линии B | 373 000 | 0.011 | 0.0000 | 0.002 |
| PLP HDPE 2021, доля ≥ 0.5 | 114 | 0.009 | 0.0001 | 0.10 |
| PLP дерево 2021 | 160 | 0.000 | 0.004 | 0.05 |
| PLP2019 пластик > 0 | 48 | 0.083 | 0.0000 | 0.002 |
| фон U (FO + PLP) | 320 202 | 0.000 | 0.000 | 0.000 |

По регионам у Refined: neworleans 0.096, venice 0.081, lagos 0.007, accra **0.000** (681 px), marmara 0.000.

Гипотезы с числами для детекторного исполнителя (L92):
1. **Текущая модель на Sen2Cor L2A новых мест почти не видит подтверждённый мусор.** Полнота 2.8 % на точной разметке Refined против 0.915 на val MARIDA. При этом ложных срабатываний на D и U нет вовсе (0 из 1 181 и 0 из 320 202). Это согласуется с «5 объектов на 22 сценах пар». Порог 0.63 выбран на ACOLITE-val; на L2A вероятности сдвинуты вниз (p90 = 0.21). Проверять надо так: дообучить на FO/Refined B + D (сплит по `group`) и смотреть полноту на отложенных регионах Refined при фиксированной доле ложных срабатываний на U/D.
2. **Линии FO шумные, точки Refined чистые.** FDI-контраст 0.004 против 0.027, медиана NDVI −0.27 против +0.13. Линии лучше брать с малым весом или только пиксели с положительным контрастом. Refined разумно держать как валидацию по регионам (так делали авторы).
3. **Искусственные мишени PLP — отдельный тест «малых объектов».** Мишени 2022 меньше пикселя (0 px с долей ≥ 0.5), у HDPE 2021 NDVI отрицательный (−0.14), у мусора MARIDA — положительный. Смешивать PLP с естественными скоплениями в одну метку нельзя. PLP подходит как тест чувствительности к доле покрытия (есть frac), а не как обучающий позитив.

## Воспроизведение
```
.venv/Scripts/python.exe scripts/extra_data/remote_zip.py --url <zip> --list ... / --extract "floatingobjects/shapefiles/*" ...
.venv/Scripts/python.exe scripts/extra_data/plp_features.py
.venv/Scripts/python.exe scripts/extra_data/fo_fetch.py plan
.venv/Scripts/python.exe scripts/extra_data/fo_fetch.py fetch --budget-mb 1400
.venv/Scripts/python.exe scripts/extra_data/fo_features.py
.venv/Scripts/python.exe scripts/extra_data/build_registry.py
.venv/Scripts/python.exe scripts/extra_data/mados_content_check.py
.venv/Scripts/python.exe scripts/extra_data/eval_current_detector.py
```
URL: PLP.zip, floatingobjects.zip и refinedfloatingobjects.zip — `https://marinedebrisdetector.s3.eu-central-1.amazonaws.com/data/`; PLP2019 — `https://zenodo.org/api/records/3752719/files/PLP2019_dataset.rar/content`. Выбор ячеек и оценка МБ — `data/extra/floatingobjects/cells.csv` и `fetch_ledger.csv`; сцены STAC — `items.json`.

## Цитирование
Mifdal, Longépé, Rußwurm 2021 (ISPRS Annals V-3-2021, 285–293); Carmo, Mifdal, Rußwurm 2021 (OCEANS); Rußwurm, Venkatesa, Tuia 2023 (iScience 26, 108402); Papageorgiou, Topouzelis, Suaria, Aliani, Corradi 2022 (PLP 2021); Topouzelis et al., PLP2019, Zenodo 3752719 (CC-BY-4.0).
