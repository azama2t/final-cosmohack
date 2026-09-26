# Perf — v3_iter2 (http://127.0.0.1:8072/)

2026-09-26 01:31:17 … 2026-09-26 01:34:13; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×1

**Сводка:** fps pan 143.2, flyTo 142.7, first show 0.68 s (1920x1080); console errors 0; bundle gzip 0.361 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **0.68 с** по 3 холодным загрузкам [0.49, 0.68, 5.52]; в первой загрузке 0.68 с (first GL draw after first tile); WebGL-контекст 170 мс, первый GL-draw 291 мс, первый тайл пришёл 674 мс, draw после тайла 680 мс, тайлы загружены (map idle) 1336 мс; FCP 248 мс, LCP None мс, DCL 116 мс; handle карты: __map; запросов тайлов 36.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 5, 'vertical-perspective', 7, 'mercator'], 'n_sources': 10, 'sources_by_type': {'raster': 1, 'geojson': 9}, 'n_layers': 21, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 142.5 | 953 | 0 (0) | 0.004 | 7.1 | 27.9 | 16 |  |
| pan #2 | 144 | 846 | 0 (0) | 0 | 7.2 | 7.3 | 16 |  |
| wheel_zoom | 142.7 | 447 | 0 (0) | 0.004 | 7.1 | 20.9 | — |  |
| flyto | 142.7 | 790 | 0 (0) | 0.004 | 7.2 | 20.8 | — |  |
| left_toggle | 143.3 | 425 | 0 (0) | 0.002 | 7.2 | 20.8 | 24 | left column toggle [data-testid=left-toggle] ×2 |
| views | 144 | 535 | 0 (0) | 0 | 7.1 | 7.2 | 24 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 7 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 142.8 | 1083 | 0 (0) | 0.003 | 7.2 | 27.8 | 24 | открытие студии 88 мс, выбор снимка max кадр 7 мс; виды: Снимок: 7 мс; Спектральный: 7 мс; Детекция: 14 мс; Качество: 7 мс; Снимок: 28 мс |
| idle | 144 | 435 | 0 (0) | 0 | 7.2 | 7.3 | — |  |

## 1366x768


Первый показ: медиана **0.5 с** по 3 холодным загрузкам [0.46, 0.5, 0.76]; в первой загрузке 0.5 с (first GL draw after first tile); WebGL-контекст 140 мс, первый GL-draw 194 мс, первый тайл пришёл 490 мс, draw после тайла 499 мс, тайлы загружены (map idle) 1841 мс; FCP 172 мс, LCP None мс, DCL 118 мс; handle карты: __map; запросов тайлов 34.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 5, 'vertical-perspective', 7, 'mercator'], 'n_sources': 10, 'sources_by_type': {'raster': 1, 'geojson': 9}, 'n_layers': 21, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 144 | 848 | 0 (0) | 0 | 7.1 | 7.2 | 16 |  |
| pan #2 | 144 | 856 | 0 (0) | 0 | 7.1 | 7.2 | 16 |  |
| wheel_zoom | 144 | 442 | 0 (0) | 0 | 7 | 7.1 | — |  |
| flyto | 143.8 | 797 | 0 (0) | 0 | 7.2 | 14.2 | — |  |
| left_toggle | 144 | 416 | 0 (0) | 0 | 7.1 | 7.2 | 16 | left column toggle [data-testid=left-toggle] ×2 |
| views | 144 | 526 | 0 (0) | 0 | 7.1 | 7.3 | 16 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 7 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 144 | 1349 | 0 (0) | 0 | 7.2 | 7.3 | 16 | открытие студии 41 мс, выбор снимка max кадр 7 мс; виды: L8 10:26: 7 мс; S2 10:40: 7 мс; Снимок: 7 мс; Спектральный: 7 мс; Детекция: 7 мс; Качество: 7 мс; L8 10:26: 7 мс |
| idle | 144 | 434 | 0 (0) | 0 | 7.1 | 7.2 | — |  |

## Профиль CPU (pan + flyTo, 1-й размер)

Сценарий: {'pan_s': 5.89, 'flyto_s': 5.54}; всего 11736 мс, из них idle/program 9198 мс.

