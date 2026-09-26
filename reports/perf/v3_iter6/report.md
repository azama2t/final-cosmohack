# Perf — v3_iter6 (http://127.0.0.1:8072/)

2026-09-26 02:23:09 … 2026-09-26 02:26:22; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×1
Сборка: {'entry_js': ['index-hW8zXmE7.js'], 'index_html_sha1': '8f7e22c3cae8'}

**Сводка:** fps pan 143.7, flyTo 143.8, first show 2.09 s (1920x1080); console errors 0; bundle gzip 0.364 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **2.09 с** по 3 холодным загрузкам [0.58, 2.09, 6.49]; в первой загрузке 2.09 с (first GL draw after first tile); WebGL-контекст 214 мс, первый GL-draw 277 мс, первый тайл пришёл 2076 мс, draw после тайла 2087 мс, тайлы загружены (map idle) 4217 мс; FCP 260 мс, LCP None мс, DCL 152 мс; handle карты: __map; запросов тайлов 36.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 3, 'vertical-perspective', 4.5, 'mercator'], 'n_sources': 6, 'sources_by_type': {'raster': 1, 'geojson': 5}, 'n_layers': 15, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 143.8 | 1271 | 0 (0) | 0.001 | 7.2 | 20.9 | 24 |  |
| pan #2 | 143.5 | 894 | 0 (0) | 0.001 | 7.2 | 27.8 | 24 |  |
| wheel_zoom | 137.6 | 453 | 1 (0.002) | 0.007 | 7.2 | 97.3 | — |  |
| flyto | 143.8 | 800 | 0 (0) | 0 | 7.2 | 14.2 | — |  |
| left_toggle | 144 | 421 | 0 (0) | 0 | 7.2 | 7.3 | 16 | left column toggle [data-testid=left-toggle] ×2 |
| views | 143.4 | 523 | 0 (0) | 0.002 | 7.1 | 20.9 | 40 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 21 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 142.7 | 1167 | 0 (0) | 0.003 | 7.1 | 27.9 | 24 | открытие студии 75 мс, выбор снимка max кадр 7 мс; виды: Снимок: 28 мс; Спектральный: 7 мс; Детекция: 7 мс; Качество: 7 мс; Снимок: 21 мс |
| idle | 144 | 441 | 0 (0) | 0 | 7.1 | 7.2 | — |  |

## 1366x768


Первый показ: медиана **0.57 с** по 3 холодным загрузкам [0.47, 0.57, 0.62]; в первой загрузке 0.47 с (first GL draw after first tile); WebGL-контекст 144 мс, первый GL-draw 196 мс, первый тайл пришёл 465 мс, draw после тайла 471 мс, тайлы загружены (map idle) 1333 мс; FCP 188 мс, LCP None мс, DCL 109 мс; handle карты: __map; запросов тайлов 34.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 3, 'vertical-perspective', 4.5, 'mercator'], 'n_sources': 6, 'sources_by_type': {'raster': 1, 'geojson': 5}, 'n_layers': 15, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 144 | 892 | 0 (0) | 0 | 7.2 | 7.3 | — |  |
| pan #2 | 144 | 945 | 0 (0) | 0 | 7.1 | 7.3 | 16 |  |
| wheel_zoom | 143.1 | 474 | 0 (0) | 0 | 7.2 | 13.9 | — |  |
| flyto | 143.5 | 804 | 0 (0) | 0 | 7 | 13.9 | — |  |
| left_toggle | 143.4 | 491 | 0 (0) | 0.002 | 7.1 | 20.9 | 16 | left column toggle [data-testid=left-toggle] ×2 |
| views | 144 | 559 | 0 (0) | 0 | 7.1 | 7.2 | — | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 7 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 143.8 | 1355 | 0 (0) | 0 | 7.1 | 14.3 | 24 | открытие студии 63 мс, выбор снимка max кадр 7 мс; виды: L8 10:26: 7 мс; S2 10:40: 7 мс; Снимок: 7 мс; Спектральный: 7 мс; Детекция: 7 мс; Качество: 7 мс; L8 10:26: 7 мс |
| idle | 144 | 433 | 0 (0) | 0 | 7 | 7.1 | — |  |

## Профиль CPU (pan + flyTo, 1-й размер)

Сценарий: {'pan_s': 10.95, 'flyto_s': 5.54}; всего 16837 мс, из них idle/program 13666 мс.

