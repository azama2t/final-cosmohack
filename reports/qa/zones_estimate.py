"""QA agent 19, §34 п.4 (в): 5 random finds (seed 34) — research estimate shown in the UI card = formula §34 п.2
(n_pixels × [470; 670] / zone area km², point = geometric mean 561.16) = export CSV = API. Also: whether the zone list shows
шт./км² (§39 п.3: must not) and whether «нижняя граница» is still written (§39 п.3: remove).
usage: python reports/qa/zones_estimate.py <base> <stamp> <out.json>"""
import csv
import io
import json
import math
import random
import re
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

base, stamp, outp = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
img = Path(__file__).resolve().parent / "img" / f"ze_{stamp}"
img.mkdir(parents=True, exist_ok=True)
fc = json.loads(urllib.request.urlopen(base + "/api/v3/scene_zones").read())
finds = sorted(f["id"] for f in fc["features"] if f["properties"].get("is_find"))
pick = random.Random(34).sample(finds, 5)
byid = {f["id"]: f for f in fc["features"]}
t = urllib.request.urlopen(base + "/api/v3/export?layer=scene_zones&format=csv").read().decode("utf-8-sig")
csvby = {r["zone_id"]: r for r in csv.DictReader(io.StringIO(t))}


def r3(x):  # 3 significant digits, as the API rounds
    if x == 0:
        return 0
    return round(x, -int(math.floor(math.log10(abs(x)))) + 2)


def nums(s):
    s = (s or "").replace(" ", " ").replace(" ", " ")
    return [float(m.replace(" ", "").replace(",", ".")) for m in re.findall(r"\d{1,3}(?: \d{3})+|\d+(?:[.,]\d+)?", s)]


res = {"base": base, "n_finds": len(finds), "zones": []}
with sync_playwright() as pw:
    b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
    pg = b.new_page(viewport={"width": 1920, "height": 1080}, locale="ru-RU")
    errs = []
    pg.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
    for zid in pick:
        p = byid[zid]["properties"]
        m = p["measured"]
        re_ = p.get("research_estimate") or {}
        npx, area = m["n_pixels"], m["zone_area_km2"]
        formula = {"lo": r3(npx * 470 / area), "hi": r3(npx * 670 / area), "value": r3(npx * 561.16 / area)}
        api = {"lo": (re_.get("calibration_spread") or {}).get("lo", re_.get("lo")), "hi": (re_.get("calibration_spread") or {}).get("hi", re_.get("hi")),
               "value": re_.get("value"), "lower_bound": re_.get("lower_bound"), "label_short": re_.get("label_short")}
        c = csvby.get(zid, {})
        csvv = {"lo": c.get("research_calibration_spread_lo"), "hi": c.get("research_calibration_spread_hi"),
                "value": c.get("research_estimate_value"), "lower_bound": c.get("research_estimate_lower_bound")}
        row = {"zone_id": zid, "title": p.get("title"), "n_pixels": npx, "area_km2": area, "formula": formula, "api": api, "csv": csvv}
        pg.goto(f"{base}/?sel=zone:{zid}", wait_until="domcontentloaded")
        try:
            pg.wait_for_selector("[data-testid=scene-zone-card]", timeout=90000)
            pg.wait_for_timeout(2500)
            for sel in ("sz-plain-qty", "sz-est", "sz-est-ctx", "sz-qtop"):
                loc = pg.locator(f"[data-testid={sel}]")
                row[f"ui_{sel}"] = loc.first.inner_text()[:500] if loc.count() else None
            # open the collapsed research scenario, if any
            det = pg.locator("[data-testid=scene-zone-card]").get_by_text(re.compile("Исследовательский сценарий")).first
            if det.count():
                det.click()
                pg.wait_for_timeout(800)
                loc = pg.locator("[data-testid=sz-est]")
                row["ui_sz-est_open"] = loc.first.inner_text()[:800] if loc.count() else None
                row["ui_qtop_open"] = pg.locator("[data-testid=sz-qtop]").first.inner_text()[:1500]
            card = pg.locator("[data-testid=scene-zone-card]").first.inner_text()
            row["card_has_lower_bound_words"] = "нижняя граница" in card
            li = pg.locator("[data-testid=sz-item-est]")
            row["list_item_est_count"] = li.count()
            row["list_item_est_example"] = li.first.inner_text()[:120] if li.count() else None
            pg.screenshot(path=str(img / f"{zid}.png"))
            row["shot"] = f"reports/qa/img/ze_{stamp}/{zid}.png"
        except Exception as e:  # noqa: BLE001
            row["error"] = repr(e)[:300]
        ui_txt = " ".join(str(row.get(k) or "") for k in ("ui_sz-est", "ui_sz-est_open", "ui_sz-est-ctx", "ui_qtop_open", "ui_sz-plain-qty"))
        ui_nums = set(nums(ui_txt))
        row["formula_eq_api"] = formula["lo"] == api["lo"] and formula["hi"] == api["hi"] and formula["value"] == api["value"]
        row["api_eq_csv"] = all(str(api[k]) == str(csvv[k]) or (csvv[k] not in (None, "") and api[k] is not None and float(csvv[k]) == float(api[k]))
                                for k in ("lo", "hi", "value", "lower_bound"))
        row["ui_shows"] = sorted(x for x in ui_nums if x >= 100)
        row["ui_has_api_numbers"] = {k: (api[k] in ui_nums) for k in ("lo", "hi", "value", "lower_bound") if api[k] is not None}
        res["zones"].append(row)
        print(zid, "formula", formula, "api", {k: api[k] for k in ("lo", "hi", "value", "lower_bound")}, "csv", csvv, "ui", row["ui_shows"][:6], row["formula_eq_api"], row["api_eq_csv"])
    res["console_errors"] = errs[:10]
    b.close()
Path(outp).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
