"""L140 · §51 п.3/п.5: «Динамика района» panel — desktop 1366 (opened via openDynamics event) and iPhone 12
(entry «Динамика» on the sheet handle). Checks: panel, summary with «площадь, не число предметов», 3 charts,
table rows = API rows, picking another date opens that snapshot, no console errors; on the phone also
44 px / 14 px / no horizontal scroll. Screens → reports/qa/mobile/dyn_*.png.
Usage: .venv\\Scripts\\python.exe reports/qa/dynamics_check.py [base]"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mobile_check import MEASURE  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8094"
OUT = Path(__file__).resolve().parent / "mobile"
REGION_SCENE = "live-honduras-2019-01-12"


def api_rows(region):
    with urllib.request.urlopen(f"{BASE}/api/v3/regions/{region}/dynamics") as r:
        return json.load(r)["count"]


def run(p, name, ctx_args, mobile):
    b = p.chromium.launch()
    ctx = b.new_context(**ctx_args)
    pg = ctx.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)[:150]))
    pg.on("console", lambda m: errs.append(m.text[:150]) if m.type == "error" else None)
    res = {"device": name}
    try:
        pg.goto(f"{BASE}/?scene={REGION_SCENE}", wait_until="domcontentloaded")
        pg.wait_for_selector('[data-testid="sz-item"], [data-testid="scene-title"]', timeout=30000, state="attached")
        pg.wait_for_timeout(2000)
        if mobile and pg.locator('[data-testid="m-dynamics"]').count():
            pg.locator('[data-testid="m-dynamics"]').tap()
            res["entry"] = "m-dynamics"
        elif pg.locator('[data-testid="dynamics-open-tl"]').count():  # timeline «Динамика ↗» (L132)
            pg.locator('[data-testid="dynamics-open-tl"]').first.click()
            res["entry"] = "dynamics-open-tl"
        elif pg.locator('[data-testid="dynamics-open"]').count():
            pg.locator('[data-testid="dynamics-open"]').first.click()
            res["entry"] = "dynamics-open"
        else:
            pg.evaluate(f"() => window.dispatchEvent(new CustomEvent('mp:dynamics', {{ detail: {{ scene: '{REGION_SCENE}' }} }}))")
            res["entry"] = "event"
        pg.wait_for_selector('[data-testid="dynamics-table"]', timeout=15000)
        pg.wait_for_timeout(600)
        pg.screenshot(path=str(OUT / f"dyn_{name}_01_panel.png"), scale="css")
        res["title"] = pg.locator('[data-testid="dynamics-title"]').inner_text()
        res["rows"] = pg.locator('[data-testid="dynamics-row"]').count()
        res["api_rows"] = api_rows("honduras")
        res["charts"] = pg.locator('[data-testid^="dyn-chart-"]').count()
        res["honest"] = "не число предметов" in pg.locator('[data-testid="dynamics-honest"]').inner_text()
        if mobile:
            res["measure"] = pg.evaluate(MEASURE)
        pg.locator('[data-testid="dynamics-panel"] .dy-body').evaluate("e => e.scrollTop = e.scrollHeight")
        pg.wait_for_timeout(300)
        pg.screenshot(path=str(OUT / f"dyn_{name}_02_table.png"), scale="css")
        # pick the last date (another snapshot)
        last = pg.locator('[data-testid="dynamics-row"]').last
        last.tap() if mobile else last.click()
        pg.wait_for_timeout(2500)
        res["url_scene_after_pick"] = pg.evaluate("() => new URLSearchParams(location.search).get('scene')")
        res["row_on"] = pg.locator('[data-testid="dynamics-row"].on').count()
        pg.screenshot(path=str(OUT / f"dyn_{name}_03_picked.png"), scale="css")
        cl = pg.locator('[data-testid="dynamics-close"]')
        cl.tap() if mobile else cl.click()
        pg.wait_for_timeout(300)
        res["closed"] = pg.locator('[data-testid="dynamics-panel"]').count() == 0
        integ = pg.locator('[data-testid="scene-integral"]')
        res["scene_integral"] = integ.inner_text()[:160] if integ.count() else None
    except Exception as e:  # noqa: BLE001
        res["fail"] = str(e).splitlines()[0][:200]
        pg.screenshot(path=str(OUT / f"dyn_{name}_99_fail.png"), scale="css")
    res["errors"] = errs[:5]
    ctx.close()
    b.close()
    return res


def main():
    with sync_playwright() as p:
        ip = dict(p.devices["iPhone 12"])
        ip.pop("default_browser_type", None)
        for name, args, mob in (("d1366", dict(viewport={"width": 1366, "height": 768}), False), ("iphone12", ip, True),
                                ("w360", dict(viewport={"width": 360, "height": 800}, is_mobile=True, has_touch=True), True)):
            r = run(p, name, args, mob)
            m = r.pop("measure", None)
            if m:
                r["m"] = (m["hscroll"], m["small"][:4], m["tiny"][:4], m["over"])
            print(json.dumps(r, ensure_ascii=False))


if __name__ == "__main__":
    main()
