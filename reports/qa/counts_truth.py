"""QA agent 19, INBOX §34 п.4 «фронт не врёт».
(а) 20 random field points (seed 34) of the UI observation layer: value shown in the UI card vs organizers' CSV
    (task/macroplastic_marine_samples.csv, sample_id -> concentration_items_km2).
(б) 10 random frames (seed 34) of the held-out FML grouped test (data/extra/fml/split_grouped.json, images.test):
    human reference boxes (YOLO labels) vs the number the UI «Фото» shows after uploading the same file.
Screens -> reports/qa/img/ct_<stamp>/, JSON -> --out.

usage: python reports/qa/counts_truth.py --base http://127.0.0.1:8070 --stamp 1310 --out out/qa19/counts_truth.json
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
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
FML = ROOT / "data" / "extra" / "fml" / "fml_version2" / "full_dataset"


def num(s):
    """first number in a UI string, ru formatting (spaces / nbsp as thousands, comma decimal)."""
    s = s.replace(" ", " ").replace(" ", " ")
    m = re.search(r"(\d[\d ]*(?:[.,]\d+)?)", s)
    return float(m.group(1).replace(" ", "").replace(",", ".")) if m else None


def fml_image(stem):
    for sp in ("train", "val", "test"):
        p = FML / "images" / sp / f"{stem}.jpg"
        if p.is_file():
            return p, FML / "labels" / "yolo_format" / sp / f"{stem}.txt"
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8070")
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=34)
    a = ap.parse_args()
    img = Path(__file__).resolve().parent / "img" / f"ct_{a.stamp}"
    img.mkdir(parents=True, exist_ok=True)
    rng = random.Random(a.seed)

    csvrows = {r["sample_id"]: r for r in csv.DictReader(open(ROOT / "task/macroplastic_marine_samples.csv", encoding="utf-8"))}
    fc = json.loads(urllib.request.urlopen(a.base + "/api/v3/observations?limit=5000", timeout=120).read())
    cand = [f["properties"]["sample_id"] for f in fc["features"]
            if f["properties"].get("concentration_items_km2") is not None]
    pick = rng.sample(sorted(cand), 20)

    split = json.loads((ROOT / "data/extra/fml/split_grouped.json").read_text())
    test = sorted(split["images"]["test"])
    frames = rng.sample(test, 10)

    res = {"base": a.base, "seed": a.seed, "n_obs_ui": len(fc["features"]), "n_obs_with_conc": len(cand),
           "field": [], "photo": []}
    with sync_playwright() as pw:
        b = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        pg = b.new_page(viewport={"width": 1920, "height": 1080}, locale="ru-RU")
        errs = []
        pg.on("console", lambda m: errs.append(m.text[:200]) if m.type == "error" else None)
        # (а) field points
        for sid in pick:
            row = {"sample_id": sid, "csv": csvrows.get(sid, {}).get("concentration_items_km2")}
            try:
                pg.goto(f"{a.base}/?sel=obs:{sid}", wait_until="domcontentloaded")
                pg.wait_for_selector("[data-testid=obs-card]", timeout=90000)
                pg.wait_for_timeout(1500)
                title = pg.locator("[data-testid=panel-title], [data-testid=card-title]").first
                row["ui_title"] = title.inner_text()[:120] if title.count() else None
                card = pg.locator("[data-testid=obs-card]").first.inner_text()
                row["ui_sid_in_card"] = sid in card or sid in pg.content()
                conc = pg.locator("[data-testid=obs-conc]")
                row["ui_conc_text"] = conc.first.inner_text()[:200] if conc.count() else None
                row["ui_value"] = num(row["ui_conc_text"]) if row["ui_conc_text"] else None
                intr = pg.locator("[data-testid=obs-interval]")
                row["ui_interval"] = intr.first.inner_text()[:160] if intr.count() else None
                p = img / f"field_{sid}.png"
                pg.screenshot(path=str(p))
                row["shot"] = str(p.relative_to(ROOT)).replace("\\", "/")
            except Exception as e:  # noqa: BLE001
                row["error"] = repr(e)[:300]
            c = float(row["csv"]) if row.get("csv") not in (None, "") else None
            u = row.get("ui_value")
            # UI rounds: 3 significant digits / 1 decimal; accept the UI rounding
            row["match"] = (c is not None and u is not None and
                            abs(u - c) <= max(0.051, 0.0051 * abs(c)))
            res["field"].append(row)
            print("field", sid, row.get("csv"), "|", (row.get("ui_conc_text") or row.get("error", ""))[:80].replace("\n", " "), "|", row["match"])
        # (б) FML held-out frames via UI «Фото»
        for stem in frames:
            ip, lp = fml_image(stem)
            row = {"frame": stem}
            if ip is None:
                row["error"] = "image not found"
                res["photo"].append(row)
                continue
            row["human_boxes"] = sum(1 for ln in lp.read_text().splitlines() if ln.strip()) if lp.is_file() else None
            try:
                pg.goto(f"{a.base}/?mode=photo", wait_until="domcontentloaded")
                pg.wait_for_selector("[data-testid=photo-count]", timeout=120000)
                pg.wait_for_load_state("networkidle", timeout=120000)
                pg.wait_for_timeout(1500)
                seen = []
                pg.on("request", lambda r: seen.append(r.url) if "/api/v3/photo/count" in r.url and r.method == "POST" else None)
                with pg.expect_response(lambda r: "/api/v3/photo/count" in r.url and r.request.method == "POST",
                                        timeout=120000):
                    pg.set_input_files("[data-testid=photo-file]", str(ip))
                pg.wait_for_load_state("networkidle", timeout=120000)
                row["ui_request"] = seen[-1] if seen else None
                # the same file straight to the API (same query string as the UI request)
                bnd = "qa19ct"
                body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{ip.name}\"\r\n"
                        f"Content-Type: image/jpeg\r\n\r\n").encode() + ip.read_bytes() + f"\r\n--{bnd}--\r\n".encode()
                q = seen[-1].split("?", 1)[1] if seen and "?" in seen[-1] else ""
                rq = urllib.request.Request(f"{a.base}/api/v3/photo/count" + (f"?{q}" if q else ""), data=body, method="POST")
                rq.add_header("Content-Type", f"multipart/form-data; boundary={bnd}")
                row["api_count"] = json.loads(urllib.request.urlopen(rq, timeout=300).read())["count"]
                pg.wait_for_timeout(1500)
                row["ui_count_text"] = pg.locator("[data-testid=photo-count]").first.inner_text()[:200]
                row["ui_count"] = num(row["ui_count_text"])
                p = img / f"photo_{stem}.png"
                pg.screenshot(path=str(p))
                row["shot"] = str(p.relative_to(ROOT)).replace("\\", "/")
            except Exception as e:  # noqa: BLE001
                row["error"] = repr(e)[:300]
            row["ui_equals_api"] = row.get("ui_count") is not None and row.get("ui_count") == row.get("api_count")
            row["abs_err"] = (abs(row["ui_count"] - row["human_boxes"])
                              if row.get("ui_count") is not None and row.get("human_boxes") is not None else None)
            res["photo"].append(row)
            print("photo", stem, "human", row.get("human_boxes"), "ui", row.get("ui_count"), "api", row.get("api_count"), row.get("error", ""))
        res["console_errors"] = errs[:20]
        b.close()
    f_ok = sum(1 for r in res["field"] if r["match"])
    errs_ph = [r["abs_err"] for r in res["photo"] if r.get("abs_err") is not None]
    res["summary"] = {"field_match": f"{f_ok}/{len(res['field'])}",
                      "photo_ui_equals_api": f"{sum(1 for r in res['photo'] if r.get('ui_equals_api'))}/{len(res['photo'])}",
                      "photo_mae_vs_human": round(sum(errs_ph) / len(errs_ph), 2) if errs_ph else None,
                      "photo_exact": sum(1 for e in errs_ph if e == 0)}
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
