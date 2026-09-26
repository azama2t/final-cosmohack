"""§33: live path on v2 without fixtures — Earth overview with real finds → point → card → studio → back → another point.
Frames reports/case_demo/s33_<W>_*.png, result reports/case_demo/s33_path.json.

  .venv\\Scripts\\python.exe scripts\\case\\s33_path.py --base-url http://127.0.0.1:8070
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "case_demo"


def cam(pg):
    return pg.evaluate("() => { const m = window.__app && window.__app.map ? window.__app.map : null; return null }")


def run(base: str, W: int, H: int, pw, api_finds: set) -> dict:
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    pg = b.new_page(viewport={"width": W, "height": H}, locale="ru-RU")
    errs, bad = [], []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.url.startswith(base) and r.status >= 400 else None)
    res = {}

    def shot(n):
        pg.wait_for_timeout(1200)
        pg.screenshot(path=str(OUT / f"s33_{W}_{n}.png"))

    pg.goto(base + "/", wait_until="domcontentloaded")
    pg.wait_for_function("() => window.__app && window.__app.ready && window.__app.szReady", timeout=120000)
    pg.wait_for_timeout(3500)
    res["mock"] = pg.evaluate("() => window.__app.mock")
    # points actually rendered on the overview = the find layer (clusters + single finds)
    rendered = pg.evaluate("""() => { const m = window.__ctlMap || null; return null }""")
    res["headline_collapsed"] = pg.locator("[data-testid=headline-open]").count() == 1
    # L125 blocker: the left column fits the window, the tabs are reachable
    res["left_fits"] = pg.evaluate("""() => { const l = document.querySelector('[data-panel=left]'); const t = document.querySelector('[data-testid=tab-metrics]').getBoundingClientRect();
      return { left_h: l.scrollHeight, win_h: innerHeight, tab_bottom: Math.round(t.bottom), ok: t.bottom <= innerHeight && l.scrollHeight <= innerHeight + 1 }; }""")
    res["filters_folded"] = pg.locator("[data-testid=f-source]").count() == 0
    shot("01_earth")
    # a find from the list (first Cózar-B zone) — the card, as a click on a point does
    # click a real point on the map: find the rendered position of a find via the API centroid
    item = pg.locator("[data-testid=sz-item]").first
    item.click()
    pg.wait_for_function("() => window.__app.szDetailReady && document.querySelector('[data-testid=sz-plain]')", timeout=60000)
    pg.wait_for_timeout(2500)
    res["card"] = {k: pg.locator(f"[data-testid={k}]").inner_text() for k in
                   ("card-title", "sz-plain-what", "sz-plain-when", "sz-plain-quality", "sz-plain-area", "sz-plain-cover",
                    "sz-plain-conf", "sz-plain-qty", "sz-plain-comp", "sz-field-link")}
    res["card"]["thumb"] = pg.locator("[data-testid=sz-thumb]").count()
    res["card"]["no_far_field_numbers"] = pg.locator("[data-testid=sz-field-row]").count() == 0
    shot("02_card")
    pg.click("[data-testid=sz-studio]")
    pg.wait_for_selector("[data-testid=zone-studio]", timeout=30000)
    pg.wait_for_timeout(2500)
    shot("03_studio")
    pg.click("[data-testid=studio-back]")
    pg.wait_for_timeout(2500)
    res["back_card_closed"] = pg.locator("[data-testid=scene-zone-card]").count() == 0
    shot("04_back")
    # another find: second region — a find from another scene (unverified)
    pg.click("[data-testid=act-earth]")
    pg.wait_for_timeout(2500)
    shot("05_earth_again")
    other = pg.locator("[data-testid=sz-item]", has_text="требует проверки").first
    if other.count():
        other.click()
        pg.wait_for_function("() => window.__app.szDetailReady && document.querySelector('[data-testid=sz-plain]')", timeout=60000)
        pg.wait_for_timeout(2500)
        res["other_card"] = {k: pg.locator(f"[data-testid={k}]").inner_text() for k in ("card-title", "sz-plain-conf", "sz-plain-qty")}
        shot("06_other_card")
        pg.click("[data-testid=sz-back]")
        pg.wait_for_timeout(2000)
    # map click on a real find point (hover tooltip) — overview, search the canvas for a find
    pg.click("[data-testid=act-earth]")
    pg.wait_for_timeout(2500)
    hit = pg.evaluate("""() => {
      const a = window.__app; if (!a || !a.findPoints) return null; return a.findPoints();
    }""")
    res["find_points_screen"] = hit[:5] if hit else None
    if hit:
        x, y = hit[0]["x"], hit[0]["y"]
        box = pg.locator("[data-testid=main]").bounding_box()
        pg.mouse.move(box["x"] + x, box["y"] + y)
        pg.wait_for_timeout(700)
        res["hover"] = pg.locator("[data-testid=hover-tip]").inner_text() if pg.locator("[data-testid=hover-tip]").count() else None
        shot("07_hover")
        for _ in range(6):  # clusters: click until a single find is under the cursor, then open it
            pts = pg.evaluate("() => window.__app.findPoints()")
            if not pts:
                break
            single = [q for q in pts if not q["cluster"] and 0 < q["x"] < box["width"] - 380 and 0 < q["y"] < box["height"]]
            tgt = single[0] if single else pts[0]
            pg.mouse.click(box["x"] + tgt["x"], box["y"] + tgt["y"])
            pg.wait_for_timeout(2500)
            if pg.locator("[data-testid=scene-zone-card]").count():
                break
        res["after_point_click"] = {"card": pg.locator("[data-testid=scene-zone-card]").count(), "sel": pg.evaluate("() => window.__app.sel")}
        shot("08_point_click")
        if pg.locator("[data-testid=sz-back]").count():
            pg.click("[data-testid=sz-back]")
            pg.wait_for_timeout(2000)
            res["after_back_points"] = len(pg.evaluate("() => window.__app.findPoints()") or [])
            shot("09_back_to_points")
    res["console_errors"] = [e for e in errs if "arcgisonline" not in e and "carto" not in e][:10]
    res["http_errors"] = bad[:10]
    b.close()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8070")
    a = ap.parse_args()
    fc = json.loads(urllib.request.urlopen(a.base_url + "/api/v3/scene_zones?limit=1000", timeout=60).read())
    finds = {f["id"] for f in fc["features"] if f["properties"].get("is_find", f["properties"]["detection_status"] == "detected")}
    out = {"base": a.base_url, "n_finds_api": len(finds),
           "n_finds_b": sum(1 for f in fc["features"] if f["properties"].get("verification") == "level_B_cozar"),
           "n_zones_api": fc["total"]}
    with sync_playwright() as pw:
        for W, H in ((1366, 768), (1920, 1080)):
            try:
                out[str(W)] = run(a.base_url, W, H, pw, finds)
            except Exception as e:  # noqa: BLE001
                out[str(W)] = {"error": repr(e)[:600]}
    (OUT / "s33_path.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:5000])


if __name__ == "__main__":
    main()
