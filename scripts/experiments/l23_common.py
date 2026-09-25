"""Band-subset / light models - models on band subsets + a light fast model. Shared helpers.

Data = exactly the final-model recipe (weights/lgbm/meta.json): MARIDA train (sample_train caps, seed) + MADOS
train+val (exclude_same_place_val=True, mados_extra=False, caps with seed+7919), binary MD, pos_weight 3,
LightGBM 400 x 63 leaves. Only MARIDA train/val are read (test never). Features:
  * the 48 'win' features from the existing caches out/l3_cache (MARIDA) and out/l13_cache (MADOS) - read only;
  * 26 extra RGB-only features (band-subset / light models, computed here, cached in out/l23_cache, same pixel order as the caches):
      BRI=(B2+B3+B4)/3, NDGR=(B3-B4)/(B3+B4), NDBG=(B2-B3)/(B2+B3); mean/std 3/7/15 of each; value - local median
      (BRI 15/31, NDGR 15/31, NDBG 31) - same window code as features/pixel.py.
A band subset keeps every feature whose inputs are all in the subset (FEATURE_DEPS).
"""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("L13_THREADS", "10")
THREADS = int(os.environ.get("L23_THREADS", "10"))

from macroplastic.features import pixel as PX  # noqa: E402
from macroplastic.features.pixel import feature_names  # noqa: E402

L3_CACHE = ROOT / "out" / "l3_cache"
L13_CACHE = ROOT / "out" / "l13_cache"
CACHE = ROOT / "out" / "l23_cache"
WOUT = ROOT / "weights_exp" / "l23"
RUNS = ROOT / "out" / "l23_runs"
for d in (CACHE, WOUT, RUNS):
    d.mkdir(parents=True, exist_ok=True)

FINAL_META = json.loads((ROOT / "weights" / "lgbm" / "meta.json").read_text(encoding="utf-8"))
CFG = FINAL_META["config"]

WIN = feature_names("win")
X_KEYS = ["BRI", "NDGR", "NDBG"]
X_CONTRAST = [("BRI", 15), ("BRI", 31), ("NDGR", 15), ("NDGR", 31), ("NDBG", 31)]


def extra_names() -> list[str]:
    n = list(X_KEYS)
    for k in X_KEYS:
        for w in PX.WIN_SIZES:
            n += [f"{k}_mean{w}", f"{k}_std{w}"]
    n += [f"{k}_dmed{w}" for k, w in X_CONTRAST]
    return n


EXTRA = extra_names()
ALL = WIN + EXTRA

# inputs of every base quantity
BASE_DEPS = {b: {b} for b in PX.BANDS11}
BASE_DEPS.update({
    "FDI": {"B6", "B8", "B11"}, "FAI": {"B4", "B8", "B11"}, "NDVI": {"B4", "B8"}, "NDWI": {"B3", "B8"},
    "NDMI": {"B8", "B11"}, "SI": {"B2", "B3", "B4"}, "BSI": {"B2", "B4", "B8", "B11"}, "NRD": {"B4", "B8"},
    "BRI": {"B2", "B3", "B4"}, "NDGR": {"B3", "B4"}, "NDBG": {"B2", "B3"},
})


def feature_deps(name: str) -> set[str]:
    return BASE_DEPS[name.split("_")[0]]


def subset_features(bands: list[str], rgb_extra: bool) -> list[str]:
    s = set(bands)
    pool = WIN + (EXTRA if rgb_extra else [])
    return [n for n in pool if feature_deps(n) <= s]


# ------------------------------------------------------------------ extra RGB features
def _nd(a, b):
    return PX._ratio(a, b)


