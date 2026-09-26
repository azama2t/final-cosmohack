# Связь морского мусора, спутниковых снимков и плотности

Это самостоятельное исследование исходного CSV хакатона и открытых первичных наборов данных. Старые результаты проекта и файлы Марка в расчётах не используются. Главный результат — [`results/REPORT.md`](results/REPORT.md). Машиночитаемые таблицы: [`concrete_examples.csv`](results/concrete_examples.csv), [`source_verdicts.csv`](results/source_verdicts.csv), [`plp2019/labeled_pixels.csv`](results/plp2019/labeled_pixels.csv).

## Структура

- `data/raw/` — скачанные первичные архивы, наблюдения и погодные ответы API; содержимое не изменялось.
- `data/source_inventory.json` — размеры, SHA256 и происхождение первичных файлов.
- `results/adis_pairs/` — шесть геопривязанных Sentinel-2 кропов, отрисованные маршруты, локальные фото объектов, геометрия и оценки дрейфа.
- `results/plp2019/` — совпоставление меток доли пластика с реальными спектрами пикселей, графики и статистика.
- `results/fml_samples/` — пять действительных кадров с разметкой.
- `scripts/` — повторяемые этапы загрузки, сопоставления и проверки.

## Окружение

Из корня рабочей папки:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r quantitative_link_study/requirements.txt
```

Для распаковки RAR необходим системный `bsdtar`. `PLP2019_dataset.rar` уже распакован в `data/raw/plp2019/extracted/`.

## Повторение исследования

Все команды ниже выполняются из корня рабочей папки. `fetch_sources.py` пропускает уже существующие файлы; при чистом запуске он скачивает в том числе архив FML объёмом 2.34 ГБ. Остальные скрипты используют скачанные файлы и выполняют сетевые запросы к указанным в коде STAC и метео API.

```sh
.venv/bin/python quantitative_link_study/scripts/fetch_sources.py
.venv/bin/python quantitative_link_study/scripts/extract_plp2019.py
.venv/bin/python quantitative_link_study/scripts/fetch_plp_min_fraction.py
.venv/bin/python quantitative_link_study/scripts/analyze_fml.py
.venv/bin/python quantitative_link_study/scripts/analyze_plp_pixels.py
.venv/bin/python quantitative_link_study/scripts/plot_plp_examples.py
.venv/bin/python quantitative_link_study/scripts/verify_plp_catalog.py
.venv/bin/python quantitative_link_study/scripts/match_adis_sentinel.py
.venv/bin/python quantitative_link_study/scripts/match_adis_landsat.py
.venv/bin/python quantitative_link_study/scripts/build_adis_pairs.py
.venv/bin/python quantitative_link_study/scripts/estimate_adis_drift.py
.venv/bin/python quantitative_link_study/scripts/verify_adis_pairs.py
.venv/bin/python quantitative_link_study/scripts/audit_ruiz_catalog.py
.venv/bin/python quantitative_link_study/scripts/audit_open_ocean.py
.venv/bin/python quantitative_link_study/scripts/verify_downloads.py
.venv/bin/python quantitative_link_study/scripts/build_final_tables.py
```

`fetch_plp_min_fraction.py` читает ZIP по HTTP Range и скачивает только две даты и четыре файла из архива PLP2022/23. `extract_plp2019.py` автоматически распаковывает RAR, сохраняя внутреннюю структуру каталога.

## Проверка файлов

`results/download_verification.json` фиксирует шесть непустых GeoTIFF, 5 bands, 1200×1200 пикселей, корректный UTM CRS и покрытие обследованных маршрутов. Первичные файлы суммарно занимают 3.015 ГБ (13 файлов); основная часть — FML ZIP. Удалять архивы для повторения анализа не требуется.
