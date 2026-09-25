"""Band-subset / light models task 1: final recipe on band subsets. MARIDA val F1/IoU MD (threshold on val).

  PYTHONPATH=src .venv/Scripts/python.exe scripts/experiments/l23_channels.py [--seeds 0] [--only name,...]
Writes out/l23_runs/channels_s<seeds>.json and weights_exp/l23/<subset>_s<seed>/.
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np

import l23_common as C

SUBSETS = {
    # name: (bands, rgb_extra)
    "a_rgb_strict": (["B2", "B3", "B4"], False),
    "a_rgb": (["B2", "B3", "B4"], True),
    "b_rgbnir": (["B2", "B3", "B4", "B8"], False),  # == (c) all 10 m bands of S2
    "b_rgbnir_x": (["B2", "B3", "B4", "B8"], True),
    "f_10m_swir": (["B2", "B3", "B4", "B8", "B11", "B12"], False),
    "d_10m_20m": (["B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"], False),
    "e_full": (list(C.PX.BANDS11), False),
}


def run(name, seed):
    bands, rx = SUBSETS[name]
    feats = C.subset_features(bands, rx)
    if name == "e_full":
        assert feats == C.FINAL_META["features"]
    t = time.time()
    X, y = C.train_set(seed)
    b = C.train(X, y, feats, seed)
    thr, met, _ = C.evaluate(b, feats)
    rec = {"name": name, "channels": bands, "rgb_extra": rx, "seed": seed, "n_features": len(feats),
           "threshold": round(thr, 2), "val_md": C.rnd(met), "n_train_px": int(len(y)),
           "n_train_md": int((y == 1).sum()), "train_s": round(time.time() - t, 1)}
    C.save(b, feats, f"{name}_s{seed}", rec)
    print(f"[{name} s{seed}] nf={len(feats)} F1={met['f1']:.4f} IoU={met['iou']:.4f} P={met['precision']:.4f} "
          f"R={met['recall']:.4f} thr={thr:.2f} ({time.time() - t:.0f}s)", flush=True)
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="0")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    seeds = [int(s) for s in a.seeds.split(",")]
    names = [n for n in SUBSETS if not a.only or n in a.only.split(",")]
    out = [run(n, s) for n in names for s in seeds]
    tag = a.seeds.replace(",", "") + ("_" + a.only.replace(",", "+") if a.only else "")
    (C.RUNS / f"channels_s{tag}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
