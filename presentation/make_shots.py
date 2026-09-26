r"""Скрины сервиса v2 для деки (основная часть, слайды «Сервис»): http://localhost:8070, 1920×1080, без фикстур.

Путь §33: обзор Земли (наведение на находку — тултип) → клик по точке → сцена и карточка зоны → «В студию» →
«← Назад» → «Выгрузка» → режим «Фото». Пишет presentation/img/{earth,card,studio,export,photo}.jpg; затем
    .venv\Scripts\python.exe scripts\make_deck_case.py --pdf
вставит их в presentation/deck.pptx и reports/case_deck.pptx (кандидаты — IMAGES_MAIN в make_deck_case.py).
Сервис не перезапускает. Ошибки консоли печатает; при ошибках код возврата 1 (такие скрины в деку не годятся).

    .venv\Scripts\python.exe presentation\make_shots.py [--base http://localhost:8070] [--out out/l126/try]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "img"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8070")
    ap.add_argument("--out", default=str(OUT), help="папка скринов (для пробы — не presentation/img)")
    ap.add_argument("--by-map", action="store_true", help="открыть карточку кликом по точке карты, а не из списка")
    a = ap.parse_args(argv)
    out = Path(a.out)
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    errs: list[str] = []
    made, notes = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1920, "height": 1080})
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        pg.goto(a.base + "/", wait_until="networkidle", timeout=90000)
        pg.wait_for_timeout(6000)
        box = pg.locator("[data-testid=main]").bounding_box()
        pts = []
        for _ in range(20):
            pts = pg.evaluate("() => (window.__app && window.__app.findPoints) ? window.__app.findPoints() : null") or []
            if pts:
                break
            pg.wait_for_timeout(1000)
        # 1. Земля: наведение на одиночную находку (тултип: статус + дата снимка)
        single = [q for q in pts if not q.get("cluster") and 0 < q["x"] < box["width"] - 380 and 0 < q["y"] < box["height"]]
        if single:
            pg.mouse.move(box["x"] + single[0]["x"], box["y"] + single[0]["y"])
            pg.wait_for_timeout(900)
        else:
            notes.append("точек-находок через __app.findPoints нет — Земля без тултипа")
        pg.screenshot(path=str(out / "earth.jpg"), type="jpeg", quality=90); made.append("earth")
        # 2. точка → сцена и карточка. Для деки — находка отложенной сцены Cózar (первая в списке «Находки», уровень B);
        #    клик по точке карты проверен L111 (scripts/case/s33_path.py), здесь — --by-map
        opened = False
        for _ in range(6 if a.by_map else 0):
            pts = pg.evaluate("() => window.__app && window.__app.findPoints ? window.__app.findPoints() : null") or []
            if not pts:
                break
            single = [q for q in pts if not q.get("cluster") and 0 < q["x"] < box["width"] - 380 and 0 < q["y"] < box["height"]]
            tgt = single[0] if single else pts[0]
            pg.mouse.click(box["x"] + tgt["x"], box["y"] + tgt["y"])
            pg.wait_for_timeout(2500)
            if pg.locator("[data-testid=scene-zone-card]").count():
                opened = True
                break
        if not opened:  # запасной путь — первая находка списка (отложенная сцена Cózar)
            notes.append("карточка открыта из списка «Находки» (первая — отложенная сцена Cózar)")
            if pg.locator("[data-testid=tab-zones]").count():
                pg.click("[data-testid=tab-zones]")
            pg.locator("[data-testid=sz-item]").first.click()
        pg.wait_for_selector("[data-testid=scene-zone-card]", timeout=30000)
        pg.wait_for_timeout(6000)
        pg.screenshot(path=str(out / "card.jpg"), type="jpeg", quality=90); made.append("card")
        # 3. студия → назад
        st = pg.locator("[data-testid=sz-studio]")
        if not st.count():
            st = pg.get_by_role("button", name="В студию")
        if st.count():
            st.first.click()
            pg.wait_for_timeout(6000)
            pg.screenshot(path=str(out / "studio.jpg"), type="jpeg", quality=90); made.append("studio")
            bk = pg.locator("[data-testid=studio-back]")
            if bk.count():
                bk.first.click()
                pg.wait_for_timeout(2500)
        else:
            notes.append("кнопки «В студию» нет")
        if pg.locator("[data-testid=sz-back]").count():
            pg.click("[data-testid=sz-back]")
            pg.wait_for_timeout(2500)
        # 4. выгрузка
        pg.click("[data-testid=act-export]")
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(out / "export.jpg"), type="jpeg", quality=90); made.append("export")
        pg.keyboard.press("Escape")
        # 5. «Фото»: пример уже открыт — ждём конца счёта
        pg.click("[data-testid=mode-photo]")
        for _ in range(90):
            pg.wait_for_timeout(1000)
            if "Считаю" not in pg.inner_text("body"):
                break
        pg.wait_for_timeout(1500)
        pg.screenshot(path=str(out / "photo.jpg"), type="jpeg", quality=90); made.append("photo")
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
