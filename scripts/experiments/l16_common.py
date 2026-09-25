"""L16 metric audit - shared helpers (independent of src/macroplastic/metrics.py and scripts/train_lgbm*.py).

Only MARIDA train/val split lists are read. The MARIDA test split list and test patches are never opened.
Imports from the project: only the feature function (macroplastic.features.pixel) - it is part of the model definition.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ["CUDA_VISIBLE_DEVICES"] = ""
THREADS = int(os.environ.get("L16_THREADS", "12"))

MARIDA = ROOT / "data" / "MARIDA"
L3_CACHE = ROOT / "out" / "l3_cache"
L13_CACHE = ROOT / "out" / "l13_cache"
OUT = ROOT / "weights_exp" / "l16"
OUT.mkdir(parents=True, exist_ok=True)

TILE2REGION = {
    "16PCC": "honduras_gulf", "16PDC": "honduras_gulf",
    "16PEC": "bay_islands", "16QED": "bay_islands",
    "18QYF": "haiti_pap", "18QYG": "haiti_pap", "18QWF": "haiti_west",
    "48MXU": "jakarta", "48MYU": "jakarta",
    "36JUN": "durban", "50LLR": "bali", "51PTS": "manila", "30VWH": "scotland",
    "48PZC": "danang", "51RVQ": "shanghai", "52SDD": "busan", "19QDA": "santo_domingo",
}
_RE = re.compile(r"^(?:S2_)?(\d{1,2})-(\d{1,2})-(\d{2})_([0-9]{2}[A-Z]{3})_(\d+)$")


def split_names(split: str) -> list[str]:
    assert split in ("train", "val"), "L16 never reads the MARIDA test split"
    return [ln.strip() for ln in (MARIDA / "splits" / f"{split}_X.txt").read_text().splitlines() if ln.strip()]


def parse(name: str) -> dict:
    m = _RE.match(name)
    d, mo, yy, tile, i = m.groups()
    scene = f"S2_{d}-{mo}-{yy}_{tile}"
    return {"scene": scene, "date": f"20{yy}-{int(mo):02d}-{int(d):02d}", "tile": tile,
            "region": TILE2REGION[tile], "stem": f"{scene}_{i}"}


def patch_file(name: str) -> Path:
    p = parse(name)
    return MARIDA / "patches" / p["scene"] / f"{p['stem']}.tif"


def read_patch(name: str):
    import rasterio

    f = patch_file(name)
    with rasterio.open(f) as ds:
        img = ds.read().astype(np.float32)
        bounds, crs = ds.bounds, ds.crs
    with rasterio.open(str(f)[:-4] + "_cl.tif") as ds:
        cl = ds.read(1).astype(np.uint8)
    with rasterio.open(str(f)[:-4] + "_conf.tif") as ds:
        conf = ds.read(1).astype(np.uint8)
    return img, cl, conf, bounds, crs


def bounds_wgs84(name: str):
    import rasterio
    from rasterio.warp import transform_bounds

    with rasterio.open(patch_file(name)) as ds:
        return transform_bounds(ds.crs, "EPSG:4326", *ds.bounds, densify_pts=21)


# ------------------------------------------------------------------ minimal metrics (own code)
def confusion(true_md: np.ndarray, pred_md: np.ndarray) -> tuple[int, int, int, int]:
    t = np.asarray(true_md, bool)
    p = np.asarray(pred_md, bool)
    tp = int(np.count_nonzero(t & p))
    fp = int(np.count_nonzero(~t & p))
    fn = int(np.count_nonzero(t & ~p))
    tn = int(t.size - tp - fp - fn)
    return tp, fp, fn, tn


def scores(tp, fp, fn, tn=0) -> dict:
    f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else float("nan")
    iou = tp / (tp + fp + fn) if (tp + fp + fn) else float("nan")
    p = tp / (tp + fp) if (tp + fp) else float("nan")
    r = tp / (tp + fn) if (tp + fn) else float("nan")
    return {"f1": f1, "iou": iou, "precision": p, "recall": r, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def f1_curve(true_md, prob, grid=np.round(np.arange(0.02, 0.98 + 1e-9, 0.01), 2)):
    """F1 at every threshold (vectorised via sorting)."""
    t = np.asarray(true_md, bool)
    pos = t.sum()
    out = []
    for th in grid:
        pr = prob >= th
        tp = np.count_nonzero(pr & t)
        fp = np.count_nonzero(pr) - tp
        fn = pos - tp
        out.append(2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0)
    return grid, np.array(out)


def best_thr(true_md, prob):
    g, f = f1_curve(true_md, prob)
    i = int(np.argmax(f))  # first max, like the project (strict '>' in a forward scan)
    return float(g[i]), float(f[i])


def rnd(d, k=4):
    return {a: (round(b, k) if isinstance(b, float) else b) for a, b in d.items()}


# ------------------------------------------------------------------ caches (read-only, produced by L3/L13)
def marida_pixels():
    """MARIDA train+val labelled pixels with 'win' features from out/l3_cache, + per-pixel split/scene/region."""
    from macroplastic.features.pixel import feature_names

    Xs, ys, cs, meta = [], [], [], []
    for split in ("train", "val"):
        z = np.load(L3_CACHE / f"{split}_win.npz", allow_pickle=False)
        assert list(z["names"]) == feature_names("win")
        names = split_names(split)
        pid = z["pid"]
        assert pid.max() < len(names)
        info = [parse(n) for n in names]
        Xs.append(z["X"]); ys.append(z["y"]); cs.append(z["conf"])
        meta.append({"split": np.full(len(pid), split),
                     "scene": np.array([i["scene"] for i in info])[pid],
                     "region": np.array([i["region"] for i in info])[pid],
                     "patch": np.array([f"{split}:{n}" for n in names])[pid]})
    X = np.concatenate(Xs); y = np.concatenate(ys); c = np.concatenate(cs)
    m = {k: np.concatenate([q[k] for q in meta]) for k in meta[0]}
    return X, y, c, m


def mados_pixels_all():
    """MADOS train+val labelled pixels (extended codes 1..19) from out/l13_cache, + per-pixel scene."""
    Xs, ys, cs, sc = [], [], [], []
    for split in ("train", "val"):
        z = np.load(L13_CACHE / f"mados_{split}_win.npz", allow_pickle=False)
        patches = [str(p) for p in z["patches"]]
        scenes = np.array([p.rsplit("_", 1)[0] for p in patches])
        Xs.append(z["X"]); ys.append(z["y"]); cs.append(z["conf"]); sc.append(scenes[z["pid"]])
    return np.concatenate(Xs), np.concatenate(ys), np.concatenate(cs), np.concatenate(sc)


def mados_overlap():
    rows = list(csv.DictReader(open(ROOT / "reports" / "mados_overlap.csv", encoding="utf-8")))
    return rows


def mados_scene_regions():
    """MADOS scene -> set of MARIDA regions it overlaps (any kind), and set of scenes marked NO."""
    reg, no = {}, set()
    for r in mados_overlap():
        s = r["mados_scene"]
        reg.setdefault(s, set())
        if r["kind"] != "none" and r["tile"]:
            reg[s].add(TILE2REGION[r["tile"]])
        if r["use_in_training"].startswith("NO"):
            no.add(s)
    return reg, no


# ------------------------------------------------------------------ training (same recipe as the final model)
LGB_PARAMS = {"num_leaves": 63, "learning_rate": 0.05, "min_data_in_leaf": 20, "feature_fraction": 0.8,
              "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0, "max_bin": 255}
ROUNDS = 400
POS_WEIGHT = 3.0


def sample_caps(y, seed, cap=30000, over={7: 60000}):
    rng = np.random.default_rng(seed)
    idx = [np.flatnonzero(y == 1)]
    for c in range(2, 20):
        ii = np.flatnonzero(y == c)
        k = over.get(c, cap)
        if len(ii) > k:
            ii = np.sort(rng.choice(ii, k, replace=False))
        idx.append(ii)
    return np.sort(np.concatenate(idx))


def train_booster(X, y, seed, fidx=None, rounds=ROUNDS):
    import lightgbm as lgb
    from macroplastic.features.pixel import feature_names

    names = feature_names("win")
    fidx = list(range(X.shape[1])) if fidx is None else list(fidx)
    label = (y == 1).astype(np.float32)
    w = np.where(label > 0, POS_WEIGHT, 1.0).astype(np.float32)
    params = dict(LGB_PARAMS, objective="binary", seed=seed, bagging_seed=seed, feature_fraction_seed=seed,
                  data_random_seed=seed, num_threads=THREADS, verbose=-1, deterministic=True, force_row_wise=True)
    ds = lgb.Dataset(X[:, fidx], label=label, weight=w, feature_name=[names[i] for i in fidx], free_raw_data=True)
    return lgb.train(params, ds, num_boost_round=rounds)


def predict(booster, X, fidx=None):
    fidx = list(range(X.shape[1])) if fidx is None else list(fidx)
    p = booster.predict(X[:, fidx], num_threads=THREADS).astype(np.float32)
    p[np.isnan(X[:, :11]).any(1)] = 0.0
    return p


def dump(path: Path, obj):
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