| функция | файл:строка | self, мс | доля занятого |
|---|---|---|---|
| `O_.R.S` | maplibre-CMX7JeUv.js:5 | 158.3 | 0.05 |
| `(garbage collector)` | :0 | 141.4 | 0.045 |
| `_update` | maplibre-CMX7JeUv.js:803 | 141.0 | 0.044 |
| `getTileBoundingVolume` | maplibre-CMX7JeUv.js:799 | 120.4 | 0.038 |
| `fromInvProjectionMatrix` | maplibre-CMX7JeUv.js:799 | 96.4 | 0.03 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 90.7 | 0.029 |
| `$e` | maplibre-CMX7JeUv.js:4 | 76.7 | 0.024 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 75.0 | 0.024 |
| `draw` | maplibre-CMX7JeUv.js:801 | 73.0 | 0.023 |
| `po` | maplibre-CMX7JeUv.js:5 | 69.6 | 0.022 |
| `vn` | maplibre-CMX7JeUv.js:803 | 63.4 | 0.02 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 61.4 | 0.019 |
| `be` | maplibre-CMX7JeUv.js:5 | 58.0 | 0.018 |
| `getAllTiles` | maplibre-CMX7JeUv.js:5 | 56.9 | 0.018 |
| `set` | maplibre-CMX7JeUv.js:5 | 56.7 | 0.018 |

По файлам (self): (idle) 11773.5, maplibre-CMX7JeUv.js 2707.2, (program) 1892.6, (garbage collector) 141.4, uniformMatrix4fv 46.4, getBufferSubData 29.6, drawElements 28.1, fetch 26.2

Trace (включая вложенное время):
- renderer_main: RunTask 4614.7, ThreadControllerImpl::RunTask 4458.2, FunctionCall 3195.3, FireAnimationFrame 3190.7, Receive mojo message 275.8, RunMicrotasks 119.9, Commit 102.3, MinorGC 101.2, V8.GC_SCAVENGER 97.2, V8.GC_SCAVENGER_SCAVENGE 96.4
- gpu:CrGpuMain: RunTask 1364.8, ThreadControllerImpl::RunTask 1347.8, Scheduler::RunTask 1308.3, GpuChannel::ExecuteDeferredRequest 956.6, GPUTask 935.3, CommandBuffer::Flush 856.2, CommandBufferStub::OnAsyncFlush 852.1, CommandBufferService:PutChanged 831.6, WebGL 679.0, RendererRasterWorker 182.2
- renderer_compositor: RunTask 519.4, ThreadControllerImpl::RunTask 494.4, IOHandler::OnIOCompleted 162.0, SimpleWatcher::OnHandleReady 125.1, GpuChannelHost::VerifyFlush 90.2, Receive mojo message 85.3, CommandBufferHelper::Flush 4.3, CommandBuffer::OrderingBarrier 3.3, cc::TaskGraphWorkQueue::ScheduleTasks 2.1, CommandBufferProxyImpl::Flush 1.7
- gpu:VizCompositorThread: RunTask 369.8, ThreadControllerImpl::RunTask 354.5, SimpleWatcher::OnHandleReady 75.1, Receive mojo message 47.0, Scheduler::ScheduleTask 8.0, SyncToken::Wait 0.1

## Профиль CPU холодной загрузки (1-й размер)

1.26 с; всего 1206 мс, idle/program 700 мс.

По файлам (self): (idle) 611.7, maplibre-CMX7JeUv.js 328.5, (program) 88.6, index-hW8zXmE7.js 31.6, (garbage collector) 28.5, getShaderParameter 25.0, window.__perfFindMap 20.9, getProgramParameter 9.4, texImage2D 9.1, getBufferSubData 6.1

Топ функций: `(garbage collector)` (:0) 28.5, `getShaderParameter` (:0) 25.0, `window.__perfFindMap` (:29) 20.9, `be` (maplibre-CMX7JeUv.js:5) 11.5, `getProgramParameter` (:0) 9.4, `_render` (maplibre-CMX7JeUv.js:803) 9.3, `texImage2D` (:0) 9.1, `_calcMatrices` (maplibre-CMX7JeUv.js:799) 9.1

## Бандл

3 файлов JS/CSS, 1348032 Б, gzip 363557 Б (0.364 МБ).

| файл | байт | gzip |
|---|---|---|
| maplibre-CMX7JeUv.js | 1053118 | 282339 |
| index-hW8zXmE7.js | 210709 | 68139 |
| index-Mm80fp2Q.css | 84205 | 13079 |

## Консоль

Ошибок: 0; внешних (хосты подложки): 10; упавших локальных запросов: 0.
