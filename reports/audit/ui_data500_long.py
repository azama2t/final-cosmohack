"""Audit L107: v2 with every /api/v3/* except meta returning 500 — frames at 20 s and 60 s, error-message check (В10)."""
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT = Path(__file__).resolve().parent
def r500(rt):
    if "/api/v3/meta" in rt.request.url:
        return rt.continue_()
    return rt.fulfill(status=500, body='{"detail":"audit"}', content_type="application/json")
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = b.new_context(viewport={"width": 1920, "height": 1080}, locale="ru-RU").new_page()
    page.route("**/api/v3/**", r500)
    page.goto("http://127.0.0.1:8096/", wait_until="domcontentloaded")
    for t in (20, 60):
        time.sleep(20 if t == 20 else 40)
        page.screenshot(path=str(OUT / f"ui_p3_C_data500_{t}s.png"))
        body = page.inner_text("body")
        print(t, "s | loading:", "Загрузка" in body, "| error words:", [w for w in ("ошибк", "не загрузил", "повтор", "недоступ", "не удалось") if w in body.lower()],
              "| zones line:", [l for l in body.split("\n") if "Спутниковые зоны" in l][:2])
    b.close()
