"""Zone card -> «В студию» -> back to map; check position restore and path to Earth overview."""
import sys, os, json, time
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jh import Run, URL, D, CL
TAG = sys.argv[1]; W = int(sys.argv[2]); H = {1366: 768, 1920: 1080}[W]; R = Run(TAG, W); out = {}
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_context(viewport={"width": W, "height": H}, locale="ru-RU").new_page()
    pg.goto(URL)
    pg.wait_for_function("() => /Спутниковые зоны детектора\\s*·\\s*[1-9]/.test(document.body.innerText)", timeout=60000)
    pg.wait_for_timeout(8000)
    pg.locator("text=/30SXE · зона 7/").first.click(); pg.wait_for_timeout(4000); R.shot(pg, "s0_card", dump=False)
    t0 = time.time(); pg.click("text=В студию", timeout=5000); pg.wait_for_timeout(5000); out["studio_s"] = round(time.time() - t0, 2)
    R.shot(pg, "s1_studio"); out["studio_text"] = R.dumps["s1_studio"]["text"][:80]; out["studio_click"] = R.dumps["s1_studio"]["click"][:60]
    json.dump(out, open(os.path.join(D, f"{TAG}_{W}_probe_studio.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    b.close()
print(json.dumps(out, ensure_ascii=False)[:4000])
