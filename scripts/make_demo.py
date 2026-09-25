"""Small demo data set for a clean clone: service/data -> service/demo (kind "demo", <= 19 MB).

Usage: python scripts/make_demo.py [--src service/data] [--out service/demo] [--regions a,b] [--n-regions 5]
                                   [--max-dates 2] [--max-px 1024] [--max-mb 19] [--rgb-colors 256]
Default regions: up to `--n-regions` (with detections first, then with drift, then most (date, model) pairs, then
least cloudy); per region the `--max-dates` most interesting dates (drift, both models, cloud < 30 %, more
detections, newer). If the set is larger than --max-mb, the least-interesting extra date of the region with the most
dates is dropped (never below 1 date) until it fits.
PNGs are downscaled to <= max-px (prob.png with a 3x3 max filter first so small detections stay visible);
rgb.png is palette-quantized to --rgb-colors colours (0 = keep truecolour) to fit 5 regions into the budget.
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


def shrink_png(src: Path, dst: Path, max_px: int, is_prob: bool, colors: int = 0) -> tuple[int, int]:
    im = Image.open(src)
    if max(im.size) > max_px:
        k = max_px / max(im.size)
        size = (max(1, round(im.width * k)), max(1, round(im.height * k)))
        if is_prob:
            im = im.filter(ImageFilter.MaxFilter(3)).resize(size, Image.NEAREST)
        else:
            im = im.resize(size, Image.LANCZOS)
    if colors and not is_prob:
        im = im.convert("RGBA").quantize(colors=colors, method=Image.Quantize.FASTOCTREE)
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
    ap.add_argument("--max-mb", type=float, default=19.0)
    ap.add_argument("--n-regions", type=int, default=5)
    ap.add_argument("--rgb-colors", type=int, default=256)
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
        regs = sorted(regs, key=lambda r: (-(ndet(r) > 0), -any(d.get("drift") for d in r["dates"]),
                                           -sum(len(d["models"]) for d in r["dates"]), cloud(r)))[:a.n_regions]

    def interest_fn(rid):
        ts_all = read_json(src / rid / "timeseries.json")

        def interest(d):  # drift first, then both models, then clear sky, then more detections, then newer
            ndet = sum(x["n_detections"] for x in ts_all if x["date"] == d["date"])
            haze = bool((d.get("quality") or {}).get("haze"))
            return (bool(d.get("drift")), not haze, len(d["models"]), (d.get("cloud_frac") or 0) < 0.3, ndet > 0,
                    ndet, d["date"])
        return interest
    n_dates = {r["id"]: min(a.max_dates, len(r["dates"])) for r in regs}
    while True:
        total, new_regions = write_demo(a, src, out, man, regs, n_dates, interest_fn)
        if total <= a.max_mb * 1e6 or max(n_dates.values(), default=1) <= 1:
            break
        rid = max(n_dates, key=lambda k: (n_dates[k], k))
        n_dates[rid] -= 1
        print(f"  {total / 1e6:.2f} MB > {a.max_mb} MB: {rid} -> {n_dates[rid]} date(s)")
    print(f"demo written to {out}: {len(new_regions)} regions, {total / 1e6:.2f} MB")
    for r in new_regions:
        print(f"  {r['id']}: {[d['date'] for d in r['dates']]} drift={[bool(d.get('drift')) for d in r['dates']]}")
    if total > a.max_mb * 1e6:
        print(f"ERROR: demo is larger than {a.max_mb} MB")
        return 1
    return 0


def write_demo(a, src: Path, out: Path, man: dict, regs: list, n_dates: dict, interest_fn):
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    new_regions = []
    for r in regs:
        rid = r["id"]
        interest = interest_fn(rid)
        dates = sorted(sorted(r["dates"], key=interest)[-n_dates[rid]:], key=lambda d: d["date"])
        for d in dates:
            date = d["date"]
            rj = read_json(src / rid / date / "rgb.json")
            w, h = shrink_png(src / d["rgb"], out / d["rgb"], a.max_px, False, a.rgb_colors)
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
    return total, new_regions


if __name__ == "__main__":
    sys.exit(main())
