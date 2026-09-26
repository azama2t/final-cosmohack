# Perf — v2_final (http://127.0.0.1:8070/)

2026-09-26 04:35:50 … 2026-09-26 04:38:06; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×1
Сборка: {'entry_js': ['index-D6pMOx42.js'], 'index_html_sha1': 'b76ffd96d386'}

**Сводка:** fps pan 144.0, flyTo 144, first show 0.77 s (1920x1080); console errors 0; bundle gzip 0.699 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **0.77 с** по 3 холодным загрузкам [0.71, 0.77, 1.69]; в первой загрузке 1.69 с (first GL draw after first tile); WebGL-контекст 916 мс, первый GL-draw 1011 мс, первый тайл пришёл 1340 мс, draw после тайла 1686 мс, тайлы загружены (map idle) 4904 мс; FCP 508 мс, LCP None мс, DCL 376 мс; handle карты: __caseMap; запросов тайлов 122.

Стиль карты: {'projection': 'globe', 'n_sources': 36, 'sources_by_type': {'raster': 1, 'geojson': 8, 'image': 27}, 'n_layers': 45, 'n_canvas': 1, 'dpr': 1, 'n_dom': 346}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 144 | 873 | 0 (0) | 0 | 7.2 | 7.3 | 16 |  |
| pan #2 | 144 | 887 | 0 (0) | 0 | 7.1 | 7.3 | 16 |  |
| wheel_zoom | 143.7 | 467 | 0 (0) | 0 | 7.2 | 13.9 | — |  |
| flyto | 144 | 796 | 0 (0) | 0 | 7.1 | 7.3 | — |  |
| left_toggle | 137 | 529 | 1 (0.002) | 0.008 | 7.1 | 83.3 | 88 | no left-column toggle; fallback: open zone card from the list + close it ×2 (includes the map flyTo to the zone) |
| views | 144 | 525 | 0 (0) | 0 | 7 | 7.2 | 16 | tab-zones: 7 мс; tab-obs: 7 мс; tab-go: 7 мс; tab-metrics: 7 мс; tab-zones: 7 мс |
| studio | — | — | — | — | — | — | — | no studio in this UI (no data-testid=nav-studio) |
| idle | 144 | 432 | 0 (0) | 0 | 7.2 | 7.3 | — |  |

## 1366x768


Первый показ: медиана **0.59 с** по 3 холодным загрузкам [0.58, 0.59, 0.74]; в первой загрузке 0.59 с (first GL draw after first tile); WebGL-контекст 230 мс, первый GL-draw 291 мс, первый тайл пришёл 577 мс, draw после тайла 595 мс, тайлы загружены (map idle) 2831 мс; FCP 116 мс, LCP None мс, DCL 90 мс; handle карты: __caseMap; запросов тайлов 34.

Стиль карты: {'projection': 'globe', 'n_sources': 36, 'sources_by_type': {'raster': 1, 'geojson': 8, 'image': 27}, 'n_layers': 45, 'n_canvas': 1, 'dpr': 1, 'n_dom': 312}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 144 | 888 | 0 (0) | 0 | 7.1 | 7.2 | 16 |  |
| pan #2 | 144 | 844 | 0 (0) | 0 | 7.2 | 7.3 | 16 |  |
| wheel_zoom | 144 | 461 | 0 (0) | 0 | 7.2 | 7.2 | — |  |
| flyto | 144 | 796 | 0 (0) | 0 | 7.1 | 7.3 | — |  |
| left_toggle | 142.7 | 551 | 0 (0) | 0.002 | 7 | 20.8 | 48 | no left-column toggle; fallback: open zone card from the list + close it ×2 (includes the map flyTo to the zone) |
| views | 144 | 527 | 0 (0) | 0 | 7 | 7.2 | 24 | tab-zones: 7 мс; tab-obs: 7 мс; tab-go: 7 мс; tab-metrics: 7 мс; tab-zones: 7 мс |
| studio | — | — | — | — | — | — | — | no studio in this UI (no data-testid=nav-studio) |
| idle | 144 | 432 | 0 (0) | 0 | 7 | 7.3 | — |  |

## Профиль CPU (pan + flyTo, 1-й размер)

Сценарий: {'pan_s': 5.96, 'flyto_s': 5.53}; всего 11806 мс, из них idle/program 8077 мс.

