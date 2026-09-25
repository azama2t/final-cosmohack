#!/usr/bin/env python
"""label_forensics.py -- how was the mask made, and how much of it is derivable from the spectrum?

Usage (PowerShell, repo root):
    .venv\\Scripts\\python.exe scripts\\tools\\label_forensics.py --preset marida --out reports\\tools\\marida_forensics
    .venv\\Scripts\\python.exe scripts\\tools\\label_forensics.py --pairs-csv reports\\tools\\X_inspect\\pairs_guess.csv --out reports\\tools\\X_forensics
    .venv\\Scripts\\python.exe scripts\\tools\\label_forensics.py --images "D:\\org\\img\\*.tif" --masks "D:\\org\\lbl\\*.png" --out ...

Answers, per class (one-vs-rest, pixels, group k-fold split by scene/event/file -- never random pixels):
  * decision tree depth 3/4/5 on bands + indices (FDI/FAI/NDVI/NDWI/NBR/NDMI/NRD/BSI when bands exist, pairwise
    normalized differences otherwise, pre/post differences when 2 sources share bands) -> CV F1 (threshold tuned in-fold);
  * LightGBM (multiclass, 100 trees, max_depth 5) as "strong pixel model" reference;
  * best single threshold of one feature (CV F1) + in-sample threshold value: "mask == NBR < t ?";
  * text rules of the trees (the organizer's labelling rule may be literally there);
  * geometry: components (size, rectangularity, solidity), share of interior pixels (3x3 erosion), invariance to
    opening/closing, label edges vs spectral edges (gradient ratio), recall on edge vs interior pixels (buffers?),
    share of class pixels on tile borders vs expectation, class centroid offset (chips centred on objects?).
Pixel sampling: per file and class up to --cap pixels; every sampled pixel carries weight = count/taken, so all F1
numbers are estimates for the FULL pixel population (not for the balanced sample).
Output: <out>/forensics.md, forensics.html, forensics.json.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures as cf
import csv
import glob
import html
import json
import os
import re
import sys
import time
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO / "src"))
from inspect_dataset import _IMG_SUFFIX_RE, _MASK_SUFFIX_RE, name_template, stem_key  # noqa: E402

try:
    from macroplastic.organizer_adapter.adapter import canon_band  # noqa: E402
except Exception:  # pragma: no cover
    def canon_band(n):
        m = re.search(r"B0?(\d{1,2})(A?)", str(n).upper())
        return f"B{int(m.group(1))}{m.group(2)}" if m else str(n).upper()

MARIDA_CLASSES = {1: "Marine Debris", 2: "Dense Sargassum", 3: "Sparse Sargassum", 4: "Natural Organic Material",
                  5: "Ship", 6: "Clouds", 7: "Marine Water", 8: "Sediment-Laden Water", 9: "Foam", 10: "Turbid Water",
                  11: "Shallow Water", 12: "Waves", 13: "Cloud Shadows", 14: "Wakes", 15: "Mixed Water"}
FIRE_ROOT = Path(os.environ.get("FIRE_MONITORING_DIR", Path.home() / "hack" / "fire-monitoring")) / "data" / "train"  # optional external dataset

PRESETS = {
    "marida": dict(
        images=[str(_REPO / "data/MARIDA/patches/**/S2_*.tif")], exclude_regex=r"_(cl|conf)\.tif$",
        masks=str(_REPO / "data/MARIDA/patches/**/S2_*_cl.tif"),
        band_names="B1,B2,B3,B4,B5,B6,B7,B8,B8A,B11,B12", ignore=[0], group_regex=r"S2_(\d+-\d+-\d+_\w{5})_",
        split_lists=[str(_REPO / "data/MARIDA/splits/train_X.txt"), str(_REPO / "data/MARIDA/splits/val_X.txt")],
        class_names=MARIDA_CLASSES),
    "fire": dict(  # burn severity: S2 L2A DN pre + post, mask severity, group = fire event
        images=[str(FIRE_ROOT / "bs/sentinel2_pre/*.tif"), str(FIRE_ROOT / "bs/sentinel2_post/*.tif")],
        labels="pre,post", masks=str(FIRE_ROOT / "bs/masks/*.tif"), ignore=[255], exclude_bands=["SCL"],
        meta_csv=str(FIRE_ROOT / "bs/meta.csv"), meta_id_col="chip_id", meta_group_col="fire_event_id"),
    "fire_af": dict(  # active fire: VIIRS I1-I5 (+angles, valid), group = overpass datetime
        images=[str(FIRE_ROOT / "af/viirs/*.tif")], masks=str(FIRE_ROOT / "af/masks/*.tif"), ignore=[255],
        exclude_bands=["valid"], meta_csv=str(FIRE_ROOT / "af/meta.csv"), meta_id_col="chip_id",
        meta_group_col="acq_datetime"),
}

WL = {"B4": 664.8, "B8": 832.9, "B11": 1612.05}


# --------------------------------------------------------------------------- io
def _expand(spec: str) -> list[Path]:
    p = Path(spec)
    if p.is_dir():
        out = []
        for ext in ("tif", "tiff", "jp2", "png", "npy"):
            out += glob.glob(str(p / "**" / f"*.{ext}"), recursive=True)
        return sorted(Path(x) for x in out)
    return sorted(Path(x) for x in glob.glob(spec, recursive=True))


def _read(path: str | Path) -> tuple[np.ndarray, list, float | None]:
    p = Path(path)
    if p.suffix.lower() == ".npy":
        a = np.load(p)
        if a.ndim == 2:
            a = a[None]
        elif a.shape[-1] < a.shape[0]:
            a = np.moveaxis(a, -1, 0)
        return a, [None] * a.shape[0], None
    import rasterio

    with rasterio.open(p) as ds:
        return ds.read(), list(ds.descriptions), ds.nodata


def build_pairs(a) -> list[dict]:
    """-> [{id, images: [paths per source], mask, group}]"""
    rows = []
    if getattr(a, "manifest", None):
        # ingest manifest.csv (converted/<name>/manifest.csv): RAW organiser files (src_image/src_mask) + scene group
        with open(a.manifest, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                img = (r.get("src_image") or r["image"]).split(";")[0]
                msk = r.get("src_mask") or r.get("mask")
                if not msk:
                    continue
                rows.append({"id": r["id"], "images": [img], "mask": msk, "group": r.get("group") or None})
    elif a.pairs_csv:
        with open(a.pairs_csv, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if a.filter and not re.search(a.filter, r.get("mask", "") + r.get("mask_group", "")):
                    continue
                imgs = [r[k] for k in r if k.startswith("image") and not k.endswith("group") and r[k]]
                rows.append({"id": r.get("id") or Path(r["mask"]).stem, "images": imgs, "mask": r["mask"],
                             "group": r.get("group")})
    else:
        excl = re.compile(a.exclude_regex) if a.exclude_regex else None
        srcs = []
        n_coll = 0
        for spec in a.images:
            d = {}
            for p in _expand(spec):
                if excl and excl.search(str(p).replace("\\", "/")):
                    continue
                k = _sid(p, a.id_regex)
                n_coll += k in d
                d[k] = p
            srcs.append(d)
        masks = {}
        for p in _expand(a.masks):
            k = _sid(p, a.id_regex, mask=True)
            n_coll += k in masks
            masks[k] = p
        if n_coll:
            print(f"[forensics] ВНИМАНИЕ: {n_coll} файлов с одинаковым id (--id-regex {a.id_regex!r}) — пары "
                  "потеряны/перепутаны; задайте --id-regex, который включает префикс сцены, или --manifest")
        for sid, mp in sorted(masks.items()):
            if all(sid in s for s in srcs):
                rows.append({"id": sid, "images": [str(s[sid]) for s in srcs], "mask": str(mp), "group": None})
    # split lists (e.g. MARIDA train+val only -- keeps test untouched)
    if a.split_lists:
        member = {}
        for f in a.split_lists:
            for ln in Path(f).read_text().splitlines():
                if ln.strip():
                    member[ln.strip()] = Path(f).stem
        kept = []
        for r in rows:
            key = next((k for k in (r["id"], r["id"].removeprefix("S2_"), Path(r["images"][0]).stem.removeprefix("S2_"))
                        if k in member), None)
            if key is not None:
                if getattr(a, "group_by_split", False):
                    r["group"] = member[key]
                kept.append(r)
        rows = kept
    # groups
    meta = {}
    if a.meta_csv and Path(a.meta_csv).is_file():
        with open(a.meta_csv, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                meta[r[a.meta_id_col]] = r.get(a.meta_group_col) or ""
    for r in rows:
        g = r.get("group")
        if not g and a.group_regex:
            m = re.search(a.group_regex, Path(r["images"][0]).name)
            g = m.group(1) if m else None
        if not g and meta:
            key = next((k for k in (r["id"], Path(r["mask"]).stem.rsplit("_", 1)[0]) if k in meta), None)
            if key is None:
                key = next((k for k in meta if k in Path(r["mask"]).stem), None)
            g = meta.get(key) if key else None
            if g in ("", "nan", None):
                g = None
        r["group"] = g or r["id"]
    return rows


def _sid(p: Path, id_regex: str | None, mask: bool = False) -> str:
    """Pair key. Default = file stem without the mask/image suffix word, scene prefix KEPT
    ('r05_001_mask' -> 'r05_001'). The old default (numeric id of the name template, '001') collided across
    scenes: L30 rehearsal silently kept 30 of 207 pairs."""
    if id_regex:
        m = re.search(id_regex, str(p).replace("\\", "/"))
        if m:
            return m.group(1) if m.groups() else m.group(0)
    return stem_key(p.stem, _MASK_SUFFIX_RE if mask else _IMG_SUFFIX_RE)


# --------------------------------------------------------------------------- features
def _nd(a, b):
    with np.errstate(divide="ignore", invalid="ignore"):
        return (a - b) / (a + b)


def index_features(bands: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """bands: canonical name -> values (any shape). Only indices whose bands exist."""
    g = bands.get
    N = g("B8") if g("B8") is not None else g("B8A")
    out = {}
    if N is not None and g("B4") is not None:
        out["NDVI"] = _nd(N, g("B4"))
        out["NRD"] = N - g("B4")
    if N is not None and g("B3") is not None:
        out["NDWI"] = _nd(g("B3"), N)
    if N is not None and g("B12") is not None:
        out["NBR"] = _nd(N, g("B12"))
    if N is not None and g("B11") is not None:
        out["NDMI"] = _nd(N, g("B11"))
    if N is not None and g("B6") is not None and g("B11") is not None:
        # FDI, Biermann 2020 (lambda4 as in the paper, not lambda6 as in MARIDA code)
        out["FDI"] = N - (g("B6") + (g("B11") - g("B6")) * (WL["B8"] - WL["B4"]) / (WL["B11"] - WL["B4"]) * 10)
    if N is not None and g("B4") is not None and g("B11") is not None:
        out["FAI"] = N - (g("B4") + (g("B11") - g("B4")) * (833 - 665) / (1614 - 665))
    if all(g(k) is not None for k in ("B11", "B4", "B2")) and N is not None:
        out["BSI"] = _nd(g("B11") + g("B4"), N + g("B2"))
    return out


def make_features(src_vals: list[np.ndarray], src_names: list[list[str]], labels: list[str]) -> tuple[np.ndarray, list[str]]:
    """src_vals[i]: (C_i, n) sampled pixels of source i. -> (n, F) matrix, names."""
    cols, names = [], []
    per_src_idx = []
    for vals, bn, lab in zip(src_vals, src_names, labels):
        pre = f"{lab}_" if lab else ""
        bd = {}
        for v, n in zip(vals, bn):
            cols.append(v)
            names.append(pre + n)
            bd[canon_band(n)] = v.astype(np.float64)
        idx = index_features(bd)
        if not idx:  # unknown sensor: pairwise normalized differences of the first 8 bands
            keys = list(bd)[:8]
            for i in range(len(keys)):
                for j in range(i + 1, len(keys)):
                    idx[f"ND({keys[i]},{keys[j]})"] = _nd(bd[keys[i]], bd[keys[j]])
        for k, v in idx.items():
            cols.append(v)
            names.append(pre + k)
        per_src_idx.append(idx)
    if len(per_src_idx) == 2:  # pre/post style: difference of indices (e.g. dNBR = pre_NBR - post_NBR)
        a, b = per_src_idx
        for k in a:
            if k in b:
                cols.append(a[k] - b[k])
                names.append(f"d{k}({labels[0]}-{labels[1]})")
    X = np.stack(cols, 1).astype(np.float32)
    X[~np.isfinite(X)] = np.nan
    return X, names


# --------------------------------------------------------------------------- per-file processing
def _resize_nn(m: np.ndarray, shape) -> np.ndarray:
    if m.shape == tuple(shape):
        return m
    yi = (np.arange(shape[0]) * m.shape[0] / shape[0]).astype(int)
    xi = (np.arange(shape[1]) * m.shape[1] / shape[1]).astype(int)
    return m[yi][:, xi]


def process_file(r: dict, a, band_names_cli, rng_seed: int) -> dict | None:
    from scipy import ndimage as ndi

    rng = np.random.default_rng(rng_seed)
    srcs, names = [], []
    for k, p in enumerate(r["images"]):
        arr, desc, nod = _read(p)
        arr = arr.astype(np.float32)
        if nod is not None and np.isfinite(nod):
            arr[arr == nod] = np.nan
        if getattr(a, "nodata_zero", False):
            arr[:, (arr == 0).all(0)] = np.nan
        if getattr(a, "scale", None) not in (None, 1.0) or getattr(a, "offset", None):
            arr = arr * np.float32(a.scale or 1.0) + np.float32(a.offset or 0.0)
        if band_names_cli:
            bn = band_names_cli[k] if isinstance(band_names_cli[0], list) else band_names_cli
        else:
            bn = [d or f"band{i + 1}" for i, d in enumerate(desc)]
        keep = [i for i, n in enumerate(bn) if n not in (a.exclude_bands or [])]
        srcs.append(arr[keep])
        names.append([bn[i] for i in keep])
    H, W = srcs[0].shape[1:]
    for i in range(1, len(srcs)):
        if srcs[i].shape[1:] != (H, W):
            srcs[i] = np.stack([_resize_nn(b, (H, W)) for b in srcs[i]])
    m, _, mnod = _read(r["mask"])
    m = m[0]
    if m.dtype.kind == "f":
        m = np.where(np.isfinite(m), m, -1)
    m = _resize_nn(np.rint(m).astype(np.int64), (H, W))
    ign = set(a.ignore or [])
    if mnod is not None and np.isfinite(mnod):
        ign.add(int(mnod))
    for spec in a.merge or []:
        frm, to = spec.split(":")
        m[np.isin(m, [int(x) for x in frm.split(",")])] = int(to)
    valid = ~np.isin(m, list(ign)) & (m >= 0)
    # a pixel counts if ANY band is finite (VIIRS night chips have I1-I3 all NaN; NaN features are handled by the
    # models: trees see -999, LightGBM native missing values)
    valid &= np.any(np.stack([np.isfinite(s).any(0) for s in srcs]), 0)
    classes = [int(c) for c in np.unique(m[valid])]
    # brightness proxy for gradients: mean of per-band robust-normalised first source
    s0 = srcs[0]
    med = np.nanmedian(s0.reshape(s0.shape[0], -1), 1)[:, None, None]
    iqr = (np.nanpercentile(s0.reshape(s0.shape[0], -1), 90, 1) - np.nanpercentile(s0.reshape(s0.shape[0], -1), 10, 1))
    prox = np.nanmean((s0 - med) / (iqr[:, None, None] + 1e-9), 0)
    prox = np.nan_to_num(prox)
    grad = np.hypot(ndi.sobel(prox, 0), ndi.sobel(prox, 1))
    st3 = np.ones((3, 3), bool)
    out = {"id": r["id"], "group": r["group"], "H": H, "W": W, "classes": {}, "rows": None}
    sel_idx, sel_cls, sel_w, sel_edge = [], [], [], []
    for c in classes:
        cm = (m == c) & valid
        n = int(cm.sum())
        interior = ndi.binary_erosion(cm, st3)
        edge = cm & ~interior
        lab, ncomp = ndi.label(cm, structure=st3)
        sizes = np.bincount(lab.ravel())[1:]
        # rectangularity of components (area / bbox area), up to 300 biggest
        rect = []
        if ncomp:
            objs = ndi.find_objects(lab)
            order = np.argsort(-sizes)[:300]
            for k in order:
                if sizes[k] < 4:  # 1-3 px blobs are trivially "rectangular"
                    continue
                sl = objs[k]
                bb = (sl[0].stop - sl[0].start) * (sl[1].stop - sl[1].start)
                rect.append(sizes[k] / bb)
        ring = ndi.binary_dilation(cm, st3, iterations=3) & ~ndi.binary_dilation(cm, st3, iterations=1)
        opened = ndi.binary_opening(cm, st3)
        closed = ndi.binary_closing(cm, st3)
        b1 = np.zeros_like(cm)
        b1[:1, :] = b1[-1:, :] = True
        b1[:, :1] = b1[:, -1:] = True
        b8 = np.zeros_like(cm)
        b8[:8, :] = b8[-8:, :] = True
        b8[:, :8] = b8[:, -8:] = True
        yy, xx = np.nonzero(cm)
        out["classes"][c] = {
            "n": n, "n_interior": int(interior.sum()), "n_comp": int(ncomp), "sizes": sizes.tolist()[:2000],
            "rect": [round(float(x), 3) for x in rect],
            "open_same": int((opened & cm).sum()),
            "close_added": int((closed & ~cm & valid).sum()),
            "grad_edge": float(np.median(grad[edge])) if edge.any() else np.nan,
            "grad_interior": float(np.median(grad[interior])) if interior.any() else np.nan,
            "grad_ring": float(np.median(grad[ring & valid])) if (ring & valid).any() else np.nan,
            "grad_all": float(np.median(grad[valid])),
            "n_border1": int((cm & b1).sum()), "n_border8": int((cm & b8).sum()),
            "exp_border1": float(b1[valid].mean()) if valid.any() else 0, "exp_border8": float(b8[valid].mean()) if valid.any() else 0,
            "cy": float(yy.mean() / H - 0.5) if n else 0, "cx": float(xx.mean() / W - 0.5) if n else 0,
        }
        flat = np.flatnonzero(cm)
        take = min(len(flat), a.cap)
        pick = rng.choice(flat, take, replace=False) if take < len(flat) else flat
        sel_idx.append(pick)
        sel_cls.append(np.full(take, c))
        sel_w.append(np.full(take, len(flat) / take, dtype=np.float64))
        sel_edge.append(edge.ravel()[pick])
    if not sel_idx:
        return out
    idx = np.concatenate(sel_idx)
    vals = [s.reshape(s.shape[0], -1)[:, idx] for s in srcs]
    out["rows"] = {"vals": vals, "names": names, "y": np.concatenate(sel_cls), "w": np.concatenate(sel_w),
                   "edge": np.concatenate(sel_edge)}
    return out


# --------------------------------------------------------------------------- analysis
def wf1(y_true: np.ndarray, y_pred: np.ndarray, w: np.ndarray) -> float:
    tp = float(w[y_true & y_pred].sum())
    fp = float(w[~y_true & y_pred].sum())
    fn = float(w[y_true & ~y_pred].sum())
    return 2 * tp / (2 * tp + fp + fn) if tp > 0 else 0.0


def best_threshold(x: np.ndarray, y: np.ndarray, w: np.ndarray, nq: int = 256) -> tuple[float, int, float]:
    """-> (threshold, direction (+1: x>=t is positive, -1: x<=t), in-sample weighted F1)."""
    ok = np.isfinite(x)
    x, y, w = x[ok], y[ok], w[ok]
    if y.sum() == 0 or len(x) < 10:
        return np.nan, 1, 0.0
    o = np.argsort(x, kind="stable")
    xs, ys, ws = x[o], y[o], w[o]
    cp = np.cumsum(ws * ys)
    cn = np.cumsum(ws * ~ys)
    P, N = cp[-1], cn[-1]
    pos_idx = np.flatnonzero(ys)  # candidate cuts also at quantiles of the positives (rare classes)
    ks = np.unique(np.concatenate([np.linspace(0, len(xs) - 1, nq).astype(int),
                                   pos_idx[np.linspace(0, len(pos_idx) - 1, min(nq, len(pos_idx))).astype(int)],
                                   np.maximum(pos_idx[np.linspace(0, len(pos_idx) - 1, min(nq, len(pos_idx))).astype(int)] - 1, 0)]))
    # snap every cut to the END of its run of equal values: evaluation must match how the rule is applied
    ks = np.unique(np.searchsorted(xs, xs[ks], side="right") - 1)
    tp, fp = cp[ks], cn[ks]  # rule x <= xs[k]
    with np.errstate(divide="ignore", invalid="ignore"):
        f_le = np.where(tp > 0, 2 * tp / (2 * tp + fp + (P - tp)), 0.0)
        tp2, fp2 = P - cp[ks], N - cn[ks]  # rule x > xs[k]
        f_gt = np.where(tp2 > 0, 2 * tp2 / (2 * tp2 + fp2 + (P - tp2)), 0.0)
    i, j = int(np.argmax(f_le)), int(np.argmax(f_gt))
    if f_le[i] >= f_gt[j]:
        return float(xs[ks[i]]), -1, float(f_le[i])
    return float(xs[min(ks[j] + 1, len(xs) - 1)]), 1, float(f_gt[j])


def crossfit_f1(prob: np.ndarray, yc: np.ndarray, w: np.ndarray, folds) -> tuple[list[float], np.ndarray]:
    """OOF scores -> for each fold choose the threshold on the OTHER folds' OOF scores, score this fold.
    Returns (per-fold F1, boolean OOF predictions)."""
    fid = np.full(len(yc), -1)
    for k, (_, te) in enumerate(folds):
        fid[te] = k
    f1s, pred = [], np.zeros(len(yc), bool)
    for k, (_, te) in enumerate(folds):
        other = fid != k
        if yc[te].sum() == 0 or yc[other].sum() == 0:
            continue
        t, d, _ = best_threshold(prob[other], yc[other], w[other], nq=128)
        if not np.isfinite(t):
            continue
        pr = _apply_thr(prob[te], t, d)
        pred[te] = pr
        f1s.append(wf1(yc[te], pr, w[te]))
    return f1s, pred


def _auc(yc: np.ndarray, score: np.ndarray, w: np.ndarray) -> float:
    """Population-weighted ROC-AUC of OOF scores (prevalence-independent separability; 0.5 = chance)."""
    from sklearn.metrics import roc_auc_score

    if yc.all() or not yc.any():
        return float("nan")
    s = np.nan_to_num(score, nan=np.nanmin(score) if np.isfinite(score).any() else 0)
    return round(float(roc_auc_score(yc, s, sample_weight=w)), 4)


def _balanced(w: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Population weights w rescaled so that every class has the same total weight (mean weight 1)."""
    out = np.empty(len(w))
    cls = np.unique(y)
    for c in cls:
        m = y == c
        out[m] = w[m] / w[m].sum()
    return out * len(w) / out.sum()


