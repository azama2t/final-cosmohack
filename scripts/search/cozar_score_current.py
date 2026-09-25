"""Informational: current detector weights/lgbm (threshold from meta.json) on Cózar 2024 B pixels and unlabelled
clear-water background of the same crops (data/extra/features_cozar2024.npz). NOT a test set of the project; no
tuning on it. Output: data/extra/cozar2024/score_current.json
"""
import json
import os
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
import lightgbm as lgb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
z = np.load(ROOT / "data/extra/features_cozar2024.npz", allow_pickle=False)
m = json.load(open(ROOT / "weights/lgbm/meta.json", encoding="utf-8"))
names = list(z["names"])
idx = [names.index(n) for n in m["features"]]
b = lgb.Booster(model_file=str(ROOT / "weights/lgbm/model.txt"))
t = float(m["threshold"])
p = b.predict(z["X"][:, idx])
pb = b.predict(z["X_bg"][:, idx])
df = pd.DataFrame({"obj": z["obj"], "acq": z["acq"], "hit": p >= t})
o = df.groupby("obj").hit.agg(["any", "mean"])
res = {"threshold": t, "B_pixels": int(len(p)), "bg_pixels": int(len(pb)), "pixel_recall": float((p >= t).mean()),
       "bg_positive_rate": float((pb >= t).mean()), "filaments": int(len(o)),
       "filaments_with_ge1_px": float(o["any"].mean()), "median_filament_px_recall": float(o["mean"].median()),
       "per_scene_px_recall": {k: round(float(v), 3) for k, v in df.groupby("acq").hit.mean().items()}}
(ROOT / "data/extra/cozar2024/score_current.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
print(json.dumps(res, indent=1))
