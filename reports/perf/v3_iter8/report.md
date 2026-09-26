# Perf — v3_iter8 (http://127.0.0.1:8072/)

2026-09-26 02:39:29 … 2026-09-26 02:42:30; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×1
Сборка: {'entry_js': ['index-CFzPXQSQ.js'], 'index_html_sha1': '6098acaf6d51'}

**Сводка:** fps pan 143.8, flyTo 141.3, first show 0.67 s (1920x1080); console errors 0; bundle gzip 0.364 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **0.67 с** по 3 холодным загрузкам [0.63, 0.67, 1.42]; в первой загрузке 1.42 с (first GL draw after first tile); WebGL-контекст 556 мс, первый GL-draw 737 мс, первый тайл пришёл 1413 мс, draw после тайла 1422 мс, тайлы загружены (map idle) 2517 мс; FCP 728 мс, LCP None мс, DCL 142 мс; handle карты: __map; запросов тайлов 36.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 3, 'vertical-perspective', 4.5, 'mercator'], 'n_sources': 6, 'sources_by_type': {'raster': 1, 'geojson': 5}, 'n_layers': 15, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 143.7 | 886 | 0 (0) | 0.001 | 7.2 | 20.9 | 24 |  |
| pan #2 | 144 | 857 | 0 (0) | 0 | 7.2 | 7.3 | 24 |  |
| wheel_zoom | 135.7 | 472 | 1 (0.002) | 0.008 | 7.2 | 118.1 | — |  |
| flyto | 141.3 | 787 | 0 (0) | 0.008 | 7.2 | 20.9 | — |  |
| left_toggle | 142.7 | 439 | 0 (0) | 0.005 | 7.2 | 20.9 | — | left column toggle [data-testid=left-toggle] ×2 |
| views | 143.5 | 582 | 0 (0) | 0.002 | 7.1 | 20.9 | 40 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 21 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 143.4 | 1123 | 0 (0) | 0.002 | 7.1 | 27.7 | 24 | открытие студии 55 мс, выбор снимка max кадр 7 мс; виды: Снимок: 7 мс; Спектральный: 7 мс; Детекция: 7 мс; Качество: 7 мс; Снимок: 28 мс |
| idle | 144 | 434 | 0 (0) | 0 | 7.2 | 7.3 | — |  |

## 1366x768


Первый показ: медиана **0.59 с** по 3 холодным загрузкам [0.56, 0.59, 0.6]; в первой загрузке 0.59 с (first GL draw after first tile); WebGL-контекст 222 мс, первый GL-draw 273 мс, первый тайл пришёл 573 мс, draw после тайла 591 мс, тайлы загружены (map idle) 1572 мс; FCP 228 мс, LCP None мс, DCL 194 мс; handle карты: __map; запросов тайлов 34.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 3, 'vertical-perspective', 4.5, 'mercator'], 'n_sources': 6, 'sources_by_type': {'raster': 1, 'geojson': 5}, 'n_layers': 15, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 143.5 | 940 | 0 (0) | 0.001 | 7.1 | 27.8 | 24 |  |
| pan #2 | 144 | 861 | 0 (0) | 0 | 7.1 | 7.2 | 24 |  |
| wheel_zoom | 143 | 448 | 0 (0) | 0.002 | 7.1 | 20.9 | — |  |
| flyto | 143.6 | 798 | 0 (0) | 0.001 | 7.1 | 20.8 | — |  |
| left_toggle | 144 | 452 | 0 (0) | 0 | 7.1 | 7.2 | — | left column toggle [data-testid=left-toggle] ×2 |
| views | 144 | 555 | 0 (0) | 0 | 7.1 | 7.2 | — | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 7 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 143.8 | 1355 | 0 (0) | 0.001 | 7.1 | 20.9 | 16 | открытие студии 68 мс, выбор снимка max кадр 7 мс; виды: L8 10:26: 7 мс; S2 10:40: 7 мс; Снимок: 7 мс; Спектральный: 7 мс; Детекция: 7 мс; Качество: 7 мс; L8 10:26: 7 мс |
| idle | 144 | 434 | 0 (0) | 0 | 7.1 | 7.2 | — |  |

## Профиль CPU (pan + flyTo, 1-й размер)

Сценарий: {'pan_s': 8.65, 'flyto_s': 5.53}; всего 14520 мс, из них idle/program 11137 мс.

