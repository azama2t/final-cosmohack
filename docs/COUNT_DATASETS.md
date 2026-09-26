# COUNT_DATASETS — реестр данных «изображение → число предметов» (INBOX §17–§23, финал)

**Финальная версия** (§23 п.1). Её свёл L112 из `reports/count_datasets/{112_usv,113_uav,114_aerial_vhr,115_bridge_s2,116_roboflow_kaggle_hf}.md`, из L117 (`docs/research/marine_quantity/DECISION.md`) и из проверок Глеба (`docs/research/gleb_quantitative_link/`, `gleb_deep_research/`). Обновлено 26.09 06:00 (часы машины).

Как читать:
- Образцы лежат в `data/extra/count_ds/<name>/` (вне git). После §21/§23 там все метки и 0–6 кадров, не больше 50 МБ на набор.
- Числа посчитаны по скачанным файлам. Пометка «(по опис.)» — число взято из описания записи.
- Зеркала на Kaggle/HF сведены в одну строку.
- Пригодность: (1) обучение счётчика по фото; (2) шт./км² на масштабе кадра, если известны GSD или площадь кадра; (3) мост к Sentinel-2.

## ИТОГ (≤ 10 строк)
1. **Счётчик с известной площадью кадра** (шт./м² на кадре):
   - Winans 2023: аэро 2 см, кадр 164 м², 10 703 рамки, CC-BY; с ним в паре готовая модель DebrisScan (Apache-2.0);
   - The Ocean Cleanup River Monitoring: 38 812 рамок; GSD по участку есть у 1 837 кадров, кадр 41–122 м²; **CC-BY-NC**;
   - Martin 2021: дрон 10 м, кадр 138.6 м², опубликованные шт./м² по 44 пляжам; рамок нет;
   - Maharjan 2022: плитки 2 × 2 м над рекой, 2 094 рамки; файла лицензии нет;
   - TUD-HCMC (≈ 35 м²) и Chao Phraya (GSD на серию) — малые.
2. **Для обучения** (открытые лицензии, проверено по разметке): UCWD 26 258 рамок, FML 17 156, HF floating rubbish 14 027, IWHR 13 491, Saigon 9 352, RF100-VL 8 470 (MIT), TUD-GV 8 181, RiSID 8 022, SARA-2024 (надир, негативы). С NC-лицензией: CSIRO 18 070 предметов.
3. **FML** даёт штуки на кадр, **не шт./км²**: GSD/GPS нет (Глеб: 5 490 кадров / 17 156 рамок).
4. **Мост к S2** — только искусственные мишени PLP2018/2019 (3 600 и 416 бутылок, S2 того же дня; L115). Детектор срабатывает только при покрытии ≳ 30 % пикселя (~10⁶–10⁷ шт./км²). Природных пар «штучный счёт + S2 того же дня» в открытом доступе нет: ни у L115 (16 направлений), ни у L117 (VHR над маршрутами ADIS того же дня — 0).
5. Физика (L117): по ADIS отдельные предметы различимы у 25 % при пикселе 0.3 м и у 0 % при 3 и 10 м. VHR-счёт на реальной воде без проверки даёт гребни волн и блики.
6. Вычеркнуто: Ruiz 2020 — D (Глеб, L115). Не открыто: FloW (заявка), Roboflow Universe (нужен ключ), кадры Lebreton 2018 (по запросу), WADE (после принятия статьи).
7. Отклонено ≈ 40 записей: таблица в конце.
8. Ограничения лицензий:
   - CC-BY-NC: TOCL RMS, CSIRO, NASA PS;
   - CC-BY-NC-SA: SARA-2025;
   - GPL-3.0: FDD;
   - лицензия не указана: Kaggle ocean waste, Mixed Float Trash, Maharjan;
   - сомнительная: Marine Litter (Della Volpe).

## А. Счётчик на кадре с известной площадью (шт./м² → шт./км²) — лучшие сверху

