# Perf — v3_iter2b (http://127.0.0.1:8072/)

2026-09-26 01:43:10 … 2026-09-26 01:46:13; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×1
Сборка: {'entry_js': ['index-DbkKbWB9.js'], 'index_html_sha1': '8a5e9f3cd2db'}

**Сводка:** fps pan 143.4, flyTo 144, first show 0.61 s (1920x1080); console errors 0; bundle gzip 0.361 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **0.61 с** по 3 холодным загрузкам [0.48, 0.61, 0.61]; в первой загрузке 0.61 с (first GL draw after first tile); WebGL-контекст 190 мс, первый GL-draw 249 мс, первый тайл пришёл 599 мс, draw после тайла 609 мс, тайлы загружены (map idle) 1158 мс; FCP 232 мс, LCP None мс, DCL 130 мс; handle карты: __map; запросов тайлов 36.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 5, 'vertical-perspective', 7, 'mercator'], 'n_sources': 10, 'sources_by_type': {'raster': 1, 'geojson': 9}, 'n_layers': 21, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 143.6 | 1168 | 0 (0) | 0.001 | 7.2 | 27.8 | 16 |  |
| pan #2 | 143.2 | 1132 | 0 (0) | 0.002 | 7.2 | 27.9 | 24 |  |
| wheel_zoom | 142.4 | 451 | 0 (0) | 0.002 | 7 | 27.8 | — |  |
| flyto | 144 | 799 | 0 (0) | 0 | 7.1 | 7.3 | — |  |
| left_toggle | 143.7 | 416 | 0 (0) | 0 | 7.1 | 14.1 | 16 | left column toggle [data-testid=left-toggle] ×2 |
| views | 143.7 | 558 | 0 (0) | 0 | 7.2 | 14 | 32 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 14 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 143.9 | 1079 | 0 (0) | 0 | 7.2 | 13.7 | 24 | открытие студии 62 мс, выбор снимка max кадр 7 мс; виды: Снимок: 7 мс; Спектральный: 7 мс; Детекция: 7 мс; Качество: 7 мс; Снимок: 7 мс |
| idle | 144 | 434 | 0 (0) | 0 | 7.2 | 7.3 | — |  |

## 1366x768


Первый показ: медиана **0.63 с** по 3 холодным загрузкам [0.43, 0.63, 1.6]; в первой загрузке 1.6 с (first GL draw after first tile); WebGL-контекст 108 мс, первый GL-draw 148 мс, первый тайл пришёл 1596 мс, draw после тайла 1605 мс, тайлы загружены (map idle) 3949 мс; FCP 140 мс, LCP None мс, DCL 90 мс; handle карты: __map; запросов тайлов 34.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 5, 'vertical-perspective', 7, 'mercator'], 'n_sources': 10, 'sources_by_type': {'raster': 1, 'geojson': 9}, 'n_layers': 21, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 143.4 | 1246 | 0 (0) | 0.002 | 7.2 | 27.6 | 24 |  |
| pan #2 | 144 | 855 | 0 (0) | 0 | 7.2 | 7.3 | 24 |  |
| wheel_zoom | 144 | 443 | 0 (0) | 0 | 7.2 | 7.3 | — |  |
| flyto | 143.5 | 798 | 0 (0) | 0.001 | 7.2 | 20.8 | — |  |
| left_toggle | 144 | 424 | 0 (0) | 0 | 7.2 | 7.3 | 16 | left column toggle [data-testid=left-toggle] ×2 |
| views | 144 | 529 | 0 (0) | 0 | 7.2 | 7.3 | — | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 7 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 143.2 | 1402 | 0 (0) | 0.002 | 7.2 | 27.9 | 24 | открытие студии 45 мс, выбор снимка max кадр 7 мс; виды: L8 10:26: 21 мс; S2 10:40: 7 мс; Снимок: 7 мс; Спектральный: 7 мс; Детекция: 28 мс; Качество: 7 мс; L8 10:26: 7 мс |
| idle | 144 | 434 | 0 (0) | 0 | 7.2 | 7.5 | — |  |

## Профиль CPU (pan + flyTo, 1-й размер)

Сценарий: {'pan_s': 5.96, 'flyto_s': 5.55}; всего 11846 мс, из них idle/program 8490 мс.