def rgb_extra_features(x11: np.ndarray, names: list[str] | None = None) -> np.ndarray:
    """x11: (11,H,W) in BANDS11 order (NaN = invalid). Returns (len(names), H, W) float32 (default all EXTRA)."""
    names = EXTRA if names is None else names
    B2, B3, B4 = x11[1], x11[2], x11[3]
    base = {"BRI": ((B2 + B3 + B4) / 3.0).astype(np.float32), "NDGR": _nd(B3, B4), "NDBG": _nd(B2, B3)}
    H, W = B2.shape
    out = np.empty((len(names), H, W), np.float32)
    want = {n: i for i, n in enumerate(names)}
    for k in X_KEYS:
        if k in want:
            out[want[k]] = base[k]
        for w in PX.WIN_SIZES:
            a, b = f"{k}_mean{w}", f"{k}_std{w}"
            if a in want or b in want:
                m, s = PX._win_mean_std(base[k], w)
                if a in want:
                    out[want[a]] = m
                if b in want:
                    out[want[b]] = s
    for k, w in X_CONTRAST:
        n = f"{k}_dmed{w}"
        if n in want:
            out[want[n]] = base[k] - PX._local_median(base[k], w, PX._nan_fill(base[k]))
    nanpix = np.isnan(x11).any(0)
    if nanpix.any():
        out[:, nanpix] = np.nan
    return out


def _extra_marida(p):
    from macroplastic.data.marida import BAND_NAMES, load_patch

    img, cl, conf, _ = load_patch(p)
    x = PX.select_bands(img, BAND_NAMES)
    x[~np.isfinite(x)] = np.nan
    m = cl > 0
    return rgb_extra_features(x)[:, m].T.copy(), cl[m]


def _extra_mados(name):
    from macroplastic.data import mados as MD

    img, cl, conf, _ = MD.load_patch(name, extra=True)
    x = PX.select_bands(img, MD.BAND_NAMES)
    x[~np.isfinite(x)] = np.nan
    m = cl > 0
    return rgb_extra_features(x)[:, m].T.copy(), cl[m]


def build_extra_cache():
    from macroplastic.data.marida import list_patches

    jobs = []
    for split in ("train", "val"):
        jobs.append((f"marida_{split}", _extra_marida, list_patches(split), L3_CACHE / f"{split}_win.npz"))
    for split in ("train", "val"):
        z = np.load(L13_CACHE / f"mados_{split}_win.npz", allow_pickle=False)
        jobs.append((f"mados_{split}", _extra_mados, [str(p) for p in z["patches"]], L13_CACHE / f"mados_{split}_win.npz"))
    for tag, fn, items, ref in jobs:
        path = CACHE / f"{tag}_rgbx.npz"
        if path.is_file():
            continue
        t = time.time()
        with ProcessPoolExecutor(max_workers=THREADS) as ex:
            res = list(ex.map(fn, items, chunksize=8))
        X = np.concatenate([r[0] for r in res]).astype(np.float32)
        y = np.concatenate([r[1] for r in res]).astype(np.uint8)
        zr = np.load(ref, allow_pickle=False)
        assert np.array_equal(zr["y"], y), f"{tag}: pixel order differs from {ref}"
        np.savez(path, X=X, y=y, names=np.array(EXTRA))
        print(f"[l23 cache] {tag}: {len(items)} patches, {len(y)} px, {time.time() - t:.0f}s", flush=True)


# ------------------------------------------------------------------ data assembly (final recipe)
_DATA = None


def load_all():
    """dict: marida_train/val, mados_train/val -> (X (n, 74) float32 in ALL order, y, conf, pid[, patches])."""
    global _DATA
    if _DATA is not None:
        return _DATA
    d = {}
    for split in ("train", "val"):
        z = np.load(L3_CACHE / f"{split}_win.npz", allow_pickle=False)
        assert list(z["names"]) == WIN
        e = np.load(CACHE / f"marida_{split}_rgbx.npz", allow_pickle=False)
        d[f"marida_{split}"] = (np.hstack([z["X"], e["X"]]), z["y"], z["conf"], z["pid"], None)
    for split in ("train", "val"):
        z = np.load(L13_CACHE / f"mados_{split}_win.npz", allow_pickle=False)
        assert list(z["names"]) == WIN
        e = np.load(CACHE / f"mados_{split}_rgbx.npz", allow_pickle=False)
        d[f"mados_{split}"] = (np.hstack([z["X"], e["X"]]), z["y"], z["conf"], z["pid"], [str(p) for p in z["patches"]])
    _DATA = d
    return d