| # | набор | сегм. | ссылка | лицензия | изобр. / рамки | GSD / площадь кадра | время / гео | образец |
|---|---|---|---|---|---|---|---|---|
| A1 | **Winans et al. 2023, Гавайи** (пилотируемая аэросъёмка, берег) | 114 | Zenodo 10.5281/zenodo.8381113 | CC-BY-4.0 | 1 588 / 10 703, 8 кл., до 82 на кадр | **0.02 м, кадр 12.8 × 12.8 м = 164 м²** (aux.xml) | геопривязка кадра, 2015 | `winans2023_hawaii_aerial/` |
| A1m | **DebrisScan** (веса EfficientDet-D0, обучены на Winans) | Глеб, §24 | github.com/orbtl-ai/DebrisScan | Apache-2.0 | модель, не данные | — | — | в репозитории (не копировался) |
| A2 | **The Ocean Cleanup River Monitoring System** (FMPD) | 112 | 4TU 10.4121/fad0aa03-37b4-4a3c-8263-8ed60a03b393; статья Sci Rep 2026, 10.1038/s41598-026-48630-z | **CC-BY-NC-4.0** | 2 229 / 38 812, 11 кл., до 327; трекинг-GT 6 568 строк | **GSD по участку 0.18–0.31 см/пикс** (Suppl. A) → **кадр 41–122 м²** для 1 837 кадров; соответствие — `tocl_rms/suppl/location_gsd_map.csv` | участок и время в имени кадра; 10 рек | `tocl_rms/` (COCO, Suppl. A, карта GSD) |
| A3 | **Martin et al. 2021, Красное море** (дрон, пляжи) | 113 | Mendeley gpdsntb3y6 + Data in Brief mmc1.xlsx (PMC8102167) | CC-BY-4.0 (кадры) | 1 287 кадров, **рамок нет**; таблица 44 пляжей с шт./м² | **10 м, кадр 138.6 м²** | EXIF время + GPS | `martin_redsea_uav/` |
| A4 | **Maharjan 2022, реки Лаоса/Таиланда** (дрон над водой) | 113 | github.com/Nisha484/Nisha (Remote Sens. 14, 3049) | **файла лицензии нет** (открытость — по статье) | 1 000 плиток / 2 094 рамки | **GSD 0.82 см, плитка 2 × 2 м** | нет | `maharjan_river_uav/` |
| A5 | TUD-HCMC (мосты, Saigon) | 112 | Zenodo 10.5281/zenodo.17387612 | CC-BY-4.0 | 508 / 178 + ручной счёт по проходу | **0.12 см/пикс, кадр ≈ 35 м²** | 09.2023 | `tud_hcmc/` |
| A6 | Chao Phraya: мосты GoPro + визуальный счёт | 112 | 4TU 10.4121/f771da08-8143-4a4b-9ad6-c8e711812a63 | CC-BY-4.0 | 82 / 1 415, до 157; визуальный счёт — 485 строк | **GSD на серию 0.23–0.51 (≈ см/пикс), высота 5.5–12.4 м** | 03–04.2025, координаты мостов | `chao_phraya/` |
| A7 | Oostpoort / Groningen / Amsterdam (Jia 2024) | 112 | Zenodo 13730298 / 13730384 / 13730370 | CC-BY-4.0 | 562 / 1 014; 63 / 383; 9 / 134 | высота 5.03 м надир / 4 м / ≤ 2 м → GSD только оценкой по FOV | 2022–2023 | `tud_{oostpoort,groningen,amsterdam}/` |
| A8 | SARA trash-in-river 2024 / 2025 (мост, надир) | 116 | HF SARA-smartphone-assisted-river-analysis | 2024 CC-BY-4.0; 2025 **CC-BY-NC-SA** | 1 904 / 1 098 мусор (815 пустых); 31 938 / 3 314 | масштаб — оценкой (опора моста, люди) | время в имени файла | `hf_sara_trash_in_river_202{4,5}/` |

## Б. Обучение счётчика по фото (без площади кадра) — лучшие сверху