def _apply_thr(x, t, d):
    with np.errstate(invalid="ignore"):
        return (x >= t) if d == 1 else (x <= t)


def analyse(results: list[dict], a, class_names: dict) -> dict:
    from sklearn.model_selection import GroupKFold
    from sklearn.tree import DecisionTreeClassifier, export_text

    parts = [r for r in results if r and r["rows"] is not None]
    labels = a.labels.split(",") if a.labels else [""] * len(parts[0]["rows"]["vals"])
    Xs, ys, ws, es, gs = [], [], [], [], []
    fnames = None
    for r in parts:
        X, fn = make_features(r["rows"]["vals"], r["rows"]["names"], labels)
        if fnames is None:
            fnames = fn
        elif fn != fnames:
            continue  # inconsistent band set -> skip file
        Xs.append(X)
        ys.append(r["rows"]["y"])
        ws.append(r["rows"]["w"])
        es.append(r["rows"]["edge"])
        gs.append(np.full(len(X), r["group"], dtype=object))
    X = np.concatenate(Xs)
    y = np.concatenate(ys)
    w = np.concatenate(ws)
    edge = np.concatenate(es)
    groups = np.concatenate(gs)
    Xf = np.nan_to_num(X, nan=-999.0)
    classes = sorted(np.unique(y).tolist())
    ng = len(set(groups))
    k = max(2, min(a.folds, ng))
    folds = list(GroupKFold(n_splits=k).split(Xf, y, groups))
    res = {"n_rows": int(len(y)), "n_files": len(parts), "n_groups": ng, "folds": k, "features": fnames,
           "classes": {}, "multiclass": {}}
    depths = [int(d) for d in a.depths.split(",")]

    # ---- per-class one-vs-rest trees + single thresholds
    for c in classes:
        yc = y == c
        cres = {"name": class_names.get(c, str(c)), "value": c,
                "pixels_total": float(w[yc].sum()), "sampled": int(yc.sum()),
                "files_with": int(sum(1 for r in parts if c in r["classes"]))}
        if yc.sum() < 5:
            res["classes"][c] = cres
            continue
        bal = _balanced(w, yc)
        for d in depths:
            oof = np.zeros(len(y))
            for tr, te in folds:
                t = DecisionTreeClassifier(max_depth=d, min_samples_leaf=10, random_state=0)
                t.fit(Xf[tr], yc[tr], sample_weight=bal[tr])
                pp = t.predict_proba(Xf[te])
                oof[te] = pp[:, 1] if pp.shape[1] > 1 else (float(t.classes_[0]) * np.ones(len(te)))
            f1s, pred_all = crossfit_f1(oof, yc, w, folds)
            cres[f"tree{d}_f1"] = float(np.mean(f1s)) if f1s else np.nan
            cres[f"tree{d}_f1_std"] = float(np.std(f1s)) if f1s else np.nan
            cres[f"tree{d}_auc"] = _auc(yc, oof, w)
            if d == max(depths):
                ye, ie = yc & edge, yc & ~edge
                cres["recall_edge_px"] = float(w[ye & pred_all].sum() / w[ye].sum()) if ye.any() else np.nan
                cres["recall_interior_px"] = float(w[ie & pred_all].sum() / w[ie].sum()) if ie.any() else np.nan
        # rules: depth-3 tree on all data (population-balanced weights)
        t3 = DecisionTreeClassifier(max_depth=3, min_samples_leaf=10, random_state=0)
        t3.fit(Xf, yc, sample_weight=bal)
        cres["rules_depth3"] = export_text(t3, feature_names=fnames, decimals=4, show_weights=False)
        # single-feature thresholds: threshold picked on the other folds, F1 on this fold
        thr_rows = []
        for j, fn in enumerate(fnames):
            f1s, _ = crossfit_f1(X[:, j], yc, w, folds)
            if f1s:
                thr_rows.append((float(np.mean(f1s)), fn, j))
        thr_rows.sort(reverse=True)
        best = []
        for f1cv, fn, j in thr_rows[:3]:
            t, dr, f1in = best_threshold(X[:, j], yc, w, nq=512)
            best.append({"feature": fn, "rule": f"{fn} {'>=' if dr == 1 else '<='} {t:.4g}", "cv_f1": round(f1cv, 4),
                         "insample_f1": round(f1in, 4)})
        cres["best_thresholds"] = best
        res["classes"][c] = cres

    # ---- multiclass: depth-3 rules; argmax F1 of the depth-max tree; LightGBM (OOF probs, cross-fitted thresholds)
    balm = _balanced(w, y)
    tall = DecisionTreeClassifier(max_depth=3, min_samples_leaf=10, random_state=0)
    tall.fit(Xf, y, sample_weight=balm)
    res["multiclass_rules_depth3"] = export_text(tall, feature_names=fnames, decimals=4)
    dmax = max(depths)
    pred = np.zeros(len(y), dtype=y.dtype)
    for tr, te in folds:
        t = DecisionTreeClassifier(max_depth=dmax, min_samples_leaf=10, random_state=0)
        t.fit(Xf[tr], y[tr], sample_weight=balm[tr])
        pred[te] = t.predict(Xf[te])
    res["multiclass"][f"tree{dmax}_argmax"] = {int(c): round(wf1(y == c, pred == c, w), 4) for c in classes}
    if not a.no_lgbm:
        try:
            import lightgbm as lgb

            oof = np.full((len(y), len(classes)), -1e9)  # log-probabilities; absent class = -inf
            for tr, te in folds:
                mdl = lgb.LGBMClassifier(n_estimators=100, num_leaves=31, max_depth=5, learning_rate=0.05, min_child_samples=50,
                                         subsample=0.8, subsample_freq=1, colsample_bytree=0.8, verbose=-1,
                                         random_state=0, n_jobs=a.threads)
                # weights: mean 1, clipped at 10 (unclipped class-balanced weights made multiclass boosting unstable: MD AUC 0.75 vs 0.99)
                mdl.fit(X[tr], y[tr], sample_weight=np.clip(balm[tr] * len(tr) / balm[tr].sum(), 0, 10))
                # log-softmax of raw scores: predict_proba saturates to exact 0/1 on unseen scenes and the ties
                # hurt ranking. Unconstrained depth (150 trees, leaf-wise) overfit scenes: MARIDA MD fold AUC 0.69 vs 0.99 at max_depth=5
                raw = mdl.predict(X[te], raw_score=True)
                raw = raw.reshape(len(te), -1) if raw.ndim > 1 or len(mdl.classes_) > 2 else np.stack([-raw, raw], 1) / 2
                pp = raw - raw.max(1, keepdims=True)
                pp = pp - np.log(np.exp(pp).sum(1, keepdims=True))
                for ci, c in enumerate(mdl.classes_):
                    oof[te, classes.index(int(c))] = pp[:, ci]
            res["multiclass"]["lgbm_argmax"] = {int(c): round(wf1(y == c, np.array(classes)[oof.argmax(1)] == c, w), 4)
                                                for c in classes}
            res["multiclass"]["lgbm_thr"] = {}
            for ci, c in enumerate(classes):
                f1s, _ = crossfit_f1(oof[:, ci], y == c, w, folds)
                res["multiclass"]["lgbm_thr"][int(c)] = round(float(np.mean(f1s)), 4) if f1s else np.nan
                res["classes"][c]["lgbm_auc"] = _auc(y == c, oof[:, ci], w)
        except Exception as e:
            res["lgbm_error"] = str(e)

    # ---- geometry aggregates
    for c in classes:
        geo = [r["classes"][c] for r in results if r and c in r["classes"]]
        n = sum(g["n"] for g in geo)
        sizes = np.concatenate([np.array(g["sizes"]) for g in geo]) if geo else np.array([])
        rect = np.concatenate([np.array(g["rect"]) for g in geo if g["rect"]]) if any(g["rect"] for g in geo) else np.array([])
        cres = res["classes"].setdefault(c, {"name": class_names.get(c, str(c)), "value": c})
        wsum = lambda key: float(np.nansum([g[key] * g["n"] for g in geo]) / max(n, 1))  # noqa: E731
        cres.update({
            "n_px_full": int(n), "n_components": int(sum(g["n_comp"] for g in geo)),
            "comp_size_median": float(np.median(sizes)) if sizes.size else np.nan,
            "comp_size_p90": float(np.percentile(sizes, 90)) if sizes.size else np.nan,
            "single_px_comp_frac": float((sizes == 1).mean()) if sizes.size else np.nan,
            "rectangularity_median": float(np.median(rect)) if rect.size else np.nan,
            "rect_like_comp_frac": float(np.mean(rect >= 0.95)) if rect.size else np.nan,
            "interior_frac": float(sum(g["n_interior"] for g in geo) / max(n, 1)),
            "opening_keeps_frac": float(sum(g["open_same"] for g in geo) / max(n, 1)),
            "closing_adds_frac": float(sum(g["close_added"] for g in geo) / max(n, 1)),
            "grad_edge_over_interior": float(np.nanmedian([g["grad_edge"] / g["grad_interior"] for g in geo
                                                           if np.isfinite(g["grad_edge"]) and np.isfinite(g["grad_interior"]) and g["grad_interior"] > 0]))
            if any(np.isfinite(g["grad_interior"]) for g in geo) else np.nan,
            "grad_edge_over_all": float(np.nanmedian([g["grad_edge"] / g["grad_all"] for g in geo
                                                      if np.isfinite(g["grad_edge"]) and g["grad_all"] > 0])) if geo else np.nan,
            "grad_ring_over_all": float(np.nanmedian([g["grad_ring"] / g["grad_all"] for g in geo
                                                      if np.isfinite(g["grad_ring"]) and g["grad_all"] > 0])) if geo else np.nan,
            "border1_ratio": float(sum(g["n_border1"] for g in geo) / max(n, 1) / max(wsum("exp_border1"), 1e-9)),
            "border8_ratio": float(sum(g["n_border8"] for g in geo) / max(n, 1) / max(wsum("exp_border8"), 1e-9)),
            "centroid_offset_mean": float(np.mean([np.hypot(g["cy"], g["cx"]) for g in geo])) if geo else np.nan,
        })
    return res


