"""Measure first-screen load: DOM zone list filled + first frame with markers (screenshot right after)."""
import sys, time, os, json
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jh import URL, D, IMG, orange_markers
TAG = sys.argv[1]; out = []
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    for W, H in [(1366, 768), (1920, 1080)]:
        for rep in range(3):
            ctx = b.new_context(viewport={"width": W, "height": H}, locale="ru-RU"); pg = ctx.new_page()
            t0 = time.time(); pg.goto(URL, wait_until="domcontentloaded"); t_dom = time.time() - t0
            pg.wait_for_function("() => /30SXE · зона/.test(document.body.innerText)", timeout=60000, polling=100)
            t_list = time.time() - t0
            ph = os.path.join(IMG, f"{TAG}_{W}_load{rep}.png"); pg.screenshot(path=ph); t_shot = time.time() - t0
            mk = orange_markers(ph, pg)
            pg.wait_for_timeout(1000); ph2 = os.path.join(IMG, f"{TAG}_{W}_load{rep}b.png"); pg.screenshot(path=ph2)
            mk2 = orange_markers(ph2, pg)
            out.append({"w": W, "rep": rep, "dom_s": round(t_dom, 2), "list_filled_s": round(t_list, 2), "shot_s": round(t_shot, 2),
                        "markers_at_list": len(mk), "markers_plus1s": len(mk2)})
            print(out[-1]); ctx.close()
    b.close()
json.dump(out, open(os.path.join(D, f"{TAG}_loadtime.json"), "w"), indent=1)