| функция | файл:строка | self, мс | доля занятого |
|---|---|---|---|
| `(garbage collector)` | :0 | 137.2 | 0.054 |
| `O_.R.S` | maplibre-CMX7JeUv.js:5 | 113.0 | 0.045 |
| `_update` | maplibre-CMX7JeUv.js:803 | 109.7 | 0.043 |
| `fromInvProjectionMatrix` | maplibre-CMX7JeUv.js:799 | 86.4 | 0.034 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 83.3 | 0.033 |
| `draw` | maplibre-CMX7JeUv.js:801 | 58.1 | 0.023 |
| `vn` | maplibre-CMX7JeUv.js:803 | 56.9 | 0.022 |
| `$e` | maplibre-CMX7JeUv.js:4 | 56.4 | 0.022 |
| `_renderTileMasks` | maplibre-CMX7JeUv.js:803 | 50.9 | 0.02 |
| `W` | maplibre-CMX7JeUv.js:4 | 50.0 | 0.02 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 50.0 | 0.02 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 48.2 | 0.019 |
| `be` | maplibre-CMX7JeUv.js:5 | 46.2 | 0.018 |
| `getAllTiles` | maplibre-CMX7JeUv.js:5 | 43.6 | 0.017 |
| `K` | maplibre-CMX7JeUv.js:4 | 43.4 | 0.017 |

По файлам (self): (idle) 8006.3, maplibre-CMX7JeUv.js 2122.9, (program) 1191.8, (garbage collector) 137.2, uniformMatrix4fv 36.6, drawElements 27.4, getBufferSubData 25.8, fetch 18.6

Trace (включая вложенное время):
- renderer_main: RunTask 3539.9, ThreadControllerImpl::RunTask 3411.1, FunctionCall 2466.6, FireAnimationFrame 2458.3, Receive mojo message 196.3, RunMicrotasks 97.3, MinorGC 93.6, V8.GC_SCAVENGER 89.8, V8.GC_SCAVENGER_SCAVENGE 89.2, BlinkScheduler_PerformMicrotaskCheckpoint 79.5
- renderer_compositor: RunTask 390.3, ThreadControllerImpl::RunTask 370.9, IOHandler::OnIOCompleted 117.4, SimpleWatcher::OnHandleReady 90.1, GpuChannelHost::VerifyFlush 78.5, Receive mojo message 61.0, CommandBufferHelper::Flush 2.9, CommandBuffer::OrderingBarrier 2.4, cc::TaskGraphWorkQueue::ScheduleTasks 1.5, CommandBufferProxyImpl::Flush 1.2
- gpu:VizCompositorThread: RunTask 269.3, ThreadControllerImpl::RunTask 257.7, SimpleWatcher::OnHandleReady 56.4, Receive mojo message 35.2, Scheduler::ScheduleTask 4.8, SyncToken::Wait 0.1
- gpu:CrGpuMain: RunTask 1023.2, ThreadControllerImpl::RunTask 1009.4, Scheduler::RunTask 978.3, GpuChannel::ExecuteDeferredRequest 724.7, GPUTask 710.6, CommandBuffer::Flush 652.4, CommandBufferStub::OnAsyncFlush 649.3, CommandBufferService:PutChanged 635.0, WebGL 516.1, RendererRasterWorker 139.9

## Профиль CPU холодной загрузки (1-й размер)

1.61 с; всего 1501 мс, idle/program 1057 мс.

По файлам (self): (idle) 961.6, maplibre-CMX7JeUv.js 313.8, (program) 95.1, index-RK3EAQ13.js 25.2, (garbage collector) 21.5, getShaderParameter 18.4, P.<computed> 10.5, getProgramParameter 5.7, texImage2D 4.9, uniform4f 4.6

Топ функций: `(garbage collector)` (:0) 21.5, `getShaderParameter` (:0) 18.4, `draw` (maplibre-CMX7JeUv.js:801) 10.8, `P.<computed>` (:22) 10.5, `render` (maplibre-CMX7JeUv.js:803) 7.8, `_calcMatrices` (maplibre-CMX7JeUv.js:799) 7.8, `_renderTileClippingMasks` (maplibre-CMX7JeUv.js:803) 6.9, `(anonymous)` (index-RK3EAQ13.js:1) 6.9

## Бандл

3 файлов JS/CSS, 1338971 Б, gzip 360988 Б (0.361 МБ).

| файл | байт | gzip |
|---|---|---|
| maplibre-CMX7JeUv.js | 1053118 | 282339 |
| index-C_WOooco.js | 202205 | 65696 |
| index-BA2dAAPK.css | 83648 | 12953 |

## Консоль

Ошибок: 0; внешних (хосты подложки): 0; упавших локальных запросов: 0.
