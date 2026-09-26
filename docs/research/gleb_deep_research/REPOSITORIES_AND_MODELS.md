# Каталог кода, весов и данных для передачи в рабочий чат

Проверено 26 сентября 2026. В архиве лежат **ссылки и инструкции**, а не сторонние веса/модели и не полные сторонние репозитории. Полная оценка методов, ограничений и архитектур — в [`DEEP_RESEARCH.md`](DEEP_RESEARCH.md). Статус «код доступен» не означает, что inference проверен на нашем ноутбуке.

## Sentinel-2: детекция зон

### marine-debris-ml-model

- Код и установка: https://github.com/danieltyukov/marine-debris-ml-model; MIT; Python 3.11/3.12, PyTorch. `pip install -e ".[all]"`.
- Минимальная команда из README: `mdebris detect --bbox -0.35,5.45,-0.05,5.65 --start 2024-01-01 --end 2024-06-30`.
- Вход: AOI и даты, Sentinel-2 L2A через публичный STAC; 11 каналов/спектральные индексы. Выход: геопривязанные кандидаты, не количество предметов.
- Модели: OWLv2/SAM2 загружаются их библиотеками; конкретную версию и лицензию весов фиксировать при запуске. README также описывает обученный классификатор `models/marida_spectral.joblib`, но в проверенном Git tree его **нет**. Для воспроизведения: `python scripts/train_marida.py`, который скачивает MARIDA и обучает модель. Не путать результаты README с нашим независимым тестом.
- Контроль: MARIDA https://zenodo.org/records/5151941; код/информация https://github.com/marine-debris/marine-debris.github.io; pretrained U-Net по ссылке в README.
- Риск: модель путает суда и мусор; порог существенно меняет precision/recall. Нужны маска суши/облаков и ручная проверка.

### marineDebrisDetector

- Код: https://github.com/MarcCoru/marinedebrisdetector; MIT. Веса доступны по ссылке из README репозитория, в самом Git tree стандартного checkpoint нет.
- Вход: 12 каналов Sentinel-2 с совместимой предобработкой. Выход: вероятностная маска marine debris. Пример вызова Torch Hub/CLI и демонстрационный Durban crop — в README.
- Это детектор *зон*, не счётчик бутылок. На наших снимках надо проверить совпадение порядка bands, reflectance scaling, CRS и облака.

### MADOS / MariNeXt

- Код: https://github.com/gkakogeorgiou/mados; MIT. Данные: https://zenodo.org/records/10664073 . Веса нескольких запусков — Google Drive ссылка в README репозитория.
- Вход: Sentinel-2 multispectral tiles. Выход: 15 тематических классов, включая debris и confusers.
- Исходный стек Python 3.8, CUDA 11.3, mmcv и GDAL 3.3; перенос на современный Mac требует работы. Полезен прежде всего как дополнительный классификатор и источник классов ложных срабатываний.

### POS2IDON / ESA FloatingObjects

- POS2IDON: https://github.com/AIRCentre/POS2IDON; GPL-3.0; Sentinel-2 L1C → ACOLITE → masking → RF/XGB/U-Net → пиксельные классы. Стандартные веса в Git tree не найдены; обучать/получать отдельно.
- FloatingObjects: https://github.com/ESA-PhiLab/floatingobjects; Apache-2.0; код подготовки разметки, обучения и inference для плавающих объектов. Готовых весов в дереве репозитория не найдено.
- Оба дают локализацию/классы, не физические шт./км².

## Детальное изображение: подсчёт отдельных предметов

### DebrisScan — самый готовый количественный каркас

- Код: https://github.com/orbtl-ai/DebrisScan; Apache-2.0; вес EfficientDet-D0 уже лежит в репозитории, но **не копируется в этот архив**.
- Официальная команда: `docker compose --env-file .env.dev up --build`; интерфейс `http://localhost:8080/`. Docker официально поддерживает Windows/Linux AMD64; Apple Silicon не поддержан.
- Вход: аэрофото берегового мусора, оптимальный GSD около **2 см**, опционально высота полёта и параметры камеры. Выход: боксы/классы отдельных предметов, отчёты, карта, REST API.
- Для воды: переобучить/проверить на плавающем мусоре, фильтровать блики/пену/водоросли, объединять перекрывающиеся footprints. Плотность = число уникальных подтверждённых предметов / площадь действительно осмотренной воды.
- Контрольные изображения/метки и модели: https://zenodo.org/records/8381113 (аэро береговой мусор, 2 см GSD, 10 703 размеченных объектов). Скачать напрямую из Zenodo при необходимости.

### Trash_Track — дедупликация и карта

