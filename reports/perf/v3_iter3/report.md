# Perf — v3_iter3 (http://127.0.0.1:8072/)

2026-09-26 02:00:42 … 2026-09-26 02:03:48; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×1
Сборка: {'entry_js': ['index-Ce-Qf8rc.js'], 'index_html_sha1': 'e16fb348fe04'}

**Сводка:** fps pan 143.3, flyTo 144, first show 0.64 s (1920x1080); console errors 0; bundle gzip 0.362 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **0.64 с** по 3 холодным загрузкам [0.61, 0.64, 0.66]; в первой загрузке 0.66 с (first GL draw after first tile); WebGL-контекст 179 мс, первый GL-draw 239 мс, первый тайл пришёл 635 мс, draw после тайла 660 мс, тайлы загружены (map idle) 1435 мс; FCP 228 мс, LCP None мс, DCL 122 мс; handle карты: __map; запросов тайлов 36.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 5, 'vertical-perspective', 7, 'mercator'], 'n_sources': 6, 'sources_by_type': {'raster': 1, 'geojson': 5}, 'n_layers': 15, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 142.6 | 1349 | 0 (0) | 0.002 | 7.1 | 27.8 | 16 |  |
| pan #2 | 144 | 852 | 0 (0) | 0 | 7.2 | 7.3 | 24 |  |
| wheel_zoom | 142.1 | 451 | 0 (0) | 0.004 | 7 | 34.7 | — |  |
| flyto | 144 | 796 | 0 (0) | 0 | 7.2 | 7.3 | — |  |
| left_toggle | 144 | 420 | 0 (0) | 0 | 7.2 | 7.3 | 16 | left column toggle [data-testid=left-toggle] ×2 |
| views | 142.9 | 524 | 0 (0) | 0.002 | 7.1 | 28 | 48 | nav-sites: 15 мс; nav-studio: 7 мс; nav-layers: 28 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 143.5 | 1174 | 0 (0) | 0.001 | 7.1 | 20.8 | 32 | открытие студии 96 мс, выбор снимка max кадр 7 мс; виды: Снимок: 7 мс; Спектральный: 7 мс; Детекция: 14 мс; Качество: 7 мс; Снимок: 7 мс |
| idle | 144 | 433 | 0 (0) | 0 | 7.2 | 7.3 | — |  |

## 1366x768


Первый показ: медиана **1.51 с** по 3 холодным загрузкам [0.93, 1.51, 1.82]; в первой загрузке 1.82 с (first GL draw after first tile); WebGL-контекст 120 мс, первый GL-draw 160 мс, первый тайл пришёл 1817 мс, draw после тайла 1822 мс, тайлы загружены (map idle) 3305 мс; FCP 148 мс, LCP None мс, DCL 101 мс; handle карты: __map; запросов тайлов 34.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 5, 'vertical-perspective', 7, 'mercator'], 'n_sources': 6, 'sources_by_type': {'raster': 1, 'geojson': 5}, 'n_layers': 15, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 143.6 | 1379 | 0 (0) | 0.001 | 7.2 | 20.8 | 24 |  |
| pan #2 | 144 | 858 | 0 (0) | 0 | 7.2 | 7.3 | 24 |  |
| wheel_zoom | 144 | 449 | 0 (0) | 0 | 7.1 | 7.2 | — |  |
| flyto | 144 | 795 | 0 (0) | 0 | 7.2 | 7.3 | — |  |
| left_toggle | 144 | 435 | 0 (0) | 0 | 7.1 | 7.3 | 16 | left column toggle [data-testid=left-toggle] ×2 |
| views | 144 | 540 | 0 (0) | 0 | 7.1 | 7.2 | 16 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 7 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 144 | 1355 | 0 (0) | 0 | 7.1 | 7.4 | 16 | открытие студии 76 мс, выбор снимка max кадр None мс; виды: L8 10:26: 7 мс; S2 10:40: 7 мс; Снимок: 7 мс; Спектральный: 7 мс; Детекция: 7 мс; Качество: 7 мс; L8 10:26: 7 мс |
| idle | 144 | 434 | 0 (0) | 0 | 7.1 | 7.2 | — |  |

## Профиль CPU (pan + flyTo, 1-й размер)

Сценарий: {'pan_s': 6.56, 'flyto_s': 5.53}; всего 12403 мс, из них idle/program 9106 мс.

