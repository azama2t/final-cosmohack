"""Small demo data set for a clean clone: service/data -> service/demo (kind "demo", <= 20 MB).

Usage: python scripts/make_demo.py [--src service/data] [--out service/demo] [--regions a,b] [--max-dates 2]
                                   [--max-px 1024] [--max-mb 20]
Default regions: up to 2 (with drift first, then most (date, model) pairs, then least cloudy); per region the
`--max-dates` most interesting dates (drift, both models, cloud < 30 %, more detections, newer).
PNGs are downscaled to <= max-px (prob.png with a 3x3 max filter first so small detections stay visible).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageFilter


def read_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def shrink_png(src: Path, dst: Path, max_px: int, is_prob: bool) -> tuple[int, int]:
    im = Image.open(src)
    if max(im.size) > max_px:
        k = max_px / max(im.size)
        size = (max(1, round(im.width * k)), max(1, round(im.height * k)))
        if is_prob:
            im = im.filter(ImageFilter.MaxFilter(3)).resize(size, Image.NEAREST)
        else:
            im = im.resize(size, Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, optimize=True)
    return im.size


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="service/data")
    ap.add_argument("--out", default="service/demo")
    ap.add_argument("--regions", default="")
    ap.add_argument("--max-dates", type=int, default=2)
    ap.add_argument("--max-px", type=int, default=1024)
    ap.add_argument("--max-mb", type=float, default=20.0)
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    src, out = Path(a.src), Path(a.out)
    man = read_json(src / "manifest.json")
    regs = man["regions"]
    if a.regions:
        want = a.regions.split(",")
        regs = [r for r in regs if r["id"] in want]
    else:
        def cloud(r):
            v = [d["cloud_frac"] for d in r["dates"] if d.get("cloud_frac") is not None]
            return sum(v) / len(v) if v else 1.0
        def ndet(r):
            return sum(x["n_detections"] for x in read_json(src / r["id"] / "timeseries.json"))
        regs = sorted(regs, key=lambda r: (-any(d.get("drift") for d in r["dates"]), -(ndet(r) > 0),
                                           -sum(len(d["models"]) for d in r["dates"]), cloud(r)))[:2]
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    new_regions = []
    for r in regs:
        rid = r["id"]
        ts_all = read_json(src / rid / "timeseries.json")

        def interest(d):  # drift first, then both models, then clear sky, then more detections, then newer
            ndet = sum(x["n_detections"] for x in ts_all if x["date"] == d["date"])
            haze = bool((d.get("quality") or {}).get("haze"))
            return (bool(d.get("drift")), not haze, len(d["models"]), (d.get("cloud_frac") or 0) < 0.3, ndet > 0,
                    ndet, d["date"])
        dates = sorted(sorted(r["dates"], key=interest)[-a.max_dates:], key=lambda d: d["date"])
        for d in dates:
            date = d["date"]
            rj = read_json(src / rid / date / "rgb.json")
            w, h = shrink_png(src / d["rgb"], out / d["rgb"], a.max_px, False)
            rj["width"], rj["height"] = w, h
            write_json(out / rid / date / "rgb.json", rj)
            if d.get("thumb") and (src / d["thumb"]).exists():
                shutil.copyfile(src / d["thumb"], out / d["thumb"])
            if d.get("drift"):
                shutil.copyfile(src / d["drift"], out / d["drift"])
            for m in d["models"]:
                base = f"{rid}/{date}/{m}"
                shrink_png(src / base / "prob.png", out / base / "prob.png", a.max_px, True)
                for f in ("prob.tif", "detections.geojson", "h3.geojson", "zones.json"):
                    shutil.copyfile(src / base / f, out / base / f)
        keep = {d["date"] for d in dates}
        ts = [row for row in read_json(src / rid / "timeseries.json") if row["date"] in keep]
        write_json(out / rid / "timeseries.json", ts)
        rr = dict(r)
        rr["dates"] = dates
        clear = [d for d in dates if not (d.get("quality") or {}).get("haze")]
        latest = (clear or dates)[-1]
        prim = "mdd" if "mdd" in latest["models"] else latest["models"][0]
        row = next((x for x in ts if x["date"] == latest["date"] and x["model"] == prim), None)
        rr["default_date"] = latest["date"]
        if row:
            rr["summary"] = {"latest_date": latest["date"], "model": prim,
                             "haze": bool((latest.get("quality") or {}).get("haze")), "index_permille": row["mean_index"],
                             "n_detections": row["n_detections"], "total_debris_area_m2": row["total_debris_area_m2"]}
        new_regions.append(rr)
    man = dict(man)
    man.update({"kind": "demo", "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "regions": new_regions, "demo_note": f"подмножество {a.src}: {len(new_regions)} регион(а), "
                                                     f"≤ {a.max_dates} даты, PNG ≤ {a.max_px} px"})
    write_json(out / "manifest.json", man)
    total = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"demo written to {out}: {len(new_regions)} regions, {total / 1e6:.2f} MB")
    if total > a.max_mb * 1e6:
        print(f"ERROR: demo is larger than {a.max_mb} MB")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
