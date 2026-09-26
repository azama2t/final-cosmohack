# Perf — v3_iter6b (http://127.0.0.1:8072/)

2026-09-26 02:31:14 … 2026-09-26 02:34:32; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×1
Сборка: {'entry_js': ['index-D-Q0E8sP.js'], 'index_html_sha1': '6098acaf6d51'}

**Сводка:** fps pan 143.8, flyTo 142.9, first show 0.65 s (1920x1080); console errors 0; bundle gzip 0.364 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **0.65 с** по 3 холодным загрузкам [0.5, 0.65, 12.26]; в первой загрузке 12.26 с (first GL draw after first tile); WebGL-контекст 227 мс, первый GL-draw 419 мс, первый тайл пришёл 12241 мс, draw после тайла 12263 мс, тайлы загружены (map idle) 12996 мс; FCP 408 мс, LCP None мс, DCL 116 мс; handle карты: __map; запросов тайлов 16.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 3, 'vertical-perspective', 4.5, 'mercator'], 'n_sources': 6, 'sources_by_type': {'geojson': 6}, 'n_layers': 16, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 144 | 996 | 0 (0) | 0 | 7.1 | 7.2 | 16 |  |
| pan #2 | 143.6 | 1486 | 0 (0) | 0.001 | 7.1 | 21 | 24 |  |
| wheel_zoom | 139 | 502 | 1 (0.002) | 0.002 | 7.1 | 111.1 | — |  |
| flyto | 142.9 | 791 | 0 (0) | 0.003 | 7.1 | 20.8 | — |  |
| left_toggle | 144 | 422 | 0 (0) | 0 | 7 | 7.4 | — | left column toggle [data-testid=left-toggle] ×2 |
| views | 143.2 | 526 | 0 (0) | 0.002 | 7 | 27.8 | 48 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 28 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 143.9 | 1083 | 0 (0) | 0 | 7.2 | 13.9 | 32 | открытие студии 62 мс, выбор снимка max кадр 7 мс; виды: Снимок: 7 мс; Спектральный: 7 мс; Детекция: 14 мс; Качество: 7 мс; Снимок: 7 мс |
| idle | 144 | 434 | 0 (0) | 0 | 7 | 7.1 | — |  |

## 1366x768


Первый показ: медиана **0.67 с** по 3 холодным загрузкам [0.49, 0.67, 0.87]; в первой загрузке 0.49 с (first GL draw after first tile); WebGL-контекст 147 мс, первый GL-draw 197 мс, первый тайл пришёл 488 мс, draw после тайла 490 мс, тайлы загружены (map idle) 3172 мс; FCP 152 мс, LCP None мс, DCL 112 мс; handle карты: __map; запросов тайлов 34.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 3, 'vertical-perspective', 4.5, 'mercator'], 'n_sources': 6, 'sources_by_type': {'raster': 1, 'geojson': 5}, 'n_layers': 15, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 144 | 880 | 0 (0) | 0 | 7 | 7.2 | 16 |  |
| pan #2 | 143.7 | 1107 | 0 (0) | 0.001 | 7.2 | 20.8 | 24 |  |
| wheel_zoom | 139.6 | 472 | 0 (0) | 0.006 | 7 | 48.7 | — |  |
| flyto | 143.8 | 806 | 0 (0) | 0 | 7.2 | 14.3 | — |  |
| left_toggle | 144 | 424 | 0 (0) | 0 | 7.2 | 7.3 | 16 | left column toggle [data-testid=left-toggle] ×2 |
| views | 144.1 | 525 | 0 (0) | 0 | 7.2 | 7.3 | 16 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 7 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 143.4 | 1445 | 0 (0) | 0.001 | 7.2 | 27.7 | 32 | открытие студии 109 мс, выбор снимка max кадр 7 мс; виды: L8 10:26: 14 мс; S2 10:40: 7 мс; Снимок: 7 мс; Спектральный: 7 мс; Детекция: 7 мс; Качество: 7 мс; L8 10:26: 7 мс |
| idle | 144 | 433 | 0 (0) | 0 | 7.2 | 7.3 | — |  |

## Профиль CPU (pan + flyTo, 1-й размер)

Сценарий: {'pan_s': 7.6, 'flyto_s': 5.57}; всего 13480 мс, из них idle/program 10939 мс.