# --------------------------------------------------------------------------- report
def _fmt(v, nd=3):
    if v is None:
        return ""
    if isinstance(v, float):
        return "nan" if not np.isfinite(v) else f"{v:.{nd}f}"
    return str(v)


def verdicts(res: dict) -> list[str]:
    out = []
    tot = sum(r.get("pixels_total", 0) for r in res["classes"].values())
    for c, r in res["classes"].items():
        f5 = r.get("tree5_f1", np.nan)
        f3 = r.get("tree3_f1", np.nan)
        lg = res["multiclass"].get("lgbm_thr", {}).get(int(c), np.nan)
        bt = (r.get("best_thresholds") or [{}])[0]
        f1t = bt.get("cv_f1", np.nan) if bt else np.nan
        best = np.nanmax([f5, lg, f1t, f3]) if np.isfinite([f5, lg, f1t, f3]).any() else np.nan
        auc = np.nanmax([r.get("tree5_auc", np.nan), r.get("lgbm_auc", np.nan)]) \
            if np.isfinite([r.get("tree5_auc", np.nan), r.get("lgbm_auc", np.nan)]).any() else np.nan
        s = f"**{r['name']}** ({c}): "
        share = r.get("pixels_total", 0) / max(tot, 1e-9)
        if share > 0.5:
            s += (f"МАЖОРИТАРНЫЙ класс ({share:.0%} размеченных пикселей) — высокий F1 тривиален, смотреть AUC "
                  f"{auc:.3f} и F1 остальных классов [дерево-5 {f5:.2f}, LGBM {lg:.2f}]")
            out.append(s)
            continue
        if np.isfinite(f1t) and f1t >= 0.9:
            s += f"ОДИН ПОРОГ почти воспроизводит маску: `{bt.get('rule')}` CV F1 {f1t:.2f}"
        elif np.isfinite(f3) and f3 >= 0.9:
            s += f"почти правило: дерево глубины 3 даёт F1 {f3:.2f}"
        elif np.isfinite(best) and best >= 0.75:
            s += f"хорошо выводима из спектра пикселя (лучший CV F1 {best:.2f})"
        elif np.isfinite(best) and best >= 0.5:
            s += f"частично выводима из спектра пикселя (лучший CV F1 {best:.2f})"
        elif np.isfinite(best):
            s += f"из спектра пикселя выводима слабо (лучший CV F1 {best:.2f})"
        else:
            s += "мало данных"
        s += f" [дерево-3 {f3:.2f}, дерево-5 {f5:.2f}, LGBM {lg:.2f}, AUC {auc:.3f}]"
        if bt and not (np.isfinite(f1t) and f1t >= 0.9):
            s += f"; лучший порог `{bt.get('rule')}` CV F1 {bt.get('cv_f1')}"
        if r.get("single_px_comp_frac", 0) and r.get("single_px_comp_frac", 0) > 0.5:
            s += f"; {r['single_px_comp_frac']:.0%} компонент — одиночные пиксели"
        if np.isfinite(r.get("recall_edge_px", np.nan)) and np.isfinite(r.get("recall_interior_px", np.nan)) \
                and r["recall_interior_px"] - r["recall_edge_px"] > 0.2:
            s += f"; края хуже середины (recall {r['recall_edge_px']:.2f} vs {r['recall_interior_px']:.2f}) — возможен буфер/полигон шире объекта"
        if r.get("border8_ratio", 1) > 1.5:
            s += f"; у края тайла в {r['border8_ratio']:.1f}× чаще ожидаемого"
        if r.get("rectangularity_median", 0) > 0.9 and r.get("n_components", 0) > 10:
            s += "; компоненты почти прямоугольные (полигоны/тайлы?)"
        out.append(s)
    return out


