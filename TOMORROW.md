# TOMORROW — первые шаги после сдачи (27.09.2026)

1. **Открыть репозиторий** (Settings → Change visibility → Public) и выставить About/Website командой из `INBOX_REQUESTS.md`. Без этого проверки «только по ссылке» и `git clone` у судьи не пройдут.
2. **Сайт без ПК.** Сейчас VPS проксирует ПК команды. Нужно:
   - развернуть сервис на VPS (`git clone` → `docker compose up -d` или venv, порт 127.0.0.1:18081, systemd);
   - настроить Caddy с резервом на туннель, как в INBOX §53а п.2.
3. **Linux и macOS нативно.** Прогнать README-путь в) на VPS Ubuntu и отметить результат в README. На Mac собрать `--platform linux/arm64` и проверить.
4. **Реальное время.** Дообработать 271 пригодный снимок в очереди: `.venv\Scripts\python.exe scripts/case/live_batch.py --reuse-screen`, затем `--index-only --compact`. Вернуть снимки, отброшенные из-за края тайла (261), взяв вырезку из соседнего тайла. Проверить задачу Планировщика `MacroplasticLiveDaily`.
5. **Фронт** (доделать то, что не успели по §62/§70):
   - приближение к полевой точке;
   - окно «Полевые измерения» — в левую колонку;
   - слой NASA через setTiles с minzoom 3 в `CaseMap.tsx` (правка — строка LOG 00:24 от L140);
   - чёрная шапка у полюса;
   - тумблер PRIME на 390 px;
   - всплывающая подсказка у свежих точек.
6. **Качество.**
   - Полный `pytest tests` на финальном SHA.
   - Пересобрать ZIP с финального SHA (`git archive --format=zip HEAD`) и записать sha256 в `reports/ops/size.md`.
   - Убрать из Docker-образа лишнее: `reports/` (0,7 ГБ) и playwright.
7. **Наука.**
   - Если есть результат абляции (§64): решение KEEP/REJECT/NO_CHANGE в `reports/cheap_experiments/feature_ablation/REPORT.md`.
   - Эксперименты 02 (pretrained U-Net++) и 03 (ансамбль) — по промтам в `docs/acceptance/cheap_experiments/`.
