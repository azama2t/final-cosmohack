# Ocean Scan (ESA) и Адриатика WorldView-2: что доступно без регистрации

Проверено 26.09 с 14:10 до 14:27. Регистраций не делали. Скачано: отчёт ESA C4000131552_ESR.pdf (1.0 МБ), JSON страниц портала, метаданные STAC. Строки для сводки — `docs/research/pairs/ocean_scan.csv`, колонки как в `PAIRS.csv`.

## ИТОГ (≤ 10 строк)
1. «Ocean Scan» — это не отдельный проект с WV-2. Это портал-база ESA Discovery (oceanscan.org, разработчик — Lobelia): «in-situ наблюдения + подобранные снимки», доступ к наблюдениям только после регистрации (бесплатно, по FAQ).
2. Набор «Adriatic Sea debris observations (WorldView-2)» (Franke, Brand; Remote Sensing Solutions GmbH, 2022): 2 260 наблюдений, дельта По, 331 км², класс FLOATING. Это **метки, снятые со снимка WV-2 (панхром 0.5 м)**, а не полевой счёт: «windrows, buoys and other floating objects». Измерений (`numMeasurements`) 0, фото (`numImages`) 0.
3. Дата противоречива: в метаданных 21.03.2021, в описании 24.03.2021. Лицензия не указана (`licence: null`). DOI 10.5281/zenodo.6327046 не зарегистрирован (doi.org и DataCite отвечают 404). Чипов WV-2 (256×256, 8 каналов, 1.6 м) на портале нет — они есть «в исходном наборе RSS».
4. Отчёт C4000131552_ESR.pdf — это проект RSS «From source to sink» (контракт 4000131552/20/NL/GLC). WV-2 снят во время рейса PoPLAST (R/V Dallaporta, март 2021). Объекты на WV-2 — **«suspected»**: буи на снасти, «предположительно пластик» в нитях, лодки. Проверки судном или дроном в отчёте нет.
5. Полевой счёт PoPLAST — это манта-тралы на микропластик (> 300 мкм): 45 проб по 20 мин, 12 трансект, 85 станций. Это не видимые предметы, поэтому для «снимок → шт.» уровень D. Бенчмарк-детали (время станций, численности) в открытом доступе не найдены.
6. Sentinel-2 в эти даты чистый: 21.03 (S2A, облачность тайлов 1–6 %) и 24.03.2021 (S2A, около 0 %). WV-2 в каталоге Copernicus CCM над районом нет (38 снимков за весь архив, марта 2021 среди них нет): это коммерческая покупка RSS.
7. Уровни: WV-2-метки × S2 того же дня — **B?** (кросс-сенсорная разметка без N; содержимое не видели). PoPLAST-манта — **D**. Дрон на пляжах и камеры на мостах По — **D**. Кандидатов **A нет**.
8. Следующий шаг: решение людей о регистрации на oceanscan.org (строка в `INBOX_REQUESTS.md`). После регистрации — выгрузка 2 260 наблюдений, уточнение даты и сверка нитей WV-2 с зонами нашего детектора на S2A 21.03 и 24.03.2021.

## 1. Что открыто без регистрации
| что | ссылка | что внутри | вывод |
|---|---|---|---|
| Главная Ocean Scan | https://www.oceanscan.org/ | «global database … in-situ observations and matching remote sensing images»; «Upon registration, users can access existing campaigns, related observations…»; финансирование — ESA Discovery | портал, а не набор |
| FAQ | https://www.oceanscan.org/faqs | доступ через веб, приложение и API; «download the metadata»; сервис бесплатный; матчинг только с Sentinel-1/2/3; одиночные предметы — от 1 м²; микропластик не принимается | регистрация бесплатная, но она нужна |
| Каталог наборов | https://www.oceanscan.org/datasets (JSON `__NEXT_DATA__` в HTML) | 23 кампании: название, автор, даты, `isDataPublic`, лицензия, число наблюдений | метаданные открыты |
| Карточка набора Адриатики | https://www.oceanscan.org/dataset/2b0ff297-edca-43a5-8d23-e03663fb46c9 | см. раздел 2 | — |
| Вкладка «Data» | https://www.oceanscan.org/dataset/2b0ff297-edca-43a5-8d23-e03663fb46c9/data | «Only registered users have full access to the observation data. Log in / Register» | **наблюдения закрыты регистрацией** |
| API | `https://api.oceanscan.org/api/web-v0` (адрес из JS портала) | 26.09 около 14:20 не отвечает: HTTP 000, тайм-аут 25–40 с на `/litterTypes` и `/campaign/<id>` | проверить не удалось |
| Карточка ESA | https://activities.esa.int/4000131552 (живая — 504; снимок Wayback 13.01.2026) | RSS GmbH; OSIP «Marine Litter»; Discovery; 24 месяца с 13.07.2020; 175 000 €; субподрядчики HYDROMOD, CNR, AEBAM, Бахройт, Коимбра, Ольденбург | документов на странице нет |
| Nebula ESA | https://nebula.esa.int/content/tackling-plastic-debris-challenge-its-source-%E2%80%93-linking-eo-data-multi-source-situ-data | статус Closed, 2020–2022, публичный документ — Executive Summary | ссылка на ESR |
| **Отчёт ESR** | https://nebula.esa.int/sites/default/files/neb_tec_study/1458/C4000131552_ESR.pdf (8 стр., 26.09.2022, Ref. D ES.1) | см. раздел 3 | прочитан целиком |

