"""Zero-shot check: the CURRENT detector (weights/lgbm, threshold from meta.json) on the new labelled pixels.

No training, no threshold tuning - only measurement. Per source / kind / region: share of pixels with p >= threshold
(recall for positive kinds, false-positive rate for D / U kinds) and median probability.

  .venv/Scripts/python.exe scripts/extra_data/eval_current_detector.py -> reports/extra_data/current_detector_zero_shot.csv
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "extra_data" / "current_detector_zero_shot.csv"


def main():
    meta = json.loads((ROOT / "weights/lgbm/meta.json").read_text(encoding="utf-8"))
    thr = float(meta["threshold"])
    bst = lgb.Booster(model_file=str(ROOT / "weights/lgbm/model.txt"))
    fnames = bst.feature_name()
    rows = []
    for name in ("plp", "floatingobjects"):
        p = ROOT / "data" / "extra" / f"features_{name}.npz"
        if not p.is_file():
            continue
        z = np.load(p)
        names = [str(s) for s in z["names"]]
        idx = [names.index(f) for f in fnames]
        X = z["X"][:, idx]
        ok = np.isfinite(X).all(1)
        prob = np.full(len(X), np.nan, np.float32)
        prob[ok] = bst.predict(X[ok])
        sc = z["scenes"][z["group"]]
        kind = z["kind"]
        y = z["y"]
        df = pd.DataFrame(dict(source=name, region=sc, kind=kind, y=y, prob=prob))
        if name == "plp":
            fr = z["frac"]
            df["kind"] = np.where((kind == "plp_target") & (y == 1) & (fr >= 0.5), "plp_plastic_frac>=0.5",
                                  np.where((kind == "plp_target") & (y == 1), "plp_plastic_frac<0.5",
                                           np.where((kind == "plp_target") & (y == 2), "plp_wood", kind)))
            df["region"] = np.where(pd.Series(sc).str.startswith("S2"), "PLP2019",
                                    np.where(pd.Series(sc).str.startswith("2021"), "PLP2021", "PLP2022"))
        df = df[np.isfinite(df.prob)]
        for (reg, k), d in list(df.groupby(["region", "kind"])) + [(("ALL", k), d) for k, d in df.groupby("kind")]:
            rows.append(dict(source=name, region=reg, kind=k, n=len(d), share_p_ge_thr=round(float((d.prob >= thr).mean()), 4),
                             median_p=round(float(d.prob.median()), 4), p90=round(float(d.prob.quantile(0.9)), 4)))
    res = pd.DataFrame(rows)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT, index=False)
    print(f"threshold {thr}")
    print(res[res.region == "ALL"].to_string(index=False))
    print(res[(res.region != "ALL")].to_string(index=False))


if __name__ == "__main__":
    main()
