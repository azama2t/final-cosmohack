# §61 Docker — проверка из чистого клона (L158, 27.09 00:00–00:20)

**Итог: PASS** (Windows 10, Docker Desktop 29.2.0, linux/amd64, Compose 5.0.2).

| Прогон | Коммит | Сборка | Смоук | down → up | Смоук после up |
|---|---|---|---|---|---|
| 1. Чистый клон, `docker compose build --no-cache` | b5d950b (Docker-файлы) | 719 с ≈ 12 мин, exit 0 | 7/7 PASS | up 1 с, healthy | 7/7 PASS |
| 2. Чистый клон, `docker compose up --build -d` (слой pip из кэша) | 85a095d (после ЗАМОРОЗКИ e35abd3; HEAD main на 00:15) | 117 с, exit 0 | 7/7 PASS | ok, healthy | 7/7 PASS |
| 0. Рабочее дерево (до коммита, 23:20) | — | 11 мин 10 с | 7/7 PASS | — | — |

Клон: `git clone <репо> out/docker_clone` (локально), хост-порт 18070 через `PORT=18070` (по умолчанию в compose `${PORT:-8070}`), :8070 на ПК не трогался.

## Смоук (out/docker_smoke.py, 1366×768)
- `/` 200 (index.html static_v2) · `/api/v3/meta` 200, 30,9 КБ · `/api/v3/scene_zones?limit=5` 200, 108 КБ · `/api/v3/export?layer=scene_zones&format=csv&scene_key=demo-cozar-2021-03-11` 200, 149 КБ CSV · `/api/v3/drones` 200 · `/api/prime/csv_scenes?lite=1` 200, 235 КБ · `/docs` 200.
- Главная в Playwright Chromium: 0 ошибок консоли/страницы, заголовок «Морской мусор · Sentinel-2».
- Healthcheck compose (`/api/v3/meta`) → `healthy` через ~20 с.

## Образ
- `macroplastic-web:latest`, python:3.12-slim, Python 3.12.14, torch 2.11.0+cpu, numpy 2.5.2, fastapi 0.141.1.
- Размер: 1,68 ГБ сжатый (`docker image inspect .Size`), ≈ 3,8 ГБ распакован (site-packages 2,5 ГБ, из них torch 0,75 ГБ; /app 1,2 ГБ: reports 0,7, weights 0,24, data 0,15). `docker image ls` в Docker Desktop показывает 5,3 ГБ (сумма сжатого и распакованного).
- Пакеты: все 168 из `requirements-lock.txt` с заменой индекса cu128→cpu (`sed` в Dockerfile), без CUDA. playwright (pip-пакет, без браузеров) ставится вместе с lock — не мешал, не исключался.
- Контекст сборки по `.dockerignore` — 71 МБ даже из рабочей папки 72 ГБ (исключены .git, data/* кроме того, что в git, service/data, out, data_cache, .venv*, node_modules, models, weights_exp, presentation и др.).

## Находки
1. Без `service/data` (в .gitignore) сервис стартует на `service/demo` из git: `data root: /app/service/demo (kind=demo, regions=3)`. API v3 совпадает с :8070 (сцен 235, зон сцен 286, meta одинаковая); отличается только старый `/api/regions` — 3 региона против 18 на :8070. Для судейского маршрута (v3) не критично.
2. Размер образа 1,7 ГБ сжатый / 3,8 ГБ распакован: основное — torch CPU и полный lock (opendrift, copernicusmarine, cartopy и т. п., не нужные сервису) и reports/ (0,7 ГБ). Урезать можно после сдачи (минимальный requirements для сервиса), сейчас не трогали — проверено как есть.
3. Первая сборка ≈ 12 мин (скачивание ~2,5 ГБ пакетов), повторная с кэшем ≈ 2 мин, повторный `up` — секунды. macOS/arm64 не проверены: сборка `--platform linux/arm64` не запускалась (нет времени до 00:40); на Apple Silicon образ amd64 пойдёт через эмуляцию — «не проверено на Mac». Linux — те же команды, на этой машине не проверялось.

После проверки: `docker compose down`, `docker builder prune -f`, out/docker_clone удалён; оставлен один образ macroplastic-web:latest.

## Дополнение 27.09 00:44 (INBOX §72)
- Windows 10 / Docker Desktop, linux/amd64 — **PASS** (выше).
- Linux (Docker) — проверено командой вручную 27.09 00:38: `docker compose up --build` собрался и запустился (сообщил Фёдор, INBOX §74).
- macOS Apple Silicon — сборка падала на шаге pip (колёса torch для linux/arm64 без метки «+cpu»). Внесена правка Dockerfile (`ARG TARGETARCH`: для arm64 метка снимается), **не проверена**. Рекомендуемый путь на Mac — `DOCKER_DEFAULT_PLATFORM=linux/amd64 docker compose up --build` (эмуляция Rosetta); на Mac нами не запускался.
