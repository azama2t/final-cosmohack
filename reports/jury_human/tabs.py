"""Click every top-level tab/button and dump screenshot + visible text (in viewport).
Usage: python tabs.py TAG WIDTH
"""
import sys, time, os
from playwright.sync_api import sync_playwright

TAG = sys.argv[1]; W = int(sys.argv[2]) if len(sys.argv) > 2 else 1366
H = {1366: 768, 1920: 1080}[W]
URL = "http://localhost:8070"
D = os.path.dirname(os.path.abspath(__file__)); IMG = os.path.join(D, "img")

VIS_JS = """() => { const out=[]; const W=innerWidth,H=innerHeight;
 const walker=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT); let n;
 while(n=walker.nextNode()){ const t=n.textContent.trim(); if(!t) continue; const el=n.parentElement; if(!el) continue;
  const r=el.getBoundingClientRect(); const cs=getComputedStyle(el);
  if(cs.visibility==='hidden'||cs.display==='none'||r.width===0||r.height===0||cs.opacity==='0') continue;
  if(r.bottom>0 && r.top<H && r.right>0 && r.left<W) out.push(Math.round(r.left)+','+Math.round(r.top)+' '+t.slice(0,300)); }
 return out.join('\\n'); }"""

def dump(pg, name, log):
    pg.screenshot(path=os.path.join(IMG, f"{TAG}_{W}_{name}.png"))
    log.write(f"\n===== {name} =====\n" + pg.evaluate(VIS_JS) + "\n")

with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    ctx = b.new_context(viewport={"width": W, "height": H}, locale="ru-RU", accept_downloads=True)
    pg = ctx.new_page()
    log = open(os.path.join(D, f"{TAG}_{W}_tabs.txt"), "w", encoding="utf-8")
    pg.goto(URL); pg.wait_for_load_state("networkidle", timeout=20000); pg.wait_for_timeout(1000)
    steps = [("tab_izm", "button.tab:has-text('Измерения')"),
             ("tab_kuda", "button.tab:has-text('Куда идти')"),
             ("tab_metr", "button.tab:has-text('Метрики')"),
             ("tab_zony", "button.tab:has-text('Зоны')"),
             ("top_reestr", "button:has-text('Реестр пар')"),
             ("top_vygr", "button:has-text('Выгрузка')"),
             ("top_zapr", "button:has-text('Запросы')"),
             ("top_sloi", "button:has-text('Слои')"),
             ("title_i", "button.info-btn >> nth=0"),
             ]
    for name, sel in steps:
        try:
            t0 = time.time(); pg.click(sel, timeout=5000); pg.wait_for_timeout(1200)
            log.write(f"\n[{name}] click ok {time.time()-t0:.2f}s")
            dump(pg, name, log)
            pg.keyboard.press("Escape"); pg.wait_for_timeout(300)
        except Exception as e:
            log.write(f"\n[{name}] FAIL {e}\n")
    # zone card
    try:
        pg.click("button.tab:has-text('Зоны')"); pg.wait_for_timeout(500)
        t0 = time.time(); pg.click("button.c-zi >> nth=0"); pg.wait_for_timeout(2500)
        log.write(f"\n[zone0] {time.time()-t0:.2f}s"); dump(pg, "zone0", log)
    except Exception as e:
        log.write(f"\n[zone0] FAIL {e}\n")
    for name, sel in [("mode_live", "button:has-text('Живые снимки')"), ("mode_foto", "button:has-text('Фото')")]:
        try:
            pg.goto(URL); pg.wait_for_load_state("networkidle", timeout=20000); pg.wait_for_timeout(800)
            t0 = time.time(); pg.click(sel, timeout=5000); pg.wait_for_timeout(2500)
            log.write(f"\n[{name}] {time.time()-t0:.2f}s"); dump(pg, name, log)
        except Exception as e:
            log.write(f"\n[{name}] FAIL {e}\n")
    log.close(); b.close()
