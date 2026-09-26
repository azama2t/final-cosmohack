"""Скриншоты для README и docs/screenshots/ (1366×768, финальный UI): Земля → снимок с зонами → карточка → студия →
выгрузка → детальный кадр «Дроны». PNG → JPEG (pillow), каждый файл ≤ 400 КБ.

  .venv\\Scripts\\python.exe scripts\\case\\readme_screenshots.py --base-url http://127.0.0.1:8070
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "screenshots"
LIMIT = 400 * 1024


def save_jpg(png: bytes, name: str) -> dict:
    im = Image.open(io.BytesIO(png)).convert("RGB")
    for q in (85, 80, 75, 70, 65, 60, 55, 50):
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() <= LIMIT:
            break
    (OUT / name).write_bytes(buf.getvalue())
    return {"file": name, "kb": round(buf.tell() / 1024), "q": q}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8070")
    ap.add_argument("--w", type=int, default=1366)
    ap.add_argument("--h", type=int, default=768)
    ap.add_argument("--drone-set", default="TUN", help="text on the drone set card to open")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    res, errs = [], []
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        ctx = b.new_context(viewport={"width": a.w, "height": a.h}, locale="ru-RU", accept_downloads=True)
        pg = ctx.new_page()
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)

        def tid(k):
            return pg.locator(f"[data-testid={k}]")

        def shot(name, wait=1500):
            pg.wait_for_timeout(wait)
            res.append(save_jpg(pg.screenshot(), name))
            print(json.dumps(res[-1], ensure_ascii=False))

        pg.goto(a.base_url + "/", wait_until="domcontentloaded")
        pg.evaluate("() => { try { localStorage.removeItem('mp.case.filtersOpen') } catch (e) {} }")
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_function("() => window.__app && window.__app.ready && window.__app.szReady", timeout=120000)
        pg.wait_for_timeout(4000)
        shot("01_earth.jpg")
        alb = tid("scene-item").filter(has_text="Альборан")  # отложенная демо-сцена; иначе первый снимок списка
        (alb.first if alb.count() else tid("scene-item").first).click()
        pg.wait_for_timeout(4500)
        shot("02_scene_zones.jpg")
        tid("sz-item").first.click()
        pg.wait_for_function("() => window.__app.szDetailReady && document.querySelector('[data-testid=sz-verdict]')", timeout=60000)
        shot("03_zone_card.jpg", 3000)
        tid("sz-studio").click()
        pg.wait_for_selector("[data-testid=zone-studio]", timeout=30000)
        shot("04_studio.jpg", 3000)
        tid("studio-card").click()
        pg.wait_for_timeout(800)
        tid("act-export").click()
        shot("06_export.jpg", 1200)
        tid("act-export").click()
        pg.wait_for_timeout(500)
        # детальный кадр «Дроны»: набор → кадр → рамки «обе» (разметка набора + наш счётчик); подпись «не спутник» на кадре
        if not (tid("more-drones").count() and tid("more-drones").is_visible()):
            tid("act-more").click()
            pg.wait_for_timeout(400)
        tid("more-drones").click()
        pg.wait_for_selector("[data-testid=drones-setcard]", timeout=30000)
        sc = tid("drones-setcard").filter(has_text=a.drone_set)
        (sc.first if sc.count() else tid("drones-setcard").first).click()
        pg.wait_for_selector("[data-testid=drones-thumb]", timeout=30000)
        tid("drones-thumb").first.click()
        pg.wait_for_selector("[data-testid=drones-frame]", timeout=30000)
        if tid("drones-boxes-both").count() and tid("drones-boxes-both").is_enabled():
            tid("drones-boxes-both").click()
        shot("05_drone_frame.jpg", 3000)
        b.close()
    print(json.dumps({"shots": res, "console_errors": errs[:5]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
