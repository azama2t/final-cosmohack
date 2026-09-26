"""§34 п.3: live path on v2 without fixtures, «как Обзор районов» —
Earth overview with real finds → район/снимок in the left list → numbered zones → zone → card (research estimate) →
studio → back → another find by a map click → «Цифры» / «Фильтры» / «Проверка качества» one at a time → field layer →
filter (акватория, status) → export (list = map = CSV) → saved query → run → Earth.
Frames reports/case_demo/s34_<W>_*.png, result reports/case_demo/s34_path.json.

  .venv\\Scripts\\python.exe scripts\\case\\s34_path.py --base-url http://127.0.0.1:8070
"""
from __future__ import annotations

import argparse
import csv
import io
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


def fetch(url: str) -> bytes:
    return urllib.request.urlopen(url, timeout=60).read()


def run(base: str, W: int, H: int, pw, api: dict) -> dict:
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    ctx = b.new_context(viewport={"width": W, "height": H}, locale="ru-RU", accept_downloads=True)
    pg = ctx.new_page()
    errs, bad = [], []
    pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    pg.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.url.startswith(base) and r.status >= 400 else None)
    res: dict = {}

    def shot(n):
        pg.wait_for_timeout(1200)
        pg.screenshot(path=str(OUT / f"s34_{W}_{n}.png"))

    def tid(k):
        return pg.locator(f"[data-testid={k}]")

    def one_block():
        """«Фильтры», «Цифры», «Проверка качества»: never more than one open"""
        return {"filters": tid("f-source").count(), "nums": tid("headline").count(), "qc": tid("qc-panel").count()}

    pg.goto(base + "/", wait_until="domcontentloaded")
    pg.evaluate("() => { try { localStorage.removeItem('mp.case.filtersOpen') } catch (e) {} }")
    pg.reload(wait_until="domcontentloaded")
    pg.wait_for_function("() => window.__app && window.__app.ready && window.__app.szReady", timeout=120000)
    pg.wait_for_timeout(3500)
    res["mock"] = pg.evaluate("() => window.__app.mock")
    # 1. Earth: find points, the left list = snapshots «район · дата · N находок · облачность»
    res["earth"] = {
        "find_points": len(pg.evaluate("() => window.__app.findPoints()") or []),
        "tabs_removed": {k: tid(k).count() for k in ("mode-live", "tab-go", "tab-metrics", "tab-zones")},
        "modes": tid("mode-case").count() + tid("mode-photo").count(),
        "field_layer_off": not pg.evaluate("() => window.__app.q.layers.obs"),
        "scene_rows": tid("scene-item").count(),
        "first_rows": [tid("scene-item").nth(i).inner_text().replace("\n", " | ") for i in range(min(3, tid("scene-item").count()))],
        "counts": tid("counts").inner_text().replace("\n", " "),
        "one_block": one_block(),
    }
    rows = pg.evaluate("() => window.__app.sceneRows()")
    res["earth"]["rows_finds_sum"] = sum(r["finds"] for r in rows)
    res["earth"]["api_finds"] = api["n_finds"]
    shot("01_earth")
    # 2. район/снимок → image on the map + numbered zones
    tid("scene-item").first.click()
    pg.wait_for_timeout(3500)
    sz = pg.evaluate("() => window.__app.sceneZones()")
    res["scene"] = {
        "title": tid("scene-title").inner_text(),
        "zones_listed": tid("sz-item").count(),
        "numbers_on_map": tid("zone-num").count(),
        "with_estimate": sum(1 for z in sz if z["est"]),
        "item0": tid("sz-item").first.inner_text().replace("\n", " | ") if tid("sz-item").count() else None,
        "caption": tid("est-caption").inner_text() if tid("est-caption").count() else None,
    }
    shot("02_scene")
    # 3. zone → card
    tid("sz-item").first.click()
    pg.wait_for_function("() => window.__app.szDetailReady && document.querySelector('[data-testid=sz-plain]')", timeout=60000)
    pg.wait_for_timeout(2500)
    zid = pg.evaluate("() => window.__app.sel && window.__app.sel.id")
    card = {k: tid(k).inner_text() for k in ("card-title", "sz-plain-what", "sz-plain-qty", "sz-plain-comp", "sz-plain-area") if tid(k).count()}
    card["zone_id"] = zid
    feat = next((f for f in api["features"] if f["id"] == zid), None)
    re_api = (feat or {}).get("properties", {}).get("research_estimate")
    card["api_estimate"] = None if not re_api else {k: re_api.get(k) for k in ("value", "lo", "hi")}
    if re_api:
        fmt = lambda v: f"{int(v):,}".replace(",", " ")  # noqa: E731
        card["ui_equals_api"] = all(fmt(re_api[k]) in card.get("sz-plain-qty", "") for k in ("value", "lo", "hi"))
    res["card"] = card
    shot("03_card")
    # 4. studio → back to the card → back to the map
    tid("sz-studio").click()
    pg.wait_for_selector("[data-testid=zone-studio]", timeout=30000)
    shot("04_studio")
    tid("studio-card").click()
    pg.wait_for_timeout(800)
    tid("sz-back").click()
    pg.wait_for_timeout(2500)
    res["back"] = {"card_closed": tid("scene-zone-card").count() == 0, "scene_still_open": tid("scene-zones").count() == 1}
    shot("05_back")
    # 5. all snapshots → Earth; a find by a map click
    tid("scene-back").click()
    pg.wait_for_timeout(1500)
    tid("act-earth").click()
    pg.wait_for_timeout(3000)
    box = tid("main").bounding_box()
    opened = False
    for _ in range(7):
        pts = pg.evaluate("() => window.__app.findPoints()") or []
        if not pts:
            break
        single = [q for q in pts if not q["cluster"] and 0 < q["x"] < box["width"] - 380 and 60 < q["y"] < box["height"]]
        tgt = single[0] if single else pts[0]
        pg.mouse.click(box["x"] + tgt["x"], box["y"] + tgt["y"])
        pg.wait_for_timeout(2600)
        if tid("scene-zone-card").count():
            opened = True
            break
    res["map_click"] = {"card": opened, "sel": pg.evaluate("() => window.__app.sel"), "scene": pg.evaluate("() => window.__app.scene")}
    shot("06_map_click")
    if tid("sz-back").count():
        tid("sz-back").click()
        pg.wait_for_timeout(2500)
    res["map_click"]["back_to_list"] = tid("scene-list").count() == 1
    # 6. one block at a time: Фильтры → Цифры → Проверка качества
    tid("filters-toggle").click()
    pg.wait_for_timeout(500)
    b1 = one_block()
    tid("headline-open").click()
    pg.wait_for_timeout(700)
    b2 = one_block()
    shot("07_nums")
    tid("act-qc").click()
    pg.wait_for_function("() => window.__app.metricsReady", timeout=60000)
    pg.wait_for_timeout(700)
    b3 = one_block()
    shot("08_qc")
    tid("qc-close").click()
    pg.wait_for_timeout(400)
    res["blocks"] = {"filters": b1, "nums": b2, "qc": b3, "ok": b1 == {"filters": 1, "nums": 0, "qc": 0} and b2 == {"filters": 0, "nums": 1, "qc": 0} and b3 == {"filters": 0, "nums": 0, "qc": 1}}
    # 7. field layer: one button on/off, profile/scope inside
    tid("act-field").click()
    pg.wait_for_timeout(2500)
    res["field"] = {"panel": tid("field-panel").count(), "profile_inside": tid("field-panel").locator("[data-testid=f-profile]").count(), "on": pg.evaluate("() => window.__app.q.layers.obs")}
    shot("09_field")
    tid("act-field").click()
    pg.wait_for_timeout(1000)
    res["field"]["off_again"] = not pg.evaluate("() => window.__app.q.layers.obs") and tid("field-panel").count() == 0
    # 8. filters: акватория (район) + status «находка» → list = map = export
    tid("filters-toggle").click()
    pg.wait_for_timeout(400)
    opts = pg.evaluate("() => [...document.querySelectorAll('[data-testid=f-source] option')].map(o => o.value).filter(v => v.startsWith('r:'))")
    area = "r:honduras" if "r:honduras" in opts else (opts[0] if opts else None)
    if area:
        tid("f-source").select_option(area)
    tid("f-det-detected").click()
    pg.wait_for_function("() => window.__app.szReady", timeout=60000)
    pg.wait_for_timeout(3000)
    n_ui = pg.evaluate("() => window.__app.counts.szones")
    ids_ui = sorted(pg.evaluate("() => window.__app.szIds()"))
    href = pg.evaluate("() => window.__app.exportUrl('scene_zones', 'csv')")
    csv_rows = list(csv.DictReader(io.StringIO(fetch(href if href.startswith("http") else base + href).decode("utf-8-sig"))))
    ids_csv = sorted(r.get("zone_id") for r in csv_rows)
    res["filter_export"] = {"area": area, "n_ui": n_ui, "n_csv": len(csv_rows), "same_ids": ids_ui == ids_csv,
                            "rows_after_filter": tid("scene-item").count(), "counts": tid("counts").inner_text().replace("\n", " ")}
    tid("act-export").click()
    pg.wait_for_timeout(600)
    shot("10_export")
    tid("act-export").click()
    # 9. saved query → reset → run → the same zones
    tid("act-queries").click()
    tid("q-name").fill(f"s34 {W}")
    tid("q-save").click()
    pg.wait_for_timeout(1500)
    tid("f-reset").click()
    pg.wait_for_function("() => window.__app.szReady", timeout=60000)
    pg.wait_for_timeout(2500)
    n_reset = pg.evaluate("() => window.__app.counts.szones")
    tid("act-queries").click()
    pg.locator("[data-testid=q-item]", has_text=f"s34 {W}").first.locator("[data-testid=q-run]").click()
    pg.wait_for_function("() => window.__app.szReady", timeout=60000)
    pg.wait_for_timeout(3000)
    ids_run = sorted(pg.evaluate("() => window.__app.szIds()"))
    res["query"] = {"n_after_reset": n_reset, "n_after_run": len(ids_run), "same_as_saved": ids_run == ids_ui,
                    "area_after_run": pg.evaluate("() => window.__app.q.area"), "toast": tid("toast").inner_text() if tid("toast").count() else None}
    shot("11_query_run")
    # clean up the saved query (server side)
    tid("act-queries").click()
    row = pg.locator("[data-testid=q-item]", has_text=f"s34 {W}").first
    if row.count():
        row.locator("[data-testid=q-del]").click()
        pg.wait_for_timeout(800)
    tid("f-reset").click() if tid("f-reset").count() else None
    tid("act-earth").click()
    pg.wait_for_timeout(2500)
    shot("12_earth_end")
    res["console_errors"] = [e for e in errs if "arcgisonline" not in e and "carto" not in e][:10]
    res["http_errors"] = bad[:10]
    b.close()
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8070")
    ap.add_argument("--sizes", default="1366x768,1920x1080")
    a = ap.parse_args()
    fc = json.loads(fetch(a.base_url + "/api/v3/scene_zones?limit=1000"))
    finds = [f for f in fc["features"] if f["properties"].get("is_find", f["properties"]["detection_status"] == "detected")]
    api = {"n_finds": len(finds), "features": fc["features"]}
    out = {"base": a.base_url, "n_finds_api": len(finds),
           "n_finds_with_estimate_api": sum(1 for f in finds if f["properties"].get("research_estimate")),
           "n_nonfinds_with_estimate_api": sum(1 for f in fc["features"] if f not in finds and f["properties"].get("research_estimate")),
           "n_zones_api": fc.get("total", fc.get("count"))}
    with sync_playwright() as pw:
        for sz in a.sizes.split(","):
            W, H = (int(x) for x in sz.split("x"))
            try:
                out[str(W)] = run(a.base_url, W, H, pw, api)
            except Exception as e:  # noqa: BLE001
                out[str(W)] = {"error": repr(e)[:800]}
    (OUT / "s34_path.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:9000])


if __name__ == "__main__":
    main()
