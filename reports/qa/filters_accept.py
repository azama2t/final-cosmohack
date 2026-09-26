"""QA agent 19, приёмка Матвея п.2 (фильтры и выгрузка): 7 scenarios on the live UI.
For each: the screen (window.__app.szIds / counts, «N находок» text, the number in the export menu, the list of snapshots),
the API request the UI made (/api/v3/scene_zones?...) re-requested directly, and the two export links taken from the
open export menu (CSV, GeoJSON) — compared by id set, count, geometry (GeoJSON vs API), status fields, is_find.
Observations layer: UI count vs export CSV / GeoJSON count for the same filter.

usage: python reports/qa/filters_accept.py --base http://127.0.0.1:8070 --stamp 1540 --out out/qa19/filters_1540.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

from playwright.sync_api import sync_playwright

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def rows_csv(text):
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8070")
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    base = a.base.rstrip("/")
    img = Path(__file__).resolve().parent / "img" / f"fa_{a.stamp}"
    img.mkdir(parents=True, exist_ok=True)
    res = {"base": base, "started": time.strftime("%Y-%m-%d %H:%M:%S"), "scenarios": {}}

    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        pg = b.new_page(viewport={"width": 1920, "height": 1080}, locale="ru-RU")
        errs, bad, sz_reqs = [], [], []
        pg.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
        pg.on("response", lambda r: bad.append(f"{r.status} {r.url[:150]}") if r.url.startswith(base) and r.status >= 400 else None)
        pg.on("request", lambda r: sz_reqs.append(r.url) if urllib.parse.urlparse(r.url).path == "/api/v3/scene_zones" else None)

        def ready():
            pg.wait_for_function("() => window.__app && window.__app.ready && window.__app.szReady", timeout=120000)
            pg.wait_for_timeout(2500)

        def open_filters():
            if not pg.locator("[data-testid=f-source]").count():
                pg.click("[data-testid=filters-toggle]")
                pg.wait_for_selector("[data-testid=f-source]")

        def capture(name):
            pg.wait_for_timeout(2000)  # debounced filter inputs
            ready()
            last, same = None, 0
            for _ in range(30):  # data settled: the same ids + count three reads in a row, requests idle
                pg.wait_for_load_state("networkidle")
                cur = pg.evaluate("() => [window.__app.szReady, window.__app.counts.szones, window.__app.szIds().length, JSON.stringify(window.__app.q)]")
                same = same + 1 if cur == last and cur[0] else 0
                last = cur
                if same >= 2:
                    break
                pg.wait_for_timeout(1000)
            st = {}
            st["ui_ids"] = pg.evaluate("() => window.__app.szIds()")
            st["ui_counts"] = pg.evaluate("() => window.__app.counts")
            st["q"] = pg.evaluate("() => window.__app.q")
            cs = pg.locator("[data-testid=count-szones]")
            st["ui_count_text"] = cs.first.inner_text() if cs.count() else None
            st["ui_scene_rows"] = pg.evaluate("() => window.__app.sceneRows ? window.__app.sceneRows() : null")
            er = pg.locator("[data-testid=empty-result]")
            st["ui_empty_text"] = er.first.inner_text()[:300] if er.count() else None
            pg.screenshot(path=str(img / f"{name}_screen.png"))
            # export menu as the user sees it
            pg.click("[data-testid=act-export]")
            pg.wait_for_selector("[data-testid=export-menu]")
            pg.wait_for_timeout(400)
            st["menu_text"] = pg.locator("[data-testid=export-menu]").inner_text()[:600]
            links = {}
            for lay in ("scene_zones", "observations"):
                for fmt in ("csv", "geojson"):
                    loc = pg.locator(f"[data-testid=export-{lay}-{fmt}]")
                    links[f"{lay}.{fmt}"] = (base + loc.get_attribute("href")) if loc.count() and loc.get_attribute("href").startswith("/") else (loc.get_attribute("href") if loc.count() else None)
            pg.screenshot(path=str(img / f"{name}_export_menu.png"))
            pg.keyboard.press("Escape")
            pg.mouse.click(1000, 1000)
            st["export_links"] = links
            # API request the UI used for the list/map
            st["ui_api_url"] = sz_reqs[-1] if sz_reqs else None
            api = pg.request.get(st["ui_api_url"]).json() if st["ui_api_url"] else None
            api_feats = {f["id"]: f for f in (api or {}).get("features", [])}
            ex = {}
            for k, u in links.items():
                if not u:
                    continue
                r = pg.request.get(u)
                ex[k] = {"status": r.status, "body": r.text()}
            csv_rows = rows_csv(ex["scene_zones.csv"]["body"]) if ex.get("scene_zones.csv", {}).get("status") == 200 else []
            gj = json.loads(ex["scene_zones.geojson"]["body"]) if ex.get("scene_zones.geojson", {}).get("status") == 200 else {"features": []}
            gj_feats = {f["id"]: f for f in gj["features"]}
            csv_ids = [r.get("zone_id") for r in csv_rows]
            ui_ids = set(st["ui_ids"])
            chk = {
                "n_ui": len(ui_ids), "n_ui_counts": st["ui_counts"].get("szones"), "n_api": len(api_feats),
                "n_csv": len(csv_rows), "n_geojson": len(gj_feats),
                "ids_ui_eq_api": ui_ids == set(api_feats), "ids_ui_eq_csv": ui_ids == set(csv_ids),
                "ids_ui_eq_geojson": ui_ids == set(gj_feats), "csv_dup_ids": len(csv_ids) - len(set(csv_ids)),
                "geom_geojson_eq_api": all(gj_feats[i]["geometry"] == api_feats[i]["geometry"] for i in gj_feats if i in api_feats),
            }
            # statuses / is_find / confirmation between API, CSV, GeoJSON
            st_mis = []
            csv_by = {r.get("zone_id"): r for r in csv_rows}
            for i, f in api_feats.items():
                p = f["properties"]
                g = gj_feats.get(i, {}).get("properties", {})
                c = csv_by.get(i, {})
                for key in ("detection_status", "status", "is_find", "training_scene", "confirmation", "verification"):
                    if key in p:
                        pv = p.get(key)
                        if key in g and g.get(key) != pv:
                            st_mis.append(f"{i} {key} api={pv} geojson={g.get(key)}")
                        if key in c and str(c.get(key)).lower() not in (str(pv).lower(), "" if pv is None else "\x00"):
                            st_mis.append(f"{i} {key} api={pv} csv={c.get(key)}")
            chk["status_mismatches"] = st_mis[:10]
            chk["n_status_mismatches"] = len(st_mis)
            n_find_api = sum(1 for f in api_feats.values() if f["properties"].get("is_find"))
            m = re.search(r"(\d[\d\s]*)\s*наход", (st["ui_count_text"] or "").replace(" ", " "))
            chk["finds_screen"] = int(m.group(1).replace(" ", "")) if m else None
            chk["finds_api_is_find"] = n_find_api
            chk["finds_csv_is_find"] = sum(1 for r in csv_rows if str(r.get("is_find")).lower() in ("true", "1"))
            chk["csv_has_fields"] = {k: (k in (csv_rows[0] if csv_rows else {})) for k in
                                     ("is_find", "training_scene", "status", "confirmation", "class", "excluded_backgrounds",
                                      "scenario_status")}
            mm = re.search(r"Спутниковые зоны\s*·\s*([\d\s]+)", st["menu_text"].replace(" ", " "))
            chk["menu_n_szones"] = int(mm.group(1).replace(" ", "").strip()) if mm else None
            # observations layer
            oc = rows_csv(ex["observations.csv"]["body"]) if ex.get("observations.csv", {}).get("status") == 200 else None
            og = json.loads(ex["observations.geojson"]["body"]) if ex.get("observations.geojson", {}).get("status") == 200 else None
            chk["obs_ui"] = st["ui_counts"].get("obs")
            chk["obs_csv"] = len(oc) if oc is not None else None
            chk["obs_geojson"] = len(og["features"]) if og else None
            chk["export_http"] = {k: v["status"] for k, v in ex.items()}
            chk["ok"] = (chk["ids_ui_eq_api"] and chk["ids_ui_eq_csv"] and chk["ids_ui_eq_geojson"] and chk["geom_geojson_eq_api"]
                         and chk["n_status_mismatches"] == 0 and chk["csv_dup_ids"] == 0
                         and (chk["finds_screen"] is None or chk["finds_screen"] == chk["finds_api_is_find"])
                         and (chk["menu_n_szones"] is None or chk["menu_n_szones"] == len(api_feats))
                         and (chk["obs_csv"] is None or chk["obs_ui"] is None or chk["obs_csv"] == chk["obs_ui"] == chk["obs_geojson"]))
            st["check"] = chk
            st.pop("ui_ids")
            res["scenarios"][name] = st
            print(name, json.dumps({k: chk[k] for k in ("ok", "n_ui", "n_api", "n_csv", "n_geojson", "finds_screen", "finds_api_is_find",
                                                        "menu_n_szones", "obs_ui", "obs_csv", "obs_geojson", "n_status_mismatches")}, ensure_ascii=False))
            return st

        def reset():
            pg.goto(base + "/", wait_until="domcontentloaded")
            ready()

        # 1 no filters
        reset()
        capture("1_none")
        # options of Акватория
        open_filters()
        opts = pg.evaluate("""() => [...document.querySelectorAll('[data-testid=f-source] option')].map(o => ({v: o.value, t: o.textContent, g: o.parentElement.label || ''}))""")
        res["area_options"] = opts
        reg = next((o for o in opts if o["v"] == "r:honduras"), None) or next((o for o in opts if o["g"].startswith("Районы") and o["v"]), None)
        fld = next((o for o in opts if o["g"].startswith("Полевые") and o["v"]), None)
        # 2a area = snapshot region
        if reg:
            pg.select_option("[data-testid=f-source]", reg["v"])
            capture("2a_area_region")
        # 2b area = field source
        reset(); open_filters()
        if fld:
            pg.select_option("[data-testid=f-source]", fld["v"])
            capture("2b_area_field")
        # 3 dates only: the year of the region's scenes
        reset(); open_filters()
        pg.fill("[data-testid=f-from]", "2025-01-01")
        pg.fill("[data-testid=f-to]", "2025-12-31")
        capture("3_dates")
        # 4 area + dates
        if reg:
            reset(); open_filters()
            pg.select_option("[data-testid=f-source]", reg["v"])
            pg.fill("[data-testid=f-from]", "2026-01-01")
            pg.fill("[data-testid=f-to]", "2026-12-31")
            capture("4_area_dates")
        # 5 status = находки (detected)
        reset(); open_filters()
        chips = pg.evaluate("() => [...document.querySelectorAll('[data-testid^=f-det-]')].map(e => e.dataset.testid)")
        res["status_chips"] = chips
        pg.click("[data-testid=f-det-detected]" if "f-det-detected" in chips else f"[data-testid={chips[0]}]")
        capture("5_status_detected")
        # 6 empty
        reset(); open_filters()
        pg.fill("[data-testid=f-from]", "2019-06-01")
        pg.fill("[data-testid=f-to]", "2019-06-02")
        capture("6_empty")
        # 7 save → reset → rerun
        reset(); open_filters()
        if reg:
            pg.select_option("[data-testid=f-source]", reg["v"])
        pg.fill("[data-testid=f-from]", "2026-01-01")
        pg.fill("[data-testid=f-to]", "2026-12-31")
        before = capture("7a_before_save")
        qname = f"QA19 приёмка {a.stamp}"
        pg.click("[data-testid=act-queries]")
        pg.fill("[data-testid=q-name]", qname)
        pg.click("[data-testid=q-save]")
        pg.wait_for_timeout(1200)
        pg.keyboard.press("Escape")
        reset()
        pg.click("[data-testid=act-queries]")
        pg.locator("[data-testid=q-item]", has_text=qname).first.locator("[data-testid=q-run]").click()
        pg.wait_for_timeout(1500)
        after = capture("7b_after_rerun")
        res["rerun_same_ids"] = before["check"]["n_ui"] == after["check"]["n_ui"] and before["check"]["ids_ui_eq_api"] and after["check"]["ids_ui_eq_api"]
        res["rerun_q_before"], res["rerun_q_after"] = before["q"], after["q"]
        # cleanup of the saved query
        try:
            ql = pg.request.get(base + "/api/v3/queries").json()
            for qq in ql.get("queries", []):
                if qq.get("name") == qname:
                    pg.request.delete(base + f"/api/v3/queries/{qq['query_id']}")
        except Exception as e:  # noqa: BLE001
            res["cleanup_error"] = repr(e)[:200]
        res["console_errors"], res["http_errors"] = errs[:20], bad[:20]
        b.close()
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("rerun_same_ids", res.get("rerun_same_ids"), "console", len(errs), "http", len(bad))


if __name__ == "__main__":
    main()
