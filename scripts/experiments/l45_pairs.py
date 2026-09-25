"""Drift check step 1: find "next snapshot of the same crop 1-5 days later" for fresh scenes that have drift.json.

  .venv/Scripts/python.exe scripts/experiments/l45_pairs.py screen            # -> out/l45/candidates.json
  .venv/Scripts/python.exe scripts/experiments/l45_pairs.py fetch [--max-dt-h 120] [--per-scene 2]
        -> data/drift_check/<region>/<date2>/ (same UTM grid as scene 1: bands.tif, scl.tif, water_mask.tif, scene.json)

Scene 2 is NOT written into data/live (so the service layer builder does not pick it up).
Screening: Earth Search items of the same MGRS tile, crop cloud (SCL 8/9/10 at 20 m) < 40 %, crop valid >= 50 %;
after download the scene is rejected if glint_or_haze (median B11 of water > 0.01, the 06_live rule).
"""
import argparse
import datetime as dt
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
from macroplastic.live import stac  # noqa: E402
from fetch_live import crop_cloud, pick_item  # noqa: E402

OUT = ROOT / "out" / "l45"
DATA = ROOT / "data" / "drift_check"
MAX_CLOUD = 0.40
MIN_VALID = 0.50


def fresh_scenes():
    out = []
    for dj in sorted((ROOT / "data" / "live").glob("*/*/drift.json")):
        d = dj.parent
        if d.name < "2024-08-01":
            continue
        sc = json.loads((d / "scene.json").read_text(encoding="utf-8"))
        out.append(sc)
    return out


def screen_one(sc):
    b = sc["bounds_wgs84"]
    lon, lat = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    t1 = dt.datetime.fromisoformat(sc["datetime"].replace("Z", "+00:00"))
    d0 = (t1 + dt.timedelta(days=1)).date().isoformat()
    d1 = (t1 + dt.timedelta(days=5)).date().isoformat()
    rec = {"region": sc["region"], "date1": sc["date"], "datetime1": sc["datetime"], "tile": sc["tile"],
           "window": f"{d0}/{d1}", "candidates": []}
    try:
        items = stac.search("earth-search", lon, lat, f"{d0}/{d1}", max_cloud=100, tile=sc["tile"])
    except Exception as e:
        rec["error"] = repr(e)
        return rec
    epsg = int(sc["crs"].split(":")[-1])
    for it in sorted(items, key=lambda i: i.properties["datetime"]):
        c = {"id": it.id, "datetime2": it.properties["datetime"], "tile_cc": it.properties.get("eo:cloud_cover")}
        t2 = dt.datetime.fromisoformat(it.properties["datetime"].replace("Z", "+00:00"))
        c["dt_h"] = round((t2 - t1).total_seconds() / 3600, 2)
        if stac.item_epsg(it) != epsg:
            c["status"] = "crs mismatch"
        else:
            try:
                vf, cf, sf, wf, gl = crop_cloud(it, epsg, sc["bounds_utm"])
                c.update(valid=round(vf, 3), cloud=round(cf, 3), shadow=round(sf, 3), scl6=round(wf, 3),
                         water_b8a=round(gl, 4) if gl == gl else None)
                c["status"] = "OK" if (vf >= MIN_VALID and cf < MAX_CLOUD) else "skip"
            except Exception as e:
                c["status"] = f"error {e!r}"[:200]
        rec["candidates"].append(c)
    return rec


def cmd_screen(a):
    OUT.mkdir(parents=True, exist_ok=True)
    scenes = fresh_scenes()
    print(f"{len(scenes)} fresh scenes with drift.json", flush=True)
    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as ex:
        recs = list(ex.map(screen_one, scenes))
    for r in recs:
        oks = [c for c in r["candidates"] if c.get("status") == "OK"]
        print(f"{r['region']}/{r['date1']}: {len(r['candidates'])} cand, OK: "
              + ", ".join(f"{c['datetime2'][:10]} dt={c['dt_h']}h cl={c['cloud']} v={c['valid']} b8a={c['water_b8a']}"
                          for c in oks) + (f" ERR {r['error']}" if 'error' in r else ""), flush=True)
    (OUT / "candidates.json").write_text(json.dumps(recs, indent=1), encoding="utf-8")
    print(f"done in {time.time() - t0:.0f} s", flush=True)


def fetch_one(rec, c):
    region = rec["region"]
    sc = json.loads((ROOT / "data" / "live" / region / rec["date1"] / "scene.json").read_text(encoding="utf-8"))
    epsg = int(sc["crs"].split(":")[-1])
    d2 = c["datetime2"][:10]
    outdir = DATA / region / d2
    if (outdir / "scene.json").exists():
        return {"region": region, "date1": rec["date1"], "date2": d2, "status": "exists", "dir": str(outdir)}
    b = sc["bounds_wgs84"]
    it = pick_item(d2, sc["tile"], (b[0] + b[2]) / 2, (b[1] + b[3]) / 2)
    if stac.item_epsg(it) != epsg:
        return {"region": region, "date1": rec["date1"], "date2": d2, "status": "crs mismatch"}
    if d2 >= "2022-01-25" and stac.source_of(it) != "planetary-computer":
        return {"region": region, "date1": rec["date1"], "date2": d2, "status": "no PC item (ES clamped), skip"}
    crop = stac.read_crop(it, epsg, sc["bounds_utm"])
    meta = stac.write_scene(outdir, region, it, crop, extra=dict(
        drift_check_pair_of=f"{region}/{rec['date1']}", water_b8a_median_20m=c.get("water_b8a"),
        selection=f"L45: next snapshot {rec['window']} of the same crop (bounds_utm of {rec['date1']}); "
                  f"crop cloud<{MAX_CLOUD}, valid>={MIN_VALID}; not for the service layer"))
    return {"region": region, "date1": rec["date1"], "date2": d2, "status": "ok", "dir": str(outdir),
            "glint_or_haze": meta["glint_or_haze"], "water_b11_median": meta["water_b11_median"],
            "crop_cloud_frac": meta["crop_cloud_frac"], "valid_frac": meta["valid_frac"], "scene_id": meta["scene_id"]}


def cmd_fetch(a):
    recs = json.loads((OUT / "candidates.json").read_text(encoding="utf-8"))
    jobs = []
    for r in recs:
        oks = [c for c in r["candidates"] if c.get("status") == "OK" and c["dt_h"] <= a.max_dt_h]
        oks = sorted(oks, key=lambda c: c["dt_h"])[: a.per_scene]
        jobs += [(r, c) for c in oks]
    print(f"{len(jobs)} downloads", flush=True)
    res = []

    def job(rc):
        try:
            return fetch_one(*rc)
        except Exception as e:
            return {"region": rc[0]["region"], "date1": rc[0]["date1"], "date2": rc[1]["datetime2"][:10],
                    "status": f"error {e!r}"[:300]}

    with ThreadPoolExecutor(a.workers) as ex:
        for r in ex.map(job, jobs):
            print(json.dumps(r), flush=True)
            res.append(r)
    prev = []
    fp = OUT / "fetched.json"
    if fp.exists():
        prev = json.loads(fp.read_text(encoding="utf-8"))
    keyed = {(r["region"], r["date1"], r["date2"]): r for r in prev + res}
    fp.write_text(json.dumps(list(keyed.values()), indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["screen", "fetch"])
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--max-dt-h", type=float, default=120)
    ap.add_argument("--per-scene", type=int, default=2)
    a = ap.parse_args()
    return cmd_screen(a) if a.cmd == "screen" else cmd_fetch(a)


if __name__ == "__main__":
    sys.exit(main())
