# Офлайн-проверка карты

Сгенерировано `scripts/offline_check.py` · 2026-09-25 06:13 · http://127.0.0.1:8000 · WebGL: swiftshader · окно 1920×1080.
Офлайн = Playwright `route`: все запросы не к 127.0.0.1/localhost обрываются (`internetdisconnected`).
Кадры: `reports/screens/offline/<режим>__<шаг>.png`. «Доля содержимого» = доля пикселей карты, отличных от однотонного фона #07111f (0 → пустая карта).

## Сводка

| Режим | Карта готова | __mapReady, мс | Итоговая подложка | Тост | Сцена района | Пятен | Ошибок консоли | Заблокировано внешних запросов |
|---|---|---|---|---|---|---|---|---|
| online_dark | да | 667 | dark | — | да | 17 | 0 | 0 (—) |
| offline_dark | да | 495 | none | — | да | 17 | 1 | 1 (basemaps.cartocdn.com×1) |
| offline_none | да | 624 | none | — | да | 17 | 0 | 0 (—) |
| offline_satellite | да | 735 | none | — | да | 17 | 14 | 24 (server.arcgisonline.com×24) |

## Бандл: шрифты и внешние URL (статический grep `service/static`)

- index.html: внешних src/href — 0
- шрифты в бандле (woff2): 28; Inter: 28 (через @fontsource, локально)
- `arcgisonline.com` встречается в index-BT0soqPk.js
- `cartocdn.com` встречается в index-BT0soqPk.js
- `gstatic.com` встречается в index-BBzIiqnT.js
- `raw.githubusercontent` встречается в index-BBzIiqnT.js
- `unpkg.com` встречается в deck-CBzxig4q.js
- Реально запрошенные внешние хосты — только в колонке «Заблокировано» выше (остальные строки из grep — мёртвый код библиотек: loaders.gl CDN для воркеров, draco-декодер; в рантайме не запрашиваются).

## Шаги

### online_dark

| Шаг | ok | Доля содержимого | Подложка | Кадр | Прим. |
|---|---|---|---|---|---|
| 01_overview | да | 0.09 | dark | `online_dark__01_overview.png` |  |
| 02_region_rgb_spots | да | 0.482 | dark | `online_dark__02_region_rgb_spots.png` | detections=17 |
| 03_prob | да | 0.47 | dark | `online_dark__03_prob.png` |  |
| 04_h3 | да | 0.472 | dark | `online_dark__04_h3.png` |  |
| 05_zones | да | 0.471 | dark | `online_dark__05_zones.png` |  |
| 06_drift | да | 0.476 | dark | `online_dark__06_drift.png` |  |
| 07_compare | да | 0.329 | dark | `online_dark__07_compare.png` |  |

Прочее: `bundle_start`=index-BT0soqPk.js, `map_ready_wall_ms`=686, `fonts_inter_loaded`=True, `best_region`=manila, `date`=2025-01-06, `rgb_on`=True, `zone_markers`=7, `toast_end`=None, `bundle_end`=index-BT0soqPk.js

### offline_dark

| Шаг | ok | Доля содержимого | Подложка | Кадр | Прим. |
|---|---|---|---|---|---|
| 01_overview | да | 0.082 | none | `offline_dark__01_overview.png` |  |
| 02_region_rgb_spots | да | 0.464 | none | `offline_dark__02_region_rgb_spots.png` | detections=17 |
| 03_prob | да | 0.452 | none | `offline_dark__03_prob.png` |  |
| 04_h3 | да | 0.454 | none | `offline_dark__04_h3.png` |  |
| 05_zones | да | 0.453 | none | `offline_dark__05_zones.png` |  |
| 06_drift | да | 0.466 | none | `offline_dark__06_drift.png` |  |
| 07_compare | да | 0.318 | none | `offline_dark__07_compare.png` |  |

Прочее: `bundle_start`=index-BT0soqPk.js, `map_ready_wall_ms`=515, `fonts_inter_loaded`=True, `best_region`=manila, `date`=2025-01-06, `rgb_on`=True, `zone_markers`=7, `toast_end`=None, `bundle_end`=index-BT0soqPk.js

Ошибки консоли (первые 10):
- `Failed to load resource: net::ERR_INTERNET_DISCONNECTED [https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json]`

### offline_none

| Шаг | ok | Доля содержимого | Подложка | Кадр | Прим. |
|---|---|---|---|---|---|
| 01_overview | да | 0.082 | none | `offline_none__01_overview.png` |  |
| 02_region_rgb_spots | да | 0.464 | none | `offline_none__02_region_rgb_spots.png` | detections=17 |
| 03_prob | да | 0.452 | none | `offline_none__03_prob.png` |  |
| 04_h3 | да | 0.454 | none | `offline_none__04_h3.png` |  |
| 05_zones | да | 0.453 | none | `offline_none__05_zones.png` |  |
| 06_drift | да | 0.466 | none | `offline_none__06_drift.png` |  |
| 07_compare | да | 0.318 | none | `offline_none__07_compare.png` |  |

Прочее: `bundle_start`=index-BT0soqPk.js, `map_ready_wall_ms`=632, `fonts_inter_loaded`=True, `best_region`=manila, `date`=2025-01-06, `rgb_on`=True, `zone_markers`=7, `toast_end`=None, `bundle_end`=index-BT0soqPk.js

### offline_satellite

| Шаг | ok | Доля содержимого | Подложка | Кадр | Прим. |
|---|---|---|---|---|---|
| 01_overview | да | 0.082 | none | `offline_satellite__01_overview.png` |  |
| 02_region_rgb_spots | да | 0.464 | none | `offline_satellite__02_region_rgb_spots.png` | detections=17 |
| 03_prob | да | 0.452 | none | `offline_satellite__03_prob.png` |  |
| 04_h3 | да | 0.454 | none | `offline_satellite__04_h3.png` |  |
| 05_zones | да | 0.453 | none | `offline_satellite__05_zones.png` |  |
| 06_drift | да | 0.466 | none | `offline_satellite__06_drift.png` |  |
| 07_compare | да | 0.318 | none | `offline_satellite__07_compare.png` |  |

Прочее: `bundle_start`=index-BT0soqPk.js, `map_ready_wall_ms`=802, `fonts_inter_loaded`=True, `best_region`=manila, `date`=2025-01-06, `rgb_on`=True, `zone_markers`=7, `toast_end`=None, `bundle_end`=index-BT0soqPk.js

Ошибки консоли (первые 10):
- `Failed to load resource: net::ERR_INTERNET_DISCONNECTED [https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/3/3/4]`
- `Failed to load resource: net::ERR_INTERNET_DISCONNECTED [https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/3/3/5]`
- `Failed to load resource: net::ERR_INTERNET_DISCONNECTED [https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/3/4/4]`
- `Failed to load resource: net::ERR_INTERNET_DISCONNECTED [https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/3/4/5]`
- `Failed to load resource: net::ERR_INTERNET_DISCONNECTED [https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/3/2/4]`
- `Failed to load resource: net::ERR_INTERNET_DISCONNECTED [https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/3/2/5]`
- `Failed to load resource: net::ERR_INTERNET_DISCONNECTED [https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/3/3/3]`
- `Failed to load resource: net::ERR_INTERNET_DISCONNECTED [https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/3/3/6]`
- `Failed to load resource: net::ERR_INTERNET_DISCONNECTED [https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/3/4/3]`
- `Failed to load resource: net::ERR_INTERNET_DISCONNECTED [https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/3/2/3]`

