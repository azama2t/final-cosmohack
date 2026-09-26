r"""Скрины сервиса v2 для деки (слайды «Сервис»): http://localhost:8070, 1920×1080, без фикстур. Интерфейс §34.

Путь: обзор (слева список снимков «район · дата · N находок · облачность») → снимок Альборан/Cózar (нумерованные зоны
с исследовательской оценкой шт./км²) → зона 16 → карточка → «В студию» → назад → «Проверка качества» → «Выгрузка» →
режим «Фото». Пишет presentation/img/{earth,scene,card,studio,metrics,export,photo}.jpg; затем
    .venv\Scripts\python.exe scripts\make_deck_case.py --pdf
вставит их в presentation/deck.pptx и reports/case_deck.pptx (кандидаты — IMAGES_MAIN в make_deck_case.py).
Сервис не перезапускает, только чтение. При ошибках консоли код возврата 1 (такие скрины в деку не годятся).

    .venv\Scripts\python.exe presentation\make_shots.py [--base http://localhost:8070] [--out out/l126/try]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "img"
SCENE_TEXT = "Альборан"       # демо-снимок: отложенная сцена Cózar, 30SXE 11.03.2021
DEMO_ZONE = "SZ-demo-cozar-2021-03-11-016"   # демо-зона (в списке снимка — номер 16)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8070")
    ap.add_argument("--out", default=str(OUT), help="папка скринов (для пробы — не presentation/img)")
    ap.add_argument("--size", default="1366x768", help="окно браузера (§43: 1366×768; снимается с масштабом 2)")
    ap.add_argument("--drift-scene", default="live-guanabara-2025-09-04", help="снимок для кадра дрейфа (?scene=)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    errs: list[str] = []
    made, notes = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        w, h = (int(x) for x in a.size.split("x"))
        pg = b.new_page(viewport={"width": w, "height": h}, device_scale_factor=2)
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        def shot(name):
            pg.screenshot(path=str(out / f"{name}.jpg"), type="jpeg", quality=90)
            made.append(name)

        pg.goto(a.base + "/", wait_until="networkidle", timeout=90000)
        pg.wait_for_selector("[data-testid=scene-item]", timeout=60000)
        pg.wait_for_timeout(5000)
        shot("earth")
        # снимок → нумерованные зоны
        sc = pg.locator("[data-testid=scene-item]", has_text=SCENE_TEXT)
        found = sc.count() > 0
        (sc.first if found else pg.locator("[data-testid=scene-item]").first).click()
        if not found:
            notes.append(f"снимка «{SCENE_TEXT}» нет — открыт первый в списке")
        pg.wait_for_selector("[data-testid=sz-item]", timeout=30000)
        pg.wait_for_timeout(6000)
        shot("scene")
        # зона → карточка
        ok = pg.evaluate(f"() => !!(window.__app && window.__app.selectZone && (window.__app.selectZone('{DEMO_ZONE}'), true))")
        if not ok:
            pg.locator("[data-testid=sz-item]").first.click()
            notes.append("selectZone недоступен — открыта первая зона списка")
        pg.wait_for_selector("[data-testid=scene-zone-card]", timeout=30000)
        pg.wait_for_timeout(6000)
        shot("card")
        # сама карточка («Что это» → статус → подтверждение → «Количество … не определено») — для слайда «Сервис»
        pg.locator("[data-testid=scene-zone-card]").first.screenshot(path=str(out / "card_panel.png"))
        made.append("card_panel")
        # студия → назад
        if pg.locator("[data-testid=sz-studio]").count():
            pg.click("[data-testid=sz-studio]")
            pg.wait_for_timeout(6000)
            shot("studio")
            if pg.locator("[data-testid=studio-back]").count():
                pg.click("[data-testid=studio-back]")
                pg.wait_for_timeout(2500)
        else:
            notes.append("кнопки «В студию» нет")
        for back in ("sz-back", "scene-back"):
            if pg.locator(f"[data-testid={back}]").count():
                pg.click(f"[data-testid={back}]")
                pg.wait_for_timeout(2000)
        # «Проверка качества» (метрики)
        if pg.locator("[data-testid=act-qc]").count():
            pg.click("[data-testid=act-qc]")
            pg.wait_for_timeout(3000)
            shot("metrics")
            if pg.locator("[data-testid=qc-close]").count():
                pg.click("[data-testid=qc-close]")
            else:
                pg.keyboard.press("Escape")
            pg.wait_for_timeout(1000)
        else:
            notes.append("кнопки «Проверка качества» нет")
        # выгрузка
        pg.click("[data-testid=act-export]")
        pg.wait_for_timeout(1500)
        shot("export")
        pg.keyboard.press("Escape")
        # «Фото»: пример уже открыт — ждём конца счёта
        pg.click("[data-testid=mode-photo]")
        for _ in range(90):
            pg.wait_for_timeout(1000)
            if "Считаю" not in pg.inner_text("body"):
                break
        pg.wait_for_timeout(1500)
        shot("photo")
        # дрейф: снимок Гуанабара, первая зона → «Дрейф»
        pg.goto(a.base + f"/?scene={a.drift_scene}", wait_until="networkidle", timeout=90000)
        try:
            pg.wait_for_selector("[data-testid=sz-item]", timeout=60000)
            pg.locator("[data-testid=sz-item]").first.click()
            pg.wait_for_selector("[data-testid=scene-zone-card]", timeout=30000)
            pg.wait_for_timeout(3000)
            if pg.locator("[data-testid=sz-drift-btn]").count():
                pg.locator("[data-testid=sz-drift-btn]").first.click()
                pg.wait_for_timeout(6000)
                shot("drift")
            else:
                notes.append("кнопки дрейфа нет у первой зоны")
        except Exception as e:  # noqa: BLE001
            notes.append(f"кадр дрейфа не снят: {e}"[:200])
        b.close()
    print("скрины:", ", ".join(made), "->", out)
    for n in notes:
        print("  заметка:", n)
    print("ошибок консоли:", len(errs))
    for e in errs[:10]:
        print("   ", e[:200])
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
