# Источники данных и лицензии

Проверено 25.09.2026 (страницы Zenodo, README/LICENSE на GitHub). Числа MARIDA — из `reports/eda/eda.md` (скрипт `scripts/eda_marida.py`).

| Источник | Что берём | Лицензия | Ссылка |
|---|---|---|---|
| MARIDA v1.0.0 (Zenodo, 01.08.2021) | патчи S2 (ACOLITE rhorc, 11 каналов), разметка 15 классов + confidence, официальные сплиты | **CC BY 4.0** (данные); код репозитория — MIT | https://doi.org/10.5281/zenodo.5151941 , https://github.com/marine-debris/marine-debris.github.io |
| MADOS (Zenodo 10664073) | опционально: 174 сцены / 2 803 патча, ACOLITE rhorc в родном разрешении | **CC BY 4.0** (данные, по странице Zenodo); код https://github.com/gkakogeorgiou/mados — MIT | https://zenodo.org/records/10664073 |
| marinedebrisdetector | предобученные UNet/UNet++ для демо на живых L2A | **MIT** (LICENSE в репозитории) | https://github.com/MarcCoru/marinedebrisdetector |
| Sentinel-2 L1C/L2A (Copernicus, ESA) | исходные снимки MARIDA/MADOS и живые сцены | Copernicus Sentinel data — свободное и открытое использование (Legal Notice on the use of Copernicus Sentinel Data and Service Information); требуется атрибуция «Contains modified Copernicus Sentinel data [год]» | https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice |
| Earth Search (Element 84), коллекция `sentinel-2-l2a` | STAC-поиск и COG живых сцен, без аккаунта | данные — лицензия Copernicus (см. выше); сервис — Element 84 / AWS Open Data | https://earth-search.aws.element84.com/v1 , https://github.com/Element84/earth-search |
| Planetary Computer `sentinel-2-l2a` (запасной) | то же | данные — Copernicus; сервис — Microsoft PC terms | https://planetarycomputer.microsoft.com/dataset/sentinel-2-l2a |

## Цитирование

- **MARIDA:** Kikaki K., Kakogeorgiou I., Mikeli P., Raitsos D.E., Karantzalos K. (2022). *MARIDA: A benchmark for Marine Debris detection from Sentinel-2 remote sensing data.* PLoS ONE 17(1): e0262247. https://doi.org/10.1371/journal.pone.0262247 . Dataset: https://doi.org/10.5281/zenodo.5151941
- **MADOS:** Kikaki K., Kakogeorgiou I., Hoteit I., Karantzalos K. (2024). *Detecting Marine Pollutants and Sea Surface Features with Deep Learning in Sentinel-2 Imagery.* ISPRS Journal of Photogrammetry and Remote Sensing. https://doi.org/10.5281/zenodo.10664073
- **marinedebrisdetector:** Rußwurm M., Venkatesa S.J., Tuia D. (2023). *Large-scale detection of marine debris in coastal areas with Sentinel-2.* iScience. https://github.com/MarcCoru/marinedebrisdetector
- **FDI:** Biermann L. et al. (2020). *Finding Plastic Patches in Coastal Waters using Optical Satellite Data.* Scientific Reports 10, 5364.
- Атрибуция снимков: «Contains modified Copernicus Sentinel data 2015–2026».

## Скачанные копии

- `data/MARIDA.zip` — sha256 `e19227596018348169b11a78d033890adb97d4b4f4c069f061cfcfab21d48d08` (файл `data/MARIDA.sha256`), распакован в `data/MARIDA/` (`patches/`, `shapefiles/`, `splits/`, `labels_mapping.txt`).
- MADOS — ещё не скачан (раскладка по коду репозитория: `Scene_<k>/{10,20,60}/<ACOLITE-префикс>_L2R_rhorc_<λ>_<crop>.tif`, `..._L2R_cl_<crop>.tif`, `..._conf_...`, `splits/`; патчи 240×240 при 10 м; имена папок сцен анонимные → тайл/дата берутся из ACOLITE-префикса файлов, см. `scripts/check_mados_overlap.py`).

## Сцены MARIDA (63, фактически в архиве; дата ISO, тайл MGRS)

