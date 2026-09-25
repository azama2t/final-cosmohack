"""L23: warm timing through the PRODUCT predictor (LGBMPredictor, ctypes lib_lightgbm, CPU, 10 threads) on the 300
speed chips: final model vs light models. Features = full 48 'win' stack (product code computes all, then selects).
Adds "feature_level": "win" to the light meta.json files so LGBMPredictor loads them. Two passes, second is reported.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ["CUDA_VISIBLE_DEVICES"] = ""

import numpy as np  # noqa: E402

from macroplastic.features.pixel import BANDS11  # noqa: E402
from macroplastic.io import read_raster  # noqa: E402
from macroplastic.models.lgbm_predict import LGBMPredictor  # noqa: E402

files = sorted((ROOT / "out" / "speed_chips").glob("*.tif"))
arrs = [read_raster(f)[0] for f in files]
res = {}
for m in ["weights/lgbm", "weights_exp/l23/light_k20_t200_l31_s0", "weights_exp/l23/light_k20_t100_l15_s0"]:
    wd = ROOT / m
    meta = json.loads((wd / "meta.json").read_text(encoding="utf-8"))
    if "feature_level" not in meta:
        meta["feature_level"] = "win"
        (wd / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    pred = LGBMPredictor(wd, num_threads=10, device="cpu")
    rows = [pred.prepare_rows(a, BANDS11) for a in arrs]
    for _ in range(2):
        t = time.perf_counter()
        rows = [pred.prepare_rows(a, BANDS11) for a in arrs]
        tf = time.perf_counter() - t
        t = time.perf_counter()
        out = pred.predict_rows_many(rows)
        tm = time.perf_counter() - t
    assert all(np.isfinite(o).all() for o in out)
    res[m] = {"features_1thread_s": round(tf, 2), "model_10threads_s": round(tm, 2),
              "model_chips_per_s": round(len(arrs) / tm, 1)}
    print(m, res[m], flush=True)
(ROOT / "out" / "l23_runs" / "product_speed.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