def write_reports(res: dict, a, out: Path, seconds: float) -> None:
    depths = [int(d) for d in a.depths.split(",")]
    L = [f"# Label forensics — {a.title or a.preset or a.pairs_csv or a.masks}", "",
         f"Файлов: {res['n_files']}, групп (сцены/события) {res['n_groups']}, GroupKFold k={res['folds']}, "
         f"выборка пикселей {res['n_rows']:,} (≤{a.cap} на класс×файл, веса = count/taken → F1 на полной популяции). "
         f"Время {seconds:.0f} с.", "",
         f"Признаки ({len(res['features'])}): {', '.join(res['features'])}", "",
         "## Выводы (автоматически)", ""]
    L += [f"- {v}" for v in verdicts(res)]
    L += ["", "## Выводимость из спектра (one-vs-rest, CV F1 на полной популяции пикселей, порог подобран на ДРУГИХ фолдах)",
          "", "AUC — ROC-AUC OOF-скоров (не зависит от доли класса; 0.5 = случайно, 1 = разделимо). F1 зависит от доли "
          "класса: для редких классов (MD) даже хороший AUC даёт низкий F1 из-за ложных срабатываний на больших классах.", ""]
    dm = max(depths)
    hdr = (["класс", "px (полн.)", "файлов"] + [f"дерево{d}" for d in depths] + [f"AUC дерево{dm}", "LGBM (порог CV)",
                                                                                   "AUC LGBM", "лучший порог", "CV F1 порога"])
    L.append("| " + " | ".join(hdr) + " |")
    L.append("|" + "---|" * len(hdr))
    for c, r in res["classes"].items():
        bt = (r.get("best_thresholds") or [{}])[0]
        row = [f"{c} {r['name']}", f"{r.get('n_px_full', 0):,}", str(r.get("files_with", ""))]
        row += [f"{_fmt(r.get(f'tree{d}_f1'))}±{_fmt(r.get(f'tree{d}_f1_std'), 2)}" for d in depths]
        row += [_fmt(r.get(f"tree{dm}_auc")), _fmt(res["multiclass"].get("lgbm_thr", {}).get(int(c))), _fmt(r.get("lgbm_auc")),
                f"`{bt.get('rule', '')}`", _fmt(bt.get("cv_f1"))]
        L.append("| " + " | ".join(row) + " |")
    L += ["", "## Мультиклассовые модели (CV F1 по классам: argmax и LGBM с порогом)", ""]
    ms = list(res["multiclass"])
    L.append("| класс | " + " | ".join(ms) + " |")
    L.append("|" + "---|" * (len(ms) + 1))
    for c, r in res["classes"].items():
        L.append(f"| {c} {r['name']} | " + " | ".join(_fmt(res["multiclass"][m].get(int(c))) for m in ms) + " |")
    L += ["", "## Геометрия разметки", "",
          "interior = доля пикселей класса, переживших эрозию 3×3; opening_keeps = доля, переживших открытие 3×3 "
          "(≈1 → гладкие полигоны/морфология; ≈0 → точки/линии толщиной 1–2 px); grad edge/int = медиана градиента "
          "яркости на границе метки / внутри (≫1 → край метки идёт по краю объекта; ≈1 → край нарисован не по спектру); "
          "recall edge/int — полнота дерева-5 на краевых/внутренних пикселях; border8 = доля пикселей класса в 8-px рамке "
          "тайла / ожидаемая (≫1 — метки у краёв, ≪1 — чипы вырезаны вокруг объектов); centroid = средний сдвиг центра "
          "масс класса от центра тайла (0..0.71).", ""]
    hdr = ["класс", "компонент", "медиана размера", "p90", "1-px доля", "rect мед.", "interior", "opening_keeps",
           "closing_adds", "grad edge/int", "grad edge/all", "grad ring/all", "recall edge", "recall int", "border1", "border8", "centroid"]
    L.append("| " + " | ".join(hdr) + " |")
    L.append("|" + "---|" * len(hdr))
    for c, r in res["classes"].items():
        L.append("| " + " | ".join([f"{c} {r['name']}", str(r.get("n_components")), _fmt(r.get("comp_size_median"), 1),
                                    _fmt(r.get("comp_size_p90"), 1), _fmt(r.get("single_px_comp_frac"), 2),
                                    _fmt(r.get("rectangularity_median"), 2), _fmt(r.get("interior_frac"), 2),
                                    _fmt(r.get("opening_keeps_frac"), 2), _fmt(r.get("closing_adds_frac"), 2),
                                    _fmt(r.get("grad_edge_over_interior"), 2), _fmt(r.get("grad_edge_over_all"), 2),
                                    _fmt(r.get("grad_ring_over_all"), 2), _fmt(r.get("recall_edge_px"), 2),
                                    _fmt(r.get("recall_interior_px"), 2), _fmt(r.get("border1_ratio"), 2),
                                    _fmt(r.get("border8_ratio"), 2), _fmt(r.get("centroid_offset_mean"), 2)]) + " |")
    L += ["", "## Лучшие одиночные пороги (топ-3 признака на класс)", ""]
    for c, r in res["classes"].items():
        for b in r.get("best_thresholds", []):
            L.append(f"- {c} {r['name']}: `{b['rule']}` — CV F1 {b['cv_f1']}, in-sample F1 {b['insample_f1']}")
    L += ["", "## Правила деревьев глубины 3", "", "### Мультикласс", "", "```", res["multiclass_rules_depth3"].rstrip(), "```"]
    for c, r in res["classes"].items():
        if r.get("rules_depth3"):
            L += ["", f"### {c} {r['name']} (1 = класс)", "", "```", r["rules_depth3"].rstrip(), "```"]
    md = "\n".join(L) + "\n"
    (out / "forensics.md").write_text(md, encoding="utf-8")
    (out / "forensics.html").write_text(md_to_html(md, "label forensics"), encoding="utf-8")
    (out / "forensics.json").write_text(json.dumps(res, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)),
                                        encoding="utf-8")


