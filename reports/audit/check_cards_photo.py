"""Audit L107 (§33 / jury fixes): (1) zone cards — a Cózar zone and an unverified live zone: «Количество предметов по этому
снимку не определено», «Состав не определён», no foreign items/km2 (field segments' C = N/A) and no material classes;
(2) scene_zones CSV/GeoJSON export — contract 3.10d; (3) «Фото» mode — «посчитано по детальному фото», domains, licences
(TOCL CC-BY-NC), «модель на этот домен не перенесена». Output -> reports/audit/cards_photo.json + ui_cp_*.png."""
import csv, io, json, re, sys, time, urllib.request
from pathlib import Path
from playwright.sync_api import sync_playwright
OUT = Path(__file__).resolve().parent
B = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8070").rstrip("/")
ITEMS = re.compile(r"\d[\d  ,.]*\s*(\[[^\]]*\]\s*)?шт\.?\s*/\s*км")
CLASSES = re.compile(r"\b(ПЭТ|PET|полиэтилен|полипропилен|пенопласт|бутылк|пакет|сет[ьи]|водоросл|Sargassum|саргасс)", re.I)
res = {}
# (2) export
csvb = urllib.request.urlopen(B + "/api/v3/export?layer=scene_zones&format=csv", timeout=120).read().decode("utf-8-sig")
rows = list(csv.DictReader(io.StringIO(csvb)))
cols = list(rows[0].keys()) if rows else []
res["csv"] = {"n": len(rows), "cols": cols, "items_cols": [c for c in cols if re.search("items|c_items|ci95|conc|scen|class|material|compos", c)],
              "sample_row": {c: rows[0][c] for c in cols if re.search("field|quantity|conc|compos", c)} if rows else None}
gj = urllib.request.urlopen(B + "/api/v3/export?layer=scene_zones&format=geojson", timeout=120).read().decode("utf-8")
g = json.loads(gj)
res["geojson"] = {"n": len(g["features"]), "has_c_items_km2": gj.count("c_items_km2"), "scenario_not_null": sum(1 for f in g["features"] if f["properties"].get("scenario"))}
sz = json.loads(urllib.request.urlopen(B + "/api/v3/scene_zones?limit=5000", timeout=120).read().decode("utf-8"))
live = [f["properties"]["zone_id"] for f in sz["features"] if f["properties"]["status"] == "detected" and f["properties"]["scene_kind"] == "live"]
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    page = b.new_context(viewport={"width": 1920, "height": 1080}, locale="ru-RU").new_page()
    errs = []
    page.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
    page.goto(B + "/", wait_until="domcontentloaded")
    page.wait_for_function("() => window.__app && window.__app.ready && window.__app.szReady", timeout=120000)
    cards = {}
    for tag, zid in (("cozar", None), ("live", live[0] if live else None)):
        if zid is None:
            page.click("[data-testid=tab-zones]"); page.locator("[data-testid=sz-item]").first.click()
        else:
            page.evaluate("(id) => window.__app.selectZone(id)", zid)
        page.wait_for_function("() => window.__app.szDetailReady && document.querySelector('[data-testid=scene-zone-card]')", timeout=60000)
        page.wait_for_timeout(2500)
        for sel in ("[data-testid=sz-more] summary", "[data-testid=sz-more]"):
            if page.locator(sel).count():
                try: page.locator(sel).first.click(); page.wait_for_timeout(500)
                except Exception: pass
                break
        page.screenshot(path=str(OUT / f"ui_cp_card_{tag}.png"))
        t = page.locator("[data-testid=scene-zone-card]").inner_text()
        plain = {k: (page.locator(f"[data-testid={k}]").inner_text() if page.locator(f"[data-testid={k}]").count() else None)
                 for k in ("sz-plain-what", "sz-plain-when", "sz-plain-qty", "sz-plain-comp", "sz-plain-conf", "sz-status")}
        cards[tag] = {"zone": zid or "first in list", "plain": plain,
                      "has_qty_phrase": "Количество предметов по этому снимку не определено" in t,
                      "has_comp_phrase": "Состав не определён" in t,
                      "items_km2": [m.group(0) for m in ITEMS.finditer(t)][:10],
                      "class_words": sorted({m.group(0) for m in CLASSES.finditer(t)}),
                      "text": t[:4000]}
    res["cards"] = cards
    # (3) photo mode
    page.click("[data-testid=mode-photo]")
    page.wait_for_selector("[data-testid=photo-app]", timeout=60000)
    page.wait_for_timeout(4000)
    page.screenshot(path=str(OUT / "ui_cp_photo_0.png"))
    samples = page.locator("[data-testid=photo-sample]")
    res["photo"] = {"n_samples": samples.count(), "start_text": page.locator("[data-testid=photo-app]").inner_text()[:3000], "runs": []}
    for i in range(min(samples.count(), 4)):
        samples.nth(i).click()
        try:
            page.wait_for_selector("[data-testid=photo-count]", timeout=120000)
        except Exception as e:
            res["photo"]["runs"].append({"i": i, "error": repr(e)[:200]}); continue
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT / f"ui_cp_photo_{i+1}.png"))
        t = page.locator("[data-testid=photo-app]").inner_text()
        g2 = lambda k: page.locator(f"[data-testid={k}]").inner_text() if page.locator(f"[data-testid={k}]").count() else None
        res["photo"]["runs"].append({"i": i, "sample_info": g2("photo-sample-info"), "warn": g2("photo-sample-warn"), "source": g2("photo-source"),
            "count": g2("photo-count"), "density": g2("photo-density"), "composition": g2("photo-composition"), "limits": g2("photo-limits"),
            "model": g2("photo-model"),
            "phrases": {p: (p.lower() in t.lower()) for p in ("посчитано по детальному фото", "модель на этот домен не перенесена", "CC-BY-NC", "CC BY-NC", "TOCL", "лиценз", "домен")},
            "satellite_words": [w for w in ("Sentinel", "спутник") if w in t]})
    res["console_errors"] = errs[:10]
    b.close()
(OUT / "cards_photo.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
short = json.loads(json.dumps(res))
for c in short.get("cards", {}).values(): c.pop("text", None)
short.get("photo", {}).pop("start_text", None)
print(json.dumps(short, ensure_ascii=False, indent=1)[:9000])