| # | набор | сегм. | ссылка | лицензия | изобр. / рамки | платформа | образец |
|---|---|---|---|---|---|---|---|
| B1 | UCWD (дроны, пляжи) | 113 | Zenodo 10.5281/zenodo.20630252 | CC-BY-4.0 | 4 071 / 26 258, 7 кл., до 60 | дрон, надир; GSD нет (EXIF удалён) | `ucwd/` |
| B2 | FML (USV) — модуль L109 | 112 | SEANOE 10.17882/106148 | CC-BY-4.0 | 5 490 / 17 156 (Глеб) | USV, PTZ-камера, косой вид; GSD/GPS нет | `data/extra/fml/` |
| B3 | Floating rubbish on the water surface | 116 | HF gonzz2026/A-dataset-of-images-of-floating-rubbish… (10.57967/hf/7674) | CC-BY-4.0 (загрузивший) | 2 377 / 14 027, 11 кл., до 53 | берег, наклон; есть аугментация | `floating_rubbish_hf/` |
| B4 | IWHR_AI_Lable_Floater_V1 | 112 | figshare 10.6084/m9.figshare.27376851 (зеркало Kaggle raiat37) | Apache-2.0 | package1: 1 500 / 13 491, до 43; всего 3 000 изобр. | береговые камеры, внутренние воды Китая | `iwhr_floater/` |
| B5 | Saigon: мосты + БПЛА (пластик, гиацинт) | 112 | 4TU 10.4121/78bb4822-7b70-4632-887a-7cacd344024e | CC-BY-4.0 | 272 / 9 352, до 464 | мосты, БПЛА | `saigon_bmc/` |
| B6 | RF100-VL floating-waste | 116 | HF LibreYOLO/rf100-vl `floating-waste.tar` | MIT | 2 934 / 8 470 | камера у воды (пруд, канал) | `rf100vl_floating_waste/` |
| B7 | TUD-GV object detection | 112 | Zenodo 10.5281/zenodo.13730228 | CC-BY-4.0 | 1 501 / 8 181 | 2.7/4.0 м, 0°/45°, канал Delft | `tud_gv_od/` |
| B8 | RiSID (реки Японии) | 112 | Zenodo 10.5281/zenodo.16927238 | CC-BY-4.0 | 7 356 / 8 022 (маски) | камеры у рек | `risid/` |
| B9 | TUN-MarineLitter | 113 | Zenodo 10.5281/zenodo.21965497 | CC-BY-4.0 | 3 676 / 7 922 полигона | дрон, пляжи | `tun_marinelitter/` |
| B10 | Banjarmasin (мутные реки) | 112 | Mendeley 10.17632/j26w4m645z.2 | CC-BY-4.0 | 1 577 / 5 792 (мусор 3 718) | берег/лодка | `banjarmasin/` |
| B11 | CWG-RGB (городские каналы) | 112 | Zenodo 10.5281/zenodo.21261194 | CC-BY-4.0 | 1 176 / 3 557 + 584 пустых; `target_count` на кадр | берег/мост | `cwg_rgb/` |
| B12 | River Floating Trash (RFT) | 116 | Kaggle zhiaun/river-floating-trash-datasets | Apache-2.0 | 2 400 / 3 510 | берег | `kaggle_river_floating_trash/` |
| B13 | Ocean Plastics Waste — Float Plastics | 116 | Kaggle abdelaadimkhriss/ocean-plastics-waste | CC-BY-4.0 (Roboflow) | 1 289 / 8 524 | смесь | `kaggle_ocean_plastics_waste/` |
| B14 | PoTATO (поляриметрия, бутылки) | 112 | Zenodo 10.5281/zenodo.17117218 | CC-BY-4.0 | val 600 / 1 732; всего 12 380 (по опис.) | камера у воды | `potato/` |
| B15 | CSIRO Floating Litter, пакеты 1–10 | 116 | Kaggle kathrynwillis/floatinglitter-1…10 | **CC-BY-NC-4.0** | 7 826 / 18 070 предметов, 25 кл. | фотоловушки над водостоками | `kaggle_csiro_floatinglitter/` |
| B16 | Floating_Debris_Datasets (Shi 2024) | 116 | github.com/DawnCalm/Floating_Debris_Datasets | **GPL-3.0** | 6 694 / 15 283 (аугментация) | смесь | `github_dawncalm_fdd/` |
| B17 | Mixed Float Trash v2; ocean waste detection 1 | 116 | Kaggle salehuddinabdlatif/floating-trash; xeniapornova/ocean-waste-detection-1 | **лицензия не указана** | 1 545 / 8 253; ≈ 15 000 кадров (GoPro с судна, EXIF время + GPS) | камеры у решётки; борт судна | только для внутренней проверки |
| B18 | UAVVaste; TACO | 113 | Zenodo 8214061; github pedropro/TACO | CC-BY-4.0 | 772 / 3 718 (уже в UCWD); 1 500 / 4 784 | суша — только как фон | `uavvaste/`, `taco/` |
| B19 | ADIS вырезки (судно, GoPro) | 112 | 4TU 10.4121/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2 | CC-BY-4.0 | 22 126 вырезок без исходных кадров | вставка / классификатор; площадь — на отрезок 10 км | `adis_snippets/` (300 вырезок) |
| B20 | Marine Litter (Della Volpe, дрон) | 113 | Zenodo 10.5281/zenodo.10993447 | **сомнительно** (загрузил не автор) | 2 675 / 17 274 | дрон | после выяснения прав |

