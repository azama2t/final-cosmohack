# Perf — v2_baseline_cpu4 (http://127.0.0.1:8070/)

2026-09-26 01:28:33 … 2026-09-26 01:31:17; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×4.0

**Сводка:** fps pan 73.3, flyTo 45.1, first show 2.45 s (1920x1080); console errors 0; bundle gzip 0.699 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **2.45 с** по 3 холодным загрузкам [1.29, 2.45, 2.77]; в первой загрузке 2.77 с (first GL draw after first tile); WebGL-контекст 1049 мс, первый GL-draw 1619 мс, первый тайл пришёл 2396 мс, draw после тайла 2765 мс, тайлы загружены (map idle) 13311 мс; FCP 384 мс, LCP None мс, DCL 192 мс; handle карты: __caseMap; запросов тайлов 122.

Стиль карты: {'projection': 'globe', 'n_sources': 36, 'sources_by_type': {'raster': 1, 'geojson': 8, 'image': 27}, 'n_layers': 45, 'n_canvas': 1, 'dpr': 1, 'n_dom': 346}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 69.9 | 942 | 3 (0.003) | 0.259 | 27.8 | 90.3 | 56 |  |
| pan #2 | 76.7 | 1421 | 19 (0.013) | 0.197 | 28.2 | 83.3 | 48 |  |
| wheel_zoom | 66.8 | 287 | 6 (0.021) | 0.233 | 48.4 | 145.6 | — |  |
| flyto | 45.1 | 256 | 10 (0.039) | 0.57 | 48.7 | 76.4 | — |  |
| left_toggle | 55.6 | 308 | 15 (0.049) | 0.256 | 48.6 | 270.7 | 352 | no left-column toggle; fallback: open zone card from the list + close it ×2 (includes the map flyTo to the zone) |
| views | 134.6 | 541 | 2 (0.004) | 0.011 | 7.2 | 104.1 | 120 | tab-zones: 7 мс; tab-obs: 104 мс; tab-go: 76 мс; tab-metrics: 35 мс; tab-zones: 21 мс |
| studio | — | — | — | — | — | — | — | no studio in this UI (no data-testid=nav-studio) |
| idle | 137.4 | 415 | 1 (0.002) | 0.012 | 7.1 | 55.6 | — |  |

## 1366x768


Первый показ: медиана **1.49 с** по 3 холодным загрузкам [1.41, 1.49, 2.74]; в первой загрузке 2.74 с (first GL draw after first tile); WebGL-контекст 820 мс, первый GL-draw 1174 мс, первый тайл пришёл 2713 мс, draw после тайла 2743 мс, тайлы загружены (map idle) 7404 мс; FCP 340 мс, LCP None мс, DCL 203 мс; handle карты: __caseMap; запросов тайлов 34.

Стиль карты: {'projection': 'globe', 'n_sources': 36, 'sources_by_type': {'raster': 1, 'geojson': 8, 'image': 27}, 'n_layers': 45, 'n_canvas': 1, 'dpr': 1, 'n_dom': 312}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 79.5 | 1069 | 3 (0.003) | 0.192 | 27.8 | 83.2 | 40 |  |
| pan #2 | 117 | 1231 | 2 (0.002) | 0.037 | 14 | 69.5 | 24 |  |
| wheel_zoom | 81.8 | 295 | 4 (0.014) | 0.214 | 28 | 62.3 | — |  |
| flyto | 66.7 | 373 | 0 (0) | 0.265 | 27.8 | 48.6 | — |  |
| left_toggle | 60.8 | 304 | 18 (0.059) | 0.217 | 55.6 | 173.5 | 208 | no left-column toggle; fallback: open zone card from the list + close it ×2 (includes the map flyTo to the zone) |
| views | 137.4 | 540 | 1 (0.002) | 0.007 | 7.2 | 90.2 | 96 | tab-zones: 7 мс; tab-obs: 90 мс; tab-go: 35 мс; tab-metrics: 49 мс; tab-zones: 21 мс |
| studio | — | — | — | — | — | — | — | no studio in this UI (no data-testid=nav-studio) |
| idle | 137 | 413 | 0 (0) | 0.017 | 7.2 | 34.5 | — |  |

## Бандл

7 файлов JS/CSS, 2613867 Б, gzip 699016 Б (0.699 МБ).

| файл | байт | gzip |
|---|---|---|
| deck-DtYNjI0N.js | 1180918 | 311864 |
| maplibre-D9xxkaV4.js | 1053003 | 282295 |
| index-D6pMOx42.js | 144697 | 46539 |
| CaseApp-Dw7lYcGv.js | 104911 | 32383 |
| index-BiTfTwON.css | 103388 | 16523 |
| Info-CjefpWeA.js | 13272 | 6204 |
| CaseApp-Pvl2eKAw.css | 13678 | 3208 |

## Консоль

Ошибок: 0; внешних (хосты подложки): 2; упавших локальных запросов: 0.