def md_to_html(md: str, title: str) -> str:
    out, in_code, in_tab = [], False, False
    for ln in md.splitlines():
        if ln.startswith("```"):
            out.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            out.append(html.escape(ln))
            continue
        if ln.startswith("|"):
            cells = [c.strip() for c in ln.strip("|").split("|")]
            if all(re.fullmatch(r"-+", c) for c in cells if c):
                continue
            tag = "th" if not in_tab else "td"
            if not in_tab:
                out.append("<table>")
                in_tab = True
            out.append("<tr>" + "".join(f"<{tag}>{_inline(c)}</{tag}>" for c in cells) + "</tr>")
            continue
        if in_tab:
            out.append("</table>")
            in_tab = False
        if ln.startswith("#"):
            n = len(ln) - len(ln.lstrip("#"))
            out.append(f"<h{n}>{_inline(ln[n:].strip())}</h{n}>")
        elif ln.startswith("- "):
            out.append(f"<li>{_inline(ln[2:])}</li>")
        elif ln.strip():
            out.append(f"<p>{_inline(ln)}</p>")
    if in_tab:
        out.append("</table>")
    css = ("body{font-family:Segoe UI,Arial,sans-serif;background:#fcfcfb;color:#0b0b0b;margin:24px;max-width:1500px}"
           "table{border-collapse:collapse;font-size:12px}td,th{border:1px solid #ddd;padding:3px 6px}th{background:#f0efec}"
           "pre{background:#f4f3f0;padding:8px;font-size:11px;overflow:auto}code{background:#f0efec}")
    return f"<html><head><meta charset='utf-8'><title>{title}</title><style>{css}</style></head><body>" + "\n".join(out) + "</body></html>"


