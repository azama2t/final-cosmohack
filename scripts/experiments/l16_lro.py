"""L16 tasks 3-4: leave-region-out (LRO) on MARIDA train+val (+ MADOS), and what helps on an unseen region.

  .venv/Scripts/python.exe scripts/experiments/l16_lro.py [--seeds 0 1 2]
Writes weights_exp/l16/lro.json (+ OOF probabilities lro_oof.npz). MARIDA test is never read.

Protocol
  * region = group of MGRS tiles (reports/marida_regions.md); held-out regions: >= 50 MD px in MARIDA train+val.
  * fold R: train on MARIDA train+val pixels of all other regions (final recipe: binary, 'win' features, all MD +
    caps 30000/class, 60000 water, pos_weight 3, 400 rounds) [+ MADOS train+val, minus scenes marked NO in
    reports/mados_overlap.csv, minus every MADOS scene that overlaps (any kind) a MARIDA scene of region R;
    MADOS-only classes dropped (mados_extra=false), as in the final model].
  * threshold for fold R: F1-optimal on the pooled out-of-fold predictions of the OTHER held-out regions (same
    variant, same seed) - no label of R is used. Also reported: fixed 0.63 (final model) and the oracle on R
    (upper bound, never used for decisions).
  * score: pooled confusion over all labelled pixels of R (train+val); also on R's val pixels only, next to the
    final (in-distribution) model on the same pixels.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

import l16_common as C
from macroplastic.features.pixel import compute_features, feature_names

BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
ALL = feature_names("win")
FSETS = {
    "win": list(range(len(ALL))),
    "min": [ALL.index(n) for n in feature_names("min")],
    "nowide": [i for i, n in enumerate(ALL) if not (n.endswith("15") or n.endswith("31"))],  # drop 15/31 px context
}
AUG = {"offset": 0.01, "gain": 0.1, "blue_mult": 1.5, "clip0": True}  # = weights_exp/lgbm/bin_win_aug1 (L3 H8)


def augment(img, seed):
    """Same as scripts/train_lgbm.py::_augment (L2A-like per-band negative offset, gain, clip)."""
    rng = np.random.default_rng(seed)
    off = -rng.uniform(0.0, AUG["offset"], size=11).astype(np.float32)
    off[:3] *= AUG["blue_mult"]
    g = rng.uniform(1 - AUG["gain"], 1 + AUG["gain"], size=11).astype(np.float32)
    x = img * g[:, None, None] + off[:, None, None]
    return np.where(np.isnan(x), x, np.maximum(x, 1e-4)).astype(np.float32)


def _feat_aug(args):
    name, seed = args
    img, cl, _, _, _ = C.read_patch(name)
    f = compute_features(augment(img, seed), BANDS, "win")
    return f[:, cl > 0].T.copy()


def aug_pixels():
    """Augmented copy (seed 0) of MARIDA train (L3 cache) + val (computed here, same per-patch seeds)."""
    tr = np.load(C.L3_CACHE / "train_win_augblue_mult1.5_clip0True_gain0.1_offset0.01_s0.npz")
    path = C.OUT / "val_win_aug1_s0.npz"
    if not path.is_file():
        names = C.split_names("val")
        with ProcessPoolExecutor(max_workers=C.THREADS) as ex:
            res = list(ex.map(_feat_aug, [(n, i) for i, n in enumerate(names)], chunksize=4))
        np.savez(path, X=np.concatenate(res).astype(np.float32))
    va = np.load(path)
    return np.concatenate([tr["X"], va["X"]])


def _water_mask(img):
    b = img
    with np.errstate(invalid="ignore", divide="ignore"):
        ndwi = (b[2] - b[7]) / (b[2] + b[7])
    return np.isfinite(b).all(0) & (ndwi > 0.1) & (b[1] < 0.2) & (b[9] < 0.05)


def _scene_water(names):
    vals = []
    for n in names:
        img = C.read_patch(n)[0]
        m = _water_mask(img)
        if m.any():
            vals.append(img[:, m].T)
    if not vals:
        return None, 0
    v = np.concatenate(vals)
    return np.median(v, 0), len(v)


def _feat_shift(args):
    name, off = args
    img, cl, _, _, _ = C.read_patch(name)
    if off is not None:
        img = img + np.asarray(off, np.float32)[:, None, None]
    f = compute_features(img, BANDS, "win")
    return f[:, cl > 0].T.copy()


def harmonized_features(meta, regions, water_ref):
    """Features of every labelled px of `regions` after a per-scene shift of the open-water median (spectral mask,
    no labels) to water_ref - the lgbm_predict harmonize='water_median' rule applied per scene."""
    idx = np.flatnonzero(np.isin(meta["region"], regions))
    patches = list(dict.fromkeys(meta["patch"][idx]))  # order of appearance == cache order
    by_scene = {}
    for p in patches:
        by_scene.setdefault(C.parse(p.split(":")[1])["scene"], []).append(p.split(":")[1])
    offs = {}
    with ProcessPoolExecutor(max_workers=C.THREADS) as ex:
        res = dict(zip(by_scene, ex.map(_scene_water, by_scene.values())))
    for s, (med, n) in res.items():
        offs[s] = None if (med is None or n < 2000) else (water_ref - med).astype(np.float32)
    with ProcessPoolExecutor(max_workers=C.THREADS) as ex:
        feats = list(ex.map(_feat_shift, [(p.split(":")[1], offs[C.parse(p.split(":")[1])["scene"]]) for p in patches],
                            chunksize=4))
    Xh = np.concatenate(feats)
    assert len(Xh) == len(idx), (len(Xh), len(idx))
    return idx, Xh, {s: (None if o is None else [round(float(v), 4) for v in o]) for s, o in offs.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="*", default=[0, 1, 2])
    a = ap.parse_args()
    t0 = time.time()
    X, y, conf, meta = C.marida_pixels()
    Xm, ym, cm, scm = C.mados_pixels_all()
    keep_m = ym <= 15  # final model: mados_extra = false
    Xm, ym, scm = Xm[keep_m], ym[keep_m], scm[keep_m]
    mreg, mno = C.mados_scene_regions()
    Xa = aug_pixels()
    assert len(Xa) == len(X)
    regions = np.unique(meta["region"])
    md_by_region = {r: int(np.sum((meta["region"] == r) & (y == 1))) for r in regions}
    held = [r for r in sorted(md_by_region, key=lambda r: -md_by_region[r]) if md_by_region[r] >= 50]
    print("held-out regions:", {r: md_by_region[r] for r in held}, f"load {time.time() - t0:.0f}s", flush=True)

    variants = {  # name: (use_mados, fset, aug)
        "marida_only": (False, "win", False),
        "combined": (True, "win", False),
        "combined_aug": (True, "win", True),
        "combined_min": (True, "min", False),
        "combined_nowide": (True, "nowide", False),
    }
    oof = {}  # (variant, seed) -> full-length prob array (NaN outside held-out regions)
    fold_info = {}
    harm = {}
    for R in held:
        tr_m = meta["region"] != R
        ex_scenes = {s for s, rs in mreg.items() if R in rs} | mno
        tr_mados = ~np.isin(scm, sorted(ex_scenes))
        te = np.flatnonzero(~tr_m)
        fold_info[R] = {"n_train_marida_px": int(tr_m.sum()), "n_md_heldout": int(np.sum(y[te] == 1)),
                        "n_px_heldout": int(len(te)), "mados_scenes_excluded_for_region":
                        sorted({s for s, rs in mreg.items() if R in rs}, key=lambda s: int(s[6:])),
                        "n_mados_scenes_used": int(len(np.unique(scm[tr_mados])))}
        for seed in a.seeds:
            im = np.flatnonzero(tr_m)[C.sample_caps(y[tr_m], seed)]
            imd = np.flatnonzero(tr_mados)[C.sample_caps(ym[tr_mados], seed + 7919)]
            for v, (use_m, fs, use_aug) in variants.items():
                parts_X, parts_y = [X[im]], [y[im]]
                if use_aug:
                    parts_X.append(Xa[im]); parts_y.append(y[im])
                if use_m:
                    parts_X.append(Xm[imd]); parts_y.append(ym[imd])
                Xt, yt = np.concatenate(parts_X), np.concatenate(parts_y)
                t1 = time.time()
                b = C.train_booster(Xt, yt, seed, FSETS[fs])
                p = C.predict(b, X[te], FSETS[fs])
                arr = oof.setdefault((v, seed), np.full(len(y), np.nan, np.float32))
                arr[te] = p
                if v == "combined":
                    water_ref = np.nanmedian(Xt[yt == 7, :11], 0)
                    key = (R, "water_ref")
                    if seed == a.seeds[0]:
                        idx, Xh, offs = harmonized_features(meta, [R], water_ref.astype(np.float32))
                        harm[R] = (idx, Xh, offs)
                    idx, Xh, _ = harm[R]
                    arr2 = oof.setdefault(("combined_harmonize", seed), np.full(len(y), np.nan, np.float32))
                    arr2[idx] = C.predict(b, Xh, FSETS[fs])
                print(f"[{R} s{seed} {v}] train {len(yt)} px ({int(np.sum(yt == 1))} MD) {time.time() - t1:.0f}s",
                      flush=True)
    # ensembles
    for seed in a.seeds:
        oof[("ens_combined_plus_marida", seed)] = (oof[("combined", seed)] + oof[("marida_only", seed)]) / 2
    if len(a.seeds) >= 3:
        e = np.mean([oof[("combined", s)] for s in a.seeds], 0)
        oof[("ens_combined_3seeds", a.seeds[0])] = e

    # ------------------------------------------------------------------ scoring
    val_pred = np.load(C.OUT / "val_pred_final.npz")
    names_va = C.split_names("val")
    reg_va = np.array([C.parse(n)["region"] for n in names_va])[val_pred["pid"]]
    is_val = meta["split"] == "val"
    assert np.array_equal(y[is_val], val_pred["y"])
    thr_final = 0.63
    res = {}
    for (v, seed), p in oof.items():
        per = []
        for R in held:
            m = meta["region"] == R
            others = np.isin(meta["region"], [q for q in held if q != R])
            thr, _ = C.best_thr(y[others] == 1, p[others])
            t = y[m] == 1
            s_thr = C.scores(*C.confusion(t, p[m] >= thr))
            s_063 = C.scores(*C.confusion(t, p[m] >= thr_final))
            o_thr, o_f1 = C.best_thr(t, p[m])
            row = {"region": R, "n_md": int(t.sum()), "thr_from_other_regions": thr,
                   **{k: round(s_thr[k], 4) for k in ("f1", "iou", "precision", "recall")},
                   "f1_at_0.63": round(s_063["f1"], 4), "oracle_thr": o_thr, "oracle_f1": round(o_f1, 4)}
            mv = m & is_val
            if np.sum(y[mv] == 1) >= 20:
                tv = y[mv] == 1
                row["val_only"] = {"n_md": int(tv.sum()),
                                   "lro_f1": round(C.scores(*C.confusion(tv, p[mv] >= thr))["f1"], 4),
                                   "in_dist_final_f1": round(C.scores(*C.confusion(
                                       val_pred["y"][reg_va == R] == 1, val_pred["prob"][reg_va == R] >= thr_final))["f1"], 4)}
            per.append(row)
        # pooled over held-out regions (each at its own threshold)
        tp = fp = fn = 0
        for R, row in zip(held, per):
            m = meta["region"] == R
            a_, b_, c_, _ = C.confusion(y[m] == 1, p[m] >= row["thr_from_other_regions"])
            tp += a_; fp += b_; fn += c_
        res[f"{v}|s{seed}"] = {"variant": v, "seed": seed, "per_region": per,
                               "mean_f1": round(float(np.mean([r["f1"] for r in per])), 4),
                               "mean_iou": round(float(np.mean([r["iou"] for r in per])), 4),
                               "pooled_f1": round(C.scores(tp, fp, fn)["f1"], 4),
                               "mean_f1_at_0.63": round(float(np.mean([r["f1_at_0.63"] for r in per])), 4),
                               "mean_oracle_f1": round(float(np.mean([r["oracle_f1"] for r in per])), 4)}
        print(v, seed, res[f"{v}|s{seed}"]["mean_f1"], [r["f1"] for r in per], flush=True)
    summary = {}
    for v in sorted({k.split("|")[0] for k in res}):
        vals = [r["mean_f1"] for k, r in res.items() if r["variant"] == v]
        summary[v] = {"lro_mean_f1_by_seed": vals, "mean": round(float(np.mean(vals)), 4),
                      "std": round(float(np.std(vals, ddof=1)), 4) if len(vals) > 1 else None}
    # in-distribution reference (final model on the whole val, and on val px of each held-out region)
    in_dist = {"final_val_f1": 0.9226,
               "per_region_val": {R: round(C.scores(*C.confusion(val_pred["y"][reg_va == R] == 1,
                                                                 val_pred["prob"][reg_va == R] >= thr_final))["f1"], 4)
                                  for R in held if np.sum(val_pred["y"][reg_va == R] == 1) >= 20}}
    np.savez_compressed(C.OUT / "lro_oof.npz", **{f"{v}__s{s}": p for (v, s), p in oof.items()})
    C.dump(C.OUT / "lro.json", {"held_out": held, "md_by_region": md_by_region, "fold_info": fold_info,
                                "runs": res, "summary": summary, "in_dist": in_dist,
                                "harmonize_offsets": {R: h[2] for R, h in harm.items()},
                                "seconds": round(time.time() - t0)})
    print(json.dumps(summary, indent=1), flush=True)


if __name__ == "__main__":
    main()
