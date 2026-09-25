"""v3 extra frames (L94): level A (ADIS segment scene), oil layer on, field rows, Russian dates.

.venv\\Scripts\\python.exe service\\frontend_v3\\tools\\extra.py --out reports/screens/v3/iter7
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ADIS = [-126.60, 46.92]  # search.adis.seg~116516, 30.10.2021, level A


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8072")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    res: dict = {}
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-angle=d3d11", "--ignore-gpu-blocklist"])
        for w, h in ((1920, 1080), (1366, 768)):
            errs: list[str] = []
            pg = b.new_page(viewport={"width": w, "height": h})
            pg.on("console", lambda m: m.type == "error" and errs.append(m.text + " @ " + str((m.location or {}).get("url", ""))[:90]))
            pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
            pg.goto(a.url + "/?fresh=1")
            pg.evaluate("localStorage.clear()")
            pg.goto(a.url + "/?fresh=1")
            pg.wait_for_function("window.__mapReady === true", timeout=20000)
            pg.wait_for_timeout(2500)  # studio scene list arrives after the first paint
            # Russian dates
            pg.click("[data-testid=nav-layers]")
            ph = pg.get_attribute("input[aria-label='с']", "placeholder")
            pg.fill("input[aria-label='с']", "01.04.2016")
            pg.press("input[aria-label='с']", "Enter")
            pg.wait_for_timeout(300)
            pg.screenshot(path=str(out / f"{w}_12_dates.png"))
            pg.click("[data-testid=reset-filters]")
            # level A: ADIS segment scene via its footprint
            pg.click("[data-testid=nav-sites]")
            pg.evaluate("""async (c) => { const m = window.__map; m.jumpTo({center: c, zoom: 11}); await new Promise(r => m.once('idle', r)); }""", ADIS)
            pg.mouse.click((w + 360) // 2, h // 2)
            pg.wait_for_timeout(600)
            badge = None
            if pg.locator("[data-testid=to-studio]").count():
                pg.click("[data-testid=to-studio]")
                pg.wait_for_timeout(3500)
                bl = pg.locator(".scene-h .lv")
                badge = bl.first.inner_text() if bl.count() else None
                pg.screenshot(path=str(out / f"{w}_13_level_A.png"))
                pg.click("[data-testid=back]")
                pg.wait_for_timeout(800)
            # oil layer on (off by default)
            pg.click("[data-testid=nav-layers]")
            oil_default = pg.locator("[data-testid=oil-toggle] input").is_checked() if pg.locator("[data-testid=oil-toggle]").count() else None
            if pg.locator("[data-testid=oil-toggle]").count():
                pg.evaluate("""async () => { const m = window.__map; m.jumpTo({center: [-0.045, 5.40], zoom: 12}); await new Promise(r => m.once('idle', r)); }""")
                pg.locator("[data-testid=oil-toggle] input").check()
                pg.wait_for_timeout(2500)
                pg.screenshot(path=str(out / f"{w}_14_oil_layer.png"))
            n_oil = pg.evaluate("window.__map.getSource('oil') ? window.__map.querySourceFeatures('oil').length : 0")
            res[str(w)] = {"date_placeholder": ph, "level_badge": badge, "oil_default_checked": oil_default, "oil_features_in_view": n_oil, "console_errors": errs}
            pg.close()
        b.close()
    (out / "extra.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
