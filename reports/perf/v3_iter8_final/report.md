# Perf — v3_iter8_final (http://127.0.0.1:8072/)

2026-09-26 04:40:28 … 2026-09-26 04:43:15; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×1
Сборка: {'entry_js': ['index-CFzPXQSQ.js'], 'index_html_sha1': '6098acaf6d51'}

**Сводка:** fps pan 144.0, flyTo 144, first show 0.51 s (1920x1080); console errors 0; bundle gzip 0.364 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **0.51 с** по 3 холодным загрузкам [0.46, 0.51, 0.57]; в первой загрузке 0.57 с (first GL draw after first tile); WebGL-контекст 180 мс, первый GL-draw 239 мс, первый тайл пришёл 564 мс, draw после тайла 572 мс, тайлы загружены (map idle) 1438 мс; FCP 224 мс, LCP None мс, DCL 138 мс; handle карты: __map; запросов тайлов 36.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 3, 'vertical-perspective', 4.5, 'mercator'], 'n_sources': 6, 'sources_by_type': {'raster': 1, 'geojson': 5}, 'n_layers': 15, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 144 | 876 | 0 (0) | 0 | 7.1 | 7.3 | — |  |
| pan #2 | 144 | 847 | 0 (0) | 0 | 7.2 | 7.3 | 16 |  |
| wheel_zoom | 138 | 441 | 1 (0.002) | 0.009 | 7.2 | 69.6 | — |  |
| flyto | 144 | 798 | 0 (0) | 0 | 7.2 | 7.3 | — |  |
| left_toggle | 144 | 421 | 0 (0) | 0 | 7.2 | 7.3 | 16 | left column toggle [data-testid=left-toggle] ×2 |
| views | 144 | 536 | 0 (0) | 0 | 7.1 | 7.2 | 32 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 7 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 144 | 1084 | 0 (0) | 0 | 7 | 7.1 | 16 | открытие студии 30 мс, выбор снимка max кадр 7 мс; виды: Снимок: 7 мс; Спектральный: 7 мс; Детекция: 7 мс; Качество: 7 мс; Снимок: 7 мс |
| idle | 144 | 433 | 0 (0) | 0 | 7.2 | 7.3 | — |  |

## 1366x768


Первый показ: медиана **0.45 с** по 3 холодным загрузкам [0.45, 0.45, 0.53]; в первой загрузке 0.45 с (first GL draw after first tile); WebGL-контекст 113 мс, первый GL-draw 157 мс, первый тайл пришёл 441 мс, draw после тайла 448 мс, тайлы загружены (map idle) 1791 мс; FCP 148 мс, LCP None мс, DCL 92 мс; handle карты: __map; запросов тайлов 34.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 3, 'vertical-perspective', 4.5, 'mercator'], 'n_sources': 6, 'sources_by_type': {'raster': 1, 'geojson': 5}, 'n_layers': 15, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 144 | 846 | 0 (0) | 0 | 7.1 | 7.3 | — |  |
| pan #2 | 144 | 844 | 0 (0) | 0 | 7.2 | 7.3 | 16 |  |
| wheel_zoom | 143.7 | 436 | 0 (0) | 0 | 7 | 13.9 | — |  |
| flyto | 144 | 798 | 0 (0) | 0 | 7.1 | 7.3 | — |  |
| left_toggle | 144 | 419 | 0 (0) | 0 | 7.2 | 7.3 | — | left column toggle [data-testid=left-toggle] ×2 |
| views | 144 | 524 | 0 (0) | 0 | 7.1 | 7.2 | 16 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 7 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 144 | 1344 | 0 (0) | 0 | 7.1 | 7.2 | 16 | открытие студии 47 мс, выбор снимка max кадр 7 мс; виды: L8 10:26: 7 мс; S2 10:40: 7 мс; Снимок: 7 мс; Спектральный: 7 мс; Детекция: 7 мс; Качество: 7 мс; L8 10:26: 7 мс |
| idle | 144 | 434 | 0 (0) | 0 | 7.2 | 7.2 | — |  |

## Профиль CPU (pan + flyTo, 1-й размер)

Сценарий: {'pan_s': 6.16, 'flyto_s': 5.53}; всего 12015 мс, из них idle/program 9492 мс.

