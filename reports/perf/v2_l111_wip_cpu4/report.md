# Perf — v2_l111_wip_cpu4 (http://127.0.0.1:8070/)

2026-09-26 05:24:39 … 2026-09-26 05:27:10; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×4.0
Сборка: {'entry_js': ['index-Ba5_wDbO.js'], 'index_html_sha1': '1aa2390264fb'}

**Сводка:** fps pan 65.8, flyTo 42.2, first show 1.61 s (1920x1080); console errors 4; bundle gzip 0.704 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **1.61 с** по 3 холодным загрузкам [1.34, 1.61, 2.31]; в первой загрузке 2.31 с (first GL draw after first tile); WebGL-контекст 1013 мс, первый GL-draw 1561 мс, первый тайл пришёл 2137 мс, draw после тайла 2310 мс, тайлы загружены (map idle) 8677 мс; FCP 432 мс, LCP None мс, DCL 216 мс; handle карты: __caseMap; запросов тайлов 122.

Стиль карты: {'projection': 'globe', 'n_sources': 38, 'sources_by_type': {'raster': 1, 'geojson': 10, 'image': 27}, 'n_layers': 49, 'n_canvas': 1, 'dpr': 1, 'n_dom': 365}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 52.3 | 784 | 5 (0.006) | 0.496 | 34.8 | 62.6 | 48 |  |
| pan #2 | 79.4 | 920 | 0 (0) | 0.164 | 20.9 | 34.6 | 32 |  |
| wheel_zoom | 50.8 | 203 | 4 (0.02) | 0.478 | 41.6 | 111.1 | — |  |
| flyto | 42.2 | 236 | 2 (0.008) | 0.86 | 34.8 | 62.4 | — |  |
| left_toggle | 56.7 | 290 | 15 (0.052) | 0.207 | 55.4 | 312.6 | 320 | no left-column toggle; fallback: open zone card from the list + close it ×2 (includes the map flyTo to the zone) |
| views | 137.9 | 545 | 1 (0.002) | 0.007 | 7 | 69.4 | 80 | tab-zones: 7 мс; tab-obs: 69 мс; tab-go: 49 мс; tab-metrics: 35 мс; tab-zones: 28 мс |
| studio | — | — | — | — | — | — | — | no studio in this UI (no data-testid=nav-studio) |
| idle | 144 | 433 | 0 (0) | 0 | 7 | 7.2 | — |  |

## 1366x768


Первый показ: медиана **1.59 с** по 3 холодным загрузкам [1.18, 1.59, 1.83]; в первой загрузке 1.18 с (first GL draw after first tile); WebGL-контекст 543 мс, первый GL-draw 819 мс, первый тайл пришёл 1118 мс, draw после тайла 1180 мс, тайлы загружены (map idle) 4577 мс; FCP 252 мс, LCP None мс, DCL 142 мс; handle карты: __caseMap; запросов тайлов 34.

Стиль карты: {'projection': 'globe', 'n_sources': 38, 'sources_by_type': {'raster': 1, 'geojson': 10, 'image': 27}, 'n_layers': 49, 'n_canvas': 1, 'dpr': 1, 'n_dom': 321}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 62.3 | 839 | 0 (0) | 0.304 | 27.8 | 41.7 | 56 |  |
| pan #2 | 62.2 | 811 | 0 (0) | 0.343 | 27.8 | 27.9 | 40 |  |
| wheel_zoom | 43.9 | 193 | 17 (0.088) | 0.415 | 55.6 | 125.1 | — |  |
| flyto | 40 | 224 | 7 (0.031) | 0.768 | 48.6 | 62.6 | — |  |
| left_toggle | 47.1 | 265 | 22 (0.083) | 0.283 | 83.3 | 312.5 | 344 | no left-column toggle; fallback: open zone card from the list + close it ×2 (includes the map flyTo to the zone) |
| views | 132.2 | 540 | 3 (0.006) | 0.011 | 7 | 111.1 | 120 | tab-zones: 7 мс; tab-obs: 111 мс; tab-go: 76 мс; tab-metrics: 76 мс; tab-zones: 49 мс |
| studio | — | — | — | — | — | — | — | no studio in this UI (no data-testid=nav-studio) |
| idle | 144 | 432 | 0 (0) | 0 | 7 | 7.2 | — |  |

## Бандл

7 файлов JS/CSS, 2633926 Б, gzip 704101 Б (0.704 МБ).

| файл | байт | gzip |
|---|---|---|
| deck-DtYNjI0N.js | 1180918 | 311864 |
| maplibre-D9xxkaV4.js | 1053003 | 282295 |
| index-Ba5_wDbO.js | 144879 | 46601 |
| CaseApp-BZvGDbPs.js | 123612 | 37124 |
| index-BiTfTwON.css | 103388 | 16523 |
| Info-BkoWbCmY.js | 13272 | 6205 |
| CaseApp-D6xcfa6g.css | 14854 | 3489 |

## Консоль

Ошибок: 4; внешних (хосты подложки): 0; упавших локальных запросов: 0.
- Failed to load resource: the server responded with a status of 404 (Not Found) [http://127.0.0.1:8070/api/v3/scene_zones/scenes]
- Failed to load resource: the server responded with a status of 404 (Not Found) [http://127.0.0.1:8070/api/v3/scene_zones]
- Failed to load resource: the server responded with a status of 404 (Not Found) [http://127.0.0.1:8070/api/v3/scene_zones/scenes]
- Failed to load resource: the server responded with a status of 404 (Not Found) [http://127.0.0.1:8070/api/v3/scene_zones]
