## Запуск через Docker (основной путь, любая ОС)

Нужен Docker Desktop (Windows / macOS) или Docker Engine + Compose (Linux). Из корня клона:

```
docker compose up --build -d
```

Сайт: http://localhost:8070 (API: `/api/v3/meta`, документация `/docs`). Остановить: `docker compose down`.
Другой порт хоста: `PORT=18070 docker compose up --build -d` (PowerShell: `$env:PORT=18070; docker compose up --build -d`).

- Первая сборка ≈ 12 мин (скачиваются Python-пакеты, PyTorch CPU), образ ≈ 1,7 ГБ в сжатом виде (≈ 3,8 ГБ распакованный). Повторный `up` — секунды.
- Внутри: python:3.12-slim, точные версии из `requirements-lock.txt` с колёсами PyTorch CPU (без CUDA/GPU), веса из `weights/` и готовая сборка фронта `service/static_v2` из git; ничего не скачивается при старте, npm не нужен.
- Данные — из git (`data/case/**`, `service/demo`). Каталог `service/data` (его в git нет) в образ не входит: API v3 и карта работают полностью, старый API `/api/regions` показывает 3 демо-региона вместо 18.
- Проверено: Windows 10, Docker Desktop 29.2.0, linux/amd64, чистый клон (коммит 85a095d) → сборка → проверка (главная, API v3, CSV-выгрузка, дроны, PRIME, 0 ошибок консоли) → `down` → повторный `up`. Отчёт: `reports/selfcheck/docker_check.md`.
- macOS: через Docker Desktop, образ linux/amd64 (на Apple Silicon — через эмуляцию Rosetta/QEMU; на Mac не проверялось). Linux: те же команды, на этой машине не проверялось.

Нативный запуск без Docker (Windows, PowerShell) — раздел ниже (`run.ps1`).