## В. Мост к Sentinel-2

| # | набор | сегм. | что есть | итог |
|---|---|---|---|---|
| C1 | **PLP2018 / PLP2019 мишени** (Лесбос), Zenodo 3752719, CC-BY-4.0 | 115 | 3 600 бутылок + 138 пакетов на 100 м² (2018); 416 бутылок на мишень, 65 пикселей S2 с долей покрытия по дрону (2019); S2 того же дня | **единственные пары «число предметов + S2 того же дня»** — только искусственные (A*). Детектор: 2 из 10 пикселей с долей ≥ 0.25 (~5–7·10⁶ шт./км²), 0 из 55 с долей < 0.25 |
| C2 | PLP2021 / 2022–23 (Zenodo 7085112, 10046182) | 115 | сетка HDPE 616 м², листы меньше пикселя; штучного счёта нет | детектор 0 из 12 и 0 из 8 дат |
| C3 | NASA marine debris, PlanetScope (source.coop nasa/marine-debris), **CC-BY-NC** | 114, 115 | 1 640 рамок **пятен** 3 м; 11 сцен с S2 того же дня | спутник ↔ спутник: 0 из 123 природных рамок на годной воде S2 |
| C4 | LitterLines (Zenodo 19571437), CC-BY-4.0 | 114, 115 | 966 линий окон на PlanetScope, у 826 есть S2 того же дня (= окна Cózar 2024) | линии, не штуки; пикселей Planet в записи нет |
| C5 | ADIS × S2 (L100, Глеб) | 112, Глеб | 66 пар по месту и времени (> 5 см); у Глеба 6 пар (> 50 см) | калибровочных пар 0; на 3 самых плотных маршрутах оптических сцен того же дня нет |
| C6 | Chao Phraya (4TU f771da08) | 112 | S2-классификатор пятен гиацинта + визуальный счёт пластика в гиацинте с мостов | не проверено; кандидат на мост через гиацинт, не через пластик |

## Независимые проверки
| источник | вердикт | для реестра |
|---|---|---|
| Глеб: ADIS | A (полевое число на известной площади) | мост только на масштабе маршрута |
| Глеб: Ruiz 2020 | **D** | вычеркнут, не искать |
| Глеб: PLP2019 | C | «спектр → доля покрытия» не подтверждён (ρ FDI 0.09, NIR 0.14) |
| Глеб: FML | B | 5 490 / 17 156, без GSD/GPS → штуки на кадр |
| Глеб: DebrisScan | готовый бейзлайн (§24) | сравнить на отложенных кадрах Winans (MAE числа на кадр, ошибка шт./км²) |
| L117 | 28 сервисов вызваны без ключей; VHR того же дня над ADIS — 0; счётчик пятен на VHR 0.3 м находит гребни и блики | ТОП-2 L117 = «счёт на кадре с известной площадью» (раздел А) |