def train_set(seed: int):
    """Exactly train_lgbm_mados.cmd_train(data=combined) with the final config -> X (ALL cols), y."""
    import train_lgbm as TL
    import train_lgbm_mados as TM
    from macroplastic.data import mados as MD

    d = load_all()
    Xtr, ytr, ctr, _, _ = d["marida_train"]
    sel = TL.sample_train(ytr, CFG, seed)
    parts = [(Xtr[sel], ytr[sel])]
    excl = TM.excluded_scenes(bool(CFG.get("exclude_marida_train_overlap", False)),
                              bool(CFG.get("exclude_same_place_val", False)))
    Xs, ys = [], []
    for sp in CFG.get("mados_splits", ["train", "val"]):
        X, y, c, pid, patches = d[f"mados_{sp}"]
        keep_p = np.array([MD.parse_patch(p)[0] not in excl for p in patches])
        m = keep_p[pid]
        if not CFG.get("mados_extra", True):
            m &= y <= 15
        Xs.append(X[m]); ys.append(y[m])
    Xm = np.concatenate(Xs); ym = np.concatenate(ys)
    s2 = TM.sample_ext(ym, CFG, seed + 7919)
    parts.append((Xm[s2], ym[s2]))
    X = np.concatenate([p[0] for p in parts]); y = np.concatenate([p[1] for p in parts])
    return X, y


def train(X, y, feats: list[str], seed: int, params: dict | None = None, rounds: int | None = None):
    import lightgbm as lgb

    fidx = [ALL.index(n) for n in feats]
    p = dict(CFG["lgbm"])
    r = int(p.pop("num_boost_round"))
    if params:
        p.update(params)
    rounds = rounds or r
    p.update(seed=seed, bagging_seed=seed, feature_fraction_seed=seed, data_random_seed=seed, num_threads=THREADS,
             verbose=-1, deterministic=True, force_row_wise=True, objective="binary")
    label = (y == 1).astype(np.float32)
    w = np.where(label > 0, float(CFG["pos_weight"]), 1.0).astype(np.float32)
    ds = lgb.Dataset(X[:, fidx], label=label, weight=w, feature_name=feats, free_raw_data=True)
    return lgb.train(p, ds, num_boost_round=rounds)


def evaluate(booster, feats: list[str]):
    """MARIDA val pooled labelled px; threshold = first max of F1 on grid 0.02..0.98 step 0.01 (train_lgbm)."""
    import train_lgbm as TL

    Xva, yva, _, _, _ = load_all()["marida_val"]
    fidx = [ALL.index(n) for n in feats]
    p = booster.predict(Xva[:, fidx], num_threads=THREADS).astype(np.float32)
    p[np.isnan(Xva[:, :11]).any(1)] = 0.0
    thr, met, _ = TL.best_threshold(yva == 1, p, CFG["threshold_grid"])
    return thr, met, p


def save(booster, feats, name: str, extra: dict):
    od = WOUT / name
    od.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(od / "model.txt"))
    gain = booster.feature_importance("gain")
    imp = sorted(zip(feats, gain.tolist()), key=lambda t: -t[1])
    meta = {"model": "lgbm", "task": "binary", "features": feats, "classes": [0, 1], "md_class": 1,
            "importance_gain": [(n, round(g, 1)) for n, g in imp], "n_trees": booster.num_trees(),
            "model_size_mb": round((od / "model.txt").stat().st_size / 1e6, 3), **extra,
            "note": "L23 experiment; recipe = weights/lgbm/meta.json config; MARIDA test never read"}
    (od / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
    (RUNS / f"{name}.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False, default=float), encoding="utf-8")
    return meta


def rnd(d, k=4):
    return {a: (round(b, k) if isinstance(b, float) else b) for a, b in d.items()}


if __name__ == "__main__":
    build_extra_cache()