- Код: https://github.com/Tahiya31/Trash_Track; MIT. GroundingDINO веса: https://github.com/IDEA-Research/GroundingDINO/releases; CLIP загружается отдельно.
- README даёт шаги установки и `python app.py`; Docker image `rayw03/trash-app`. GPU рекомендуется.
- Вход: кадры дрона; выход: детекции GroundingDINO, материал CLIP, удаление повторных предметов между кадрами через SIFT, Folium/Flask карта.
- Для шт./км² дополнить настоящей геометрией кадра/GSD, корректным объединением водных footprints и полевым аудитом; опубликованный домен преимущественно береговой.

### RS-OVC, HerdNet и MegaDetector-Overhead

- RS-OVC: https://github.com/tamirshor7/RS-OVC; MIT; веса https://huggingface.co/tamirshor/RSOVC (~2.18 ГБ). Few-shot/open-vocabulary counting на изображениях высокого разрешения; требуется адаптация на debris, вероятно GPU.
- HerdNet: https://github.com/Alexandre-Delplanque/HerdNet; MIT-код; ссылки на pretrained animal weights в README (отдельные ограничения использования весов). Пример inference `python tools/infer.py ...`; point/bbox detections, тайлинг и CSV.
- MegaDetector-Overhead: https://github.com/microsoft/MegaDetector-Overhead; MIT; animal weights на HuggingFace/Zenodo через README, `uv sync` для установки. Метод счёта переносим, готовые веса не распознают пластик.
- CountGD: https://huggingface.co/nikigoli/CountGD; MIT; ~0.94 ГБ; текстовый/few-shot count. Проверять только на сантиметровых кадрах, не на S2 бутылках.

## Альтернативы и продуктовые шаблоны

- NASA IMPACT PlanetScope debris: https://github.com/NASA-IMPACT/marine_debris_ML; данные https://source.coop/nasa/marine-debris/README.md . 3-м тайлы → bbox скоплений → геокоррекция → GeoJSON/карта. Код требует TensorFlow 1.14 и Planet API; **обученный marine-debris checkpoint в Git tree отсутствует**. Боксы скоплений не равны отдельным предметам.
- Sargassum fractional cover: https://github.com/aquab1t/sargassum-satellite-ml; пример продукта с дрейфом https://github.com/Ayege/descubreplayas . Сигнал/маска → площадь покрытия; для количества пластика нужна иная калибровка.
- Spectral unmixing: https://github.com/arthur-e/unmixing; https://pysptools.sourceforge.io/abundance_maps.html . Результат — доля материала/площадь, **не число** без распределения размеров предметов.
- Счёт объектов по плотности: https://github.com/gaoguangshuai/Counting-from-Sky-A-Large-scale-Dataset-for-Remote-Sensing-Object-Counting-and-A-Benchmark-Method; https://github.com/zhiheng-ma/Bayesian-Crowd-Counting . Сумма density map даёт число только после обучения на честных точечных/счётных метках.
- Прямой важный аналог на 10-м пикселе Sentinel-2 — оценка деревьев/га: https://arxiv.org/abs/2105.11207 . Официальный готовый inference repo при этом поиске не найден.
- MARLIT: https://github.com/amonleong/MARLIT; R Shiny UI и калибровка площади по высоте/камере. В `AllPlast/app.R` его `pieces/Km2` — **число положительных ячеек**, не число предметов; как счётчик не использовать.

## Исходные количественные данные и ограничения

- ADIS / The Ocean Cleanup: https://data.4tu.nl/datasets/ddede7f5-aca5-42ae-b851-e0bbb9a2c4c2 — GPS/UTC, маршрут, площадь полосы, наблюдённые предметы >50 см, фото-фрагменты. В архиве исследования лежат скачанные CSV и наши шесть Sentinel-2 совпадений; полная геобаза/фрагменты доступны по ссылке и перечислены в `data/source_inventory.json`.
- PLP2019: https://zenodo.org/records/3752719; PLP2022/23: https://zenodo.org/records/10046182 — контролируемые пластиковые мишени и спектральная доля. **Не** натуральное количество бутылок в море.
- FML: https://doi.org/10.17882/106148 — кадры и боксы отдельных объектов, но в проверенных кадрах нет GSD/геометрии для шт./км². Полный ZIP 2.2 ГБ не включён; результаты анализа и манифест включены.
- Ruiz 2020: https://doi.org/10.3389/fmars.2020.00308 — опубликованное приложение не дало проверяемых пар `конкретный трал → GPS/время/счёт`.

Скачивать веса стоит только для выбранных к запуску методов. Лицензии кода, весов и данных могут отличаться; источник/версию фиксировать в журнале эксперимента.
