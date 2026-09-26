# Perf — v3_iter5 (http://127.0.0.1:8072/)

2026-09-26 02:11:43 … 2026-09-26 02:15:02; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×1
Сборка: {'entry_js': ['index-BVC527ne.js'], 'index_html_sha1': '1a17e4c21e95'}

**Сводка:** fps pan 143.3, flyTo 143.1, first show 0.66 s (1920x1080); console errors 0; bundle gzip 0.362 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **0.66 с** по 3 холодным загрузкам [0.5, 0.66, 2.09]; в первой загрузке 2.09 с (first GL draw after first tile); WebGL-контекст 193 мс, первый GL-draw 253 мс, первый тайл пришёл 2082 мс, draw после тайла 2089 мс, тайлы загружены (map idle) 3721 мс; FCP 240 мс, LCP None мс, DCL 133 мс; handle карты: __map; запросов тайлов 36.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 5, 'vertical-perspective', 7, 'mercator'], 'n_sources': 6, 'sources_by_type': {'raster': 1, 'geojson': 5}, 'n_layers': 15, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 144 | 977 | 0 (0) | 0 | 7.1 | 7.4 | — |  |
| pan #2 | 142.7 | 1366 | 0 (0) | 0.003 | 7.2 | 34.7 | 24 |  |
| wheel_zoom | 140.4 | 501 | 0 (0) | 0.008 | 7.2 | 41.8 | — |  |
| flyto | 143.1 | 794 | 0 (0) | 0.003 | 7 | 20.9 | — |  |
| left_toggle | 144 | 423 | 0 (0) | 0 | 7.2 | 7.3 | 24 | left column toggle [data-testid=left-toggle] ×2 |
| views | 143.7 | 527 | 0 (0) | 0 | 7.2 | 13.9 | 32 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 14 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 143.3 | 1093 | 0 (0) | 0.002 | 7.1 | 20.9 | 24 | открытие студии 88 мс, выбор снимка max кадр 7 мс; виды: Снимок: 7 мс; Спектральный: 7 мс; Детекция: 7 мс; Качество: 7 мс; Снимок: 7 мс |
| idle | 144 | 433 | 0 (0) | 0 | 7.1 | 7.3 | — |  |

## 1366x768


Первый показ: медиана **0.72 с** по 3 холодным загрузкам [0.56, 0.72, 1.98]; в первой загрузке 0.72 с (first GL draw after first tile); WebGL-контекст 164 мс, первый GL-draw 252 мс, первый тайл пришёл 710 мс, draw после тайла 717 мс, тайлы загружены (map idle) 1751 мс; FCP 168 мс, LCP None мс, DCL 133 мс; handle карты: __map; запросов тайлов 34.

Стиль карты: {'projection': ['interpolate', ['linear'], ['zoom'], 5, 'vertical-perspective', 7, 'mercator'], 'n_sources': 6, 'sources_by_type': {'raster': 1, 'geojson': 5}, 'n_layers': 15, 'n_canvas': 1, 'dpr': 1, 'n_dom': 55}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 144 | 1542 | 0 (0) | 0 | 7.1 | 7.3 | — |  |
| pan #2 | 144 | 1060 | 0 (0) | 0 | 7.1 | 7.2 | 24 |  |
| wheel_zoom | 144 | 457 | 0 (0) | 0 | 7.2 | 7.3 | — |  |
| flyto | 143.8 | 800 | 0 (0) | 0 | 7.1 | 14.4 | — |  |
| left_toggle | 144 | 425 | 0 (0) | 0 | 7.1 | 7.3 | 16 | left column toggle [data-testid=left-toggle] ×2 |
| views | 144 | 535 | 0 (0) | 0 | 7.1 | 7.2 | 16 | nav-sites: 7 мс; nav-studio: 7 мс; nav-layers: 7 мс; nav-export: 7 мс; nav-sites: 7 мс |
| studio | 143.2 | 1445 | 0 (0) | 0.001 | 7.1 | 41.7 | 40 | открытие студии 160 мс, выбор снимка max кадр 7 мс; виды: L8 10:26: 42 мс; S2 10:40: 28 мс; Снимок: 7 мс; Спектральный: 7 мс; Детекция: 7 мс; Качество: 7 мс; L8 10:26: 7 мс |
| idle | 144 | 433 | 0 (0) | 0 | 7.1 | 7.2 | — |  |

## Профиль CPU (pan + flyTo, 1-й размер)

Сценарий: {'pan_s': 7.09, 'flyto_s': 5.54}; всего 12932 мс, из них idle/program 9414 мс.

