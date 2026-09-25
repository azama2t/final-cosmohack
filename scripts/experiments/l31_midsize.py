"""Mid-size model: mid-size CPU model k20_t400_l63 (top-20 features by gain of the final model, 400 trees x 63 leaves, lr 0.05).

    cd scripts/experiments && PYTHONPATH=../../src ../../.venv/Scripts/python.exe l31_midsize.py [--seeds 0,1,2]

Recipe = l23 (== final model recipe, reproduced bit-for-bit by the band-subset run e_full_s0). Evaluation: MARIDA val, all
labelled px pooled, threshold chosen on val (grid of the final meta). MARIDA test is never read.
Writes weights_exp/l31/k20_t400_l63_s<seed>/{model.txt, meta.json} (meta in the product format: a copy of
weights/lgbm/meta.json with features/threshold/metrics replaced) and weights_exp/l31/train_val.json.
Also checks s0 against weights_exp/l23/light_k20_t400_l63_s0 (same recipe -> same model.txt).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import time

import numpy as np

import l23_common as C

C.WOUT = C.ROOT / "weights_exp" / "l31"
C.RUNS = C.ROOT / "out" / "l31_runs"
C.WOUT.mkdir(parents=True, exist_ok=True)
C.RUNS.mkdir(parents=True, exist_ok=True)

TAG, TOP_K, ROUNDS, LEAVES, LR = "k20_t400_l63", 20, 400, 63, 0.05


def ranking():
    m = json.loads((C.ROOT / "weights_exp" / "l23" / "e_full_s0" / "meta.json").read_text(encoding="utf-8"))
    return [n for n, _ in m["importance_gain"]]


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def main():
    import train_lgbm as TL

    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", default="0,1,2")
    a = ap.parse_args()
    rank = ranking()
    feats = [n for n in C.FINAL_META["features"] if n in rank[:TOP_K]]
    out = []
    for seed in [int(s) for s in a.seeds.split(",")]:
        t = time.time()
        X, y = C.train_set(seed)
        b = C.train(X, y, feats, seed, params={"num_leaves": LEAVES, "learning_rate": LR}, rounds=ROUNDS)
        thr, met, p = C.evaluate(b, feats)
        _, _, curve = TL.best_threshold(C.load_all()["marida_val"][1] == 1, p, C.CFG["threshold_grid"])
        name = f"{TAG}_s{seed}"
        C.save(b, feats, name, {"threshold": round(thr, 2)})
        od = C.WOUT / name
        # product-format meta: final meta with this model's fields
        m = copy.deepcopy(C.FINAL_META)
        yv = C.load_all()["marida_val"][1]
        conf = C.load_all()["marida_val"][2]
        pred = p >= thr
        rec_conf = {}
        for c in (1, 2, 3):
            mm = (yv == 1) & (conf == c)
            if mm.any():
                rec_conf[str(c)] = round(float(pred[mm].mean()), 4)
        per_cls = {str(c): {"n": int((yv == c).sum()), "pred_md": int(pred[yv == c].sum())}
                   for c in range(1, 16) if (yv == c).any()}
        gain = b.feature_importance("gain")
        imp = sorted(zip(feats, gain.tolist()), key=lambda t: -t[1])
        m.update({
            "features": feats, "threshold": round(thr, 2), "seed": seed, "val_md": C.rnd(met),
            "val_recall_by_conf": rec_conf, "val_per_class_pred_md": per_cls,
            "threshold_curve": [[t_, f_] for t_, f_ in curve],
            "importance_gain_top": [[n, round(g, 1)] for n, g in imp],
            "model_size_mb": round((od / "model.txt").stat().st_size / 1e6, 3),
            "n_trees": b.num_trees(),
            "note": ("L31 mid-size model k20_t400_l63: top-20 features by gain of the final 48-feature model "
                     "(weights_exp/l23/e_full_s0), same data recipe and LightGBM params (400 trees x 63 leaves, "
                     "lr 0.05); val = official MARIDA val, all labelled px pooled; threshold chosen on val; "
                     "MARIDA test never read"),
        })
        m["config"] = copy.deepcopy(C.CFG)
        m["config"]["lgbm"]["num_leaves"] = LEAVES
        m["config"]["lgbm"]["learning_rate"] = LR
        m["config"]["lgbm"]["num_boost_round"] = ROUNDS
        m["config"]["feature_subset"] = {"top_k": TOP_K, "ranking": "gain of weights_exp/l23/e_full_s0"}
        (od / "meta.json").write_text(json.dumps(m, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
        r = {"name": name, "seed": seed, "n_features": len(feats), "threshold": round(thr, 2), "val_md": C.rnd(met),
             "model_sha": sha(od / "model.txt"), "train_s": round(time.time() - t, 1)}
        ref = C.ROOT / "weights_exp" / "l23" / f"light_{TAG}_s{seed}" / "model.txt"
        if ref.is_file():
            r["same_as_l23"] = sha(ref) == r["model_sha"]
        out.append(r)
        print(json.dumps(r), flush=True)
    f1 = [r["val_md"]["f1"] for r in out]
    iou = [r["val_md"]["iou"] for r in out]
    summ = {"runs": out, "f1_mean": round(float(np.mean(f1)), 4), "f1_std": round(float(np.std(f1)), 4),
            "iou_mean": round(float(np.mean(iou)), 4), "iou_std": round(float(np.std(iou)), 4)}
    (C.WOUT / "train_val.json").write_text(json.dumps(summ, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summ.items() if k != "runs"}), flush=True)


if __name__ == "__main__":
    main()
