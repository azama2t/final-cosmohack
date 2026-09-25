# UI performance (Playwright, Chromium)

Генерируется `scripts/screenshots.py`. load_ms — от начала навигации до `window.__mapReady` (карта загружена и отрисован первый слой deck.gl); fps — число кадров requestAnimationFrame в секунду (дрейф: 5 с анимации TripsLayer; flyTo: от клика по району до остановки камеры). console_errors — ошибки консоли без учёта недоступности внешних тайлов (отдельный столбец).

| дата-время | base-url | load_ms | console_errors | fps_drift | fps_flyto | внешние ошибки | GPU / заметки |
|---|---|---|---|---|---|---|---|
| 2026-09-25 03:12 | http://127.0.0.1:5173 | 1753 | 0 | 8.9 | 8.4 | 0 | gl=swiftshader; ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver) |
| 2026-09-25 03:15 | http://127.0.0.1:5173 | 1749 | 0 | 10.6 | 8.5 | 0 | gl=swiftshader; ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver) |
| 2026-09-25 03:18 | http://127.0.0.1:8000 | 535 | 0 | None | 10.9 | 0 | gl=swiftshader; ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver); no drift.json in manifest |
| 2026-09-25 03:18 | http://127.0.0.1:5173 | 2196 | 0 | 60.2 | 59.6 | 0 | gl=gpu; ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11) |
| 2026-09-25 03:19 | http://127.0.0.1:8000 | 1394 | 0 | None | 59.7 | 0 | gl=gpu; ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11); no drift.json in manifest |
| 2026-09-25 03:20 | http://127.0.0.1:4173 | 1040 | 0 | 60 | 56.5 | 0 | gl=gpu; ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 (0x00002786) Direct3D11 vs_5_0 ps_5_0, D3D11) |
| 2026-09-25 03:22 | http://127.0.0.1:5173 | 1208 | 0 | 10 | 10.3 | 0 | gl=swiftshader; ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver) |
| 2026-09-25 03:23 | http://127.0.0.1:8000 | 644 | 0 | None | 10.1 | 0 | gl=swiftshader; ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver); no drift.json in manifest |
| 2026-09-25 03:26 | http://127.0.0.1:5173 | 968 | 0 | 8.9 | 9.7 | 0 | gl=swiftshader; ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver) |
| 2026-09-25 03:27 | http://127.0.0.1:8000 | 590 | 0 | None | 10.6 | 0 | gl=swiftshader; ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver); no drift.json in manifest |