| функция | файл:строка | self, мс | доля занятого |
|---|---|---|---|
| `_update` | maplibre-CMX7JeUv.js:803 | 178.4 | 0.051 |
| `(garbage collector)` | :0 | 168.8 | 0.048 |
| `O_.R.S` | maplibre-CMX7JeUv.js:5 | 154.3 | 0.044 |
| `fromInvProjectionMatrix` | maplibre-CMX7JeUv.js:799 | 117.8 | 0.033 |
| `getTileBoundingVolume` | maplibre-CMX7JeUv.js:799 | 110.7 | 0.031 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 92.7 | 0.026 |
| `draw` | maplibre-CMX7JeUv.js:801 | 85.9 | 0.024 |
| `po` | maplibre-CMX7JeUv.js:5 | 80.0 | 0.023 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 67.2 | 0.019 |
| `be` | maplibre-CMX7JeUv.js:5 | 66.5 | 0.019 |
| `Ni` | maplibre-CMX7JeUv.js:5 | 65.8 | 0.019 |
| `set` | maplibre-CMX7JeUv.js:5 | 65.5 | 0.019 |
| `vn` | maplibre-CMX7JeUv.js:803 | 65.2 | 0.019 |
| `$e` | maplibre-CMX7JeUv.js:4 | 62.3 | 0.018 |
| `_calcMatrices` | maplibre-CMX7JeUv.js:799 | 58.2 | 0.017 |

По файлам (self): (idle) 7788.4, maplibre-CMX7JeUv.js 2981.6, (program) 1626.0, (garbage collector) 168.8, uniformMatrix4fv 56.3, drawElements 39.9, getBufferSubData 31.7, fetch 20.6

Trace (включая вложенное время):
- renderer_main: RunTask 4932.7, ThreadControllerImpl::RunTask 4757.8, FunctionCall 3445.6, FireAnimationFrame 3439.9, Receive mojo message 294.2, RunMicrotasks 133.3, BlinkScheduler_PerformMicrotaskCheckpoint 108.8, MinorGC 106.6, Commit 105.4, V8.GC_SCAVENGER 101.8
- gpu:CrGpuMain: RunTask 1523.9, ThreadControllerImpl::RunTask 1505.3, Scheduler::RunTask 1460.0, GpuChannel::ExecuteDeferredRequest 1069.3, GPUTask 1045.9, CommandBuffer::Flush 954.6, CommandBufferStub::OnAsyncFlush 950.2, CommandBufferService:PutChanged 927.4, WebGL 757.6, RendererRasterWorker 202.4
- renderer_compositor: RunTask 526.0, ThreadControllerImpl::RunTask 501.7, IOHandler::OnIOCompleted 156.4, SimpleWatcher::OnHandleReady 120.8, GpuChannelHost::VerifyFlush 99.3, Receive mojo message 84.3, CommandBufferHelper::Flush 4.0, CommandBuffer::OrderingBarrier 3.2, cc::TaskGraphWorkQueue::ScheduleTasks 2.2, CommandBufferProxyImpl::Flush 1.7
- gpu:VizCompositorThread: RunTask 415.3, ThreadControllerImpl::RunTask 399.5, SimpleWatcher::OnHandleReady 84.5, Receive mojo message 53.7, Scheduler::ScheduleTask 8.7, SyncToken::Wait 0.0

## Профиль CPU холодной загрузки (1-й размер)

1.35 с; всего 1308 мс, idle/program 853 мс.

По файлам (self): (idle) 769.0, maplibre-CMX7JeUv.js 309.4, (program) 83.8, index-7pTPdkkI.js 27.7, getShaderParameter 25.0, (garbage collector) 19.4, window.__perfFindMap 18.8, getProgramParameter 8.9, getBufferSubData 6.0, texImage2D 5.1

Топ функций: `getShaderParameter` (:0) 25.0, `(garbage collector)` (:0) 19.4, `window.__perfFindMap` (:29) 18.8, `draw` (maplibre-CMX7JeUv.js:801) 10.2, `O_.R.S` (maplibre-CMX7JeUv.js:5) 9.0, `getProgramParameter` (:0) 8.9, `(anonymous)` (index-7pTPdkkI.js:1) 8.0, `(anonymous)` (maplibre-CMX7JeUv.js:5) 7.1

## Бандл

3 файлов JS/CSS, 1342862 Б, gzip 362280 Б (0.362 МБ).

| файл | байт | gzip |
|---|---|---|
| maplibre-CMX7JeUv.js | 1053118 | 282339 |
| index-BVC527ne.js | 205751 | 66913 |
| index-BWHFusGZ.css | 83993 | 13028 |

## Консоль

Ошибок: 0; внешних (хосты подложки): 4; упавших локальных запросов: 0.
