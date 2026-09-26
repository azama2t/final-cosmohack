# Размер репозитория и сдача — L148 (§53 п.3), фаза 1

Замерено 26.09.2026 ~21:15–21:40. Коммит на момент замера: `e6860903df05` (main == origin/main, `git fetch origin main` не сдвинул SHA).

## main на GitHub
- Публичный GitHub API (`curl https://api.github.com/repos/azama2t/final-cosmohack`, без токена) вернул **404 Not Found** — репозиторий приватный или недоступен анонимно с этой машины. Число из постановки (`01_PROMPT_FINAL.txt`, замер оркестратора в 20:30 МСК 26.09): **5 169 файлов, 912 221 610 байт (~870 МиБ) содержимого; поле API `size` 1 589 187 KiB (~1.52 GiB)**.
- Перемерено локально по дереву `origin/main` (после `git fetch`, без изменения кода/индекса): `git ls-tree -r -l origin/main` → **5 179 файлов, 921 564 397 байт (≈ 878,9 МиБ)** содержимого блобов. Разница с замером оркестратора (5169→5179 файлов, +9,3 МБ) — коммиты между 20:30 и 21:15 (правки после §51/§52в и др.), не расхождение метода.
- Поле API `size` (~1.52 GiB, packed+overhead) перепроверить не удалось без токена — совпадает по порядку с `git count-objects` ниже (packed 203.79 MiB + рыхлые объекты).

## Локальная папка (рабочее дерево, без git)
- Всего (включая `data/`, `data_cache/`, `out/`, `.venv/`, + локальные незакоммиченные `models/`, `weights_exp/`, `.venv-baseline/`): **72,55 ГБ**.
- Без `data/`, `data_cache/`, `out/`, `.venv/`, `node_modules/`: **11,84 ГБ**. Из них НЕ в git (проверено `git status --ignored`): `models/` 3,82 ГБ, `weights_exp/` 2,56 ГБ, `.venv-baseline/` 1,72 ГБ — локальные эксперименты/старое окружение, в `.gitignore`, для чистого клона не нужны.
- `service/*/node_modules` (frontend, frontend_v2, frontend_v3) — по 375–412 МБ каждая, тоже вне git, ставятся `npm install` при сборке фронта.
- Вывод: рабочая папка «раздута» локальными артефактами (models/weights_exp/.venv-baseline ≈ 8,1 ГБ) и большими данными (`data/` ≈ 41 ГБ по прежнему замеру L144, `reports/ops/vps.md`) — ни то, ни другое не попадает в git и не нужно для чистого клона.

## .git
- `du -sh .git` = **1,7 ГБ**.
- `git count-objects -vH`: count 5618, size 1.45 GiB (рыхлые объекты), in-pack 4884, packs 1, size-pack 203.79 MiB, prune-packable 0, garbage 0.
- Много рыхлых объектов (1.45 GiB не упаковано) — `git gc` не выполнялся в рамках этой задачи (git — только чтение/clone по правилам L148); при финальном ZIP это не влияет (архив берётся из дерева коммита, не из `.git`).

## 20 крупнейших файлов в git (main, `git ls-tree -r -l`)
| Файл | Байт |
|---|---|
| weights/labels/frcnn_winans_material_fp16.pth | 82 998 093 |
| weights/photo_count/frcnn_winans_spatial_fp16.pth | 82 925 995 |
| weights/photo_count/frcnn_fml_grouped_fp16.pth | 82 924 933 |
| data/case/detector_preds/test_preds.npz | 13 623 747 |
| data/case/detector_preds/val_preds.npz | 12 756 657 |
| presentation/demo.mp4 | 8 547 454 |
| reports/audit/K2_all_live_detected_zones.png | 5 722 619 |
| presentation/template/fire_template_moti.pptx | 5 238 680 |
| reports/audit/exp_pairs.geojson | 4 480 003 |
| presentation/deck_checkpoint.pptx | 3 882 865 |
| docs/prep/deck.pptx | 3 630 520 |
| presentation/deck_latest.pptx | 3 606 780 |
| docs/research/gleb_quantitative_link/results/adis_pairs/335772/rgb_pair_verified.png | 2 980 282 |
| reports/case_deck.pptx | 2 924 795 |
| data/pairs/quality/S3_HE460_MarLitter_transect03/rgb.png | 2 920 482 |
| weights/lgbm/model.txt | 2 849 563 |
| weights/lgbm_live/model.txt | 2 834 120 |
| docs/research/gleb_quantitative_link/results/adis_pairs/335765/rgb_pair_verified.png | 2 827 857 |
| presentation/deck_checkpoint.pdf | 2 758 238 |
| docs/acceptance/egor_fix_pack/screenshots/08_studio_media_not_clickable.png | 2 712 303 |

Топ-3 (три модели `.pth`, ≈ 249 МБ суммарно) — обязательные веса, см. ниже. Остальное в топ-20 — презентации/деки/скрины/примеры данных, не нужны для запуска сервиса.

