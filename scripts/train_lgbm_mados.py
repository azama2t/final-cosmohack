"""Lane L13: MADOS in pixel LightGBM training (MARIDA val is the only judge, SPEC 1.7).

Sub-commands (repo root, PYTHONPATH=src, CUDA_VISIBLE_DEVICES=""):
  .venv/Scripts/python.exe scripts/train_lgbm_mados.py overlap
      MADOS <-> MARIDA (train+val only; MARIDA test is never read) content de-duplication -> reports/mados_overlap.csv
  .venv/Scripts/python.exe scripts/train_lgbm_mados.py overlap --with-marida-test   (orchestrator only, see --help)
  .venv/Scripts/python.exe scripts/train_lgbm_mados.py train --data marida|mados|combined --seed 0 [--exp NAME]
      [--set key=value ...]  (same config keys as configs/lgbm.yaml, plus the L13 keys below)
  .venv/Scripts/python.exe scripts/train_lgbm_mados.py train ... --final   -> weights_exp/mados/final/

Why content matching: MADOS GeoTIFFs are not georeferenced (identity transform, EPSG:4326, no tags) and folder names
are anonymous (Scene_k), so tile+date cannot be read. MARIDA and MADOS are both ACOLITE rhorc, but values are not
bit-identical (0 exact matches of (B4,B8) float pairs over all 2 803 MADOS crops), so matching uses a radiometry-robust
local binary pattern: for every pixel, 48 bits = sign(neighbour - centre > 3e-4) over the 24 neighbours of a 5x5 window
in B8 and in B4 (10 m native bands in both datasets). MARIDA train+val pixels (stride 2) are indexed by this key; every
MADOS pixel is looked up and each hit votes for (MARIDA patch, dy, dx). A true overlap gives thousands of votes at one
offset; random hits give <= 7 votes (median 2). A MADOS crop is matched to a MARIDA patch if votes >= MIN_VOTES and the offset
gives a non-empty intersection. A MADOS scene is 'marida_val' if ANY of its crops overlaps a MARIDA val patch ->
excluded from training entirely (all its crops, all MADOS splits).

L13 config keys (--set):
  mados_splits: [train, val]      MADOS splits used for training (MADOS test never used)
  mados_extra: true               MADOS-only classes (oil spill, oil platform, jellyfish, sea snot) as negatives
                                  (codes 16..19); false -> unlabelled (ignored)
  mados_weight: 1.0               per-pixel weight multiplier of MADOS pixels
  mados_classes: null             keep only these (MARIDA-coded) MADOS classes, e.g. [1] = MADOS debris only
  exclude_marida_train_overlap: false   also drop MADOS scenes = same acquisition as MARIDA train (duplicates)
  exclude_same_place_val: false   also drop MADOS scenes at the same place as a MARIDA val scene (other date)
Every run writes out/l13_runs/<exp>_s<seed>.json; weights -> weights_exp/mados/<exp>_s<seed>/ (never weights/lgbm).
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import os
import sys
import time
import warnings
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ["CUDA_VISIBLE_DEVICES"] = ""
warnings.filterwarnings("ignore")

import train_lgbm as TL  # noqa: E402
from macroplastic.data import mados as MD  # noqa: E402
from macroplastic.data import marida as MR  # noqa: E402
from macroplastic.features.pixel import compute_features, feature_names  # noqa: E402

MAX_THREADS = int(os.environ.get("L13_THREADS", "12"))
TL.n_threads = lambda: MAX_THREADS  # other lanes share the CPU (<= 12 threads)
CACHE = ROOT / "out" / "l13_cache"
RUNS = ROOT / "out" / "l13_runs"
WEXP = ROOT / "weights_exp" / "mados"
OVERLAP_CSV = ROOT / "reports" / "mados_overlap.csv"
OVERLAP_JSON = CACHE / "mados_overlap_crops.json"

LBP_EPS = 3e-4
LBP_OFFS = [(dy, dx) for dy in range(-2, 3) for dx in range(-2, 3) if (dy, dx) != (0, 0)]
MIN_VOTES = int(os.environ.get("L13_MIN_VOTES", "8"))  # random hits give <= 7 votes (observed max)
SAME_ACQ_RATIO = 0.01


# ============================================================================ overlap (content de-duplication)
def lbp_key(b4: np.ndarray, b8: np.ndarray):
    """48-bit local binary pattern key per pixel (5x5, B8 and B4). Returns key (H,W) uint64 and valid mask."""
    from scipy import ndimage

    H, W = b8.shape
    k = np.zeros((H - 4, W - 4), np.uint64)
    bit = 0
    for b in (b8, b4):
        c = b[2:H - 2, 2:W - 2]
        for dy, dx in LBP_OFFS:
            n = b[2 + dy:H - 2 + dy, 2 + dx:W - 2 + dx]
            with np.errstate(invalid="ignore"):
                k |= ((n - c) > LBP_EPS).astype(np.uint64) << np.uint64(bit)
            bit += 1
    ok = np.ones_like(k, bool)
    for b in (b8, b4):
        ok &= ndimage.minimum_filter(np.isfinite(b).astype(np.uint8), 5)[2:H - 2, 2:W - 2] > 0
    key = np.zeros((H, W), np.uint64)
    valid = np.zeros((H, W), bool)
    key[2:H - 2, 2:W - 2] = k
    valid[2:H - 2, 2:W - 2] = ok
    return key, valid


def _marida_keys(args):
    i, p = args
    import rasterio

    with rasterio.open(p) as ds:
        a = ds.read([4, 8]).astype(np.float32)  # B4, B8 (1-based band indices in MARIDA order)
    key, ok = lbp_key(a[0], a[1])
    ok[1::2, :] = False
    ok[:, 1::2] = False
    yy, xx = np.nonzero(ok)
    return key[ok], np.full(len(yy), i, np.int32), (yy * 256 + xx).astype(np.int32)


def _mados_b4b8(name):
    r = MD.resolve_root()
    sc, cr = MD.parse_patch(name)
    b4 = MD._read(r / sc / "10" / f"{sc}_L2R_rhorc_665_{cr}.tif").astype(np.float32)
    b8 = MD._read(r / sc / "10" / f"{sc}_L2R_rhorc_833_{cr}.tif").astype(np.float32)
    return b4, b8


_IDX = None


def _init_idx(path):
    global _IDX
    z = np.load(path)
    _IDX = (z["keys"], z["pid"], z["pix"])


def _match_crop(name):
    keys, pid, pix = _IDX
    b4, b8 = _mados_b4b8(name)
    key, ok = lbp_key(b4, b8)
    yy, xx = np.nonzero(ok)
    k = key[ok]
    j = np.minimum(np.searchsorted(keys, k), len(keys) - 1)
    hit = keys[j] == k
    if not hit.any():
        return name, []
    q = pid[j[hit]]
    mp = pix[j[hit]]
    dy = (mp // 256 - yy[hit]).astype(np.int64)
    dx = (mp % 256 - xx[hit]).astype(np.int64)
    code = (q.astype(np.int64) * 1024 + (dy + 512)) * 1024 + (dx + 512)
    u, c = np.unique(code, return_counts=True)
    sel = c >= MIN_VOTES
    out = []
    for cc, n in zip(u[sel], c[sel]):
        qq = int(cc // (1024 * 1024))
        ddy = int((cc // 1024) % 1024) - 512
        ddx = int(cc % 1024) - 512
        # intersection of MADOS crop (240x240 at 0,0) shifted into MARIDA patch coords (256x256)
        h = max(0, min(256, 240 + ddy) - max(0, ddy))
        w = max(0, min(256, 240 + ddx) - max(0, ddx))
        if h * w > 0:
            out.append((qq, ddy, ddx, int(n), int(h * w)))
    second = int(c[~sel].max()) if (~sel).any() else 0  # best non-match vote (noise level)
    return name, [o + (second,) for o in out]


def cmd_overlap(a):
    CACHE.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    msplit = {}
    pats = []
    splits = ("train", "val", "test") if a.with_marida_test else ("train", "val")  # test: orchestrator only
    for s in splits:
        for p in MR.list_patches(s):
            pats.append(p)
            msplit[p.stem] = s
    idx_path = CACHE / ("marida_all_lbp_index.npz" if a.with_marida_test else "marida_trval_lbp_index.npz")
    out_csv = CACHE / "mados_overlap_with_test.csv" if a.with_marida_test else OVERLAP_CSV
    if not idx_path.is_file():
        with ProcessPoolExecutor(max_workers=MAX_THREADS) as ex:
            res = list(ex.map(_marida_keys, list(enumerate(pats)), chunksize=8))
        keys = np.concatenate([r[0] for r in res])
        pid = np.concatenate([r[1] for r in res])
        pix = np.concatenate([r[2] for r in res])
        o = np.argsort(keys, kind="stable")
        np.savez(idx_path, keys=keys[o], pid=pid[o], pix=pix[o],
                 names=np.array([p.stem for p in pats]))
    z = np.load(idx_path)
    names_idx = [str(s) for s in z["names"]]
    assert names_idx == [p.stem for p in pats], "stale index"
    print(f"[overlap] index {len(z['keys'])} keys, {time.time() - t0:.1f}s", flush=True)

    crops = MD.list_patches("all")
    t1 = time.time()
    with ProcessPoolExecutor(max_workers=MAX_THREADS, initializer=_init_idx, initargs=(str(idx_path),)) as ex:
        res = list(ex.map(_match_crop, crops, chunksize=8))
    print(f"[overlap] matched {len(crops)} MADOS crops in {time.time() - t1:.1f}s", flush=True)

    dsplit = MD.split_of()
    per_crop = {}
    for name, hits in res:
        per_crop[name] = [{"marida_patch": names_idx[q], "marida_split": msplit[names_idx[q]], "dy": dy, "dx": dx,
                           "votes": n, "overlap_px": ov, "second_votes": sec} for q, dy, dx, n, ov, sec in hits]
    (OVERLAP_JSON.with_name("mados_overlap_crops_with_test.json") if a.with_marida_test else OVERLAP_JSON).write_text(
        json.dumps(per_crop, indent=0), encoding="utf-8")

    # scene-level table. A match is 'same_acquisition' (the same S2 pixels: votes/overlap >= SAME_ACQ_RATIO,
    # observed 0.02..0.25) or 'same_place_other_date' (static coast/land texture only: ratio ~0.0002..0.002).
    by_scene = collections.defaultdict(list)
    for name in crops:
        by_scene[MD.parse_patch(name)[0]].append(name)
    rows = []
    for sc in MD.list_scenes():
        cr = by_scene[sc]
        msc = {}
        for n in cr:
            for h in per_crop[n]:
                kind = "same_acquisition" if h["votes"] / h["overlap_px"] >= SAME_ACQ_RATIO else "same_place_other_date"
                s = MR.parse_scene(h["marida_patch"])["scene"]
                d = msc.setdefault((s, kind), {"crops": set(), "px": 0, "votes": [], "split": h["marida_split"]})
                d["crops"].add(n)
                d["px"] += h["overlap_px"]
                d["votes"].append(h["votes"])
        sp = collections.Counter(dsplit.get(n, "none") for n in cr)
        sensor = MD.load_patch(cr[0])[3]["sensor"] if cr else ""
        base = {"mados_scene": sc, "sensor": sensor, "mados_n_crops": len(cr),
                "mados_splits": ";".join(f"{k}:{v}" for k, v in sorted(sp.items()))}
        acq_val = any(k == "same_acquisition" and d["split"] == "val" for (_, k), d in msc.items())
        acq_tr = any(k == "same_acquisition" and d["split"] == "train" for (_, k), d in msc.items())
        place_val = any(k == "same_place_other_date" and d["split"] == "val" for (_, k), d in msc.items())
        acq_test = any(k == "same_acquisition" and d["split"] == "test" for (_, k), d in msc.items())
        if set(sp) == {"test"}:
            use = "NO (MADOS test split: never used in L13)"
        elif acq_val:
            use = "NO (same acquisition as MARIDA val)"
        elif acq_test:
            use = "NO (same acquisition as MARIDA test)"
        elif place_val:
            use = "yes (same place as MARIDA val, other date; excluded only in variant excl_place_val)"
        elif acq_tr:
            use = "yes (same acquisition as MARIDA train)"
        else:
            use = "yes"
        if not msc:
            rows.append({**base, "marida_scene": "", "tile": "", "date": "", "marida_split": "", "kind": "none",
                         "n_mados_crops_overlapping": 0, "overlap_px_sum": 0, "min_votes": 0, "max_votes": 0,
                         "use_in_training": use})
        for (s, kind), d in sorted(msc.items()):
            info = MR.parse_scene(s)
            rows.append({**base, "marida_scene": s, "tile": info["tile"], "date": info["date"],
                         "marida_split": d["split"], "kind": kind, "n_mados_crops_overlapping": len(d["crops"]),
                         "overlap_px_sum": d["px"], "min_votes": min(d["votes"]), "max_votes": max(d["votes"]),
                         "use_in_training": use})
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    cnt = collections.Counter({r["mados_scene"]: r["use_in_training"] for r in rows}.values())
    print(f"[overlap] MADOS scenes: {len(by_scene)}; " + "; ".join(f"{k}: {v}" for k, v in sorted(cnt.items()))
          + f" -> {out_csv} ({time.time() - t0:.0f}s)", flush=True)


def excluded_scenes(include_train_overlap=False, include_place_val=False) -> set[str]:
    if not OVERLAP_CSV.is_file():
        raise SystemExit("run `overlap` first (reports/mados_overlap.csv missing)")
    out = set()
    with open(OVERLAP_CSV, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["use_in_training"].startswith("NO"):
                out.add(r["mados_scene"])
            elif include_train_overlap and r["kind"] == "same_acquisition" and r["marida_split"] == "train":
                out.add(r["mados_scene"])
            elif include_place_val and r["kind"] != "none" and r["marida_split"] == "val":
                out.add(r["mados_scene"])
    return out


# ============================================================================ MADOS pixel cache
def _extract_mados(name):
    img, cl, conf, _ = MD.load_patch(name, extra=True)
    m = cl > 0
    f = compute_features(img, MD.BAND_NAMES, "win")
    return f[:, m].T.copy(), cl[m], conf[m]


def load_mados(split: str):
    """All labelled pixels of a MADOS split with 'win' features, extended MARIDA codes 1..19. Cached."""
    assert split in ("train", "val"), "MADOS test is not used in L13"
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"mados_{split}_win.npz"
    names = feature_names("win")
    patches = MD.list_patches(split)
    if path.is_file():
        z = np.load(path, allow_pickle=False)
        if list(z["names"]) == names and list(z["patches"]) == patches:
            return z["X"], z["y"], z["conf"], z["pid"], patches
    t = time.time()
    with ProcessPoolExecutor(max_workers=MAX_THREADS) as ex:
        res = list(ex.map(_extract_mados, patches, chunksize=8))
    X = np.concatenate([r[0] for r in res]).astype(np.float32)
    y = np.concatenate([r[1] for r in res]).astype(np.uint8)
    conf = np.concatenate([r[2] for r in res]).astype(np.uint8)
    pid = np.concatenate([np.full(len(r[1]), i, np.int32) for i, r in enumerate(res)])
    np.savez(path, X=X, y=y, conf=conf, pid=pid, names=np.array(names), patches=np.array(patches))
    print(f"[cache] MADOS {split}: {len(patches)} patches, {len(y)} labelled px, {time.time() - t:.1f}s", flush=True)
    return X, y, conf, pid, patches


def sample_ext(y, cfg, seed):
    """TL.sample_train extended to codes 16..19 (MADOS-only classes)."""
    rng = np.random.default_rng(seed)
    s = cfg["sampling"]
    cap = int(s["cap_per_class"])
    over = {int(k): int(v) for k, v in (s.get("cap_overrides") or {}).items()}
    idx = [np.flatnonzero(y == 1)]
    for c in range(2, 20):
        ii = np.flatnonzero(y == c)
        k = over.get(c, cap)
        if len(ii) > k:
            ii = np.sort(rng.choice(ii, k, replace=False))
        idx.append(ii)
    return np.sort(np.concatenate(idx))


def mados_pixels(cfg, seed):
    excl = excluded_scenes(bool(cfg.get("exclude_marida_train_overlap", False)),
                           bool(cfg.get("exclude_same_place_val", False)))
    Xs, ys, cs, n_patches, used_scenes = [], [], [], 0, set()
    for sp in cfg.get("mados_splits", ["train", "val"]):
        X, y, c, pid, patches = load_mados(sp)
        keep_p = np.array([MD.parse_patch(p)[0] not in excl for p in patches])
        n_patches += int(keep_p.sum())
        used_scenes |= {MD.parse_patch(p)[0] for p, k in zip(patches, keep_p) if k}
        m = keep_p[pid]
        if not cfg.get("mados_extra", True):
            m &= y <= 15
        if cfg.get("mados_classes"):
            m &= np.isin(y, [int(k) for k in cfg["mados_classes"]])
        Xs.append(X[m]); ys.append(y[m]); cs.append(c[m])
    X = np.concatenate(Xs); y = np.concatenate(ys); c = np.concatenate(cs)
    sel = sample_ext(y, cfg, seed + 7919)
    return X[sel], y[sel], c[sel], {"excluded_scenes": sorted(excl, key=lambda s: int(s.split("_")[1])),
                                    "n_patches": n_patches, "n_scenes": len(used_scenes),
                                    "n_labelled_px": int(len(y)), "n_md_px": int(np.sum(y == 1))}


# ============================================================================ train / eval
def cmd_train(a):
    cfg = yaml.safe_load(Path(a.config).read_text(encoding="utf-8"))
    cfg.update({"mados_splits": ["train", "val"], "mados_extra": True, "mados_weight": 1.0,
                "exclude_marida_train_overlap": False, "exclude_same_place_val": False})
    for kv in a.set:
        k, v = kv.split("=", 1)
        TL.set_key(cfg, k, v)
    seed = int(a.seed)
    cfg["seed"] = seed
    cfg["data"] = a.data
    np.random.seed(seed)
    t0 = time.time()
    Xva, yva, cva, _ = TL.load_split("val")  # MARIDA val: the only judge
    parts, src_info = [], {}
    if a.data in ("marida", "combined"):
        Xtr, ytr, ctr, _ = TL.load_split("train")
        sel = TL.sample_train(ytr, cfg, seed)
        parts.append((Xtr[sel], ytr[sel], ctr[sel], np.ones(len(sel), np.float32)))
        src_info["marida"] = {"n_px": int(len(sel)), "n_md": int(np.sum(ytr[sel] == 1))}
    if a.data in ("mados", "combined"):
        Xm, ym, cm, info = mados_pixels(cfg, seed)
        parts.append((Xm, ym, cm, np.full(len(ym), float(cfg["mados_weight"]), np.float32)))
        src_info["mados"] = {"n_px": int(len(ym)), "n_md": int(np.sum(ym == 1)), **info}
    X = np.concatenate([p[0] for p in parts]); y = np.concatenate([p[1] for p in parts])
    c = np.concatenate([p[2] for p in parts]); sw = np.concatenate([p[3] for p in parts])
    t_data = time.time() - t0

    fnames = feature_names(cfg["features"])
    all_names = feature_names("win")
    fidx = [all_names.index(n) for n in fnames]
    # per-source weight: fold into confidence weights via a synthetic conf -> use explicit weight path
    t1 = time.time()
    booster = train_weighted(X, y, c, sw, cfg, seed, fidx)
    t_train = time.time() - t1

    pv = TL.prob_md(booster, Xva[:, fidx], "binary", [0, 1])
    pv[np.isnan(Xva[:, :11]).any(1)] = 0.0
    true_md = yva == 1
    thr, met, curve = TL.best_threshold(true_md, pv, cfg["threshold_grid"])
    per_class = {str(k): {"n": int(np.sum(yva == k)), "pred_md": int(np.sum(pv[yva == k] >= thr))}
                 for k in range(1, 16) if np.any(yva == k)}
    rec_conf = {str(k): round(float(np.mean(pv[true_md & (cva == k)] >= thr)), 4)
                for k in (1, 2, 3) if np.any(true_md & (cva == k))}

    name = f"{a.exp or a.data}_s{seed}"
    out_dir = WEXP / "final" if a.final else WEXP / name
    out_dir.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out_dir / "model.txt"))
    size_mb = (out_dir / "model.txt").stat().st_size / 1e6
    gain = booster.feature_importance("gain")
    imp = sorted(zip(fnames, gain.tolist()), key=lambda t: -t[1])
    meta = {
        "model": "lgbm", "task": "binary", "feature_level": cfg["features"], "features": fnames,
        "channels": MR.BAND_NAMES, "input": "reflectance 0..1 (trained on ACOLITE rhorc: " + a.data + ")",
        "classes": [0, 1], "md_class": 1,
        "water_ref": [round(float(v), 5) for v in np.nanmedian(X[y == 7, :11], 0)],
        "threshold": round(thr, 3), "postprocess_min_px": 0, "seed": seed, "config": cfg,
        "train_sources": src_info, "n_train_px": int(len(y)), "n_train_md": int(np.sum(y == 1)),
        "n_val_px": int(len(yva)), "n_val_md": int(true_md.sum()),
        "val_md": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in met.items()},
        "val_recall_by_conf": rec_conf, "val_per_class_pred_md": per_class, "threshold_curve": curve,
        "importance_gain_top": [(n, round(g, 1)) for n, g in imp[:20]], "model_size_mb": round(size_mb, 2),
        "seconds": {"data": round(t_data, 1), "train": round(t_train, 1)},
        "note": "val = official MARIDA val, all labelled px pooled; threshold chosen on val; MARIDA test never read",
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{name}.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    m = meta["val_md"]
    print(f"[{name}] F1={m['f1']:.4f} IoU={m['iou']:.4f} P={m['precision']:.4f} R={m['recall']:.4f} thr={thr:.2f} "
          f"| train px {len(y)} (MD {int(np.sum(y == 1))}) | {t_train:.0f}s | {size_mb:.1f}MB", flush=True)


def train_weighted(X, y, conf, sw, cfg, seed, fidx):
    """TL.train_model (binary) with an extra per-pixel source weight."""
    import lightgbm as lgb

    cw = {int(k): float(v) for k, v in cfg["conf_weights"].items()}
    w = np.array([cw.get(int(k), 1.0) for k in range(4)], np.float32)[np.minimum(conf, 3)] * sw
    params = dict(cfg["lgbm"])
    rounds = int(params.pop("num_boost_round"))
    params.update(seed=seed, bagging_seed=seed, feature_fraction_seed=seed, data_random_seed=seed,
                  num_threads=MAX_THREADS, verbose=-1, deterministic=True, force_row_wise=True, objective="binary")
    label = (y == 1).astype(np.float32)
    w = w * np.where(label > 0, float(cfg["pos_weight"]), 1.0).astype(np.float32)
    ds = lgb.Dataset(X[:, fidx], label=label, weight=w, feature_name=[feature_names("win")[i] for i in fidx],
                     free_raw_data=True)
    return lgb.train(params, ds, num_boost_round=rounds)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("overlap")
    o.add_argument("--with-marida-test", action="store_true",
                   help="ORCHESTRATOR ONLY: also index MARIDA test IMAGES (no labels, no metrics) to check that no "
                        "MADOS training scene is the same acquisition as a MARIDA test scene; "
                        "writes out/l13_cache/mados_overlap_with_test.csv")
    t = sub.add_parser("train")
    t.add_argument("--config", default=str(ROOT / "configs" / "lgbm.yaml"))
    t.add_argument("--data", choices=["marida", "mados", "combined"], required=True)
    t.add_argument("--seed", type=int, default=0)
    t.add_argument("--exp", default=None)
    t.add_argument("--set", nargs="*", default=[])
    t.add_argument("--final", action="store_true", help="write weights_exp/mados/final (never weights/lgbm)")
    a = ap.parse_args()
    {"overlap": cmd_overlap, "train": cmd_train}[a.cmd](a)


if __name__ == "__main__":
    main()