## 2. Набор «Adriatic Sea debris observations (WorldView-2)» — поля карточки
Источник — JSON страницы набора (поле `campaign`).
- Авторы: Jonas Franke (ProjectLeader, ContactPerson), Remote Sensing Solutions GmbH. Цитирование: «Franke, J., Brand, A. (2022). Adriatic Sea debris observations (WorldView-2) [Data set]. Ocean Scan. DOI: 10.5281/zenodo.6327046».
- Описание дословно: «hundreds of marine litter observations (windrows, buoys and other floating objects) for 24/03/2021 in the Northern Adriatic Sea (Po Delta). Observations were extracted from WorldView-2 panchromatic WV-2 images with 50-cm spatial resolution. The original dataset from Remote Sensing Solutions GmbH also contains hundreds of image chips (256x256 pixels) for the same observations, each composed of 8 spectral bands and with a pixel resolution of 1.6 m».
- `targetStart = targetEnd = 2021-03-21T12:00Z`. Это расходится с «24/03/2021» в описании. Полдень UTC похож на условное значение, а не на время съёмки. Время съёмки WV-2 — не проверено.
- Рамка: 12.417–12.630° в. д., 44.816–44.993° с. ш. Площадь 331 км².
- `stats`: 2 260 наблюдений, 0 измерений, 0 изображений, 61 152 «matching EO products», 100 % наблюдений с совпадениями. Совпадения — автоматический подбор Sentinel-1/2/3 порталом (по FAQ), это не проверенные пары.
- `isDataPublic: true`, но наблюдения показываются только вошедшим. `licence: null` — лицензия не указана.
- DOI 10.5281/zenodo.6327046: `https://doi.org/…` — 404, DataCite — 404, Zenodo API `records/6327046` — 404. Похоже, DOI был зарезервирован, но не опубликован. Для сравнения: у других кампаний портала записи Zenodo есть — Marlisat 6245460 (открытый JSON), TISPLALI 6409151 (restricted), Korea 10398851 (restricted).
- Схема наблюдения видна по открытому JSON Marlisat (Zenodo 6245460): `geometry` (полигон), `timestamp`, `class` (например PATCH), `estimatedPatchAreaM2`, `estimatedFilamentLengthM`, `sourceType`, `validationType` (например IN_SITU), `isAbsence`, `measurements[]`, `images[]`. Поля числа предметов в схеме нет. Число можно записать в `measurements`, но у набора Адриатики их 0.

**Какие метки (ответ на вопрос задания):**
- объекты — да: нити, буи, прочие плавающие объекты, выделенные на WV-2;
- материал — не указан (по отчёту — «suspected»);
- N предметов — нет;
- контуры — вероятно, да (в схеме полигоны), но не видели;
- полевая проверка — в карточке не указана, `validationType` не видели.

## 3. Отчёт C4000131552_ESR.pdf — что сказано (страницы по PDF)
- С. 2: проект «From source to sink», RSS + 6 партнёров, август 2020 — сентябрь 2022, район — река По и её дельта.
- С. 3–4, разд. 3.1: камеры Raspberry Pi на мостах По. 18 848 кадров, из них размечено 3 526. Faster-RCNN, AP 0.61. Это макропластик в реке, без спутниковой пары.
- С. 4, разд. 3.2: TSM по Sentinel-3 и S2 (10 м). **«Very high-resolution satellite data were acquired from the WorldView-2 mission during the main offshore data collection campaign (PoPLAST 2021 cruise)»**. Два подхода: края по панхрому + спектр, и SAM + края. «Objects such as suspected buoys along a fishing line, suspected plastic litter along windrows as well as fishing boats…». Рис. 3 — буи на снасти. Метрик точности и числа объектов в тексте нет.
- С. 5, разд. 3.3: PoPLAST — март 2021, две недели на R/V Dallaporta, 12 трансект, 85 станций. 45 проб манта-тралом (сеть 330 мкм, 20 мин), 15 проб зоопланктона, 15 проб грунта. Частицы > 300 мкм, FTIR: PE, PP, PS. На станциях с высокой численностью — фронты, «also visible in the satellite-based total suspended matter maps from the same date». Вторая кампания — октябрь 2021.
- С. 6–7, разд. 3.4: ROMS, ICHTHYOP, SHYFEM, HYDROMOD-Tracer. Рис. 5 — моделирование на 16 и 19 марта.
- С. 7, разд. 3.5: дроны (M600 + RedEdge, Phantom 4 Pro) на пляжах дельты, трансекты 100 м, LitterDrone, предметы > 2.5 см. Это пляж, а не вода.
- Таблиц численностей, дат и времени станций, идентификаторов WV-2 в ESR нет.

