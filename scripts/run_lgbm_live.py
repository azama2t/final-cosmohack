"""Run our LightGBM (LightGBM, weights/lgbm) on live scene folders -> prob_lgbm.tif / prob_lgbm.json (CPU).

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/run_lgbm_live.py --all [--force]
  defaults (orchestrator decision 25.09): --weights weights/lgbm_live --harmonize water_median, water mask = SCL==6
  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/run_lgbm_live.py data/live/honduras/2025-04-05

Domain caveat: trained on MARIDA ACOLITE rhorc (from L1C); live input is Sen2Cor L2A surface reflectance.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def run_scene(pred, d: Path, weights: Path, harmonize):
    with rasterio.open(d / "bands.tif") as src:
        x = src.read()
        names = list(src.descriptions)
        prof = dict(driver="GTiff", width=src.width, height=src.height, count=1, dtype="uint8", crs=src.crs,
                    transform=src.transform, compress="deflate", tiled=True, blockxsize=256, blockysize=256)
    scl = rasterio.open(d / "scl.tif").read(1)
    t0 = time.time()
    prob = pred.predict_proba(x, names, water_mask=(scl == 6))
    rt = time.time() - t0
    prob = np.nan_to_num(prob, nan=0.0)
    u8 = np.clip(np.round(prob * 255), 0, 255).astype(np.uint8)
    with rasterio.open(d / "prob_lgbm.tif", "w", **prof) as dst:
        dst.write(u8[None])
        dst.set_band_description(1, "P(marine debris)*255")
    with rasterio.open(d / "water_mask.tif") as src:
        water = src.read(1).astype(bool)
    above = prob >= pred.threshold
    off = getattr(pred, "last_offset", None)
    meta = dict(model=f"lgbm (ours, lane L3, pixel LightGBM on MARIDA; weights {weights.name})",
                threshold=float(pred.threshold), weights=weights.name, weights_dir=str(weights.relative_to(ROOT)).replace("\\", "/"),
                harmonize=harmonize or "none", harmonize_water_mask="scl.tif == 6",
                harmonize_offset=(np.asarray(off).round(5).tolist() if off is not None else None),
                runtime_s=round(rt, 2), device="cpu",
                threads=pred.num_threads, n_above_threshold=int(above.sum()),
                n_above_threshold_water=int((above & water).sum()), encoding="uint8 = round(P*255)",
                input="bands.tif Sen2Cor L2A reflectance, 11 MARIDA bands picked by name (B9 ignored)",
                domain_note="trained on ACOLITE rhorc (L1C); L2A levels differ -> probability not calibrated here",
                postprocess_min_px_in_model_meta=pred.min_px)
    (d / "prob_lgbm.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenes", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--weights", default=str(ROOT / "weights" / "lgbm_live"))
    ap.add_argument("--harmonize", default="water_median", choices=["water_median", "none"])
    a = ap.parse_args()
    from macroplastic.models.lgbm_predict import load_predictor
    w = Path(a.weights)
    harm = None if a.harmonize == "none" else a.harmonize
    pred = load_predictor(w, harmonize=harm)
    scenes = [Path(s) if Path(s).is_absolute() else ROOT / s for s in a.scenes]
    if a.all:
        scenes += [p.parent for p in sorted((ROOT / "data" / "live").glob("*/*/bands.tif"))
                   if a.force or not (p.parent / "prob_lgbm.tif").exists()]
    for d in scenes:
        m = run_scene(pred, d, w, harm)
        print(f"{d}: {m['runtime_s']} s, thr={m['threshold']:.4f}, above={m['n_above_threshold']} "
              f"(water {m['n_above_threshold_water']})", flush=True)


if __name__ == "__main__":
    main()
