"""Train the L3 pixel LightGBM on an ingested dataset (python -m macroplastic.ingest ... --convert).

  .venv\\Scripts\\python.exe scripts\\train_lgbm_ingest.py --data-root data\\ingest\\<name>
  .venv\\Scripts\\python.exe scripts\\train_lgbm_ingest.py --data-root data\\ingest\\<name> --set lgbm.num_boost_round=100

Re-uses scripts/train_lgbm.py (train_model, sample_train, prob_md, best_threshold, md_metrics) and
features.pixel.compute_features; scripts/train_lgbm.py itself is unchanged (MARIDA-only by default).

Split: official one if the manifest has both 'train' and 'val' (from folder names / CSV split column);
otherwise a GROUP split (manifest 'group' column = scene/prefix from the file name, or --group-regex on the id),
val = --val-frac of groups, positives and negatives groups split separately so val has both.
Missing model bands (e.g. no B1/B8 in L2A 20 m stacks) are filled from the nearest-wavelength band present
(recorded in meta.json as band_fill) -- a crutch, reported, not hidden.
Chip-level datasets (manifest chip_level=1): additionally chip metrics (score = p95 of pixel probability).
Output: weights_exp/lgbm_ingest/<name>_s<seed>/{model.txt, meta.json}; never touches weights/lgbm*.

L32 (organiser metric):
  --zero-as negative|ignore|both   organiser 0 / background (our 99 after ingest) as negative (default) or ignored;
                                   'both' = CV both and keep the better one.
  --cv K       group K-fold by scene (manifest 'group'), out-of-fold probabilities pooled over ALL valid pixels of
               the organiser train = their metric "F1 of the debris class over all pixels" (99 counted as negative;
               --metric-zero ignore to drop it); threshold = best OOF threshold (grid up to 0.999); the final model is
               then trained on ALL train chips with that threshold (meta.json 'threshold').
  --baseline DIR   (repeatable) evaluate an existing model (e.g. weights\lgbm = "our model as-is") on the SAME
                   chips with the same metric -> the fork "as-is vs trained on their train" in one table.
  --init-model DIR fine-tune: continue boosting from DIR/model.txt (+ num_boost_round trees).
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
if not os.environ.get("CUDA_VISIBLE_DEVICES"):  # PowerShell 5.1: $env:X="" deletes the variable
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

import train_lgbm as TL  # noqa: E402
import warnings as _w
_w.filterwarnings("ignore", message=".*(geotransform|NotGeoreferenced).*")  # rasterio on PNG/plain TIFF: noise in PowerShell
from macroplastic.features.pixel import BANDS11, compute_features, feature_names  # noqa: E402

UNLAB = 99   # ingest: organiser 0 / background
NEG0 = 14    # training code for "organiser 0 as negative" (capped separately from water 7, merged to 7 in labels)
# up to 0.999: on organiser data with 0 = negative the best OOF threshold of L30/L32 sat at the old grid edge
GRID = np.round(np.concatenate([np.arange(0.02, 0.98, 0.01), np.arange(0.98, 0.995, 0.005),
                                np.arange(0.995, 0.9991, 0.001)]), 3)


def zero_map(m: np.ndarray, zero_as: str) -> np.ndarray:
    if not (m == UNLAB).any():
        return m
    m = m.copy()
    m[m == UNLAB] = NEG0 if zero_as == "negative" else 0
    return m


WL = {"B1": 443, "B2": 492, "B3": 560, "B4": 665, "B5": 704, "B6": 740, "B7": 783, "B8": 833, "B8A": 865,
      "B9": 945, "B10": 1374, "B11": 1614, "B12": 2202}


def find_manifest(data_root: Path) -> Path:
    if (data_root / "manifest.csv").is_file():
        return data_root / "manifest.csv"
    hits = sorted(data_root.glob("converted/*/manifest.csv"))
    if not hits:
        raise SystemExit(f"no manifest.csv in {data_root} (run python -m macroplastic.ingest ... --convert first)")
    return hits[0]


def _abs(p: str, base: Path) -> Path:
    q = Path(p)
    if q.is_file():
        return q
    for b in (base, ROOT):
        if (b / p).is_file():
            return b / p
    return q


def band_fill(names: list[str]) -> dict:
    fill = {}
    for b in BANDS11:
        if b not in names:
            cand = [n for n in names if n in WL]
            if not cand:
                raise SystemExit(f"no Sentinel-2 bands among {names}")
            fill[b] = min(cand, key=lambda n: abs(WL[n] - WL[b]))
    return fill


def load_chip(img_path: Path, fill: dict):
    import rasterio

    with rasterio.open(img_path) as ds:
        a = ds.read().astype(np.float32)
        names = [d for d in ds.descriptions]
    if fill:
        extra = [a[names.index(src)] for src in fill.values()]
        a = np.concatenate([a, np.stack(extra)], 0)
        names = names + list(fill.keys())
    return a, names


def extract(rows, base, fill, max_px, seed):
    import rasterio

    rng = np.random.default_rng(seed)
    Xs, ys, pid = [], [], []
    for i, r in enumerate(rows):
        a, names = load_chip(_abs(r["image"], base), fill)
        with rasterio.open(_abs(r["mask"], base)) as ds:
            m = zero_map(ds.read(1), ZERO_AS[0])
        f = compute_features(a, names, "win")
        idx = np.flatnonzero(m.ravel() > 0)
        if len(idx) > max_px:
            pos = idx[m.ravel()[idx] == 1]
            neg = idx[m.ravel()[idx] != 1]
            neg = rng.choice(neg, max(0, max_px - len(pos)), replace=False) if len(neg) > max_px - len(pos) else neg
            idx = np.sort(np.concatenate([pos, neg]))
        F = f.reshape(f.shape[0], -1)
        Xs.append(F[:, idx].T.copy())
        ys.append(m.ravel()[idx])
        pid.append(np.full(len(idx), i, np.int32))
    if not Xs:
        return np.zeros((0, len(feature_names("win"))), np.float32), np.zeros(0, np.uint8), np.zeros(0, np.int32)
    return np.concatenate(Xs).astype(np.float32), np.concatenate(ys).astype(np.uint8), np.concatenate(pid)


ZERO_AS = ["negative"]


def _groups(rows, group_regex=None):
    out = []
    for r in rows:
        g = r.get("group") or r["id"]
        if group_regex:
            m = re.search(group_regex, r["id"])
            g = (m.group(1) if m.groups() else m.group(0)) if m else r["id"]
        out.append(g)
    return out


def _counts_at(hist_pos, hist_neg):
    """Cumulative (from the top) counts -> tp, fp, fn per GRID threshold (probabilities binned at 1e-3)."""
    cp = np.cumsum(hist_pos[::-1])[::-1]
    cn = np.cumsum(hist_neg[::-1])[::-1]
    idx = np.clip(np.round(GRID * 1000).astype(int), 0, 1000)
    tp, fp = cp[idx], cn[idx]
    fn = hist_pos.sum() - tp
    return tp, fp, fn


def _f1(tp, fp, fn):
    return np.where(tp > 0, 2 * tp / np.maximum(1, 2 * tp + fp + fn), 0.0)


def run_cv(a, rows, man, fill, cfg):
    """Group K-fold OOF on the organiser metric (+ baselines).  -> (result dict, pass-1 training data)."""
    import lightgbm as lgb
    import rasterio

    groups = _groups(rows, a.group_regex)
    ug = sorted(set(groups))
    rng = np.random.default_rng(a.seed)
    # pass 1: training pixels per chip (all target + up to --neg-per-chip others) + per-chip target presence
    Xs, Ys, cid = [], [], []
    has_pos = {}
    for i, r in enumerate(rows):
        a_, names = load_chip(_abs(r["image"], man.parent), fill)
        with rasterio.open(_abs(r["mask"], man.parent)) as ds:
            m0 = ds.read(1)
        f = compute_features(a_, names, "win").reshape(len(feature_names("win")), -1)
        mr = m0.ravel()
        valid = ~np.isnan(a_).any(0).ravel()
        pos = np.flatnonzero((mr == 1) & valid)
        oth = np.flatnonzero((mr > 1) & valid)
        if len(oth) > a.neg_per_chip:
            oth = rng.choice(oth, a.neg_per_chip, replace=False)
        idx = np.sort(np.concatenate([pos, oth]))
        Xs.append(f[:, idx].T.astype(np.float32))
        Ys.append(mr[idx].astype(np.uint8))
        cid.append(np.full(len(idx), i, np.int32))
        has_pos[groups[i]] = has_pos.get(groups[i], False) or bool(len(pos))
    X = np.concatenate(Xs)
    Y = np.concatenate(Ys)
    C = np.concatenate(cid)
    del Xs, Ys
    posg = [g for g in ug if has_pos[g]]
    negg = [g for g in ug if not has_pos[g]]
    rng.shuffle(posg)
    rng.shuffle(negg)
    K = max(2, min(a.cv, len(ug)))
    fold_of = {g: i % K for i, g in enumerate(posg)} | {g: (i + len(posg)) % K for i, g in enumerate(negg)}
    chip_fold = np.array([fold_of[g] for g in groups])
    modes = ["negative", "ignore"] if a.zero_as == "both" else [a.zero_as]
    all_names = feature_names("win")
    fidx = [all_names.index(n) for n in feature_names(cfg["features"])]
    models = {}
    for mode in modes:
        for k in range(K):
            tr = chip_fold[C] != k
            y = zero_map(Y[tr], mode)
            sel = TL.sample_train(y, cfg, a.seed)
            models[(mode, k)] = fit(X[tr][sel], y[sel], cfg, a.seed, fidx, a.init_model)
            print(f"[ingest-train] cv {mode} fold {k}: {int(tr.sum())} px pool, {len(sel)} sampled, "
                  f"target {int((y[sel] == 1).sum())}", flush=True)
    base = {}
    for bdir in a.baseline or []:
        bm = json.loads((Path(bdir) / "meta.json").read_text(encoding="utf-8"))
        bb = lgb.Booster(model_file=str(Path(bdir) / "model.txt"))
        base[str(bdir)] = (bb, [all_names.index(n) for n in bm["features"]], bm)
    # pass 2: OOF probabilities on ALL valid pixels, histogrammed (memory-free), per fold too
    keys = [f"train:{m}" for m in modes] + [f"baseline:{b}" for b in base]
    H = {k: {"pos": np.zeros((K, 1001)), "neg": np.zeros((K, 1001))} for k in keys}
    zero_neg = a.metric_zero == "negative"
    n_pos = n_valid = 0
    for i, r in enumerate(rows):
        a_, names = load_chip(_abs(r["image"], man.parent), fill)
        with rasterio.open(_abs(r["mask"], man.parent)) as ds:
            m0 = ds.read(1).ravel()
        valid = ~np.isnan(a_).any(0).ravel()
        lab = valid & ((m0 > 0) & (m0 != UNLAB) | ((m0 == UNLAB) & zero_neg))
        if not lab.any():
            continue
        F = compute_features(a_, names, "win").reshape(len(all_names), -1).T[lab]
        yp = m0[lab] == 1
        n_pos += int(yp.sum())
        n_valid += int(lab.sum())
        k = chip_fold[i]
        for key in keys:
            if key.startswith("train:"):
                p = TL.prob_md(models[(key[6:], k)], F[:, fidx], cfg["task"], [0, 1])
            else:
                bb, bf, bm = base[key[9:]]
                p = TL.prob_md(bb, F[:, bf], bm["task"], list(bm["classes"]))
            b = np.clip((np.nan_to_num(p) * 1000).astype(int), 0, 1000)
            H[key]["pos"][k] += np.bincount(b[yp], minlength=1001)
            H[key]["neg"][k] += np.bincount(b[~yp], minlength=1001)
    res = {"K": K, "folds": {g: int(f) for g, f in fold_of.items()}, "n_eval_px": n_valid, "n_target_px": n_pos,
           "metric": f"F1 of target over ALL valid pixels of the organiser train (OOF), organiser 0 "
                     f"{'= negative' if zero_neg else 'ignored'}", "variants": {}}
    for key in keys:
        hp, hn = H[key]["pos"].sum(0), H[key]["neg"].sum(0)
        tp, fp, fn = _counts_at(hp, hn)
        f1 = _f1(tp, fp, fn)
        j = int(np.argmax(f1))
        thr = float(GRID[j])
        folds = []
        for k in range(K):
            t2, f2, n2 = _counts_at(H[key]["pos"][k], H[key]["neg"][k])
            folds.append(round(float(_f1(t2, f2, n2)[j]), 4) if H[key]["pos"][k].sum() else None)
        d = {"f1": round(float(f1[j]), 4), "threshold": thr, "tp": int(tp[j]), "fp": int(fp[j]), "fn": int(fn[j]),
             "precision": round(float(tp[j] / max(1, tp[j] + fp[j])), 4),
             "recall": round(float(tp[j] / max(1, tp[j] + fn[j])), 4),
             "iou": round(float(tp[j] / max(1, tp[j] + fp[j] + fn[j])), 4), "fold_f1": folds,
             "fold_std": round(float(np.std([x for x in folds if x is not None])), 4) if any(
                 x is not None for x in folds) else None}
        if key.startswith("baseline:"):
            bthr = float(base[key[9:]][2].get("threshold") or 0.5)
            jj = int(np.argmin(np.abs(GRID - bthr)))
            d["f1_at_own_threshold"] = round(float(f1[jj]), 4)
            d["own_threshold"] = bthr
        res["variants"][key] = d
    return res, (X, Y, fidx)


def fit(X, y, cfg, seed, fidx, init_model=None):
    if not init_model:
        b, _ = TL.train_model(X, y, np.ones(len(y), np.uint8), cfg, seed, fidx)
        return b
    import lightgbm as lgb

    ym = TL.merge(y, cfg.get("merge_water", True))
    label = (ym == 1).astype(np.float32)
    w = np.where(label > 0, float(cfg["pos_weight"]), 1.0).astype(np.float32)
    prm = dict(cfg["lgbm"])
    n = int(prm.pop("num_boost_round"))
    prm.update(objective="binary", seed=seed, verbose=-1, deterministic=True, force_row_wise=True,
               num_threads=TL.n_threads())
    names = feature_names("win")
    ds = lgb.Dataset(X[:, fidx], label=label, weight=w, feature_name=[names[i] for i in fidx], free_raw_data=False)
    return lgb.train(prm, ds, num_boost_round=n, init_model=str(Path(init_model) / "model.txt"))


def split_rows(rows, val_frac, seed, group_regex=None):
    sp = {r.get("split", "") for r in rows}
    if {"train", "val"} <= sp:
        return [r for r in rows if r["split"] == "train"], [r for r in rows if r["split"] == "val"], \
            "official (manifest split column)"
    for r in rows:
        g = r.get("group") or r["id"]
        if group_regex:
            m = re.search(group_regex, r["id"])
            g = (m.group(1) if m.groups() else m.group(0)) if m else r["id"]
        r["_g"] = g
    groups = {}
    for r in rows:
        groups.setdefault(r["_g"], []).append(r)
    rng = np.random.default_rng(seed)
    pos = sorted(g for g, rs in groups.items() if any(int(x.get("n_debris_px") or 0) > 0 for x in rs))
    neg = sorted(g for g in groups if g not in set(pos))
    val = set()
    for lst in (pos, neg):
        lst = list(lst)
        rng.shuffle(lst)
        k = int(round(val_frac * len(lst)))
        if len(lst) >= 2:
            k = min(max(k, 1), len(lst) - 1)
        val |= set(lst[:k])
    tr = [r for r in rows if r["_g"] not in val]
    va = [r for r in rows if r["_g"] in val]
    how = (f"group split by {'--group-regex' if group_regex else 'manifest group'}: {len(groups)} groups, "
           f"val {len(val)} groups (positive groups {len(pos)}, negative {len(neg)}), seed {seed}")
    return tr, va, how


def auc(y, s):
    y = np.asarray(y, bool)
    if y.all() or (~y).all():
        return None
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s))
    ranks[order] = np.arange(1, len(s) + 1)
    # average ranks for ties
    su = np.asarray(s)[order]
    i = 0
    while i < len(su):
        j = i
        while j + 1 < len(su) and su[j + 1] == su[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j + 2) / 2
        i = j + 1
    n1 = y.sum()
    return float((ranks[y].sum() - n1 * (n1 + 1) / 2) / (n1 * (len(y) - n1)))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-root", required=True, help="data/ingest/<name> (or folder with manifest.csv)")
    ap.add_argument("--config", default=str(ROOT / "configs" / "lgbm.yaml"))
    ap.add_argument("--set", nargs="*", default=[], help="config overrides k=v (as in train_lgbm.py)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--val-frac", type=float, default=0.25)
    ap.add_argument("--group-regex", default=None, help="regex on sample id; group 1 = split group")
    ap.add_argument("--max-px-per-chip", type=int, default=20000)
    ap.add_argument("--out", default=None, help="default weights_exp/lgbm_ingest/<name>_s<seed>")
    ap.add_argument("--zero-as", choices=["negative", "ignore", "both"], default="negative",
                    help="organiser 0 / background (99 after ingest): negative (default; metric over ALL pixels), "
                         "ignore (0 = unlabelled), both (needs --cv: keep the better by OOF F1)")
    ap.add_argument("--metric-zero", choices=["negative", "ignore"], default="negative",
                    help="--cv metric: organiser 0 pixels count as negatives (their metric over all pixels) or not")
    ap.add_argument("--cv", type=int, default=0, help="group K-fold OOF on the organiser metric (0 = one split)")
    ap.add_argument("--baseline", action="append", default=None,
                    help="existing model dir evaluated on the same chips (e.g. weights\\lgbm = as-is)")
    ap.add_argument("--init-model", default=None, help="fine-tune from this model dir (continue boosting)")
    ap.add_argument("--neg-per-chip", type=int, default=4000, help="--cv: non-target training px per chip")
    ap.add_argument("--cv-only", action="store_true", help="--cv: do not train/save the final model")
    a = ap.parse_args(argv)
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    t0 = time.time()
    data_root = Path(a.data_root).resolve()
    man = find_manifest(data_root)
    rows = [r for r in csv.DictReader(open(man, encoding="utf-8")) if r.get("mask")]
    if not rows:
        raise SystemExit(f"{man}: no samples with masks")
    name = man.parent.name
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    for kv in a.set:
        k, v = kv.split("=", 1)
        TL.set_key(cfg, k, v)
    cfg["seed"] = a.seed
    names0 = rows[0]["bands"].split(",")
    fill = band_fill(names0)
    if fill:
        print(f"[ingest-train] missing model bands filled from nearest wavelength: {fill}")
    if a.zero_as == "both" and not a.cv:
        raise SystemExit("--zero-as both needs --cv K")
    if a.cv:
        return main_cv(a, rows, man, name, cfg, fill, t0)
    ZERO_AS[0] = a.zero_as
    tr, va, how = split_rows(rows, a.val_frac, a.seed, a.group_regex)
    print(f"[ingest-train] {len(rows)} samples: train {len(tr)}, val {len(va)} -- {how}")
    Xtr, ytr, _ = extract(tr, man.parent, fill, a.max_px_per_chip, a.seed)
    Xva, yva, pva = extract(va, man.parent, fill, a.max_px_per_chip * 5, a.seed + 1)
    t_feat = time.time() - t0
    if not (ytr == 1).any():
        raise SystemExit("no target (class 1) pixels in train -- check classes.map in adapter.yaml")
    all_names = feature_names("win")
    fnames = feature_names(cfg["features"])
    fidx = [all_names.index(n) for n in fnames]
    sel = TL.sample_train(ytr, cfg, a.seed)
    conf = np.ones(len(sel), np.uint8)
    t1 = time.time()
    booster, classes = TL.train_model(Xtr[sel], ytr[sel], conf, cfg, a.seed, fidx)
    t_train = time.time() - t1
    res = {}
    if len(yva):
        pv = TL.prob_md(booster, Xva[:, fidx], cfg["task"], classes)
        pv[np.isnan(Xva[:, :11]).any(1)] = 0.0
        true = yva == 1
        thr, met, _ = TL.best_threshold(true, pv, cfg["threshold_grid"])
        res = {"threshold": round(thr, 3), **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in met.items()},
               "at_0.5": {k: (round(v, 4) if isinstance(v, float) else v)
                          for k, v in TL.md_metrics(true, pv >= 0.5).items()},
               "auc": auc(true, pv), "n_val_px": int(len(yva)), "n_val_target_px": int(true.sum())}
        if any(str(r.get("chip_level", "0")) == "1" for r in va):
            sc = np.array([np.percentile(pv[pva == i], 95) if (pva == i).any() else 0.0 for i in range(len(va))])
            yc = np.array([int(r.get("chip_class") or 0) == 1 for r in va])
            best = max(((t, TL.md_metrics(yc, sc >= t)) for t in np.arange(0.02, 0.99, 0.01)),
                       key=lambda z: z[1]["f1"])
            res["chip"] = {"n": int(len(va)), "n_pos": int(yc.sum()), "auc": auc(yc, sc),
                           "threshold": round(float(best[0]), 3), "f1": round(best[1]["f1"], 4),
                           "precision": round(best[1]["precision"], 4), "recall": round(best[1]["recall"], 4),
                           "score": "p95 of pixel P(target) over the chip; threshold chosen on this val"}
    out = Path(a.out) if a.out else ROOT / "weights_exp" / "lgbm_ingest" / f"{name}_s{a.seed}"
    out.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out / "model.txt"))
    meta = {"model": "lgbm", "dataset": name, "manifest": str(man), "task": cfg["task"],
            "feature_level": cfg["features"], "features": fnames, "channels": BANDS11, "band_fill": fill,
            "classes": classes, "md_class": 1, "split": how, "n_train_samples": len(tr), "n_val_samples": len(va),
            "val_ids": [r["id"] for r in va], "n_train_px": int(len(sel)), "n_train_target_px": int((ytr[sel] == 1).sum()),
            "val": res, "threshold": res.get("threshold"), "config": cfg,
            "seconds": {"features": round(t_feat, 1), "train": round(t_train, 1), "total": round(time.time() - t0, 1)},
            "zero_as": a.zero_as,
            "note": "val metrics: pooled over labelled val pixels (ignore=0; organiser 0 counted per --zero-as), "
                    "threshold chosen on the same val (optimistic); small datasets -> high variance"}
    (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    msg = (f"[ingest-train] {name}: val F1={res.get('f1')} IoU={res.get('iou')} AUC={res.get('auc') and round(res['auc'], 4)}"
           f" thr={res.get('threshold')} (val px {res.get('n_val_px')}, target {res.get('n_val_target_px')})")
    if "chip" in res:
        msg += f" | chip F1={res['chip']['f1']} AUC={res['chip']['auc'] and round(res['chip']['auc'], 4)}"
    print(msg + f" | {meta['seconds']['total']}s -> {out}")
    return meta


def main_cv(a, rows, man, name, cfg, fill, t0):
    res, (X, Y, fidx) = run_cv(a, rows, man, fill, cfg)
    tv = {k: v for k, v in res["variants"].items() if k.startswith("train:")}
    best_key = max(tv, key=lambda k: tv[k]["f1"])
    mode = best_key[6:]
    print(f"[ingest-train] CV ({res['K']} folds by scene, {res['n_eval_px']} px, target {res['n_target_px']}; "
          f"{res['metric']}):")
    for k, v in res["variants"].items():
        extra = (f"  | at its own thr {v['own_threshold']}: F1 {v['f1_at_own_threshold']}"
                 if "own_threshold" in v else "")
        print(f"   {k:<40s} F1 {v['f1']:.4f} (thr {v['threshold']}, P {v['precision']}, R {v['recall']}) "
              f"folds {v['fold_f1']} std {v['fold_std']}{extra}")
    out = Path(a.out) if a.out else ROOT / "weights_exp" / "lgbm_ingest" / f"{name}_s{a.seed}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "cv.json").write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    if a.cv_only:
        print(f"[ingest-train] --cv-only: {out / 'cv.json'} ({time.time() - t0:.0f}s)")
        return {"cv": res}
    y = zero_map(Y, mode)
    sel = TL.sample_train(y, cfg, a.seed)
    booster = fit(X[sel], y[sel], cfg, a.seed, fidx, a.init_model)
    booster.save_model(str(out / "model.txt"))
    thr = tv[best_key]["threshold"]
    meta = {"model": "lgbm", "dataset": name, "manifest": str(man), "task": cfg["task"],
            "feature_level": cfg["features"], "features": feature_names(cfg["features"]), "channels": BANDS11,
            "band_fill": fill, "classes": [0, 1], "md_class": 1, "zero_as": mode, "init_model": a.init_model,
            "split": f"final model on ALL {len(rows)} train chips; threshold = best OOF threshold of group "
                     f"{res['K']}-fold CV", "threshold": thr, "cv": res, "val": tv[best_key],
            "n_train_px": int(len(sel)), "n_train_target_px": int((y[sel] == 1).sum()), "config": cfg,
            "seconds": {"total": round(time.time() - t0, 1)},
            "note": "OOF threshold on pooled organiser-train pixels: optimistic; fold spread in cv.fold_f1"}
    (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"[ingest-train] final: zero-as {mode}, thr {thr}, CV F1 {tv[best_key]['f1']} -> {out} "
          f"({meta['seconds']['total']}s)")
    return meta


if __name__ == "__main__":
    main()
