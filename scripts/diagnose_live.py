"""Where do detections of a live-scene model sit? (live scenes diagnostics; CPU)

For every data/live/<region>/<date>/ with prob_<model>.tif: P quantiles over water, pixel counts above several
thresholds, share of detections near land (<= 3 px), in turbid water (B4 > 0.04), by SCL class, water NIR level
(glint/haze proxy), component count. Prints markdown; --json writes out/l6/diagnose_<model>.json.

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/diagnose_live.py --model mdd
"""
import argparse
import json
from pathlib import Path

import numpy as np
import rasterio
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
THRS = [0.0639, 0.2, 0.3, 0.5, 0.7]


def diag(d: Path, model: str, thr_main: float):
    with rasterio.open(d / "bands.tif") as s:
        x = s.read()
    scl = rasterio.open(d / "scl.tif").read(1)
    water = rasterio.open(d / "water_mask.tif").read(1).astype(bool)
    p = rasterio.open(d / f"prob_{model}.tif").read(1).astype(np.float32) / 255
    b4, b8, b11 = x[3], x[7], x[10]
    land = ~water & np.isin(scl, (2, 4, 5, 7))  # land-ish (not cloud / shadow / nodata)
    dist_land = ndimage.distance_transform_edt(~land) if land.any() else np.full(water.shape, 1e9)
    det = (p >= thr_main) & water
    lab, ncomp = ndimage.label(det)
    r = dict(scene=f"{d.parent.name}/{d.name}", water_px=int(water.sum()),
             water_b8_median=round(float(np.nanmedian(b8[water])), 4),
             water_b11_median=round(float(np.nanmedian(b11[water])), 4),
             p_water_q=[round(float(v), 3) for v in np.percentile(p[water], [50, 90, 99, 99.9])],
             **{f"n_ge_{t}": int(((p >= t) & water).sum()) for t in THRS},
             permille_at_thr=round(1000 * det.sum() / max(water.sum(), 1), 2), n_components=int(ncomp))
    if det.any():
        r["det_near_land_3px"] = round(float((dist_land[det] <= 3).mean()), 3)
        r["det_turbid_b4_gt_0.04"] = round(float((b4[det] > 0.04).mean()), 3)
        r["water_turbid_b4_gt_0.04"] = round(float((b4[water] > 0.04).mean()), 3)
        u, c = np.unique(scl[det], return_counts=True)
        r["det_scl"] = {int(a): round(float(b) / det.sum(), 3) for a, b in zip(u, c)}
        r["det_b8_minus_water_median"] = round(float(np.nanmedian(b8[det]) - np.nanmedian(b8[water])), 4)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="mdd")
    ap.add_argument("--thr", type=float, default=None, help="default: threshold from prob_<model>.json")
    a = ap.parse_args()
    rows = []
    for pj in sorted((ROOT / "data" / "live").glob(f"*/*/prob_{a.model}.json")):
        m = json.loads(pj.read_text(encoding="utf-8"))
        thr = a.thr if a.thr is not None else m.get("threshold_checkpoint", m["threshold"])
        rows.append(diag(pj.parent, a.model, thr))
        print(json.dumps(rows[-1]), flush=True)
    out = ROOT / "out" / "l6" / f"diagnose_{a.model}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