def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preset", choices=list(PRESETS))
    ap.add_argument("--images", action="append", help="dir or glob (repeat for several co-registered sources, e.g. pre/post)")
    ap.add_argument("--masks", help="dir or glob of masks")
    ap.add_argument("--pairs-csv", help="csv with columns id,image[,image_2...],mask[,group] (inspect_dataset pairs_guess.csv)")
    ap.add_argument("--manifest", help="ingest manifest.csv (data/ingest/<name>/converted/<name>/manifest.csv): ALL "
                                       "pairs, raw organiser files, scene groups -- recommended")
    ap.add_argument("--scale", type=float, default=None, help="multiply image values (e.g. 0.0001 for DN) -> "
                                                               "thresholds in reflectance")
    ap.add_argument("--offset", type=float, default=None, help="add after --scale (e.g. -0.1)")
    ap.add_argument("--nodata-zero", action="store_true", help="pixels with all bands == 0 -> nodata")
    ap.add_argument("--filter", help="regex on mask path / mask_group to select rows of --pairs-csv")
    ap.add_argument("--exclude-regex", help="skip image files matching this regex")
    ap.add_argument("--id-regex", help="regex (group 1) = sample id shared by image and mask; default: name template")
    ap.add_argument("--labels", help="comma labels of image sources (e.g. pre,post)")
    ap.add_argument("--band-names", help="comma band names in file order (default: GeoTIFF descriptions)")
    ap.add_argument("--exclude-bands", nargs="*", default=None, help="band names to drop (e.g. SCL valid)")
    ap.add_argument("--ignore", type=int, nargs="*", default=None, help="mask values to ignore (e.g. 0 or 255)")
    ap.add_argument("--merge", nargs="*", help='class merges "12,13,14,15:7"')
    ap.add_argument("--group-regex", help="regex (group 1) on image name = scene/group id for the split")
    ap.add_argument("--meta-csv", help="csv with sample id -> group column (for the group split)")
    ap.add_argument("--meta-id-col", default=None, help="default chip_id")
    ap.add_argument("--meta-group-col", default=None, help="default fire_event_id")
    ap.add_argument("--split-lists", nargs="*", help="keep only ids from these list files")
    ap.add_argument("--group-by-split", action="store_true",
                    help="folds = the --split-lists files themselves (e.g. official train vs val) instead of scene groups")
    ap.add_argument("--max-files", type=int, default=3000)
    ap.add_argument("--cap", type=int, default=300, help="max sampled pixels per class per file")
    ap.add_argument("--folds", type=int, default=3)
    ap.add_argument("--depths", default="3,4,5")
    ap.add_argument("--no-lgbm", action="store_true")
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--title")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    if not sys.stdout.isatty():
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    class_names = {}
    if a.preset:
        for k, v in PRESETS[a.preset].items():
            if k == "class_names":
                class_names = v
            elif getattr(a, k, None) in (None, [], False):
                setattr(a, k, v)
    a.meta_id_col = a.meta_id_col or "chip_id"
    a.meta_group_col = a.meta_group_col or "fire_event_id"
    if not (a.manifest or a.pairs_csv or (a.images and a.masks)):
        ap.error("give --preset, or --manifest, or --pairs-csv, or --images + --masks")
    if a.manifest:  # take band order / scale / offset from the ingest adapter.yaml next to the manifest
        ap_yaml = Path(a.manifest).resolve().parents[2] / "adapter.yaml"
        if ap_yaml.is_file():
            import yaml

            acfg = yaml.safe_load(ap_yaml.read_text(encoding="utf-8")) or {}
            src = (acfg.get("bands") or {}).get("source")
            if not a.band_names and isinstance(src, list):
                a.band_names = ",".join(map(str, src))
            rad = acfg.get("radiometry") or {}
            if a.scale is None and isinstance(rad.get("scale"), (int, float)):
                a.scale = float(rad["scale"])
            if a.offset is None and isinstance(rad.get("offset"), (int, float)):
                a.offset = float(rad["offset"])
            if 0 in ((acfg.get("nodata") or {}).get("values") or []):
                a.nodata_zero = True
            print(f"[forensics] из {ap_yaml}: каналы {a.band_names}, scale {a.scale}, offset {a.offset}")
    band_names = a.band_names.split(",") if a.band_names else None
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    pairs = build_pairs(a)
    n_all = len(pairs)
    if len(pairs) > a.max_files:
        rng = np.random.default_rng(0)
        pairs = [pairs[i] for i in sorted(rng.choice(len(pairs), a.max_files, replace=False))]
        print(f"[forensics] ВЫБОРКА: {len(pairs)} из {n_all} пар (--max-files {a.max_files}); все пары: "
              f"--max-files {n_all}")
    n_grp = len(set(p['group'] for p in pairs))
    print(f"[forensics] {len(pairs)} image-mask pairs (всего найдено {n_all}), {n_grp} groups")
    if n_grp == len(pairs) and len(pairs) > 20:
        print("[forensics] ВНИМАНИЕ: группа = файл (сцены не найдены): соседние кропы одной сцены попадут в разные "
              "фолды -> оптимистичные CV; задайте --group-regex или --manifest")
    if not pairs:
        return 2
    with cf.ThreadPoolExecutor(a.threads) as ex:
        results = list(ex.map(lambda ir: process_file(ir[1], a, band_names, ir[0]), enumerate(pairs)))
    print(f"[forensics] read + sampled in {time.time() - t0:.0f} s")
    res = analyse(results, a, class_names)
    write_reports(res, a, out, time.time() - t0)
    print(f"[forensics] done in {time.time() - t0:.0f} s -> {out / 'forensics.md'}")
    for v in verdicts(res):
        print("  -", v)
    return 0


if __name__ == "__main__":
    sys.exit(main())
