"""Band-subset / light models task 2: light LightGBM (fewer features / trees / leaves), all-11-band recipe, MARIDA val F1.

  PYTHONPATH=src .venv/Scripts/python.exe scripts/experiments/l23_light.py [--seeds 0]
Feature ranking = gain importance of weights_exp/l23/e_full_s0 (== final model, same seed/data).
Writes out/l23_runs/light_s<seeds>.json and weights_exp/l23/light_<tag>_s<seed>/.
"""
from __future__ import annotations

import argparse
import json
import time

import l23_common as C

GRID = [
    # tag, top_k (None = all 48), rounds, num_leaves, learning_rate
    ("k48_t400_l63", None, 400, 63, 0.05),
    ("k48_t100_l15", None, 100, 15, 0.1),
    ("k20_t400_l63", 20, 400, 63, 0.05),
    ("k20_t150_l31", 20, 150, 31, 0.1),
    ("k20_t100_l31", 20, 100, 31, 0.1),
    ("k20_t200_l31", 20, 200, 31, 0.08),
    ("k20_t100_l15", 20, 100, 15, 0.1),
    ("k20_t50_l15", 20, 50, 15, 0.15),
    ("k12_t100_l15", 12, 100, 15, 0.1),
    ("k12_t50_l15", 12, 50, 15, 0.15),
    ("k8_t100_l15", 8, 100, 15, 0.1),
]


def ranking():
    m = json.loads((C.WOUT / "e_full_s0" / "meta.json").read_text(encoding="utf-8"))
    return [n for n, _ in m["importance_gain"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="0")
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    rank = ranking()
    out = []
    for seed in [int(s) for s in a.seeds.split(",")]:
        X, y = C.train_set(seed)
        for tag, k, rounds, leaves, lr in GRID:
            if a.only and tag not in a.only.split(","):
                continue
            feats = C.FINAL_META["features"] if k is None else [n for n in C.FINAL_META["features"] if n in rank[:k]]
            t = time.time()
            b = C.train(X, y, feats, seed, params={"num_leaves": leaves, "learning_rate": lr}, rounds=rounds)
            thr, met, _ = C.evaluate(b, feats)
            rec = {"name": f"light_{tag}", "seed": seed, "top_k": k, "n_features": len(feats), "n_trees": rounds,
                   "num_leaves": leaves, "learning_rate": lr, "threshold": round(thr, 2), "val_md": C.rnd(met),
                   "train_s": round(time.time() - t, 1)}
            meta = C.save(b, feats, f"light_{tag}_s{seed}", {**rec, "threshold": round(thr, 2)})
            rec["model_size_mb"] = meta["model_size_mb"]
            out.append(rec)
            print(f"[{tag} s{seed}] nf={len(feats)} F1={met['f1']:.4f} IoU={met['iou']:.4f} thr={thr:.2f} "
                  f"size={meta['model_size_mb']}MB", flush=True)
    tag = a.seeds.replace(",", "") + ("_" + a.only.replace(",", "+") if a.only else "")
    (C.RUNS / f"light_s{tag}.json").write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
