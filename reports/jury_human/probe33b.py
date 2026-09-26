"""Probe step-by-step UI states for §33 (dump visible text + clickables per state).
Usage: python probe33b.py TAG WIDTH
"""
import sys, time, os, json
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jh import Run, URL, D

TAG = sys.argv[1]; W = int(sys.argv[2]); H = {1366: 768, 1920: 1080}[W]
R = Run(TAG, W); log = {}

def safe(name, fn):
    try: log[name] = fn()
    except Exception as e: log[name] = {"error": str(e)[:300]}

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={"width": W, "height": H}, locale="ru-RU", accept_downloads=True)
    pg = ctx.new_page()
    pg.goto(URL)
    mk, t = R.wait_markers(pg, "b0"); log["markers"] = [mk, t]
    R.shot(pg, "b0")
    # click the biggest marker (cluster) -> zoom
    x, y, _ = max(mk, key=lambda m: m[2]) if mk else (0, 0, 0)
    pg.mouse.click(x, y); pg.wait_for_timeout(4000)
    mk2, t2 = R.wait_markers(pg, "b1"); log["markers_after_zoom"] = [mk2, t2]
    R.shot(pg, "b1")
    if mk2:
        x, y, _ = mk2[0]
        pg.mouse.click(x, y); pg.wait_for_timeout(4000)
        R.shot(pg, "b2_card")
        mk3, _ = R.wait_markers(pg, "b2m", tries=3); log["markers_card"] = mk3
    # back buttons
    safe("back_candidates", lambda: pg.evaluate("""() => Array.from(document.querySelectorAll('button,a')).filter(e=>/Назад|обзор|Обзор|←/.test(e.innerText||e.title||'')).map(e=>{const r=e.getBoundingClientRect();return [(e.innerText||e.title).trim().slice(0,60), Math.round(r.left), Math.round(r.top), r.width>0]})"""))
    # Studio button?
    safe("studio_candidates", lambda: pg.evaluate("""() => Array.from(document.querySelectorAll('button,a')).filter(e=>/студи|Исследовать/i.test(e.innerText||'')).map(e=>{const r=e.getBoundingClientRect();return [(e.innerText).trim().slice(0,60), Math.round(r.left), Math.round(r.top)]})"""))
    # full text of page (incl. out of view) in card state
    from jh import ALLTXT
    R.dumps["b2_card_all"] = {"text": pg.evaluate(ALLTXT), "click": []}
    # fresh -> Цифры panel
    pg.goto(URL); pg.wait_for_timeout(3000)
    safe("cifry", lambda: (pg.click("text=Цифры: поле", timeout=8000), pg.wait_for_timeout(1500), R.shot(pg, "b3_cifry"))[-1])
    R.dumps["b3_cifry_all"] = {"text": pg.evaluate(ALLTXT), "click": []}
    pg.goto(URL); pg.wait_for_timeout(3000)
    safe("sloi", lambda: (pg.click("button:has-text('Слои')", timeout=8000), pg.wait_for_timeout(1200), R.shot(pg, "b4_sloi"))[-1])
    pg.goto(URL); pg.wait_for_timeout(3000)
    safe("vygr", lambda: (pg.click("button:has-text('Выгрузка')", timeout=8000), pg.wait_for_timeout(1200), R.shot(pg, "b5_vygr"))[-1])
    pg.goto(URL); pg.wait_for_timeout(3000)
    safe("metr", lambda: (pg.click("text=Метрики", timeout=8000), pg.wait_for_timeout(1200), R.shot(pg, "b6_metr"))[-1])
    R.dumps["b6_metr_all"] = {"text": pg.evaluate(ALLTXT), "click": []}
    pg.goto(URL); pg.wait_for_timeout(3000)
    safe("foto", lambda: (pg.click("button:has-text('Фото')", timeout=8000), pg.wait_for_timeout(5000), R.shot(pg, "b7_foto"))[-1])
    R.dumps["b7_foto_all"] = {"text": pg.evaluate(ALLTXT), "click": pg.evaluate(__import__('jh').CL)}
    json.dump({"log": log, "dumps": R.dumps}, open(os.path.join(D, f"{TAG}_{W}_probe33b.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    b.close()
print(json.dumps(log, ensure_ascii=False)[:3000])
