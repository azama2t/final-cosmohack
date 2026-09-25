"""L16: does per-scene water-median harmonization hurt in-distribution? Final model on MARIDA val, with/without it.
Also: spatial overlap between held-out regions (bay_islands vs honduras_gulf patches).

  .venv/Scripts/python.exe scripts/experiments/l16_harm_val.py
"""
from __future__ import annotations

import json

import numpy as np

import l16_common as C
import l16_lro as L


def main():
    import lightgbm as lgb

    meta_f = json.loads((C.ROOT / "weights" / "lgbm" / "meta.json").read_text(encoding="utf-8"))
    X, y, conf, meta = C.marida_pixels()
    b = lgb.Booster(model_file=str(C.ROOT / "weights" / "lgbm" / "model.txt"))
    isv = meta["split"] == "val"
    out = {}
    # restrict harmonization to val pixels: harmonized_features works per region -> build a val-only meta view
    mv = {k: v[isv] for k, v in meta.items()}
    idx, Xh, offs = L.harmonized_features(mv, list(np.unique(mv["region"])), np.asarray(meta_f["water_ref"], np.float32))
    assert np.array_equal(idx, np.arange(isv.sum()))
    t = y[isv] == 1
    p0 = C.predict(b, X[isv])
    p1 = C.predict(b, Xh)
    for nm, p in (("as_is", p0), ("harmonized", p1)):
        s = C.scores(*C.confusion(t, p >= 0.63))
        bt, bf = C.best_thr(t, p)
        out[nm] = {"f1_at_0.63": round(s["f1"], 4), "iou": round(s["iou"], 4), "best_thr": bt, "best_f1": round(bf, 4)}
    out["n_scenes_without_offset"] = sum(o is None for o in offs.values())
    # spatial overlap between regions (patch bboxes, WGS84)
    names = C.split_names("train") + C.split_names("val")
    reg = np.array([C.parse(n)["region"] for n in names])
    bb = np.array([C.bounds_wgs84(n) for n in names])
    cross = {}
    for r1 in ("bay_islands", "honduras_gulf"):
        for r2 in np.unique(reg):
            if r2 == r1:
                continue
            A, B = bb[reg == r1], bb[reg == r2]
            n = 0
            for a in A:
                w = np.minimum(a[2], B[:, 2]) - np.maximum(a[0], B[:, 0])
                h = np.minimum(a[3], B[:, 3]) - np.maximum(a[1], B[:, 1])
                n += int(np.any((w > 0) & (h > 0)))
            if n:
                cross[f"{r1}->{r2}"] = n
    out["cross_region_patch_overlaps"] = cross
    C.dump(C.OUT / "harm_val.json", out)
    print(out)


if __name__ == "__main__":
    main()