| функция | файл:строка | self, мс | доля занятого |
|---|---|---|---|
| `(garbage collector)` | :0 | 175.4 | 0.052 |
| `O_.R.S` | maplibre-CMX7JeUv.js:5 | 151.3 | 0.045 |
| `_update` | maplibre-CMX7JeUv.js:803 | 141.1 | 0.042 |
| `getTileById` | maplibre-CMX7JeUv.js:5 | 118.0 | 0.035 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 115.8 | 0.034 |
| `fromInvProjectionMatrix` | maplibre-CMX7JeUv.js:799 | 97.0 | 0.029 |
| `getTileBoundingVolume` | maplibre-CMX7JeUv.js:799 | 93.9 | 0.028 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 90.6 | 0.027 |
| `getAllTiles` | maplibre-CMX7JeUv.js:5 | 75.6 | 0.022 |
| `draw` | maplibre-CMX7JeUv.js:801 | 75.5 | 0.022 |
| `vn` | maplibre-CMX7JeUv.js:803 | 74.9 | 0.022 |
| `be` | maplibre-CMX7JeUv.js:5 | 59.3 | 0.018 |
| `set` | maplibre-CMX7JeUv.js:5 | 55.2 | 0.016 |
| `Ni` | maplibre-CMX7JeUv.js:5 | 51.1 | 0.015 |
| `uniformMatrix4fv` | :0 | 50.0 | 0.015 |

По файлам (self): (idle) 9681.0, maplibre-CMX7JeUv.js 2834.3, (program) 1455.7, (garbage collector) 175.4, uniformMatrix4fv 50.0, drawElements 45.6, getBufferSubData 35.1, fetch 22.6

Trace (включая вложенное время):
- renderer_main: RunTask 4709.4, ThreadControllerImpl::RunTask 4527.4, FunctionCall 3264.1, FireAnimationFrame 3256.9, Receive mojo message 272.4, RunMicrotasks 146.2, Commit 116.8, BlinkScheduler_PerformMicrotaskCheckpoint 116.1, MinorGC 98.1, V8.GC_SCAVENGER 93.0
- gpu:CrGpuMain: RunTask 1428.1, ThreadControllerImpl::RunTask 1411.1, Scheduler::RunTask 1368.2, GpuChannel::ExecuteDeferredRequest 1026.5, GPUTask 1006.2, CommandBuffer::Flush 926.3, CommandBufferStub::OnAsyncFlush 922.3, CommandBufferService:PutChanged 902.4, WebGL 727.3, RendererRasterWorker 203.7
- renderer_compositor: RunTask 501.4, ThreadControllerImpl::RunTask 477.5, IOHandler::OnIOCompleted 151.6, SimpleWatcher::OnHandleReady 117.0, GpuChannelHost::VerifyFlush 94.6, Receive mojo message 79.5, CommandBufferHelper::Flush 3.5, CommandBuffer::OrderingBarrier 3.1, cc::TaskGraphWorkQueue::ScheduleTasks 2.0, CommandBufferProxyImpl::Flush 1.4
- gpu:VizCompositorThread: RunTask 364.1, ThreadControllerImpl::RunTask 349.3, SimpleWatcher::OnHandleReady 77.0, Receive mojo message 48.6, Scheduler::ScheduleTask 6.7, SyncToken::Wait 0.1

## Профиль CPU холодной загрузки (1-й размер)

1.27 с; всего 1120 мс, idle/program 654 мс.

По файлам (self): (idle) 554.5, maplibre-CMX7JeUv.js 302.7, (program) 99.9, index-CFzPXQSQ.js 31.9, (garbage collector) 28.2, getShaderParameter 25.7, window.__perfFindMap 15.0, getProgramParameter 7.9, texImage2D 6.1, fetch 5.0

Топ функций: `(garbage collector)` (:0) 28.2, `getShaderParameter` (:0) 25.7, `eo` (maplibre-CMX7JeUv.js:803) 23.4, `window.__perfFindMap` (:29) 15.0, `O_.R.S` (maplibre-CMX7JeUv.js:5) 11.5, `oc` (maplibre-CMX7JeUv.js:5) 8.7, `(anonymous)` (maplibre-CMX7JeUv.js:5) 8.5, `(anonymous)` (index-CFzPXQSQ.js:1) 8.1

## Бандл

3 файлов JS/CSS, 1350488 Б, gzip 364465 Б (0.364 МБ).

| файл | байт | gzip |
|---|---|---|
| maplibre-CMX7JeUv.js | 1053118 | 282339 |
| index-CFzPXQSQ.js | 213105 | 69035 |
| index-ChPxewb1.css | 84265 | 13091 |

## Консоль

Ошибок: 0; внешних (хосты подложки): 0; упавших локальных запросов: 0.
