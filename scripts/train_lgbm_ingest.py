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
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import train_lgbm as TL  # noqa: E402
from macroplastic.features.pixel import BANDS11, compute_features, feature_names  # noqa: E402

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
            m = ds.read(1)
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
            "note": "val metrics: pooled over labelled val pixels (ignore=0), threshold chosen on the same val "
                    "(optimistic); small datasets -> high variance"}
    (out / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    msg = (f"[ingest-train] {name}: val F1={res.get('f1')} IoU={res.get('iou')} AUC={res.get('auc') and round(res['auc'], 4)}"
           f" thr={res.get('threshold')} (val px {res.get('n_val_px')}, target {res.get('n_val_target_px')})")
    if "chip" in res:
        msg += f" | chip F1={res['chip']['f1']} AUC={res['chip']['auc'] and round(res['chip']['auc'], 4)}"
    print(msg + f" | {meta['seconds']['total']}s -> {out}")
    return meta


if __name__ == "__main__":
    main()
