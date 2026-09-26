r"""Запасное видео демо (на случай, если сервис не поднимется на приёмке): запись Playwright с http://localhost:8070,
1920×1080, без фикстур, 2–3 мин, паузы 2–3 с на ключевых экранах.

Путь (интерфейс §34): обзор — список снимков → снимок Альборан (отложенная сцена Cózar 30SXE) → нумерованные зоны
с исследовательской оценкой шт./км² → зона 16 → карточка → «В студию» → «Назад» → другой снимок и находка →
«Цифры» → «Проверка качества» → слой «Полевые измерения» → «Фото» (пример) → «Выгрузка» → «Запросы» (сохранить →
повторить). Пишет presentation/demo.webm и, если есть ffmpeg, presentation/demo.mp4 (H.264, CRF 30; цель < 45 МБ).
Сервис не перезапускает; только чтение.

    .venv\Scripts\python.exe presentation\make_demo_video.py [--base http://localhost:8070]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEMO_ZONE = "SZ-demo-cozar-2021-03-11-016"
SCENE_TEXT = "Альборан"
QNAME = "Демо-видео: отложенная сцена Cózar"
FFMPEG = Path(r"C:\ffmpeg\bin\ffmpeg.exe")


def cleanup_queries(base: str) -> None:
    """Сохранённые запросы видео (и прежних прогонов записи) удаляются из хранилища сервиса — продукт как был."""
    import json
    import urllib.request
    with urllib.request.urlopen(base + "/api/v3/queries", timeout=30) as r:
        body = json.load(r)
    qs = (body.get("data") or body).get("queries") or []
    for q in qs:
        if str(q.get("name", "")).startswith(("Демо-видео:", "Демо: отложенная сцена Cózar")):
            req = urllib.request.Request(f"{base}/api/v3/queries/{q['query_id']}", method="DELETE")
            urllib.request.urlopen(req, timeout=30).close()
            print("удалён сохранённый запрос видео:", q["query_id"], q.get("name"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8070")
    ap.add_argument("--out", default=str(HERE / "demo.webm"))
    a = ap.parse_args(argv)
    from playwright.sync_api import sync_playwright

    out = Path(a.out)
    tmp = HERE.parent / "out" / "demo_video_tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    errs: list[str] = []
    log: list[str] = []
    t0 = time.time()

    def step(msg):
        log.append(f"{time.time() - t0:6.1f} с  {msg}")

    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1920, "height": 1080}, record_video_dir=str(tmp),
                            record_video_size={"width": 1920, "height": 1080})
        pg = ctx.new_page()
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        bad_http: list[str] = []
        pg.on("response", lambda r: bad_http.append(f"{r.status} {r.url}") if r.status >= 400 else None)
        pause = lambda ms: pg.wait_for_timeout(int(ms * 1.4))  # noqa: E731 — темп для зрителя

        def main_box():
            return pg.locator("[data-testid=main]").bounding_box()

        def points():
            return pg.evaluate("() => window.__app && window.__app.findPoints ? window.__app.findPoints() : []") or []

        def glide_click(x, y):
            box = main_box()
            pg.mouse.move(box["x"] + x, box["y"] + y, steps=25)
            pause(1200)
            pg.mouse.click(box["x"] + x, box["y"] + y)

        pg.goto(a.base + "/", wait_until="networkidle", timeout=90000)
        pg.wait_for_selector("[data-testid=scene-item]", timeout=60000)
        step("обзор: Земля и список снимков"); pause(4500)

        def back_to_scenes():
            for bk in ("sz-back", "scene-back"):
                if pg.locator(f"[data-testid={bk}]").count():
                    pg.click(f"[data-testid={bk}]"); pause(2000)

        def open_zone(zone_id=None):
            """Зона — клик по строке списка (номер N = строка N); запасной путь — selectZone."""
            items = pg.locator("[data-testid=sz-item]")
            n = int(zone_id.rsplit("-", 1)[1]) if zone_id else 1
            if items.count() >= n:
                it = items.nth(n - 1)
                it.scroll_into_view_if_needed(); it.hover(); pause(1200); it.click()
            elif zone_id:
                pg.evaluate(f"() => window.__app.selectZone('{zone_id}')")
            pg.wait_for_selector("[data-testid=scene-zone-card]", timeout=30000)

        # снимок Альборан (отложенная сцена Cózar) → нумерованные зоны → зона 16 → карточка
        sc = pg.locator("[data-testid=scene-item]", has_text=SCENE_TEXT)
        (sc.first if sc.count() else pg.locator("[data-testid=scene-item]").first).hover(); pause(1200)
        (sc.first if sc.count() else pg.locator("[data-testid=scene-item]").first).click()
        pg.wait_for_selector("[data-testid=sz-item]", timeout=30000)
        step("снимок: зоны с исследовательской оценкой"); pause(6000)
        open_zone(DEMO_ZONE)
        step("карточка зоны 16"); pause(6000)
        card = pg.locator("[data-testid=scene-zone-card]")
        card.hover(); pg.mouse.wheel(0, 500); pause(3500); pg.mouse.wheel(0, -500); pause(1500)
        if pg.locator("[data-testid=sz-studio]").count():
            pg.click("[data-testid=sz-studio]"); step("студия"); pause(5500)
            if pg.locator("[data-testid=studio-back]").count():
                pg.click("[data-testid=studio-back]"); step("назад к карточке"); pause(2500)
        back_to_scenes(); step("назад к списку снимков"); pause(2000)

        # другой снимок и находка (требует проверки)
        items = pg.locator("[data-testid=scene-item]")
        oth = [i for i in range(items.count()) if SCENE_TEXT not in items.nth(i).inner_text()]
        if oth:
            items.nth(oth[0]).hover(); pause(1000); items.nth(oth[0]).click()
            pg.wait_for_selector("[data-testid=sz-item]", timeout=30000); step("другой снимок"); pause(4000)
            open_zone(); step("другая находка — карточка"); pause(5500)
            back_to_scenes()

        # «Цифры», «Проверка качества», слой «Полевые измерения»
        if pg.locator("[data-testid=headline-open]").count():
            pg.click("[data-testid=headline-open]"); step("Цифры"); pause(6000)
            if pg.locator("[data-testid=headline-close]").count():
                pg.click("[data-testid=headline-close]")
            else:
                pg.keyboard.press("Escape")
            pause(1000)
        if pg.locator("[data-testid=act-qc]").count():
            pg.click("[data-testid=act-qc]"); step("Проверка качества"); pause(6000)
            if pg.locator("[data-testid=qc-close]").count():
                pg.click("[data-testid=qc-close]")
            else:
                pg.keyboard.press("Escape")
            pause(1000)
        if pg.locator("[data-testid=act-field]").count():
            pg.click("[data-testid=act-field]"); step("слой «Полевые измерения»"); pause(5000)
            pg.click("[data-testid=act-field]"); pause(1500)

        # «Фото»: пример уже открыт
        pg.click("[data-testid=mode-photo]"); step("Фото")
        for _ in range(60):
            pause(1000)
            if "Считаю" not in pg.inner_text("body"):
                break
        pause(5000)
        pg.click("[data-testid=mode-case]"); pause(3000)

        # даты сцены → выгрузка → запросы: сохранить → сбросить → повторить
        # (даты сцены не ставим: на обзоре Земли при фильтре одного дня поверх карты — «Нет данных под выбранные фильтры»)
        pg.click("[data-testid=act-export]"); step("Выгрузка"); pause(4000)
        pg.keyboard.press("Escape"); pg.mouse.click(100, 60); pause(1000)
        pg.click("[data-testid=act-queries]"); pause(1500)
        if pg.locator("[data-testid=q-name]").count():
            pg.fill("[data-testid=q-name]", QNAME); pause(1000)
            pg.click("[data-testid=q-save]"); step("запрос сохранён"); pause(2000)
        pg.keyboard.press("Escape"); pause(500)
        if pg.locator("[data-testid=f-reset]").count():
            pg.click("[data-testid=f-reset]"); step("сброс"); pause(3000)
        pg.click("[data-testid=act-queries]"); pause(1500)
        row = pg.locator("[data-testid=q-item]", has_text=QNAME).first
        if row.count():
            row.locator("[data-testid=q-run]").click(); step("повтор запроса"); pause(5000)
        pg.keyboard.press("Escape"); pause(1000)
        pg.click("[data-testid=act-earth]"); step("обзор Земли — конец"); pause(3000)
        video = pg.video
        ctx.close()
        b.close()
        src = Path(video.path())
    shutil.move(str(src), str(out))
    cleanup_queries(a.base)
    size = out.stat().st_size / 1e6
    print("\n".join(log))
    print(f"видео: {out} ({size:.1f} МБ, {time.time() - t0:.0f} с записи)")
    if FFMPEG.exists():  # mp4 H.264 — играет везде (PowerPoint, Windows); webm остаётся исходником
        mp4 = out.with_suffix(".mp4")
        subprocess.run([str(FFMPEG), "-y", "-i", str(out), "-c:v", "libx264", "-crf", "30", "-preset", "medium",
                        "-pix_fmt", "yuv420p", "-an", str(mp4)], check=True, capture_output=True)
        print(f"сжато: {mp4} ({mp4.stat().st_size / 1e6:.1f} МБ)")
    print("HTTP ≥ 400:", len(bad_http))
    for u in bad_http[:10]:
        print("   ", u[:200])
    print("ошибок консоли:", len(errs))
    for e in errs[:10]:
        print("   ", e[:200])
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
