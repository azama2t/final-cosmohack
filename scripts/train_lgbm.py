"""Lane L3: train pixel LightGBM for Marine Debris on MARIDA, select threshold on val.

  .venv/Scripts/python.exe scripts/train_lgbm.py --config configs/lgbm.yaml --exp bin_min --seed 0
  .venv/Scripts/python.exe scripts/train_lgbm.py --config configs/lgbm.yaml --set task=multiclass features=win --final

--set k=v overrides config keys (dotted keys allowed, values parsed as YAML).
--final writes weights/lgbm/{model.txt, meta.json}; otherwise weights_exp/lgbm/<exp>_s<seed>/.
Every run writes out/l3_runs/<exp>_s<seed>.json (config, val metrics, timings).
Only the official train and val splits are read. The test split is never touched (SPEC 1.7).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

from macroplastic.data.marida import BAND_NAMES, list_patches, load_patch  # noqa: E402
from macroplastic.features.pixel import compute_features, feature_names  # noqa: E402

CACHE = ROOT / "out" / "l3_cache"
RUNS = ROOT / "out" / "l3_runs"
ALLOWED_SPLITS = ("train", "val")  # never "test"


def n_threads() -> int:
    try:
        return len(os.sched_getaffinity(0))
    except AttributeError:
        try:
            import psutil

            return len(psutil.Process().cpu_affinity())
        except Exception:
            return os.cpu_count() or 4


# ----------------------------------------------------------------------------- data
def _augment(img, aug, seed):
    """Simulate Sen2Cor L2A vs ACOLITE rhorc: per-band negative offset (bigger in blue), gain, clip at 0."""
    rng = np.random.default_rng(seed)
    off = -rng.uniform(0.0, float(aug["offset"]), size=11).astype(np.float32)
    off[:3] *= float(aug.get("blue_mult", 1.5))
    g = rng.uniform(1 - float(aug["gain"]), 1 + float(aug["gain"]), size=11).astype(np.float32)
    x = img * g[:, None, None] + off[:, None, None]
    if aug.get("clip0", True):
        x = np.where(np.isnan(x), x, np.maximum(x, 1e-4))
    return x.astype(np.float32)


def _extract(p, aug=None, seed=0):
    img, cl, conf, _ = load_patch(p)
    if aug:
        img = _augment(img, aug, seed)
    m = cl > 0
    f = compute_features(img, BAND_NAMES, "win")
    return f[:, m].T.copy(), cl[m], conf[m]


def load_split(split: str, aug=None, aug_seed=0):
    """All labelled pixels of a split with 'win' features (superset of 'min'). Cached.

    aug (dict or None): if set, every patch is photometrically perturbed (see _augment) with
    a per-patch seed derived from aug_seed - an extra augmented copy of the split.
    """
    assert split in ALLOWED_SPLITS, f"split {split!r} not allowed in L3 training"
    CACHE.mkdir(parents=True, exist_ok=True)
    tag = "" if not aug else "_aug" + "_".join(f"{k}{aug[k]}" for k in sorted(aug)) + f"_s{aug_seed}"
    path = CACHE / f"{split}_win{tag}.npz"
    names = feature_names("win")
    if path.is_file():
        z = np.load(path, allow_pickle=False)
        if list(z["names"]) == names:
            return z["X"], z["y"], z["conf"], z["pid"]
    patches = list_patches(split)
    t = time.time()
    with ProcessPoolExecutor(max_workers=min(16, n_threads())) as ex:
        res = list(ex.map(_extract, patches, [aug] * len(patches),
                          [aug_seed * 100003 + i for i in range(len(patches))], chunksize=4))
    X = np.concatenate([r[0] for r in res]).astype(np.float32)
    y = np.concatenate([r[1] for r in res]).astype(np.uint8)
    conf = np.concatenate([r[2] for r in res]).astype(np.uint8)
    pid = np.concatenate([np.full(len(r[1]), i, np.int32) for i, r in enumerate(res)])
    np.savez(path, X=X, y=y, conf=conf, pid=pid, names=np.array(names))
    print(f"[cache] {split}: {len(patches)} patches, {len(y)} labelled px, {time.time() - t:.1f}s", flush=True)
    return X, y, conf, pid


def merge(y, enabled=True):
    y = y.copy()
    if enabled:
        y[np.isin(y, (12, 13, 14, 15))] = 7
    return y


def sample_train(y, cfg, seed):
    rng = np.random.default_rng(seed)
    s = cfg["sampling"]
    cap = int(s["cap_per_class"])
    over = {int(k): int(v) for k, v in (s.get("cap_overrides") or {}).items()}
    idx = [np.flatnonzero(y == 1)]
    for c in range(2, 16):
        ii = np.flatnonzero(y == c)
        k = over.get(c, cap)
        if len(ii) > k:
            ii = np.sort(rng.choice(ii, k, replace=False))
        idx.append(ii)
    return np.sort(np.concatenate(idx))


# ----------------------------------------------------------------------------- metrics
def md_metrics(true_md: np.ndarray, pred_md: np.ndarray) -> dict:
    """Pooled binary confusion over labelled pixels (ignore=0 already removed); class = MD."""
    tp = int(np.sum(true_md & pred_md))
    fp = int(np.sum(~true_md & pred_md))
    fn = int(np.sum(true_md & ~pred_md))
    tn = int(np.sum(~true_md & ~pred_md))
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * tp / (2 * tp + fp + fn) if tp else 0.0
    iou = tp / (tp + fp + fn) if tp else 0.0
    return {"f1": f1, "iou": iou, "precision": p, "recall": r, "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def best_threshold(true_md, prob, grid):
    lo, hi, st = grid
    best = None
    curve = []
    for t in np.arange(lo, hi + 1e-9, st):
        m = md_metrics(true_md, prob >= t)
        curve.append((round(float(t), 3), round(m["f1"], 4)))
        if best is None or m["f1"] > best[1]["f1"]:
            best = (float(t), m)
    return best[0], best[1], curve


# ----------------------------------------------------------------------------- model
def train_model(X, y_orig, conf, cfg, seed, fidx):
    import lightgbm as lgb

    task = cfg["task"]
    ym = merge(y_orig, cfg.get("merge_water", True))
    cw = {int(k): float(v) for k, v in cfg["conf_weights"].items()}
    w = np.array([cw.get(int(c), 1.0) for c in range(4)], np.float32)[np.minimum(conf, 3)]
    params = dict(cfg["lgbm"])
    rounds = int(params.pop("num_boost_round"))
    params.update(seed=seed, bagging_seed=seed, feature_fraction_seed=seed, data_random_seed=seed,
                  num_threads=n_threads(), verbose=-1, deterministic=True, force_row_wise=True)
    if task == "binary":
        label = (ym == 1).astype(np.float32)
        w = w * np.where(label > 0, float(cfg["pos_weight"]), 1.0).astype(np.float32)
        params.update(objective="binary")
        classes = [0, 1]
    elif task == "multiclass":
        classes = sorted(int(c) for c in np.unique(ym))
        lut = np.full(16, -1, np.int32)
        lut[classes] = np.arange(len(classes))
        label = lut[ym].astype(np.float32)
        cnt = np.bincount(label.astype(int), minlength=len(classes)).astype(np.float64)
        cwt = (cnt.max() / np.maximum(cnt, 1)) ** float(cfg["class_weight_power"])
        w = w * cwt[label.astype(int)].astype(np.float32)
        params.update(objective="multiclass", num_class=len(classes))
    else:
        raise ValueError(task)
    ds = lgb.Dataset(X[:, fidx], label=label, weight=w, feature_name=[feature_names("win")[i] for i in fidx],
                     free_raw_data=True)
    booster = lgb.train(params, ds, num_boost_round=rounds)
    return booster, classes


def prob_md(booster, X, task, classes, nthreads=None):
    p = booster.predict(X, num_threads=nthreads or n_threads())
    if task == "multiclass":
        return p[:, classes.index(1)].astype(np.float32)
    return p.astype(np.float32)


# ----------------------------------------------------------------------------- full-patch eval (postprocess)
_BOOSTER = None


def _init_booster(model_path):
    global _BOOSTER
    import lightgbm as lgb

    _BOOSTER = lgb.Booster(model_file=str(model_path))


def _full_prob(args):
    p, fidx, task, classes = args
    booster = _BOOSTER
    img, cl, _, _ = load_patch(p)
    f = compute_features(img, BAND_NAMES, "win")[fidx]
    F, H, W = f.shape
    pr = prob_md(booster, f.reshape(F, -1).T, task, classes, nthreads=2).reshape(H, W)
    pr[np.isnan(img).any(0)] = 0.0
    return pr, cl


def remove_small(mask, min_px):
    from scipy import ndimage

    lab, n = ndimage.label(mask, structure=np.ones((3, 3), bool))
    if n == 0:
        return mask
    sizes = np.bincount(lab.ravel())
    keep = sizes >= min_px
    keep[0] = False
    return keep[lab]


def eval_full_val(model_path, fidx, task, classes, thr_grid, min_px_list=(0, 2, 3)):
    patches = list_patches("val")
    args = [(p, fidx, task, classes) for p in patches]
    with ProcessPoolExecutor(max_workers=min(10, n_threads()), initializer=_init_booster,
                             initargs=(str(model_path),)) as ex:
        res = list(ex.map(_full_prob, args, chunksize=4))
    out = {}
    lo, hi, st = thr_grid
    for mp in min_px_list:
        best = None
        for t in np.arange(lo, hi + 1e-9, st):
            tm, pm = [], []
            for pr, cl in res:
                m = pr >= t
                if mp > 0:
                    m = remove_small(m, mp)
                lab = cl > 0
                tm.append(cl[lab] == 1)
                pm.append(m[lab])
            met = md_metrics(np.concatenate(tm), np.concatenate(pm))
            if best is None or met["f1"] > best[1]["f1"]:
                best = (float(t), met)
        out[str(mp)] = {"threshold": round(best[0], 3), **best[1]}
    return out


# ----------------------------------------------------------------------------- main
def set_key(cfg, key, val):
    d = cfg
    ks = key.split(".")
    for k in ks[:-1]:
        d = d.setdefault(k, {})
    d[ks[-1]] = yaml.safe_load(val)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "configs" / "lgbm.yaml"))
    ap.add_argument("--set", nargs="*", default=[])
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--exp", default="default")
    ap.add_argument("--final", action="store_true", help="write weights/lgbm")
    ap.add_argument("--full-eval", action="store_true", help="full-patch val maps + postprocess check")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    for kv in args.set:
        k, v = kv.split("=", 1)
        set_key(cfg, k, v)
    seed = int(args.seed if args.seed is not None else cfg["seed"])
    cfg["seed"] = seed
    np.random.seed(seed)

    t0 = time.time()
    Xtr, ytr, ctr, _ = load_split("train")
    Xva, yva, cva, pva = load_split("val")
    t_cache = time.time() - t0

    all_names = feature_names("win")
    fnames = feature_names(cfg["features"])
    fidx = [all_names.index(n) for n in fnames]

    sel = sample_train(ytr, cfg, seed)
    Xs, ys, cs = Xtr[sel], ytr[sel], ctr[sel]
    aug = cfg.get("augment") or None
    if aug and int(aug.get("copies", 0)) > 0:
        a = {k: v for k, v in aug.items() if k != "copies"}
        parts = [(Xs, ys, cs)]
        for c in range(int(aug["copies"])):
            Xa, ya, ca, _ = load_split("train", a, aug_seed=c)
            parts.append((Xa[sel], ya[sel], ca[sel]))  # same pixel order as the clean cache
        Xs = np.concatenate([q[0] for q in parts]); ys = np.concatenate([q[1] for q in parts])
        cs = np.concatenate([q[2] for q in parts])
    t1 = time.time()
    booster, classes = train_model(Xs, ys, cs, cfg, seed, fidx)
    t_train = time.time() - t1

    t2 = time.time()
    pv = prob_md(booster, Xva[:, fidx], cfg["task"], classes)
    pv[np.isnan(Xva[:, :11]).any(1)] = 0.0
    t_pred = time.time() - t2
    true_md = yva == 1
    thr, met, curve = best_threshold(true_md, pv, cfg["threshold_grid"])
    met05 = md_metrics(true_md, pv >= 0.5)

    # which true classes produce MD false positives (original 15-class scheme)
    per_class = {}
    for c in range(1, 16):
        m = yva == c
        if m.any():
            per_class[str(c)] = {"n": int(m.sum()), "pred_md": int(np.sum(pv[m] >= thr))}
    # recall by confidence of MD val pixels
    rec_conf = {}
    for c in (1, 2, 3):
        m = true_md & (cva == c)
        if m.any():
            rec_conf[str(c)] = round(float(np.mean(pv[m] >= thr)), 4)

    name = f"{args.exp}_s{seed}"
    out_dir = ROOT / "weights" / "lgbm" if args.final else ROOT / "weights_exp" / "lgbm" / name
    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / "model.txt"
    booster.save_model(str(model_path))
    size_mb = model_path.stat().st_size / 1e6

    full = None
    if args.full_eval or int(cfg.get("postprocess_min_px", 0)) > 0:
        t3 = time.time()
        full = eval_full_val(model_path, fidx, cfg["task"], classes, cfg["threshold_grid"])
        full["seconds"] = round(time.time() - t3, 1)

    gain = booster.feature_importance("gain")
    imp = sorted(zip(fnames, gain.tolist()), key=lambda t: -t[1])
    mc_conf = None
    if cfg["task"] == "multiclass":
        pr = booster.predict(Xva[:, fidx], num_threads=n_threads())
        am = np.array(classes)[pr.argmax(1)]
        yt = merge(yva, True)
        mc_conf = {"classes": classes,
                   "matrix": [[int(np.sum((yt == a) & (am == b))) for b in classes] for a in classes]}

    pp = int(cfg.get("postprocess_min_px", 0))
    final_thr = thr
    final_met = met
    if pp > 0 and full is not None:
        final_thr = full[str(pp)]["threshold"]
        final_met = {k: v for k, v in full[str(pp)].items() if k != "threshold"}

    meta = {
        "model": "lgbm",
        "task": cfg["task"],
        "feature_level": cfg["features"],
        "features": fnames,
        "channels": BAND_NAMES,
        "input": "reflectance 0..1 (trained on MARIDA ACOLITE rhorc)",
        "classes": classes,
        "md_class": 1,
        "water_ref": [round(float(v), 5) for v in np.nanmedian(Xtr[ytr == 7, :11], 0)],  # MARIDA train Marine Water medians (BANDS11)
        "threshold": round(final_thr, 3),
        "postprocess_min_px": pp,
        "seed": seed,
        "config": cfg,
        "n_train_px": int(len(ys)),
        "n_train_md": int(np.sum(ys == 1)),
        "n_val_px": int(len(yva)),
        "n_val_md": int(true_md.sum()),
        "val_md": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in final_met.items()},
        "val_md_pixelpool_no_pp": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in met.items()},
        "val_md_at_0.5": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in met05.items()},
        "val_recall_by_conf": rec_conf,
        "val_per_class_pred_md": per_class,
        "val_full_patch": full,
        "val_multiclass_confusion": mc_conf,
        "threshold_curve": curve,
        "importance_gain_top": [(n, round(g, 1)) for n, g in imp],
        "model_size_mb": round(size_mb, 2),
        "seconds": {"cache": round(t_cache, 1), "train": round(t_train, 1), "predict_val": round(t_pred, 1)},
        "note": "metrics on ALL labelled val pixels (ignore=0), pooled confusion; threshold chosen on val",
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{name}.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    m = meta["val_md"]
    print(f"[{name}] F1={m['f1']:.4f} IoU={m['iou']:.4f} P={m['precision']:.4f} R={m['recall']:.4f} "
          f"thr={meta['threshold']} | noPP F1={met['f1']:.4f} | train {t_train:.0f}s size {size_mb:.1f}MB"
          + (f" | full {json.dumps({k: round(v['f1'], 4) for k, v in full.items() if k != 'seconds'})}" if full else ""),
          flush=True)


if __name__ == "__main__":
    main()
