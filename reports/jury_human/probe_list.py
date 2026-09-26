"""Follow-up: list click -> wait longer; inspect truncated 'Количество ... не определено' line."""
import sys, os, json
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from jh import Run, URL, D
TAG = sys.argv[1]; W = int(sys.argv[2]); H = {1366: 768, 1920: 1080}[W]; R = Run(TAG, W); out = {}
with sync_playwright() as p:
    b = p.chromium.launch(headless=True)
    pg = b.new_context(viewport={"width": W, "height": H}, locale="ru-RU").new_page()
    pg.goto(URL)
    pg.wait_for_function("() => /Спутниковые зоны детектора\\s*·\\s*[1-9]/.test(document.body.innerText)", timeout=60000)
    pg.wait_for_timeout(int(os.environ.get("JH_DELAY", "1500")))
    pg.locator("text=/30SXE · зона 16/").first.click()
    for s in (1, 3, 6):
        pg.wait_for_timeout(1000 if s == 1 else 2000 if s == 3 else 3000); R.shot(pg, f"l_{s}s", dump=False)
    out["count_line"] = pg.evaluate("""() => { const el=[...document.querySelectorAll('*')].find(e=>e.children.length===0 && /Количество предметов по этому снимку не определено/.test(e.textContent)); if(!el) return null;
      const p=el.closest('div,li,p')||el; const cs=getComputedStyle(el); return {text: el.textContent, parentText: p.innerText.slice(0,400), title: el.title||p.title||null, overflow: cs.textOverflow, ws: cs.whiteSpace, w: el.scrollWidth, cw: el.clientWidth} }""")
    try:
        pg.hover("text=/Количество предметов по этому снимку не определено/", timeout=3000); pg.wait_for_timeout(800); R.shot(pg, "l_hover_count", dump=False)
        out["after_hover"] = [h for h in R.inview(pg, r"калибров|детальн|фото|пар") if h.get("inView")][:6]
    except Exception as e: out["hover_err"] = str(e)[:200]
    try:
        pg.click("text=Исследовать дальше", timeout=3000); pg.wait_for_timeout(1200); R.shot(pg, "l_explore", dump=False)
        out["explore"] = [h["t"] for h in R.inview(pg, r"детальн|дрон|фото|сверить|поле") if h.get("inView")][:8]
    except Exception as e: out["explore_err"] = str(e)[:200]
    try:
        pg.click("text=/Ближайшее полевое измерение/", timeout=3000); pg.wait_for_timeout(4000); R.shot(pg, "l_nearest_field", dump=False)
        out["nearest_field_after"] = [h["t"][:160] for h in R.inview(pg, r"шт\./км²|MPL-|измерение") if h.get("inView")][:10]
    except Exception as e: out["nearest_err"] = str(e)[:200]
    try:
        pg.click("text=В студию", timeout=3000); pg.wait_for_timeout(5000); R.shot(pg, "l_studio", dump=False)
        out["studio"] = [h["t"][:120] for h in R.inview(pg, r"Назад|студи|карт|Обзор") if h.get("inView")][:8]
        pg.click("text=/Назад|К карте|← /", timeout=4000); pg.wait_for_timeout(2500); R.shot(pg, "l_studio_back", dump=False)
        out["after_studio_back_card"] = pg.evaluate("() => [...document.querySelectorAll('button')].some(b => /В студию/.test(b.innerText) && b.getBoundingClientRect().width > 0)")
    except Exception as e: out["studio_err"] = str(e)[:200]
    json.dump(out, open(os.path.join(D, f"{TAG}_{W}_probe_list.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    b.close()
print(json.dumps(out, ensure_ascii=False)[:3000])

