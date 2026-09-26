# Perf — v2_l111_wip (http://127.0.0.1:8070/)

2026-09-26 05:22:19 … 2026-09-26 05:24:39; Chromium 153.0.8010.12, gl=gpu, headless=True, CPU throttle ×1
Сборка: {'entry_js': ['index-DMGdLZ6A.js'], 'index_html_sha1': '1aa2390264fb'}

**Сводка:** fps pan 144.0, flyTo 143.8, first show 0.85 s (1920x1080); console errors 4; bundle gzip 0.704 MB

## 1920x1080

GPU: ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11)
Первый показ: медиана **0.85 с** по 3 холодным загрузкам [0.7, 0.85, 2.27]; в первой загрузке 2.27 с (first GL draw after first tile); WebGL-контекст 1272 мс, первый GL-draw 1413 мс, первый тайл пришёл 1970 мс, draw после тайла 2266 мс, тайлы загружены (map idle) 5553 мс; FCP 608 мс, LCP None мс, DCL 445 мс; handle карты: __caseMap; запросов тайлов 122.

Стиль карты: {'projection': 'globe', 'n_sources': 38, 'sources_by_type': {'raster': 1, 'geojson': 10, 'image': 27}, 'n_layers': 49, 'n_canvas': 1, 'dpr': 1, 'n_dom': 362}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 144 | 923 | 0 (0) | 0 | 7 | 7.8 | 16 |  |
| pan #2 | 144 | 894 | 0 (0) | 0 | 7 | 7.6 | 16 |  |
| wheel_zoom | 142.4 | 454 | 0 (0) | 0.002 | 7 | 20.9 | — |  |
| flyto | 143.8 | 796 | 0 (0) | 0 | 7 | 13.9 | — |  |
| left_toggle | 132.3 | 530 | 2 (0.004) | 0.015 | 7.1 | 111.1 | 120 | no left-column toggle; fallback: open zone card from the list + close it ×2 (includes the map flyTo to the zone) |
| views | 144 | 532 | 0 (0) | 0 | 7 | 7.1 | 24 | tab-zones: 7 мс; tab-obs: 7 мс; tab-go: 7 мс; tab-metrics: 7 мс; tab-zones: 7 мс |
| studio | — | — | — | — | — | — | — | no studio in this UI (no data-testid=nav-studio) |
| idle | 144 | 433 | 0 (0) | 0 | 7 | 7.7 | — |  |

## 1366x768


Первый показ: медиана **0.63 с** по 3 холодным загрузкам [0.63, 0.63, 0.79]; в первой загрузке 0.63 с (first GL draw after first tile); WebGL-контекст 263 мс, первый GL-draw 335 мс, первый тайл пришёл 631 мс, draw после тайла 634 мс, тайлы загружены (map idle) 2856 мс; FCP 128 мс, LCP None мс, DCL 104 мс; handle карты: __caseMap; запросов тайлов 34.

Стиль карты: {'projection': 'globe', 'n_sources': 38, 'sources_by_type': {'raster': 1, 'geojson': 10, 'image': 27}, 'n_layers': 49, 'n_canvas': 1, 'dpr': 1, 'n_dom': 321}

| сценарий | fps | кадров | > 50 мс (доля) | доля > 17.5 мс | p95, мс | max, мс | click→paint max, мс | примечание |
|---|---|---|---|---|---|---|---|---|
| pan #1 | 144 | 875 | 0 (0) | 0 | 7 | 7.7 | 16 |  |
| pan #2 | 144 | 873 | 0 (0) | 0 | 7 | 7.8 | 16 |  |
| wheel_zoom | 144 | 461 | 0 (0) | 0 | 7 | 7.6 | — |  |
| flyto | 144 | 797 | 0 (0) | 0 | 7 | 7.4 | — |  |
| left_toggle | 138.7 | 552 | 1 (0.002) | 0.005 | 7.1 | 55.6 | 88 | no left-column toggle; fallback: open zone card from the list + close it ×2 (includes the map flyTo to the zone) |
| views | 144 | 532 | 0 (0) | 0 | 7 | 7.9 | 16 | tab-zones: 8 мс; tab-obs: 7 мс; tab-go: 7 мс; tab-metrics: 7 мс; tab-zones: 7 мс |
| studio | — | — | — | — | — | — | — | no studio in this UI (no data-testid=nav-studio) |
| idle | 144 | 432 | 0 (0) | 0 | 7 | 7.4 | — |  |

## Профиль CPU (pan + flyTo, 1-й размер)

Сценарий: {'pan_s': 6.49, 'flyto_s': 5.53}; всего 12342 мс, из них idle/program 7754 мс.