Полная таблица с числом патчей, MD-пикселями, центрами и bbox WGS84 — `reports/marida_scenes.csv`.

S2_29-11-15_16PEC (2015-11-29), S2_17-7-16_51PTS (2016-07-17), S2_4-9-16_16PCC (2016-09-04), S2_3-11-16_16PDC (2016-11-03), S2_12-1-17_16PCC (2017-01-12), S2_12-1-17_16PEC (2017-01-12), S2_21-2-17_16PCC (2017-02-21), S2_29-8-17_51RVQ (2017-08-29), S2_30-8-17_16PCC (2017-08-30), S2_9-10-17_16PEC (2017-10-09), S2_6-12-17_48MYU (2017-12-06), S2_18-1-18_48PZC (2018-01-18), S2_16-2-18_16PEC (2018-02-16), S2_21-2-18_16PCC (2018-02-21), S2_26-2-18_16PCC (2018-02-26), S2_4-3-18_50LLR (2018-03-04), S2_8-3-18_16PEC (2018-03-08), S2_8-3-18_16QED (2018-03-08), S2_20-4-18_30VWH (2018-04-20), S2_11-6-18_16PCC (2018-06-11), S2_30-8-18_16PCC (2018-08-30), S2_14-9-18_16PCC (2018-09-14), S2_19-9-18_16PCC (2018-09-19), S2_19-9-18_16PDC (2018-09-19), S2_7-10-18_52SDD (2018-10-07), S2_24-10-18_16PDC (2018-10-24), S2_3-11-18_16PDC (2018-11-03), S2_14-11-18_48PZC (2018-11-14), S2_6-12-18_48MXU (2018-12-06), S2_13-12-18_16PCC (2018-12-13), S2_11-1-19_19QDA (2019-01-11), S2_12-1-19_16PEC (2019-01-12), S2_27-1-19_16PCC (2019-01-27), S2_27-1-19_16QED (2019-01-27), S2_24-4-19_36JUN (2019-04-24), S2_18-5-19_51PTS (2019-05-18), S2_25-5-19_48MXU (2019-05-25), S2_4-9-19_16PCC (2019-09-04), S2_24-11-19_48PZC (2019-11-24), S2_1-12-19_48MYU (2019-12-01), S2_7-3-20_18QYG (2020-03-07), S2_14-3-20_18QYF (2020-03-14), S2_19-3-20_18QYF (2020-03-19), S2_22-3-20_18QWF (2020-03-22), S2_24-3-20_18QYF (2020-03-24), S2_24-8-20_16PCC (2020-08-24), S2_15-9-20_18QYF (2020-09-15), S2_18-9-20_16PCC (2020-09-18), S2_18-9-20_16PDC (2020-09-18), **S2_23-9-20_16PCC (2020-09-23, помечена excluded — см. ниже)**, S2_28-9-20_16PCC (2020-09-28), S2_28-9-20_16PDC (2020-09-28), S2_15-10-20_18QYF (2020-10-15), S2_20-10-20_18QYF (2020-10-20), S2_15-11-20_16PCC (2020-11-15), S2_29-11-20_18QYF (2020-11-29), S2_4-12-20_18QYF (2020-12-04), S2_12-12-20_16PCC (2020-12-12), S2_14-12-20_18QYF (2020-12-14), S2_22-12-20_18QYF (2020-12-22), S2_29-12-20_18QYF (2020-12-29), S2_3-1-21_18QYF (2021-01-03), S2_23-1-21_18QYF (2021-01-23).

## Оговорки

- Сцена `S2_23-9-20_16PCC`: по ТЗ §2 авторы MARIDA её исключили (плохая атмосферная коррекция). **В архиве v1.0.0 она есть** (56 патчей, все в train, 77 MD px). В README GitHub и на странице Zenodo упоминания об исключении не найдено — источник утверждения не подтверждён; помечаем `excluded=True` и не используем как «подтверждённую» для демо.
- Разметка MARIDA частичная (размечено 0.93 % пикселей) — площадь/массу пластика по маскам не считаем.
- marinedebrisdetector обучался в том числе на MARIDA → его метрики на MARIDA не независимы.