## 4. Sentinel-2 и VHR над районом (проверено)
- Earth Search STAC, `sentinel-2-l2a`, рамка набора, 15–31.03.2021: 16 дат-тайлов. Нужные даты:
  - **21.03.2021**: S2A_MSIL2A_20210321T100031_N0214_R122_T32TQQ_20210321T125704 (облачность тайла 4.4 %) и T33TUK (0.9 %);
  - **24.03.2021**: S2A_MSIL2A_20210324T101021_N0214_R022_T32TQQ_20210324T143456 (0.14 %) и T33TUK (0.002 %);
  - ещё 16, 19, 26, 29 и 31.03 — рейс PoPLAST шёл две недели.
- CDSE CCM (`ccm-optical`), рамка набора: 38 снимков за весь архив, даты съёмки 2017–2024 (Pléiades, GeoSat-2, Pléiades Neo и др.). **Марта 2021 и WorldView-2 нет**. WV-2 RSS — коммерческая покупка, открыто не выложена.

## 5. Публикации и Data availability
- Найдено в Crossref по запросам «PoPLAST», «Po delta WorldView-2 floating plastic», «WorldView-2 marine litter windrows Adriatic»: **статьи с WV-2 из этого проекта не найдено**. Поиск неполный: WebSearch в сессии был исчерпан, работали через Crossref.
- Смежное по району, но не из проекта: Taddia et al. 2021, Drones 5(4):140, doi 10.3390/drones5040140 (дрон на пляжах дельты По); Ciappa 2022, Remote Sens. 14:2409, doi 10.3390/rs14102409 (S2, север Адриатики, лето 2020). Data availability не открывали — не проверено.

## 6. Находки и уровни
| id (ocean_scan.csv) | что | уровень | обоснование | следующий шаг |
|---|---|---|---|---|
| OS-Adriatic-WV2-20210321 | 2 260 меток объектов и нитей на WV-2 0.5 м, дельта По | **B?** | независимая от S2 VHR-разметка того же дня; S2 чистый. Но N нет, материал «suspected», полевой проверки нет, дата 21 или 24.03, лицензии нет, содержимое не видели | регистрация (решение людей) → выгрузка → дата → сверка с зонами детектора на S2A 21.03 и 24.03 |
| OS-RSS-PoPLAST-2021-manta | 45 манта-проб микропластика, март 2021 | **D** | > 300 мкм не видно на S2 и WV-2; таблиц в открытом доступе нет | только как контекст фронтов; запрос станций — к CNR-ISMAR/RSS, если понадобится |
| OS-RSS-Po-drone-beach-2021 | дрон-ортомозаики пляжей, LitterDrone > 2.5 см | **D** | пляж, не вода; дат и N в ESR нет | нет |
| OS-RSS-Po-bridge-cameras | 18 848 кадров, 3 526 размечено, мосты По | **D** | река, камера, без спутниковой пары | возможен счётчик по фото, если данные откроют (не проверено) |
| OS-TOC-NPacific-2019-2021 | Ocean Scan: «Dispersed floating macroplastic incidences in the North Pacific Ocean» (The Ocean Cleanup), 416 наблюдений | **C?** (лид) | метаданные открыты, наблюдения за регистрацией; метод (судно или БПЛА) не проверен | смотреть после регистрации; сверить с ADIS/The Ocean Cleanup в реестре |
| OS-Mifdal-FloatingDebris | Ocean Scan: «Floating debris» (J. Mifdal, ESA), 3 378 наблюдений, 2018–2020 | **C?** (лид) | вероятно, метки по S2 (не проверено); N нет | после регистрации — сверить с MARIDA и нашими зонами |

Кандидатов A нет, поэтому строки «Ocean Scan → КРИТИК» нет.

## 7. Что нужно от людей
Строка в `INBOX_REQUESTS.md` «Ocean Scan (26.09 14:27)»: регистрация на oceanscan.org (бесплатно) и запрос в RSS о лицензии и чипах WV-2.