| функция | файл:строка | self, мс | доля занятого |
|---|---|---|---|
| `calculatePosMatrix` | maplibre-D9xxkaV4.js:799 | 183.0 | 0.049 |
| `(anonymous)` | maplibre-D9xxkaV4.js:5 | 173.2 | 0.046 |
| `intersectsFrustum` | maplibre-D9xxkaV4.js:799 | 135.5 | 0.036 |
| `_calcMatrices` | maplibre-D9xxkaV4.js:799 | 95.9 | 0.026 |
| `(garbage collector)` | :0 | 95.5 | 0.026 |
| `getTileBoundingVolume` | maplibre-D9xxkaV4.js:799 | 93.1 | 0.025 |
| `B_.R.S` | maplibre-D9xxkaV4.js:5 | 88.9 | 0.024 |
| `Qn` | maplibre-D9xxkaV4.js:801 | 88.0 | 0.024 |
| `draw` | maplibre-D9xxkaV4.js:801 | 84.4 | 0.023 |
| `getRenderableIds` | maplibre-D9xxkaV4.js:5 | 81.0 | 0.022 |
| `vn` | maplibre-D9xxkaV4.js:803 | 77.1 | 0.021 |
| `render` | maplibre-D9xxkaV4.js:803 | 69.3 | 0.019 |
| `_calcMatrices` | maplibre-D9xxkaV4.js:799 | 62.5 | 0.017 |
| `fromInvProjectionMatrix` | maplibre-D9xxkaV4.js:799 | 60.4 | 0.016 |
| `_calcMatrices` | maplibre-D9xxkaV4.js:799 | 56.8 | 0.015 |

По файлам (self): (idle) 5935.3, maplibre-D9xxkaV4.js 3159.3, (program) 2141.6, (garbage collector) 95.5, getBufferSubData 41.7, uniformMatrix4fv 36.7, bindTexture 36.3, bindVertexArray 36.3

Trace (включая вложенное время):
- renderer_main: RunTask 5733.5, ThreadControllerImpl::RunTask 5555.8, FireAnimationFrame 4132.2, FunctionCall 4066.2, Receive mojo message 217.4, Layout 212.7, Layerize 211.7, RunMicrotasks 186.5, Commit 119.7, BlinkScheduler_PerformMicrotaskCheckpoint 105.8
- gpu:VizCompositorThread: RunTask 484.4, ThreadControllerImpl::RunTask 468.1, SimpleWatcher::OnHandleReady 100.0, Receive mojo message 64.7, Scheduler::ScheduleTask 9.2, SyncToken::Wait 0.0
- renderer_compositor: RunTask 680.3, ThreadControllerImpl::RunTask 652.2, IOHandler::OnIOCompleted 175.7, SimpleWatcher::OnHandleReady 131.4, GpuChannelHost::VerifyFlush 97.7, Receive mojo message 85.9, CommandBufferHelper::Flush 4.1, CommandBuffer::OrderingBarrier 3.9, cc::TaskGraphWorkQueue::ScheduleTasks 2.2, CommandBufferProxyImpl::Flush 1.6
- gpu:CrGpuMain: RunTask 2167.0, ThreadControllerImpl::RunTask 2143.0, Scheduler::RunTask 2091.3, GpuChannel::ExecuteDeferredRequest 1581.1, GPUTask 1559.8, CommandBuffer::Flush 1455.9, CommandBufferStub::OnAsyncFlush 1450.9, CommandBufferService:PutChanged 1423.7, WebGL 1246.4, RendererRasterWorker 209.8

## Профиль CPU холодной загрузки (1-й размер)

1.19 с; всего 1153 мс, idle/program 526 мс.

По файлам (self): maplibre-D9xxkaV4.js 432.5, (idle) 372.9, (program) 153.3, (garbage collector) 26.3, CaseApp-Dw7lYcGv.js 25.2, getShaderParameter 25.0, index-D6pMOx42.js 18.4, toDataURL 14.3, getProgramParameter 10.0, Info-CjefpWeA.js 8.9

Топ функций: `(garbage collector)` (:0) 26.3, `getShaderParameter` (:0) 25.0, `toDataURL` (:0) 14.3, `intersectsFrustum` (maplibre-D9xxkaV4.js:799) 12.9, `hr` (maplibre-D9xxkaV4.js:5) 11.2, `(anonymous)` (maplibre-D9xxkaV4.js:5) 10.6, `(anonymous)` (maplibre-D9xxkaV4.js:5) 10.0, `getProgramParameter` (:0) 10.0

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

Ошибок: 0; внешних (хосты подложки): 0; упавших локальных запросов: 0.
