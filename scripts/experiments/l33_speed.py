"""Scene-relative features: feature (and LightGBM predict) time on a 2500x2500 tile: 48 'win' features vs + scene-relative extras.

    cd scripts/experiments && PYTHONPATH=../../src ../../.venv/Scripts/python.exe l33_speed.py [--repeats 3]
Tile = mosaic 10x10 of the first 100 MARIDA val patches (256 px), cropped to 2500x2500 (test never read).
Features: pixel.compute_features_blocked (block 512, halo 32) - the product path, one process.
Extras: tile water stats once (stride 4) + per block: zs (pointwise) and/or wide (dmed63 -> halo 64).
Predict: models weights_exp/l33/val_<variant>_s0.txt, 10 threads. Writes weights_exp/l33/speed.json.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time

import numpy as np

import l16_common as C
import l33_feats as F
from macroplastic.features import pixel as PX

S = 2500


def mosaic():
    names = C.split_names("val")[:100]
    arr = np.zeros((11, 2560, 2560), np.float32)
    for i, n in enumerate(names):
        img = C.read_patch(n)[0]
        r, c = divmod(i, 10)
        arr[:, r * 256:(r + 1) * 256, c * 256:(c + 1) * 256] = img
    return arr[:, :S, :S].copy()


def base_feats(arr):
    out = np.empty((48, S, S), np.float32)
    for (y0, y1, x0, x1), f in PX.compute_features_blocked(arr, PX.BANDS11, "win"):
        out[:, y0:y1, x0:x1] = f
    return out


def extra_feats(arr, zs, wide):
    x = arr.copy()
    x[~np.isfinite(x)] = np.nan
    med, mad, _, _ = F.tile_scene_stats(x, PX.BANDS11)
    n = (15 if zs else 0) + (len(F.WIDE) if wide else 0)
    out = np.empty((n, S, S), np.float32)
    halo = 64 if wide else 0
    for y0, y1, x0, x1, hy0, hy1, hx0, hx1 in PX.iter_blocks(S, S, 512, halo):
        f = F.tile_extra_block(x[:, hy0:hy1, hx0:hx1], med, mad, wide=wide, zs=zs)
        out[:, y0:y1, x0:x1] = f[:, y0 - hy0:y1 - hy0, x0 - hx0:x1 - hx0]
    return out


def main():
    import lightgbm as lgb

    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=3)
    a = ap.parse_args()
    arr = mosaic()
    cfg = {"zs": (True, False), "zd": (True, False), "wide": (False, True), "zs_wide": (True, True)}
    t = {k: [] for k in ["base"] + list(cfg)}
    for _ in range(a.repeats):
        t0 = time.perf_counter(); fb = base_feats(arr); t["base"].append(time.perf_counter() - t0)
        for k, (zs, wide) in cfg.items():
            t0 = time.perf_counter(); fe = extra_feats(arr, zs, wide); t[k].append(time.perf_counter() - t0)
    feat = {k: round(statistics.median(v), 2) for k, v in t.items()}
    res = {"tile": f"{S}x{S}", "feature_seconds_median": {"base": feat["base"]}}
    for k in cfg:
        res["feature_seconds_median"][k] = round(feat["base"] + feat[k], 2)
        res["feature_seconds_median"][k + "_extra_only"] = feat[k]
        res.setdefault("feature_time_increase_pct", {})[k] = round(100 * feat[k] / feat["base"], 1)
    # predict (subsample 1/4 of the tile rows to keep it short; relative numbers)
    Xb = fb[:, ::2, :].reshape(48, -1).T
    pred = {}
    for v in ["base"] + list(cfg):
        path = C.ROOT / "weights_exp" / "l33" / f"val_{v}_s0.txt"
        if not path.is_file():
            continue
        b = lgb.Booster(model_file=str(path))
        nf = b.num_feature()
        X = Xb if nf == 48 else np.concatenate([Xb, np.random.default_rng(0).normal(size=(len(Xb), nf - 48)).astype(np.float32)], 1)
        ts = []
        for _ in range(2):
            t0 = time.perf_counter(); b.predict(X, num_threads=10); ts.append(time.perf_counter() - t0)
        pred[v] = {"n_features": nf, "seconds_half_tile": round(min(ts), 2)}
    res["predict_half_tile_10_threads"] = pred
    res["note"] = ("features: one process (blocked product path). zd has the same cost as zs. wide needs halo 64 "
                   "(dmed63); predict uses random values for extra columns (time only).")
    C.dump(C.ROOT / "weights_exp" / "l33" / "speed.json", res)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
