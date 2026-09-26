"""Probe deeper panels: measurement card, metrics panel full text, photo example, export download.
Usage: python probe.py TAG WIDTH
"""
import sys, time, os
from playwright.sync_api import sync_playwright

TAG = sys.argv[1]; W = int(sys.argv[2]) if len(sys.argv) > 2 else 1366
H = {1366: 768, 1920: 1080}[W]
URL = "http://localhost:8070"
D = os.path.dirname(os.path.abspath(__file__)); IMG = os.path.join(D, "img")
VIS_JS = open(os.path.join(D, "vis.js"), encoding="utf-8").read()

def dump(pg, name, log, full=False):
    pg.screenshot(path=os.path.join(IMG, f"{TAG}_{W}_{name}.png"))
    log.write(f"\n===== {name} =====\n" + pg.evaluate(VIS_JS, full) + "\n")

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={"width": W, "height": H}, locale="ru-RU", accept_downloads=True)
    pg = ctx.new_page()
    log = open(os.path.join(D, f"{TAG}_{W}_probe.txt"), "w", encoding="utf-8")
    pg.goto(URL); pg.wait_for_load_state("networkidle", timeout=20000); pg.wait_for_timeout(800)
    # measurement card
    try:
        pg.click("button.tab:has-text('Измерения')"); pg.wait_for_timeout(600)
        items = pg.locator("button.c-zi, .c-mi, [class*=item]")
        t0 = time.time()
        pg.locator("text=станция 41.3").first.click(); pg.wait_for_timeout(2500)
        log.write(f"\n[meas0] {time.time()-t0:.2f}s"); dump(pg, "meas0", log)
        log.write("\n--- meas0 full panel text ---\n" + pg.evaluate(VIS_JS, True)[:6000])
    except Exception as e:
        log.write(f"\n[meas0] FAIL {e}\n")
    # metrics full
    try:
        pg.goto(URL); pg.wait_for_load_state("networkidle", timeout=20000); pg.wait_for_timeout(800)
        pg.click("button.tab:has-text('Метрики')"); pg.wait_for_timeout(1000)
        dump(pg, "metr", log, True)
    except Exception as e:
        log.write(f"\n[metr] FAIL {e}\n")
    # export
    try:
        pg.click("button:has-text('Выгрузка')"); pg.wait_for_timeout(600)
        t0 = time.time()
        with pg.expect_download(timeout=20000) as dl:
            pg.locator("text=CSV").first.click()
        d = dl.value; path = os.path.join(D, "img", f"{TAG}_export_{d.suggested_filename}")
        d.save_as(path)
        log.write(f"\n[export csv] {time.time()-t0:.2f}s file={d.suggested_filename} size={os.path.getsize(path)}\n")
        with open(path, encoding="utf-8", errors="replace") as f:
            log.write("head: " + "".join(f.readlines()[:3]) + "\n")
    except Exception as e:
        log.write(f"\n[export] FAIL {e}\n")
    # photo example
    try:
        pg.goto(URL); pg.wait_for_load_state("networkidle", timeout=20000); pg.wait_for_timeout(500)
        pg.click("button:has-text('Фото')"); pg.wait_for_timeout(1500)
        t0 = time.time(); pg.click("text=Пример 1")
        try:
            pg.wait_for_function("() => /\\d+\\s*предмет/.test(document.body.innerText) || document.querySelector('canvas,img.result')", timeout=30000)
        except Exception:
            pass
        pg.wait_for_timeout(3000)
        log.write(f"\n[photo ex1] {time.time()-t0:.2f}s"); dump(pg, "photo_ex1", log)
    except Exception as e:
        log.write(f"\n[photo] FAIL {e}\n")
    log.close(); b.close()