| функция | файл:строка | self, мс | доля занятого |
|---|---|---|---|
| `(anonymous)` | maplibre-D9xxkaV4.js:5 | 214.5 | 0.047 |
| `intersectsFrustum` | maplibre-D9xxkaV4.js:799 | 178.5 | 0.039 |
| `B_.R.S` | maplibre-D9xxkaV4.js:5 | 159.7 | 0.035 |
| `draw` | maplibre-D9xxkaV4.js:801 | 134.0 | 0.029 |
| `Qn` | maplibre-D9xxkaV4.js:801 | 112.7 | 0.025 |
| `getTileBoundingVolume` | maplibre-D9xxkaV4.js:799 | 105.9 | 0.023 |
| `getRenderableIds` | maplibre-D9xxkaV4.js:5 | 100.3 | 0.022 |
| `(garbage collector)` | :0 | 96.9 | 0.021 |
| `getMeshFromTileID` | maplibre-D9xxkaV4.js:799 | 84.8 | 0.018 |
| `_calcMatrices` | maplibre-D9xxkaV4.js:799 | 81.4 | 0.018 |
| `render` | maplibre-D9xxkaV4.js:803 | 79.6 | 0.017 |
| `_s` | maplibre-D9xxkaV4.js:5 | 73.1 | 0.016 |
| `getTileById` | maplibre-D9xxkaV4.js:5 | 69.3 | 0.015 |
| `uniformMatrix4fv` | :0 | 68.6 | 0.015 |
| `_render` | maplibre-D9xxkaV4.js:803 | 62.5 | 0.014 |

По файлам (self): (idle) 4846.3, maplibre-D9xxkaV4.js 3832.3, (program) 2907.4, (garbage collector) 96.9, uniformMatrix4fv 68.6, drawElements 59.6, bindTexture 50.6, getBufferSubData 50.6

Trace (включая вложенное время):
- renderer_main: RunTask 7189.2, ThreadControllerImpl::RunTask 6981.9, FireAnimationFrame 5262.3, FunctionCall 5184.7, Receive mojo message 269.2, Layerize 261.1, Layout 250.6, RunMicrotasks 219.0, Commit 154.3, BlinkScheduler_PerformMicrotaskCheckpoint 121.5
- gpu:VizCompositorThread: RunTask 583.0, ThreadControllerImpl::RunTask 564.4, SimpleWatcher::OnHandleReady 116.2, Receive mojo message 75.8, Scheduler::ScheduleTask 12.2, SyncToken::Wait 0.0
- renderer_compositor: RunTask 783.7, ThreadControllerImpl::RunTask 751.4, IOHandler::OnIOCompleted 205.9, SimpleWatcher::OnHandleReady 154.9, Receive mojo message 102.2, GpuChannelHost::VerifyFlush 96.1, CommandBufferHelper::Flush 5.4, CommandBuffer::OrderingBarrier 4.6, cc::TaskGraphWorkQueue::ScheduleTasks 2.6, CommandBufferProxyImpl::Flush 2.2
- gpu:CrGpuMain: RunTask 2940.5, ThreadControllerImpl::RunTask 2912.1, Scheduler::RunTask 2845.4, GpuChannel::ExecuteDeferredRequest 2218.9, GPUTask 2189.8, CommandBuffer::Flush 2054.4, CommandBufferStub::OnAsyncFlush 2047.6, CommandBufferService:PutChanged 2009.7, WebGL 1846.0, RendererRasterWorker 209.2

## Профиль CPU холодной загрузки (1-й размер)

1.46 с; всего 1409 мс, idle/program 630 мс.

По файлам (self): maplibre-D9xxkaV4.js 507.5, (idle) 456.5, (program) 173.7, CaseApp-BZvGDbPs.js 37.0, getShaderParameter 35.1, (garbage collector) 30.4, index-Ba5_wDbO.js 27.2, toDataURL 17.0, getProgramParameter 14.8, getContext 12.1

Топ функций: `getShaderParameter` (:0) 35.1, `(garbage collector)` (:0) 30.4, `toDataURL` (:0) 17.0, `getProgramParameter` (:0) 14.8, `B_.R.S` (maplibre-D9xxkaV4.js:5) 13.9, `intersectsFrustum` (maplibre-D9xxkaV4.js:799) 13.0, `getContext` (:0) 12.1, `render` (maplibre-D9xxkaV4.js:803) 10.0

## Бандл

7 файлов JS/CSS, 2632860 Б, gzip 703707 Б (0.704 МБ).

| файл | байт | gzip |
|---|---|---|
| deck-DtYNjI0N.js | 1180918 | 311864 |
| maplibre-D9xxkaV4.js | 1053003 | 282295 |
| index-DMGdLZ6A.js | 144879 | 46600 |
| CaseApp-CqEGsJS8.js | 122546 | 36731 |
| index-BiTfTwON.css | 103388 | 16523 |
| Info-DPv-PrZZ.js | 13272 | 6205 |
| CaseApp-D6xcfa6g.css | 14854 | 3489 |

## Консоль

Ошибок: 4; внешних (хосты подложки): 0; упавших локальных запросов: 0.
- Failed to load resource: the server responded with a status of 404 (Not Found) [http://127.0.0.1:8070/api/v3/scene_zones/scenes]
- Failed to load resource: the server responded with a status of 404 (Not Found) [http://127.0.0.1:8070/api/v3/scene_zones]
- Failed to load resource: the server responded with a status of 404 (Not Found) [http://127.0.0.1:8070/api/v3/scene_zones/scenes]
- Failed to load resource: the server responded with a status of 404 (Not Found) [http://127.0.0.1:8070/api/v3/scene_zones]
