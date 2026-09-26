"""Audit L107 (§23 recheck): independent live walk of the 30SXE demo path in v2 (no mocks), auditor service :8096.
Map -> zones tab -> first zone (held-out Cózar scene) -> card text (must say «концентрация по снимку не подтверждена», no
satellite items/km2 numbers) -> false-alarm example -> date filter 2021-03-11 -> export CSV/GeoJSON (count = UI = API, no
scenario columns) -> save query -> reset -> rerun (same count). Frames and JSON -> reports/audit/ui_p3_*."""
import csv, io, json, re, sys, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "audit"
B = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8096"
BAD = ["10 000", "10000", "10⁴", "10⁵", "10⁸", "100 млн", "100 000 000", "500 000", "Условный диапазон", "ЕСЛИ это", "мусорная полоса"]
api = lambda p: json.loads(urllib.request.urlopen(B + p, timeout=60).read().decode("utf-8"))
res = {"api_demo_day": api("/api/v3/scene_zones?date_from=2021-03-11&date_to=2021-03-11")["total"]}
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    ctx = b.new_context(viewport={"width": 1920, "height": 1080}, locale="ru-RU", accept_downloads=True)
    page = ctx.new_page()
    errs, bad = [], []
    page.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
    page.on("response", lambda r: bad.append(f"{r.status} {r.url}") if r.url.startswith(B) and r.status >= 400 else None)
    shot = lambda n: (page.wait_for_timeout(900), page.screenshot(path=str(OUT / f"ui_p3_{n}.png")))
    page.goto(B + "/", wait_until="domcontentloaded")
    page.wait_for_function("() => window.__app && window.__app.ready && window.__app.szReady", timeout=120000)
    res["open"] = page.evaluate("() => ({mock: window.__app.mock, counts: window.__app.counts})")
    shot("01_map")
    page.click("[data-testid=tab-zones]")
    items = page.locator("[data-testid=sz-item]")
    res["zone_list_first3"] = [items.nth(i).inner_text().replace("\n", " · ")[:160] for i in range(min(3, items.count()))]
    items.first.click()
    page.wait_for_function("() => window.__app.szDetailReady && document.querySelector('[data-testid=scene-zone-card]')", timeout=60000)
    page.wait_for_timeout(2500)
    shot("02_demo_card")
    card = page.locator("[data-testid=scene-zone-card]").inner_text()
    if page.locator("[data-testid=sz-quantity-more] summary").count():
        page.click("[data-testid=sz-quantity-more] summary"); page.wait_for_timeout(400)
        card = page.locator("[data-testid=scene-zone-card]").inner_text()
        shot("03_demo_card_quantity")
    res["demo_card_text"] = card
    res["demo_card_has_not_confirmed"] = "концентрация по снимку не подтверждена" in card.lower()
    res["demo_card_bad_strings"] = [x for x in BAD if x in card]
    res["demo_card_items_km2_lines"] = [l for l in card.split("\n") if "шт./км" in l]
    page.click("[data-testid=sz-example-false_alarm]")
    page.wait_for_function("() => window.__app.szDetailReady && document.querySelector('[data-testid=sz-status]') && document.querySelector('[data-testid=sz-status]').innerText.startsWith('ложное')", timeout=60000)
    page.wait_for_timeout(2500)
    fa = page.locator("[data-testid=scene-zone-card]").inner_text()
    res["false_alarm"] = {"status": page.locator("[data-testid=sz-status]").inner_text(), "bad_strings": [x for x in BAD if x in fa]}
    shot("04_false_alarm")
    page.fill("[data-testid=f-from]", "2021-03-11"); page.fill("[data-testid=f-to]", "2021-03-11")
    page.wait_for_function("() => window.__app.ready && window.__app.q.to === '2021-03-11' && window.__app.szReady && window.__app.counts.szones !== null && window.__app.counts.szones < 286", timeout=60000)
    n_ui = page.evaluate("() => window.__app.counts.szones")
    page.click("[data-testid=act-export]")
    exp = {}
    for fmt in ("csv", "geojson"):
        with page.expect_download() as dl:
            page.click(f"[data-testid=export-scene_zones-{fmt}]")
        p = OUT / f"ui_p3_export.{fmt}"; dl.value.save_as(str(p)); body = p.read_text(encoding="utf-8-sig")
        if fmt == "csv":
            rows = list(csv.DictReader(io.StringIO(body))); exp["csv"] = len(rows)
            exp["csv_scenario_cols"] = [c for c in (rows[0].keys() if rows else []) if re.search("scen|typical|conc", c)]
            exp["csv_scenario_nonempty"] = sum(1 for r in rows for c in exp["csv_scenario_cols"] if r[c] not in ("", "null", "None", "unavailable", "not_confirmed") and "scen" in c)
        else:
            fs = json.loads(body)["features"]; exp["geojson"] = len(fs)
            exp["geojson_scenario_not_null"] = sum(1 for f in fs if f["properties"].get("scenario"))
            exp["geojson_bad_strings"] = sorted({x for x in BAD if x in body})
    res["export"] = {"ui": n_ui, "api": res["api_demo_day"], **exp}
    page.keyboard.press("Escape"); page.mouse.click(960, 40)
    page.click("[data-testid=act-queries]"); page.fill("[data-testid=q-name]", "Аудит L107 30SXE"); page.click("[data-testid=q-save]")
    page.wait_for_timeout(1200); page.keyboard.press("Escape")
    page.click("[data-testid=f-reset]")
    page.wait_for_function("() => window.__app.ready && window.__app.q.from === null && window.__app.szReady && window.__app.counts.szones > 23", timeout=60000)
    after_reset = page.evaluate("() => window.__app.counts.szones")
    page.click("[data-testid=act-queries]")
    page.locator("[data-testid=q-item]", has_text="Аудит L107 30SXE").first.locator("[data-testid=q-run]").click()
    page.wait_for_function("() => window.__app.ready && window.__app.q.from === '2021-03-11' && window.__app.szReady", timeout=60000)
    page.wait_for_timeout(1500)
    res["rerun"] = {"after_reset": after_reset, "szones": page.evaluate("() => window.__app.counts.szones")}
    shot("05_rerun")
    res["console_errors"] = [e for e in errs if "arcgisonline" not in e and "carto" not in e][:10]
    res["http_errors"] = bad[:10]
    b.close()
(OUT / "ui_p3_30sxe.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: v for k, v in res.items() if k != "demo_card_text"}, ensure_ascii=False, indent=1))
