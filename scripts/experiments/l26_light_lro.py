"""L26: leave-region-out for the light L23 model (top-20 features, 200 trees x 31 leaves, lr 0.08) - the same
protocol as scripts/experiments/l16_lro.py, variant 'combined' (MARIDA train+val of the other regions + MADOS
train+val minus overlapping scenes, MADOS-only classes dropped, caps 30000/class, 60000 water, pos_weight 3).

    cd scripts/experiments && PYTHONPATH=../../src ../../.venv/Scripts/python.exe l26_light_lro.py [--seeds 0 1 2]

Also re-runs the final recipe ('combined', 48 features, 400 x 63) in the same process as a control (must reproduce
weights_exp/l16/lro.json). Writes weights_exp/l26/light_lro.json. MARIDA test is never read.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

import l16_common as C
from macroplastic.features.pixel import feature_names

ROOT = Path(__file__).resolve().parents[2]
LIGHT = ROOT / "weights_exp" / "l23" / "light_k20_t200_l31_s0"
OUT = ROOT / "weights_exp" / "l26"
ALL = feature_names("win")


def train(X, y, seed, fidx, params, rounds):
    import lightgbm as lgb

    label = (y == 1).astype(np.float32)
    w = np.where(label > 0, C.POS_WEIGHT, 1.0).astype(np.float32)
    p = dict(C.LGB_PARAMS, **params)
    p.update(objective="binary", seed=seed, bagging_seed=seed, feature_fraction_seed=seed, data_random_seed=seed,
             num_threads=C.THREADS, verbose=-1, deterministic=True, force_row_wise=True)
    ds = lgb.Dataset(X[:, fidx], label=label, weight=w, feature_name=[ALL[i] for i in fidx], free_raw_data=True)
    return lgb.train(p, ds, num_boost_round=rounds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*", default=[0, 1, 2])
    ap.add_argument("--no-control", action="store_true")
    ap.add_argument("--light", nargs="*", default=["light_k20_t200_l31"],
                    help="L23 configs (weights_exp/l23/<name>_s0/meta.json gives features/leaves/lr/trees)")
    ap.add_argument("--out", default="light_lro.json")
    a = ap.parse_args()
    t0 = time.time()
    variants = {}
    for name in a.light:
        meta_l = json.loads((ROOT / "weights_exp" / "l23" / f"{name}_s0" / "meta.json").read_text(encoding="utf-8"))
        variants[name] = ([ALL.index(n) for n in meta_l["features"]],
                          {"num_leaves": int(meta_l["num_leaves"]), "learning_rate": float(meta_l["learning_rate"])},
                          int(meta_l["n_trees"]))
    if not a.no_control:
        variants["final_combined_control"] = (list(range(len(ALL))), {}, C.ROUNDS)
    X, y, conf, meta = C.marida_pixels()
    Xm, ym, cm, scm = C.mados_pixels_all()
    keep_m = ym <= 15
    Xm, ym, scm = Xm[keep_m], ym[keep_m], scm[keep_m]
    mreg, mno = C.mados_scene_regions()
    regions = np.unique(meta["region"])
    md_by_region = {r: int(np.sum((meta["region"] == r) & (y == 1))) for r in regions}
    held = [r for r in sorted(md_by_region, key=lambda r: -md_by_region[r]) if md_by_region[r] >= 50]
    print("held-out:", held, f"load {time.time() - t0:.0f}s", flush=True)
    oof = {}
    for R in held:
        tr_m = meta["region"] != R
        ex_scenes = {s for s, rs in mreg.items() if R in rs} | mno
        tr_mados = ~np.isin(scm, sorted(ex_scenes))
        te = np.flatnonzero(~tr_m)
        for seed in a.seeds:
            im = np.flatnonzero(tr_m)[C.sample_caps(y[tr_m], seed)]
            imd = np.flatnonzero(tr_mados)[C.sample_caps(ym[tr_mados], seed + 7919)]
            Xt, yt = np.concatenate([X[im], Xm[imd]]), np.concatenate([y[im], ym[imd]])
            for v, (fidx, params, rounds) in variants.items():
                t1 = time.time()
                b = train(Xt, yt, seed, fidx, params, rounds)
                arr = oof.setdefault((v, seed), np.full(len(y), np.nan, np.float32))
                arr[te] = C.predict(b, X[te], fidx)
                print(f"[{R} s{seed} {v}] {time.time() - t1:.1f}s", flush=True)
    res, summary = {}, {}
    for (v, seed), p in oof.items():
        per = []
        tp = fp = fn = 0
        for R in held:
            m = meta["region"] == R
            others = np.isin(meta["region"], [q for q in held if q != R])
            thr, _ = C.best_thr(y[others] == 1, p[others])
            t = y[m] == 1
            s = C.scores(*C.confusion(t, p[m] >= thr))
            a_, b_, c_, _ = C.confusion(t, p[m] >= thr)
            tp += a_; fp += b_; fn += c_
            per.append({"region": R, "n_md": int(t.sum()), "thr_from_other_regions": thr,
                        **{k: round(s[k], 4) for k in ("f1", "iou", "precision", "recall")}})
        res[f"{v}|s{seed}"] = {"variant": v, "seed": seed, "per_region": per,
                               "mean_f1": round(float(np.mean([r["f1"] for r in per])), 4),
                               "pooled_f1": round(C.scores(tp, fp, fn)["f1"], 4)}
        print(v, seed, res[f"{v}|s{seed}"]["mean_f1"], [r["f1"] for r in per], flush=True)
    for v in variants:
        vals = [r["mean_f1"] for r in res.values() if r["variant"] == v]
        summary[v] = {"lro_mean_f1_by_seed": vals, "mean": round(float(np.mean(vals)), 4),
                      "std": round(float(np.std(vals, ddof=1)), 4) if len(vals) > 1 else None}
    ref = json.loads((C.OUT / "lro.json").read_text(encoding="utf-8"))["summary"]["combined"]
    crit = round(ref["mean"] - 0.01, 4)
    verdict = {"final_lro_l16": ref, "criterion": f"light mean LRO F1 >= {crit} (final {ref['mean']} - 0.01)",
               **{v: {"mean": summary[v]["mean"], "pass": bool(summary[v]["mean"] >= crit)} for v in a.light}}
    OUT.mkdir(parents=True, exist_ok=True)
    C.dump(OUT / a.out, {"held_out": held, "runs": res, "summary": summary, "verdict": verdict,
                                    "seconds": round(time.time() - t0)})
    print(json.dumps({"summary": summary, "verdict": verdict}, indent=1), flush=True)


if __name__ == "__main__":
    main()
