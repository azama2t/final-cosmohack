# Perf — v3_iter6_cpu4 (http://127.0.0.1:8072/)

2026-09-26 02:26:22 … 2026-09-26 02:29:42; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×4.0
Сборка: {'entry_js': ['index-D0Xnu6Ni.js'], 'index_html_sha1': '36b53aa4bcfa'}

**Сводка:** fps pan 105.3, flyTo 59.5, first show 1.41 s (1920x1080); console errors 2; bundle gzip 0.364 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **1.41 с** по 3 холодным загрузкам [1.06, 1.41, 1.44]; в первой загрузке 1.44 с (first GL draw after first tile); WebGL-контекст 562 мс, первый GL-draw 937 мс, первый тайл пришёл 1336 мс, draw после тайла 1437 мс, тайлы загружены (map idle) 2769 мс; FCP 504 мс, LCP None мс, DCL 292 мс; handle карты: __map; запросов тайлов 36.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 3, 'vertical-perspective', 4.5, 'mercator'], 'n_sources': 6, 'sources_by_type': {'raster': 1, 'geojson': 5}, 'n_layers': 15, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 97.5 | 1159 | 0 (0) | 0.097 | 20.8 | 41.7 | 48 |  |
| pan #2 | 113.1 | 1793 | 1 (0.001) | 0.049 | 14.2 | 62.6 | 32 |  |
| wheel_zoom | 67.4 | 300 | 10 (0.033) | 0.213 | 48.6 | 173.5 | — |  |
| flyto | 59.5 | 332 | 3 (0.009) | 0.389 | 34.7 | 97.2 | — |  |
| left_toggle | 140.2 | 440 | 0 (0) | 0.002 | 7.1 | 34.6 | 48 | left column toggle [data-testid=left-toggle] ×2 |
| views | 141.9 | 539 | 0 (0) | 0.002 | 7.1 | 48.8 | 80 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 49 мс; nav-export: 14 мс; nav-sites: 7 мс |
| studio | 93 | 862 | 14 (0.016) | 0.13 | 28 | 90.2 | 64 | открытие студии 468 мс, выбор снимка max кадр 35 мс; виды: Снимок: 49 мс; Спектральный: 21 мс; Детекция: 35 мс; Качество: 90 мс; Снимок: 48 мс |
| idle | 136.1 | 415 | 1 (0.002) | 0.017 | 7.2 | 55.5 | — |  |

## 1366x768


Первый показ: медиана **1.22 с** по 3 холодным загрузкам [1.08, 1.22, 1.31]; в первой загрузке 1.08 с (first GL draw after first tile); WebGL-контекст 254 мс, первый GL-draw 559 мс, первый тайл пришёл 1030 мс, draw после тайла 1078 мс, тайлы загружены (map idle) 2098 мс; FCP 244 мс, LCP None мс, DCL 175 мс; handle карты: __map; запросов тайлов 34.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 3, 'vertical-perspective', 4.5, 'mercator'], 'n_sources': 6, 'sources_by_type': {'raster': 1, 'geojson': 5}, 'n_layers': 15, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 95.5 | 1133 | 0 (0) | 0.092 | 20.8 | 41.7 | 48 |  |
| pan #2 | 101.8 | 1607 | 2 (0.001) | 0.065 | 20.8 | 76.5 | 40 |  |
| wheel_zoom | 65.6 | 292 | 9 (0.031) | 0.267 | 41.7 | 152.7 | — |  |
| flyto | 58.7 | 330 | 8 (0.024) | 0.385 | 41.7 | 76.4 | — |  |
| left_toggle | 139.4 | 451 | 0 (0) | 0.004 | 7.1 | 41.7 | 72 | left column toggle [data-testid=left-toggle] ×2 |
| views | 141.7 | 552 | 0 (0) | 0.004 | 7.2 | 48.6 | 64 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 49 мс; nav-export: 21 мс; nav-sites: 7 мс |
| studio | 110.7 | 1299 | 6 (0.005) | 0.079 | 20.9 | 90.4 | 72 | открытие студии 742 мс, выбор снимка max кадр 28 мс; виды: L8 10:26: 35 мс; S2 10:40: 90 мс; Снимок: 42 мс; Спектральный: 21 мс; Детекция: 35 мс; Качество: 21 мс; L8 10:26: 42 мс |
| idle | 134.8 | 411 | 0 (0) | 0.019 | 7.2 | 34.9 | — |  |

## Бандл

3 файлов JS/CSS, 1349699 Б, gzip 364224 Б (0.364 МБ).

| файл | байт | gzip |
|---|---|---|
| maplibre-CMX7JeUv.js | 1053118 | 282339 |
| index-D0Xnu6Ni.js | 212335 | 68796 |
| index-4kFagHpe.css | 84246 | 13089 |

## Консоль

Ошибок: 2; внешних (хосты подложки): 0; упавших локальных запросов: 2.
- Failed to load resource: net::ERR_CONNECTION_REFUSED [http://127.0.0.1:8072/api/v3/pairs?source=S2_SARGASSO_MSM41&date_from=2015-04-20&date_to=2015-04-26&limit=5000]
- Failed to load resource: net::ERR_CONNECTION_REFUSED [http://127.0.0.1:8072/api/v3/studio/scenes?bbox=-58.0739,29.0041,-57.9793,29.2784&limit=2000]
- failed: net::ERR_CONNECTION_REFUSED http://127.0.0.1:8072/api/v3/pairs?source=S2_SARGASSO_MSM41&date_from=2015-04-20&date_to=2015-04-26&limit=5000
- failed: net::ERR_CONNECTION_REFUSED http://127.0.0.1:8072/api/v3/studio/scenes?bbox=-58.0739,29.0041,-57.9793,29.2784&limit=2000