| функция | файл:строка | self, мс | доля занятого |
|---|---|---|---|
| `_update` | maplibre-CMX7JeUv.js:803 | 127.4 | 0.05 |
| `(garbage collector)` | :0 | 118.2 | 0.047 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 99.8 | 0.04 |
| `getTileBoundingVolume` | maplibre-CMX7JeUv.js:799 | 70.9 | 0.028 |
| `getTileById` | maplibre-CMX7JeUv.js:5 | 69.9 | 0.028 |
| `fromInvProjectionMatrix` | maplibre-CMX7JeUv.js:799 | 69.1 | 0.027 |
| `O_.R.S` | maplibre-CMX7JeUv.js:5 | 64.0 | 0.025 |
| `vn` | maplibre-CMX7JeUv.js:803 | 59.4 | 0.024 |
| `draw` | maplibre-CMX7JeUv.js:801 | 55.2 | 0.022 |
| `ys` | maplibre-CMX7JeUv.js:5 | 53.7 | 0.021 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 48.5 | 0.019 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 46.5 | 0.018 |
| `$e` | maplibre-CMX7JeUv.js:4 | 44.2 | 0.018 |
| `uniformMatrix4fv` | :0 | 42.8 | 0.017 |
| `be` | maplibre-CMX7JeUv.js:5 | 42.7 | 0.017 |

По файлам (self): (idle) 8422.5, maplibre-CMX7JeUv.js 2101.2, (program) 1069.6, (garbage collector) 118.2, uniformMatrix4fv 42.8, getBufferSubData 21.4, (anonymous) 18.0, fetch 17.4

Trace (включая вложенное время):
- renderer_main: RunTask 3508.2, ThreadControllerImpl::RunTask 3346.2, FunctionCall 2379.8, FireAnimationFrame 2375.0, Receive mojo message 205.3, RunMicrotasks 124.6, BlinkScheduler_PerformMicrotaskCheckpoint 104.2, Commit 73.8, UpdateLayoutTree 70.3, MinorGC 69.2
- renderer_compositor: RunTask 412.2, ThreadControllerImpl::RunTask 390.4, IOHandler::OnIOCompleted 136.1, SimpleWatcher::OnHandleReady 103.9, GpuChannelHost::VerifyFlush 80.2, Receive mojo message 69.9, CommandBufferHelper::Flush 3.0, CommandBuffer::OrderingBarrier 2.6, cc::TaskGraphWorkQueue::ScheduleTasks 1.5, CommandBufferProxyImpl::Flush 1.4
- gpu:VizCompositorThread: RunTask 286.9, ThreadControllerImpl::RunTask 274.4, SimpleWatcher::OnHandleReady 61.7, Receive mojo message 38.4, Scheduler::ScheduleTask 4.5, SyncToken::Wait 0.1
- gpu:CrGpuMain: RunTask 1037.2, ThreadControllerImpl::RunTask 1023.0, Scheduler::RunTask 990.6, GpuChannel::ExecuteDeferredRequest 728.2, GPUTask 713.0, CommandBuffer::Flush 651.1, CommandBufferStub::OnAsyncFlush 648.2, CommandBufferService:PutChanged 633.6, WebGL 513.9, RendererRasterWorker 141.0

## Профиль CPU холодной загрузки (1-й размер)

1.47 с; всего 1351 мс, idle/program 846 мс.

По файлам (self): (idle) 736.1, maplibre-CMX7JeUv.js 330.8, (program) 109.8, index-CFzPXQSQ.js 28.6, (garbage collector) 25.9, getShaderParameter 18.9, window.__perfFindMap 14.0, getProgramParameter 8.4, bindVertexArray 7.1, getBufferSubData 6.3

Топ функций: `(garbage collector)` (:0) 25.9, `getShaderParameter` (:0) 18.9, `window.__perfFindMap` (:29) 14.0, `getProgramParameter` (:0) 8.4, `render` (maplibre-CMX7JeUv.js:803) 8.4, `(anonymous)` (maplibre-CMX7JeUv.js:5) 8.2, `_render` (maplibre-CMX7JeUv.js:803) 7.9, `_calcMatrices` (maplibre-CMX7JeUv.js:799) 7.5

## Бандл

3 файлов JS/CSS, 1350488 Б, gzip 364465 Б (0.364 МБ).

| файл | байт | gzip |
|---|---|---|
| maplibre-CMX7JeUv.js | 1053118 | 282339 |
| index-CFzPXQSQ.js | 213105 | 69035 |
| index-ChPxewb1.css | 84265 | 13091 |

## Консоль

Ошибок: 0; внешних (хосты подложки): 0; упавших локальных запросов: 0.