## По заявке / не открыто
| набор | сегм. | что есть | причина |
|---|---|---|---|
| FloW (Orca Tech) | 112 | 2 000 изобр., 5 271 рамка (по опис.) | заявка через orca-tech.cn, лицензия не указана |
| Roboflow Universe (floatplasticwasteriver 2 820, rifatx 6 050, kharisma-punya 3 160 и др.) | 116 | > 300 проектов | нужен API-ключ Roboflow |
| Lebreton 2018 аэромозаики 0.1 м | 114 | 7 298 кадров (по статье) | по запросу к The Ocean Cleanup; шаблон письма — `114_aerial_vhr.md` |
| Park 2021 WV-3; Garaba 2018 SWIR; García-Garin 2019–21; Rivages Pro Tech | 114, 115 | снимки / треки | «по запросу к авторам»; запросы не отправлены (решение людей) |
| WADE (Бангладеш) | 112, 116 | 2 167 изобр., 13 608 рамок (по опис.) | «после принятия статьи» |
| AquaSurf-Malnad-223; Beach-Litter-UAV; AquaPlasticWaste 2026 | 112, 113 | IEEE DataPort | подписка |
| UAV-Flow (FLD-Net) | 113 | Baidu Netdisk | лицензии нет |

## Отрицательные результаты
| набор | сегм. | причина |
|---|---|---|
| Ruiz et al. 2020 | Глеб, 115 | нет таблицы «трал → время, координата, счёт» |
| SeaClear; OceanGuard eval; Roboflow marine debris (подводные); J-Litter | 112, 116 | подводная / глубоководная съёмка |
| TUD-GV полный (7636124); Surface Water Floating Waste (Mendeley 7c5gdjbhpt); APLASTIC-Q; MARLIT | 112, 113, 115 | метки на уровне кадра или плитки, без рамок |
| YOLOv8 River Debris (Zenodo 20413205); Dataset downsizing (18386827); FloatWaste (Kaggle) | 112, 116 | только веса / PDF / данных ещё нет |
| DOORS cruise 3; Средиземное море, наблюдатели (18387541); NOAA NCEI Midway | 112, 114 | полевые шт./км² без снимков |
| NOAA MDMAP, Plastic Pirates, EMODnet | 113 | эталон шт./площадь на земле, снимков предметов нет (полезно как полевая проверка) |
| FLM-MLDT | 112 | гидролокатор |
| Trash and water segmentations (4TU 90d13261); Tijuana (5634334); Bacolod (20506213) | 112, 113 | пятна / масса, не предметы |
| Batis (17805043); River Floating Debris (Kaggle raiat37); Нинбо (figshare 24123666); Drone Saigon (4TU 21648152) | 113, 116 | изображения без разметки |
| BePLi v1; AquaTrash; Aquatic-drone hyperspectral | 113, 116 | NC-SA, не вода / лоток |
| Plastic-Bottles-Dataset, HydroFloat (GitHub); Nautilus AquaVision; AquaSense | 116 | лицензии нет / AGPL, источник не раскрыт |
| Gonçalves 2020, Merlino 2020, Papakonstantinou 2021, Geraeds 2019, Salgar 2024; Pléiades | 113, 114 | открытых данных нет |
| Themistocleous 2020; Freitas 2021; Kremezi 2021; Sakti 2023; The Ocean Cleanup БПЛА 2021–22 | 115 | данных нет / S2 закрыт облаками / PRISMA / время не совпадает / доступ закрыт |
| Maathuis 2025; Airborne spectral reflectance (7043318) | 113, 114 | мишени и спектры, не штуки |
| Participatory lake pollution (Zenodo 5094576) | 112 | не плавающий мусор |

Подробности — в файлах сегментов: `reports/count_datasets/112_usv.md`, `113_uav.md`, `114_aerial_vhr.md`, `115_bridge_s2.md`, `116_roboflow_kaggle_hf.md`.
