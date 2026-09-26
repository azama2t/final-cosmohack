# Perf — v2_baseline (http://127.0.0.1:8070/)

2026-09-26 01:25:38 … 2026-09-26 01:28:32; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×1

**Сводка:** fps pan 143.1, flyTo 142.6, first show 1.33 s (1920x1080); console errors 0; bundle gzip 0.699 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **1.33 с** по 3 холодным загрузкам [0.59, 1.33, 3.35]; в первой загрузке 3.35 с (first GL draw after first tile); WebGL-контекст 594 мс, первый GL-draw 702 мс, первый тайл пришёл 3341 мс, draw после тайла 3346 мс, тайлы загружены (map idle) 8871 мс; FCP 284 мс, LCP None мс, DCL 241 мс; handle карты: __caseMap; запросов тайлов 122.

Стиль карты: {'projection': 'globe', 'n_sources': 36, 'sources_by_type': {'raster': 1, 'geojson': 8, 'image': 27}, 'n_layers': 45, 'n_canvas': 1, 'dpr': 1, 'n_dom': 346}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 142.5 | 1161 | 0 (0) | 0.003 | 7.1 | 27.8 | 16 |  |
| pan #2 | 143.6 | 973 | 0 (0) | 0.001 | 7.2 | 20.8 | 24 |  |
| wheel_zoom | 143.4 | 447 | 0 (0) | 0 | 7 | 13.9 | — |  |
| flyto | 142.6 | 790 | 0 (0) | 0.004 | 7 | 27.8 | — |  |
| left_toggle | 131.5 | 535 | 2 (0.004) | 0.013 | 7.1 | 139.1 | 96 | no left-column toggle; fallback: open zone card from the list + close it ×2 (includes the map flyTo to the zone) |
| views | 143.5 | 554 | 0 (0) | 0.002 | 7.1 | 20.8 | 16 | tab-zones: 7 мс; tab-obs: 7 мс; tab-go: 7 мс; tab-metrics: 21 мс; tab-zones: 8 мс |
| studio | — | — | — | — | — | — | — | no studio in this UI (no data-testid=nav-studio) |
| idle | 144 | 433 | 0 (0) | 0 | 7.1 | 7.2 | — |  |

## 1366x768


Первый показ: медиана **1.11 с** по 3 холодным загрузкам [0.65, 1.11, 1.18]; в первой загрузке 0.65 с (first GL draw after first tile); WebGL-контекст 242 мс, первый GL-draw 305 мс, первый тайл пришёл 642 мс, draw после тайла 650 мс, тайлы загружены (map idle) 3056 мс; FCP 128 мс, LCP None мс, DCL 105 мс; handle карты: __caseMap; запросов тайлов 34.

Стиль карты: {'projection': 'globe', 'n_sources': 36, 'sources_by_type': {'raster': 1, 'geojson': 8, 'image': 27}, 'n_layers': 45, 'n_canvas': 1, 'dpr': 1, 'n_dom': 312}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 144 | 1239 | 0 (0) | 0 | 7.2 | 7.3 | — |  |
| pan #2 | 143.4 | 1215 | 0 (0) | 0.002 | 7.2 | 28 | 24 |  |
| wheel_zoom | 143.7 | 470 | 0 (0) | 0 | 7.2 | 14.2 | — |  |
| flyto | 144 | 796 | 0 (0) | 0 | 7.2 | 7.3 | — |  |
| left_toggle | 138.6 | 559 | 0 (0) | 0.009 | 7.1 | 48.6 | 80 | no left-column toggle; fallback: open zone card from the list + close it ×2 (includes the map flyTo to the zone) |
| views | 143.7 | 534 | 0 (0) | 0 | 7.1 | 13.9 | 24 | tab-zones: 7 мс; tab-obs: 14 мс; tab-go: 7 мс; tab-metrics: 7 мс; tab-zones: 7 мс |
| studio | — | — | — | — | — | — | — | no studio in this UI (no data-testid=nav-studio) |
| idle | 144 | 432 | 0 (0) | 0 | 7 | 7.1 | — |  |

## Профиль CPU (pan + flyTo, 1-й размер)

Сценарий: {'pan_s': 10.05, 'flyto_s': 5.58}; всего 16027 мс, из них idle/program 11451 мс.