| функция | файл:строка | self, мс | доля занятого |
|---|---|---|---|
| `_update` | maplibre-CMX7JeUv.js:803 | 175.0 | 0.069 |
| `O_.R.S` | maplibre-CMX7JeUv.js:5 | 128.5 | 0.051 |
| `Jr` | maplibre-CMX7JeUv.js:5 | 124.0 | 0.049 |
| `(garbage collector)` | :0 | 103.0 | 0.041 |
| `fromInvProjectionMatrix` | maplibre-CMX7JeUv.js:799 | 98.3 | 0.039 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 83.5 | 0.033 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 77.6 | 0.031 |
| `vn` | maplibre-CMX7JeUv.js:803 | 72.9 | 0.029 |
| `draw` | maplibre-CMX7JeUv.js:801 | 64.5 | 0.025 |
| `renderLayer` | maplibre-CMX7JeUv.js:803 | 57.6 | 0.023 |
| `$e` | maplibre-CMX7JeUv.js:4 | 56.5 | 0.022 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 55.0 | 0.022 |
| `be` | maplibre-CMX7JeUv.js:5 | 48.4 | 0.019 |
| `_renderTileMasks` | maplibre-CMX7JeUv.js:803 | 45.3 | 0.018 |
| `Fi` | maplibre-CMX7JeUv.js:4 | 43.0 | 0.017 |

По файлам (self): (idle) 9471.6, maplibre-CMX7JeUv.js 2144.9, (program) 1467.2, (garbage collector) 103.0, uniformMatrix4fv 41.4, drawElements 36.9, bindVertexArray 33.7, stencilFunc 25.4

Trace (включая вложенное время):
- renderer_main: RunTask 3761.2, ThreadControllerImpl::RunTask 3710.1, FunctionCall 2509.2, FireAnimationFrame 2476.5, Receive mojo message 305.6, UpdateLayoutTree 105.0, Layout 96.1, Commit 93.4, PrePaint 82.9, Layerize 81.4
- gpu:CrGpuMain: RunTask 1205.9, ThreadControllerImpl::RunTask 1190.0, Scheduler::RunTask 1150.4, GpuChannel::ExecuteDeferredRequest 833.8, GPUTask 813.0, CommandBuffer::Flush 733.1, CommandBufferStub::OnAsyncFlush 729.2, CommandBufferService:PutChanged 709.4, WebGL 532.1, RendererRasterWorker 205.5
- renderer_compositor: RunTask 526.6, ThreadControllerImpl::RunTask 501.8, IOHandler::OnIOCompleted 174.1, SimpleWatcher::OnHandleReady 136.5, GpuChannelHost::VerifyFlush 96.0, Receive mojo message 95.9, CommandBufferHelper::Flush 4.1, CommandBuffer::OrderingBarrier 3.4, cc::TaskGraphWorkQueue::ScheduleTasks 2.0, CommandBufferProxyImpl::Flush 1.7
- gpu:VizCompositorThread: RunTask 350.1, ThreadControllerImpl::RunTask 335.6, SimpleWatcher::OnHandleReady 75.5, Receive mojo message 47.6, Scheduler::ScheduleTask 6.5, SyncToken::Wait 0.1

## Профиль CPU холодной загрузки (1-й размер)

1.13 с; всего 1100 мс, idle/program 669 мс.

По файлам (self): (idle) 576.3, maplibre-CMX7JeUv.js 291.0, (program) 92.2, index-CFzPXQSQ.js 27.7, getShaderParameter 23.0, (garbage collector) 21.5, window.__perfFindMap 19.9, getProgramParameter 6.2, fetch 6.1, texImage2D 4.8

Топ функций: `getShaderParameter` (:0) 23.0, `(garbage collector)` (:0) 21.5, `window.__perfFindMap` (:29) 19.9, `_render` (maplibre-CMX7JeUv.js:803) 10.2, `_calcMatrices` (maplibre-CMX7JeUv.js:799) 9.9, `be` (maplibre-CMX7JeUv.js:5) 8.2, `(anonymous)` (index-CFzPXQSQ.js:1) 8.0, `oc` (maplibre-CMX7JeUv.js:5) 7.0

## Бандл

3 файлов JS/CSS, 1350374 Б, gzip 364440 Б (0.364 МБ).

| файл | байт | gzip |
|---|---|---|
| maplibre-CMX7JeUv.js | 1053118 | 282339 |
| index-D-Q0E8sP.js | 213010 | 69012 |
| index-4kFagHpe.css | 84246 | 13089 |

## Консоль

Ошибок: 0; внешних (хосты подложки): 0; упавших локальных запросов: 0.