| функция | файл:строка | self, мс | доля занятого |
|---|---|---|---|
| `Jr` | maplibre-CMX7JeUv.js:5 | 159.5 | 0.048 |
| `_update` | maplibre-CMX7JeUv.js:803 | 140.2 | 0.042 |
| `(garbage collector)` | :0 | 123.8 | 0.037 |
| `fromInvProjectionMatrix` | maplibre-CMX7JeUv.js:799 | 115.2 | 0.034 |
| `O_.R.S` | maplibre-CMX7JeUv.js:5 | 103.8 | 0.031 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 95.3 | 0.028 |
| `calculatePosMatrix` | maplibre-CMX7JeUv.js:799 | 81.9 | 0.024 |
| `draw` | maplibre-CMX7JeUv.js:801 | 79.1 | 0.024 |
| `set` | maplibre-CMX7JeUv.js:5 | 77.2 | 0.023 |
| `uniformMatrix4fv` | :0 | 63.9 | 0.019 |
| `_renderTileMasks` | maplibre-CMX7JeUv.js:803 | 62.9 | 0.019 |
| `$e` | maplibre-CMX7JeUv.js:4 | 62.4 | 0.019 |
| `vn` | maplibre-CMX7JeUv.js:803 | 60.8 | 0.018 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 60.8 | 0.018 |
| `getRenderableIds` | maplibre-CMX7JeUv.js:5 | 59.8 | 0.018 |

По файлам (self): (idle) 6942.5, maplibre-CMX7JeUv.js 2810.5, (program) 1547.5, (garbage collector) 123.8, uniformMatrix4fv 63.9, drawElements 55.4, stencilFunc 25.8, getBufferSubData 24.6

Trace (включая вложенное время):
- renderer_main: RunTask 4649.0, ThreadControllerImpl::RunTask 4507.9, FunctionCall 3352.3, FireAnimationFrame 3338.2, Receive mojo message 256.3, RunMicrotasks 107.7, Commit 88.6, UpdateLayoutTree 85.1, BlinkScheduler_PerformMicrotaskCheckpoint 84.0, MinorGC 83.0
- renderer_compositor: RunTask 487.3, ThreadControllerImpl::RunTask 463.1, IOHandler::OnIOCompleted 144.4, SimpleWatcher::OnHandleReady 111.1, GpuChannelHost::VerifyFlush 85.9, Receive mojo message 75.7, CommandBufferHelper::Flush 3.8, CommandBuffer::OrderingBarrier 3.2, cc::TaskGraphWorkQueue::ScheduleTasks 1.9, CommandBufferProxyImpl::Flush 1.5
- gpu:VizCompositorThread: RunTask 334.7, ThreadControllerImpl::RunTask 320.7, SimpleWatcher::OnHandleReady 68.1, Receive mojo message 42.7, Scheduler::ScheduleTask 7.1, SyncToken::Wait 0.1
- gpu:CrGpuMain: RunTask 1379.2, ThreadControllerImpl::RunTask 1362.7, Scheduler::RunTask 1322.4, GpuChannel::ExecuteDeferredRequest 997.1, GPUTask 978.4, CommandBuffer::Flush 901.3, CommandBufferStub::OnAsyncFlush 897.2, CommandBufferService:PutChanged 877.2, WebGL 726.8, RendererRasterWorker 179.5

## Профиль CPU холодной загрузки (1-й размер)

1.03 с; всего 990 мс, idle/program 656 мс.

По файлам (self): (idle) 578.9, maplibre-CMX7JeUv.js 222.7, (program) 77.1, index-DbkKbWB9.js 24.2, (garbage collector) 20.9, getShaderParameter 15.9, getProgramParameter 6.5, P.<computed> 4.8, texImage2D 3.2, drawElements 2.7

Топ функций: `(garbage collector)` (:0) 20.9, `getShaderParameter` (:0) 15.9, `_render` (maplibre-CMX7JeUv.js:803) 6.8, `getProgramParameter` (:0) 6.5, `(anonymous)` (index-DbkKbWB9.js:1) 6.4, `be` (maplibre-CMX7JeUv.js:5) 5.7, `_calcMatrices` (maplibre-CMX7JeUv.js:799) 5.0, `P.<computed>` (:22) 4.8

## Бандл

3 файлов JS/CSS, 1339034 Б, gzip 361011 Б (0.361 МБ).

| файл | байт | gzip |
|---|---|---|
| maplibre-CMX7JeUv.js | 1053118 | 282339 |
| index-DbkKbWB9.js | 202268 | 65719 |
| index-BA2dAAPK.css | 83648 | 12953 |

## Консоль

Ошибок: 0; внешних (хосты подложки): 0; упавших локальных запросов: 0.
