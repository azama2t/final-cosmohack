# Perf — v2_final_cpu4 (http://127.0.0.1:8070/)

2026-09-26 04:38:06 … 2026-09-26 04:40:28; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×4.0
Сборка: {'entry_js': ['index-D6pMOx42.js'], 'index_html_sha1': 'b76ffd96d386'}

**Сводка:** fps pan 62.8, flyTo 36.5, first show 1.72 s (1920x1080); console errors 0; bundle gzip 0.699 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **1.72 с** по 3 холодным загрузкам [1.7, 1.72, 2.03]; в первой загрузке 2.03 с (first GL draw after first tile); WebGL-контекст 980 мс, первый GL-draw 1462 мс, первый тайл пришёл 1823 мс, draw после тайла 2027 мс, тайлы загружены (map idle) 7314 мс; FCP 432 мс, LCP None мс, DCL 202 мс; handle карты: __caseMap; запросов тайлов 122.

Стиль карты: {'projection': 'globe', 'n_sources': 36, 'sources_by_type': {'raster': 1, 'geojson': 8, 'image': 27}, 'n_layers': 45, 'n_canvas': 1, 'dpr': 1, 'n_dom': 346}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 61.2 | 841 | 4 (0.005) | 0.334 | 34.7 | 76.3 | 40 |  |
| pan #2 | 64.3 | 829 | 0 (0) | 0.32 | 27.8 | 41.8 | 48 |  |
| wheel_zoom | 46.5 | 200 | 6 (0.03) | 0.5 | 48.6 | 138.9 | — |  |
| flyto | 36.5 | 205 | 4 (0.02) | 0.849 | 41.8 | 62.5 | — |  |
| left_toggle | 55.3 | 311 | 16 (0.051) | 0.257 | 55.5 | 270.8 | 296 | no left-column toggle; fallback: open zone card from the list + close it ×2 (includes the map flyTo to the zone) |
| views | 137.3 | 549 | 2 (0.004) | 0.009 | 7.2 | 62.6 | 64 | tab-zones: 7 мс; tab-obs: 56 мс; tab-go: 35 мс; tab-metrics: 63 мс; tab-zones: 35 мс |
| studio | — | — | — | — | — | — | — | no studio in this UI (no data-testid=nav-studio) |
| idle | 144 | 432 | 0 (0) | 0 | 7.1 | 7.2 | — |  |

## 1366x768


Первый показ: медиана **1.41 с** по 3 холодным загрузкам [1.22, 1.41, 1.57]; в первой загрузке 1.57 с (first GL draw after first tile); WebGL-контекст 718 мс, первый GL-draw 1079 мс, первый тайл пришёл 1383 мс, draw после тайла 1570 мс, тайлы загружены (map idle) 4874 мс; FCP 328 мс, LCP None мс, DCL 173 мс; handle карты: __caseMap; запросов тайлов 34.

Стиль карты: {'projection': 'globe', 'n_sources': 36, 'sources_by_type': {'raster': 1, 'geojson': 8, 'image': 27}, 'n_layers': 45, 'n_canvas': 1, 'dpr': 1, 'n_dom': 312}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 101.6 | 1067 | 0 (0) | 0.072 | 20.8 | 34.7 | 40 |  |
| pan #2 | 118.8 | 1204 | 0 (0) | 0.021 | 13.9 | 28 | 24 |  |
| wheel_zoom | 80.5 | 290 | 1 (0.003) | 0.217 | 34.7 | 55.5 | — |  |
| flyto | 76.7 | 428 | 0 (0) | 0.164 | 20.9 | 41.7 | — |  |
| left_toggle | 77.4 | 378 | 15 (0.04) | 0.122 | 48.5 | 145.9 | 176 | no left-column toggle; fallback: open zone card from the list + close it ×2 (includes the map flyTo to the zone) |
| views | 139.9 | 543 | 0 (0) | 0.007 | 7.2 | 41.6 | 56 | tab-zones: 7 мс; tab-obs: 42 мс; tab-go: 35 мс; tab-metrics: 28 мс; tab-zones: 35 мс |
| studio | — | — | — | — | — | — | — | no studio in this UI (no data-testid=nav-studio) |
| idle | 144 | 432 | 0 (0) | 0 | 7.1 | 7.2 | — |  |

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

Ошибок: 0; внешних (хосты подложки): 0; упавших локальных запросов: 0.