| функция | файл:строка | self, мс | доля занятого |
|---|---|---|---|
| `_update` | maplibre-CMX7JeUv.js:803 | 146.6 | 0.044 |
| `(garbage collector)` | :0 | 128.2 | 0.039 |
| `calculatePosMatrix` | maplibre-CMX7JeUv.js:799 | 115.3 | 0.035 |
| `getTileBoundingVolume` | maplibre-CMX7JeUv.js:799 | 107.6 | 0.033 |
| `O_.R.S` | maplibre-CMX7JeUv.js:5 | 106.6 | 0.032 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 104.3 | 0.032 |
| `fromInvProjectionMatrix` | maplibre-CMX7JeUv.js:799 | 95.6 | 0.029 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 93.3 | 0.028 |
| `getTileById` | maplibre-CMX7JeUv.js:5 | 79.1 | 0.024 |
| `draw` | maplibre-CMX7JeUv.js:801 | 74.7 | 0.023 |
| `$e` | maplibre-CMX7JeUv.js:4 | 71.6 | 0.022 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 64.3 | 0.02 |
| `set` | maplibre-CMX7JeUv.js:5 | 61.6 | 0.019 |
| `Ni` | maplibre-CMX7JeUv.js:5 | 57.5 | 0.017 |
| `uniformMatrix4fv` | :0 | 57.0 | 0.017 |

По файлам (self): (idle) 7662.9, maplibre-CMX7JeUv.js 2765.3, (program) 1442.8, (garbage collector) 128.2, uniformMatrix4fv 57.0, drawElements 43.5, getBufferSubData 30.8, bindVertexArray 26.4

Trace (включая вложенное время):
- renderer_main: RunTask 4643.2, ThreadControllerImpl::RunTask 4463.1, FunctionCall 3216.3, FireAnimationFrame 3206.9, Receive mojo message 319.7, RunMicrotasks 137.8, BlinkScheduler_PerformMicrotaskCheckpoint 115.1, Commit 94.6, UpdateLayoutTree 90.3, Layout 84.9
- renderer_compositor: RunTask 522.4, ThreadControllerImpl::RunTask 496.7, IOHandler::OnIOCompleted 159.3, SimpleWatcher::OnHandleReady 122.3, GpuChannelHost::VerifyFlush 95.2, Receive mojo message 83.7, CommandBufferHelper::Flush 4.0, CommandBuffer::OrderingBarrier 3.5, cc::TaskGraphWorkQueue::ScheduleTasks 2.0, CommandBufferProxyImpl::Flush 1.7
- gpu:VizCompositorThread: RunTask 362.2, ThreadControllerImpl::RunTask 347.4, SimpleWatcher::OnHandleReady 75.4, Receive mojo message 47.4, Scheduler::ScheduleTask 6.9, SyncToken::Wait 0.1
- gpu:CrGpuMain: RunTask 1418.3, ThreadControllerImpl::RunTask 1401.6, Scheduler::RunTask 1362.9, GpuChannel::ExecuteDeferredRequest 1007.7, GPUTask 987.9, CommandBuffer::Flush 906.5, CommandBufferStub::OnAsyncFlush 902.5, CommandBufferService:PutChanged 881.7, WebGL 721.7, RendererRasterWorker 189.9

## Профиль CPU холодной загрузки (1-й размер)

1.06 с; всего 1008 мс, idle/program 645 мс.

По файлам (self): (idle) 558.5, maplibre-CMX7JeUv.js 222.5, (program) 86.0, index-DjMjfWHr.js 29.9, (garbage collector) 22.4, window.__perfFindMap 21.0, getShaderParameter 18.0, getProgramParameter 5.5, fetch 4.0, getContext 4.0

Топ функций: `(garbage collector)` (:0) 22.4, `window.__perfFindMap` (:29) 21.0, `getShaderParameter` (:0) 18.0, `(anonymous)` (index-DjMjfWHr.js:1) 9.0, `_calcMatrices` (maplibre-CMX7JeUv.js:799) 7.8, `O_.R.S` (maplibre-CMX7JeUv.js:5) 6.3, `_calcMatrices` (maplibre-CMX7JeUv.js:799) 5.6, `getProgramParameter` (:0) 5.5

## Бандл

3 файлов JS/CSS, 1341265 Б, gzip 361852 Б (0.362 МБ).

| файл | байт | gzip |
|---|---|---|
| maplibre-CMX7JeUv.js | 1053118 | 282339 |
| index-Ce-Qf8rc.js | 204401 | 66535 |
| index-B4DP61bC.css | 83746 | 12978 |

## Консоль

Ошибок: 0; внешних (хосты подложки): 2; упавших локальных запросов: 0.