| функция | файл:строка | self, мс | доля занятого |
|---|---|---|---|
| `(anonymous)` | maplibre-D9xxkaV4.js:5 | 196.0 | 0.043 |
| `_s` | maplibre-D9xxkaV4.js:5 | 185.5 | 0.041 |
| `intersectsFrustum` | maplibre-D9xxkaV4.js:799 | 179.7 | 0.039 |
| `draw` | maplibre-D9xxkaV4.js:801 | 139.3 | 0.03 |
| `(garbage collector)` | :0 | 134.2 | 0.029 |
| `Qn` | maplibre-D9xxkaV4.js:801 | 118.5 | 0.026 |
| `getTileBoundingVolume` | maplibre-D9xxkaV4.js:799 | 112.4 | 0.025 |
| `getVisibleCoordinates` | maplibre-D9xxkaV4.js:5 | 105.5 | 0.023 |
| `render` | maplibre-D9xxkaV4.js:803 | 99.0 | 0.022 |
| `_calcMatrices` | maplibre-D9xxkaV4.js:799 | 97.9 | 0.021 |
| `getMeshFromTileID` | maplibre-D9xxkaV4.js:799 | 91.5 | 0.02 |
| `getAllTiles` | maplibre-D9xxkaV4.js:5 | 84.1 | 0.018 |
| `B_.R.S` | maplibre-D9xxkaV4.js:5 | 82.8 | 0.018 |
| `update` | maplibre-D9xxkaV4.js:799 | 74.6 | 0.016 |
| `fromInvProjectionMatrix` | maplibre-D9xxkaV4.js:799 | 68.2 | 0.015 |

По файлам (self): (idle) 7351.1, (program) 4100.2, maplibre-D9xxkaV4.js 3861.0, (garbage collector) 134.2, drawElements 60.8, bindVertexArray 60.3, getBufferSubData 59.4, uniformMatrix4fv 55.8

Trace (включая вложенное время):
- renderer_main: RunTask 7511.7, ThreadControllerImpl::RunTask 7388.2, FireAnimationFrame 5449.7, FunctionCall 5360.4, Receive mojo message 290.8, Layerize 279.5, Commit 253.4, Layout 235.8, PrePaint 139.9, RunMicrotasks 133.2
- gpu:VizCompositorThread: RunTask 679.1, ThreadControllerImpl::RunTask 657.1, SimpleWatcher::OnHandleReady 129.2, Receive mojo message 82.2, Scheduler::ScheduleTask 15.5, SyncToken::Wait 0.0
- renderer_compositor: RunTask 853.8, ThreadControllerImpl::RunTask 818.6, IOHandler::OnIOCompleted 234.3, SimpleWatcher::OnHandleReady 180.1, Receive mojo message 118.8, GpuChannelHost::VerifyFlush 84.9, CommandBufferHelper::Flush 6.5, CommandBuffer::OrderingBarrier 4.9, cc::TaskGraphWorkQueue::ScheduleTasks 3.3, CommandBufferProxyImpl::Flush 2.5
- gpu:CrGpuMain: RunTask 3010.7, ThreadControllerImpl::RunTask 2981.1, Scheduler::RunTask 2911.3, GpuChannel::ExecuteDeferredRequest 2117.1, GPUTask 2089.4, CommandBuffer::Flush 1964.7, CommandBufferStub::OnAsyncFlush 1957.6, CommandBufferService:PutChanged 1921.9, WebGL 1788.6, DXGISwapChainImageBacking::Present 238.1

## Профиль CPU холодной загрузки (1-й размер)

2.22 с; всего 2191 мс, idle/program 1524 мс.

По файлам (self): (idle) 1376.3, maplibre-D9xxkaV4.js 402.9, (program) 147.6, CaseApp-Dw7lYcGv.js 55.4, (garbage collector) 32.3, getShaderParameter 23.0, index-D6pMOx42.js 21.7, toDataURL 20.7, getBufferSubData 12.3, Info-CjefpWeA.js 11.5

Топ функций: `ea` (CaseApp-Dw7lYcGv.js:1) 33.4, `(garbage collector)` (:0) 32.3, `getShaderParameter` (:0) 23.0, `toDataURL` (:0) 20.7, `(anonymous)` (maplibre-D9xxkaV4.js:5) 13.1, `getBufferSubData` (:0) 12.3, `getProgramParameter` (:0) 11.2, `getAllTiles` (maplibre-D9xxkaV4.js:5) 10.9

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

Ошибок: 0; внешних (хосты подложки): 10; упавших локальных запросов: 0.
