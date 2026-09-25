"""L90: how often does the CURRENT detector (weights/lgbm, read-only, threshold from meta.json, no harmonization,
no post-processing) call the new negatives 'debris'? Pixel-level false-positive rate per class / subset.

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/extra_data/l90_fp_check.py
Output: reports/extra_data/fp_current_detector.json (+ printed table).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

os.environ["CUDA_VISIBLE_DEVICES"] = ""
ROOT = Path(__file__).resolve().parents[2]


def main():
    import lightgbm as lgb
    meta = json.loads((ROOT / "weights" / "lgbm" / "meta.json").read_text(encoding="utf-8"))
    bst = lgb.Booster(model_file=str(ROOT / "weights" / "lgbm" / "model.txt"))
    thr = float(meta["threshold"])
    out = dict(model="weights/lgbm (read-only)", threshold=thr, sets={})
    for name in ("vessels_fi", "clouds_cmc"):
        p = ROOT / "data" / "extra" / f"features_{name}.npz"
        if not p.is_file():
            continue
        d = np.load(p, allow_pickle=False)
        names = [str(n) for n in d["names"]]
        idx = [names.index(f) for f in meta["features"]]
        X = d["X"][:, idx]
        prob = bst.predict(X, num_threads=8).astype(np.float32)
        pos = prob >= thr
        y = d["y"]
        subsets = {}
        if name == "vessels_fi":
            b8c = d["b8_contrast"]
            subsets["ship_box_all"] = y == 5
            subsets["ship_hull (B8_dmed15>0.01)"] = (y == 5) & (b8c > 0.01)
            subsets["water_ring"] = y == 7
            # box level: a box is 'flagged' if any of its pixels is positive
            bid = d["box_id"]
            sb = bid >= 0
            flagged = np.zeros(bid.max() + 1, bool)
            np.logical_or.at(flagged, bid[sb], pos[sb])
            out.setdefault("box_level", {})[name] = dict(boxes=int(len(flagged)), flagged=int(flagged.sum()),
                                                          frac=round(float(flagged.mean()), 4))
        else:
            e = d["edge"]
            for code, k in ((6, "cloud"), (13, "shadow"), (7, "clear_water")):
                subsets[k] = y == code
                subsets[k + "_edge"] = (y == code) & e
                subsets[k + "_interior"] = (y == code) & ~e
            sc = d["scene"]
            out.setdefault("scene_level", {})[name] = {
                "scenes": int(sc.max() + 1),
                "scenes_with_any_fp": int(len(np.unique(sc[pos]))),
            }
        out["sets"][name] = {k: dict(n=int(m.sum()), fp=int((pos & m).sum()),
                                     fp_rate=round(float((pos & m).sum() / max(m.sum(), 1)), 4),
                                     prob_p99=round(float(np.percentile(prob[m], 99)), 4) if m.any() else None)
                             for k, m in subsets.items()}
    rp = ROOT / "reports" / "extra_data"
    rp.mkdir(parents=True, exist_ok=True)
    (rp / "fp_current_detector.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