## Обязательные веса для запуска
`run.ps1 -Case all -Offline` и сервис используют только `weights/` (grep по `src/`, `scripts/`, `service/` на `weights/`): `weights/labels/*`, `weights/lgbm/*`, `weights/lgbm_live/*`, `weights/photo_count/*`, `weights/case_conc/*.json`. **Все перечисленные файлы уже лежат в git** (`git ls-files weights` — 12 файлов, включая 3 `.pth` по ~83 МБ, `weights/lgbm*/model.txt`, JSON карточки/конфиги концентрации). Ничего не скачивается извне на этапе запуска — веса не нужно докачивать отдельно. `weights_exp/` и `models/` (не в git) для основного маршрута `-Case all -Offline` не требуются.

## Docker
`docker version` — клиент 29.2.0 (Docker Desktop, windows/amd64) отвечает, но `docker info` / `docker version` (server) — **ошибка**: `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine ... The system cannot find the file specified`. `sc query com.docker.service` → `STATE: STOPPED` (код выхода службы 1077/0x435 — служба не запускалась). `docker desktop status` → «Could not retrieve status. Is Docker Desktop running?».
**Docker: не проверено, причина — служба Docker Desktop (сервер) не запущена на этой машине, попытка `docker desktop start` не входит в разрешённые действия без подтверждения (сетевой демон требует запуска GUI/VM).** Dockerfile/compose не добавлялись и нигде не заявляются как рабочие, GPU-образ не собирался.

## README — замечания (см. также `reports/ops/readme_run_notes.md`)
- Абсолютных путей `C:\Users\...` в README не найдено.
- `run.ps1` принимает параметр `-DataRoot <path>` (`param(... [string]$DataRoot ...)`), в README он нигде не описан — мелкая недокументированная опция, безопасно оставить как есть (по умолчанию `service\demo`), но стоит упомянуть.
- Версии пакетов: `requirements.txt`/`requirements-cpu.txt` закрепляют только `torch`, `torchvision`, `pyarrow`; остальное (`numpy`, `pandas`, `rasterio`, …) без версий — README уже честно предупреждает об этом (раздел «Версии», строка про numpy 2.5.3 vs 2.5.2) и указывает `requirements-lock.txt` для точных версий. Правки кода/requirements не вносились — риск низкий (числа уже проверены как совпадающие на чистом клоне 25.09, `clean_clone_1941.md`), а замена на полный lock для CPU потребовала бы нового прогона `pip freeze` на CPU-окружении, что выходит за рамки «малых безопасных правок» без времени на повторную проверку.
- `service/frontend*/package-lock.json` — есть и закреплён (3 фронтенда), это не проблема.
- Ключи `-Port`, `-NoBrowser`, `-Cpu`, `-Force`, `-Case`, `-Offline` — все описаны в README (раздел 2, строки 225–256). Неописанных ключей запуска, кроме `-DataRoot`, не найдено.

## GitHub About (gh CLI недоступен)
gh CLI не установлен на этой машине → строка добавлена в `INBOX_REQUESTS.md` для людей.

---

## Финальный ZIP (фаза 3, 27.09.2026 ~00:21)
После заморозки «ЗАМОРОЗКА main e35abd3» (00:10), HEAD на момент сборки — `21af2b2552b458a0e1066c93ace543a2490bcd90` (докатились docs/reports-коммиты между заморозкой и 00:19, включая §61 Docker от другого агента).
- Команда: `git archive --format=zip --output=out/final_main.zip HEAD` — **25,7 с**.
- Размер: **890 865 599 байт (≈ 849,6 МиБ / 0,89 ГБ)** — заметно меньше лимита GitVerse 2 ГБ, отдельный вариант со ссылкой не нужен.
- sha256: `5397020e1836c4401c8f984a1c1e294ca8bcfa6229d9dc77c0cd52fd26097f60`
- Проверка распаковки: `out/zip_check` (unzip, 19,4 с) → сервис на общем `.venv` (`PYTHONPATH=out\zip_check`, порт 8098) → `GET /health` 200, `GET /api/v3/scene_zones?scene_key=demo-cozar-2021-03-11` 200 → **PASS**. Сервис остановлен, `out/zip_check` удалён сразу после проверки.
- Место на диске C: перед сборкой ZIP — 30 ГБ свободно (после уборки `out/clone_final`), достаточно и для ZIP (~0,9 ГБ), и для распаковки (~0,9 ГБ) без предварительного удаления клона.

## Linux / VPS — для README §67 п.2в
**Linux: не проверено.** Окно 00:10–00:45 полностью ушло на клон+тесты+ZIP на ПК (по приоритету оркестратора: ПК ≤ 20 мин, VPS параллельно только если укладывается к 00:40); ssh-проверка на VPS 5.231.59.204 (§53а) не проводилась в этом финальном окне. Подробности — `reports/selfcheck/clean_clone_final.md`, раздел «VPS».
