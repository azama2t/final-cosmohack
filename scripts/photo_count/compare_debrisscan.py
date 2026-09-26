"""Our aerial model vs DebrisScan baseline on our spatial Winans test (rule recorded BEFORE the comparison, docs/LOG.md 05:51):
our model replaces the baseline only if per-frame count MAE is lower by >= 10 % AND the paired bootstrap CI95 of
(MAE_ours - MAE_baseline) is entirely < 0 AND precision is not lower than the baseline's by more than 0.02.
DebrisScan thresholds: its default 0.30 and one chosen on our VAL by the same rule as ours (min count MAE).
Two test sets: all 397 test chips (291 of them are in the authors' TRAINING split, i.e. seen by DebrisScan -> in its
favour) and the 106 test chips from the authors' EVALUATION split (unseen by both models).
Input: out/photo_count/debrisscan_{val,test}.json (scripts/photo_count/baseline_debrisscan.py, .venv-baseline),
       out/photo_count/preds_winans_w_{val,test}.npz (eval.py infer). Output: reports/photo_count/compare_debrisscan.json
"""
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "photo_count"))
from macroplastic.photo_count import metrics as M  # noqa: E402
from macroplastic.photo_count import winans as W  # noqa: E402
import eval as E  # noqa: E402

KM2 = 163.84e-6


def ds_preds(split, files):
    d = json.loads((ROOT / "out" / "photo_count" / f"debrisscan_{split}.json").read_text(encoding="utf-8"))
    return [(np.asarray(d[Path(f).name]["boxes"], float).reshape(-1, 4), np.asarray(d[Path(f).name]["scores"], float))
            for f in files]


def pick_thr(preds, gts):
    tc = np.array([len(g) for g in gts])
    grid = np.round(np.arange(0.05, 0.96, 0.05), 2)
    return float(min(grid, key=lambda t: (M.count_metrics(M.counts_at(preds, t), tc)["mae"],
                                          -M.count_metrics(M.counts_at(preds, t), tc)["exact"])))


def summary(preds, gts, thr, idx):
    tc = np.array([len(gts[i]) for i in idx])
    pc = M.counts_at([preds[i] for i in idx], thr)
    prf = M.prf_at(preds, gts, thr, idx=idx)
    return {"threshold": thr, "count_mae": float(np.abs(pc - tc).mean()), "count_bias": float((pc - tc).mean()),
            "density_mae_km2": float(np.abs(pc - tc).mean() / KM2), "ap50": M.ap_at_iou(preds, gts, idx=idx),
            "precision": prf["precision"], "recall": prf["recall"]}, np.abs(pc - tc)


def main():
    vf, vg = E.split_items("w:val")
    tf, tg = E.split_items("w:test")
    _, ov = E.load_preds(E.fname("winans", "w:val"))
    _, ot = E.load_preds(E.fname("winans", "w:test"))
    dv, dt = ds_preds("val", vf), ds_preds("test", tf)
    thr_o = pick_thr(ov, vg)
    thr_d = pick_thr(dv, vg)
    ch = W.load_all()
    auth = [ch[Path(f).name]["author_split"] for f in tf]
    subsets = {"all_test": list(range(len(tf))),
               "unseen_by_debrisscan": [i for i, s in enumerate(auth) if s == "eval"]}
    out = {"rule": "ours replaces baseline iff MAE_ours <= 0.9*MAE_base AND CI95(MAE_ours-MAE_base) < 0 AND "
                   "precision_ours >= precision_base - 0.02 (recorded 05:51, before the comparison)",
           "n_test": len(tf), "n_test_in_debrisscan_training": sum(s == "train" for s in auth), "results": {}}
    for name, idx in subsets.items():
        r = {}
        so, eo = summary(ot, tg, thr_o, idx)
        r["ours"] = so
        for lab, thr in (("debrisscan_default_0.30", 0.30), ("debrisscan_val_thr", thr_d)):
            sd, ed = summary(dt, tg, thr, idx)
            d = eo - ed
            ci = M.bootstrap_ci(lambda i: float(d[i].mean()), len(d), 2000)
            acc = bool(so["count_mae"] <= 0.9 * sd["count_mae"] and ci[1] < 0 and so["precision"] >= sd["precision"] - 0.02)
            r[lab] = {**sd, "mae_diff_ours_minus_base": float(d.mean()), "mae_diff_ci95": ci, "ours_accepted": acc}
        r["n"] = len(idx)
        out["results"][name] = r
        print(name, len(idx), json.dumps({k: (v if not isinstance(v, dict) else
                                              {kk: (round(vv, 3) if isinstance(vv, float) else vv) for kk, vv in v.items()})
                                          for k, v in r.items()}, ensure_ascii=False))
    (ROOT / "reports" / "photo_count" / "compare_debrisscan.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
