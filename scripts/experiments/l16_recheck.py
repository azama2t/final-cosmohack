"""Metric audit tasks 1-2: independent recount of MARIDA val metrics + 'same place' leakage split.

  .venv/Scripts/python.exe scripts/experiments/l16_recheck.py
Writes weights_exp/l16/recheck.json and weights_exp/l16/val_pred_<model>.npz. Test split is never read.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

import l16_common as C

MODELS = {"final": C.ROOT / "weights" / "lgbm", "backup_marida_only": C.ROOT / "weights_exp" / "lgbm" / "l3_final_backup"}
BORDER = 15  # px: largest mean/std window radius is 7, local-median windows reach 15 px

_B = None
_FIDX = None


def _init(model_dir):
    global _B, _FIDX
    import lightgbm as lgb
    from macroplastic.features.pixel import feature_names

    _B = lgb.Booster(model_file=str(model_dir / "model.txt"))
    meta = json.loads((model_dir / "meta.json").read_text(encoding="utf-8"))
    allf = feature_names("win")
    _FIDX = [allf.index(n) for n in meta["features"]]
    assert _B.feature_name() == meta["features"]


def _full(name):
    """Full 256x256 patch prediction; returns only labelled pixels."""
    from macroplastic.features.pixel import compute_features

    img, cl, conf, _, _ = C.read_patch(name)
    f = compute_features(img, ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"], "win")
    F, H, W = f.shape
    pr = _B.predict(f[_FIDX].reshape(len(_FIDX), -1).T, num_threads=1).reshape(H, W).astype(np.float32)
    nan = np.isnan(img).any(0)
    pr[nan] = 0.0
    m = cl > 0
    yy, xx = np.nonzero(m)
    bd = np.minimum(np.minimum(yy, H - 1 - yy), np.minimum(xx, W - 1 - xx))
    return pr[m], cl[m], conf[m], nan[m], bd.astype(np.int16), int(np.isnan(pr).sum())


def predict_val(model_key):
    path = C.OUT / f"val_pred_{model_key}.npz"
    names = C.split_names("val")
    if path.is_file():
        z = np.load(path)
        return {k: z[k] for k in z.files}
    t = time.time()
    with ProcessPoolExecutor(max_workers=C.THREADS, initializer=_init, initargs=(MODELS[model_key],)) as ex:
        res = list(ex.map(_full, names, chunksize=4))
    out = {"prob": np.concatenate([r[0] for r in res]), "y": np.concatenate([r[1] for r in res]),
           "conf": np.concatenate([r[2] for r in res]), "nan": np.concatenate([r[3] for r in res]),
           "border": np.concatenate([r[4] for r in res]),
           "pid": np.concatenate([np.full(len(r[1]), i, np.int32) for i, r in enumerate(res)]),
           "nan_prob": np.array([r[5] for r in res])}
    np.savez_compressed(path, **out)
    print(f"[pred] {model_key}: {len(names)} val patches in {time.time() - t:.0f}s", flush=True)
    return out


def scene_bootstrap(true_md, pred_md, scene, n=2000, seed=0):
    """Pooled F1 with scenes resampled with replacement. Returns (f1 point, lo, hi, samples)."""
    rng = np.random.default_rng(seed)
    us = np.unique(scene)
    tp = np.array([np.count_nonzero(true_md[scene == s] & pred_md[scene == s]) for s in us])
    fp = np.array([np.count_nonzero(~true_md[scene == s] & pred_md[scene == s]) for s in us])
    fn = np.array([np.count_nonzero(true_md[scene == s] & ~pred_md[scene == s]) for s in us])
    f = []
    for _ in range(n):
        k = rng.integers(0, len(us), len(us))
        a, b, c = tp[k].sum(), fp[k].sum(), fn[k].sum()
        f.append(2 * a / (2 * a + b + c) if (2 * a + b + c) else np.nan)
    f = np.array(f)
    point = 2 * tp.sum() / (2 * tp.sum() + fp.sum() + fn.sum())
    return float(point), float(np.nanpercentile(f, 2.5)), float(np.nanpercentile(f, 97.5)), f


def main():
    rep = {}
    tr, va = C.split_names("train"), C.split_names("val")
    ptr, pva = [C.parse(n) for n in tr], [C.parse(n) for n in va]
    # ---------------------------------------------------------------- split integrity
    tr_sc, va_sc = {p["scene"] for p in ptr}, {p["scene"] for p in pva}
    tr_date = {p["date"] for p in ptr}
    rep["splits"] = {
        "n_train_patches": len(tr), "n_val_patches": len(va),
        "patch_overlap": len(set(tr) & set(va)), "scene_overlap": sorted(tr_sc & va_sc),
        "tile_overlap": sorted({p["tile"] for p in ptr} & {p["tile"] for p in pva}),
        "val_scenes_with_same_date_in_train": sorted({p["scene"] for p in pva if p["date"] in tr_date}),
        "n_train_scenes": len(tr_sc), "n_val_scenes": len(va_sc),
        "val_files_exist": all(C.patch_file(n).is_file() for n in va),
    }
    print(json.dumps(rep["splits"], indent=0), flush=True)

    # ---------------------------------------------------------------- cache == split list?
    zc = np.load(C.L3_CACHE / "val_win.npz")
    rep["cache_val"] = {"n_px": int(len(zc["y"])), "n_md": int(np.sum(zc["y"] == 1)),
                        "n_patches_in_cache": int(zc["pid"].max() + 1)}

    # ---------------------------------------------------------------- full-patch recount
    rep["models"] = {}
    preds = {}
    for key, mdir in MODELS.items():
        meta = json.loads((mdir / "meta.json").read_text(encoding="utf-8"))
        P = predict_val(key)
        preds[key] = P
        t = P["y"] == 1
        thr = float(meta["threshold"])
        tp, fp, fn, tn = C.confusion(t, P["prob"] >= thr)
        mine = C.rnd(C.scores(tp, fp, fn, tn))
        g, f = C.f1_curve(t, P["prob"])
        bt, bf = C.best_thr(t, P["prob"])
        # same model on cached features (the numbers in meta.json come from the cache path)
        import lightgbm as lgb
        from macroplastic.features.pixel import feature_names

        b = lgb.Booster(model_file=str(mdir / "model.txt"))
        fidx = [feature_names("win").index(n) for n in meta["features"]]
        pc = C.predict(b, zc["X"], fidx)
        same_order = bool(np.array_equal(zc["y"], P["y"]))
        # optimism of choosing the threshold on val: split val scenes in halves, choose on one, score the other
        scenes = np.array([pva[i]["scene"] for i in P["pid"]])
        us = np.unique(scenes)
        rng = np.random.default_rng(0)
        opt = []
        for _ in range(200):
            half = set(rng.permutation(us)[: len(us) // 2])
            a = np.isin(scenes, list(half))
            th_a, _ = C.best_thr(t[a], P["prob"][a])
            th_b, _ = C.best_thr(t[~a], P["prob"][~a])
            fa = C.scores(*C.confusion(t[~a], P["prob"][~a] >= th_a))["f1"]
            fb = C.scores(*C.confusion(t[~a], P["prob"][~a] >= th_b))["f1"]
            opt.append((fa, fb, th_a))
        opt = np.array(opt)
        # NaN pixels
        nanm = P["nan"]
        per_cls_nan = {int(c): int(np.sum(nanm & (P["y"] == c))) for c in np.unique(P["y"][nanm])} if nanm.any() else {}
        # borders
        near = P["border"] < BORDER
        bsc = {}
        for nm, m in (("border_lt15px", near), ("interior", ~near)):
            bsc[nm] = C.rnd({**C.scores(*C.confusion(t[m], P["prob"][m] >= thr)), "n_md": int(t[m].sum()),
                             "n_px": int(m.sum())})
        rep["models"][key] = {
            "meta_threshold": thr, "meta_val_md": meta["val_md"], "recount_full_patch": mine,
            "match_meta_3dp": all(abs(mine[k] - meta["val_md"][k]) < 5e-4 for k in ("f1", "iou", "precision", "recall")),
            "recount_cached_features": C.rnd(C.scores(*C.confusion(zc["y"] == 1, pc >= thr))),
            "max_abs_prob_diff_full_vs_cache": float(np.max(np.abs(pc - P["prob"]))) if same_order else None,
            "pixel_order_equal_to_cache": same_order,
            "n_labelled_px": int(len(t)), "n_md_px": int(t.sum()),
            "best_thr_on_val": bt, "best_f1_on_val": round(bf, 4),
            "f1_at_0.5": round(C.scores(*C.confusion(t, P["prob"] >= 0.5))["f1"], 4),
            "curve_top5": [(float(g[i]), round(float(f[i]), 4)) for i in np.argsort(-f)[:5]],
            "thr_split_half": {"f1_chosen_on_other_half_mean": round(float(opt[:, 0].mean()), 4),
                               "f1_oracle_same_half_mean": round(float(opt[:, 1].mean()), 4),
                               "optimism_mean": round(float((opt[:, 1] - opt[:, 0]).mean()), 4),
                               "thr_range": [float(opt[:, 2].min()), float(opt[:, 2].max())]},
            "nan": {"labelled_px_with_nan_band": int(nanm.sum()), "by_class": per_cls_nan,
                    "md_px_with_nan": int(np.sum(nanm & t)), "nan_probs_after_masking": int(P["nan_prob"].sum()),
                    "rule": "prob := 0 where any band NaN (same as training/eval code) -> such MD px count as FN"},
            "border": bsc,
        }
        print(key, json.dumps(rep["models"][key]["recount_full_patch"]), "meta", meta["val_md"], flush=True)

    # recall of MD by border distance in train vs val labels (is the border effect the same on both splits?)
    def md_border_share(names):
        import rasterio

        n_md = n_md_b = n_lab = n_lab_b = 0
        for n in names:
            with rasterio.open(str(C.patch_file(n))[:-4] + "_cl.tif") as ds:
                cl = ds.read(1)
            H, W = cl.shape
            yy, xx = np.mgrid[:H, :W]
            bd = np.minimum(np.minimum(yy, H - 1 - yy), np.minimum(xx, W - 1 - xx)) < BORDER
            n_md += int((cl == 1).sum()); n_md_b += int(((cl == 1) & bd).sum())
            n_lab += int((cl > 0).sum()); n_lab_b += int(((cl > 0) & bd).sum())
        return {"md_share_border": round(n_md_b / max(n_md, 1), 4), "labelled_share_border": round(n_lab_b / n_lab, 4),
                "n_md": n_md, "area_share_border": round(1 - (256 - 2 * BORDER) ** 2 / 256 ** 2, 4)}

    rep["border_train_vs_val"] = {"train": md_border_share(tr), "val": md_border_share(va)}

    # ---------------------------------------------------------------- MADOS exclusion vs overlap table
    meta = json.loads((MODELS["final"] / "meta.json").read_text(encoding="utf-8"))
    excl = set(meta["train_sources"]["mados"]["excluded_scenes"])
    rows = C.mados_overlap()
    val_linked = {r["mados_scene"] for r in rows if r["marida_split"] == "val"}
    val_acq = {r["mados_scene"] for r in rows if r["marida_split"] == "val" and r["kind"] == "same_acquisition"}
    mtest = {r["mados_scene"] for r in rows if r["mados_splits"].startswith("test")}
    import csv
    wt = C.ROOT / "reports" / "mados_overlap_with_marida_test.csv"
    test_acq = set()
    if wt.is_file():  # produced by the orchestrator (image-only match, no test labels)
        for r in csv.DictReader(open(wt, encoding="utf-8")):
            if r.get("marida_split") == "test" and r.get("kind") == "same_acquisition":
                test_acq.add(r["mados_scene"])
    Xm, ym, cm, scm = C.mados_pixels_all()
    used = set(np.unique(scm)) - excl
    rep["mados"] = {
        "n_excluded_in_meta": len(excl), "val_linked_scenes": sorted(val_linked, key=lambda s: int(s[6:])),
        "val_linked_not_excluded": sorted(val_linked - excl), "val_same_acq_not_excluded": sorted(val_acq - excl),
        "mados_test_split_scenes_in_train_caches": sorted(set(np.unique(scm)) & mtest - excl),
        "marida_test_same_acq_used_in_training": sorted(test_acq & used),
        "n_mados_scenes_used": len(used),
    }
    print(json.dumps(rep["mados"]), flush=True)

    # ---------------------------------------------------------------- same place (task 2)
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=C.THREADS) as ex:
        btr = list(ex.map(C.bounds_wgs84, tr, chunksize=16))
        bva = list(ex.map(C.bounds_wgs84, va, chunksize=16))
    btr = np.array(btr); bva = np.array(bva)
    print(f"[bounds] {time.time() - t0:.0f}s", flush=True)

    def inter(a, B):
        w = np.clip(np.minimum(a[2], B[:, 2]) - np.maximum(a[0], B[:, 0]), 0, None)
        h = np.clip(np.minimum(a[3], B[:, 3]) - np.maximum(a[1], B[:, 1]), 0, None)
        return w * h / ((a[2] - a[0]) * (a[3] - a[1]))

    cat = []
    tr_dates = np.array([p["date"] for p in ptr])
    for i, p in enumerate(pva):
        share = inter(bva[i], btr)
        same_date = tr_dates == p["date"]
        if np.any((share > 0) & same_date):
            cat.append("same_acquisition_px")  # same S2 pass, overlapping MGRS tiles (16PCC val vs 16PDC train)
        elif share.max() > 0.05:
            cat.append("same_place_other_date")
        elif share.max() > 0:
            cat.append("edge_touch_le5pct")
        else:
            cat.append("no_overlap")
    cat = np.array(cat)
    rep["same_place_patch_counts"] = {k: int(np.sum(cat == k)) for k in np.unique(cat)}
    np.save(C.OUT / "val_patch_place_category.npy", cat)
    sp = {}
    for key, P in preds.items():
        thr = rep["models"][key]["meta_threshold"]
        t = P["y"] == 1
        pm = P["prob"] >= thr
        pc = cat[P["pid"]]
        scenes = np.array([pva[i]["scene"] for i in P["pid"]])
        groups = {"all": np.ones(len(t), bool),
                  "overlap_any (>0)": pc != "no_overlap",
                  "overlap_gt5pct_or_same_acq": np.isin(pc, ["same_place_other_date", "same_acquisition_px"]),
                  "same_acquisition_px": pc == "same_acquisition_px",
                  "same_place_other_date": pc == "same_place_other_date",
                  "no_overlap": pc == "no_overlap",
                  "no_overlap_or_edge": np.isin(pc, ["no_overlap", "edge_touch_le5pct"])}
        res, samples = {}, {}
        for g, m in groups.items():
            if not t[m].any():
                res[g] = {"n_md": 0}
                continue
            pt, lo, hi, s = scene_bootstrap(t[m], pm[m], scenes[m])
            samples[g] = s
            sc = C.scores(*C.confusion(t[m], pm[m]))
            res[g] = {"f1": round(pt, 4), "ci95": [round(lo, 4), round(hi, 4)], "iou": round(sc["iou"], 4),
                      "precision": round(sc["precision"], 4), "recall": round(sc["recall"], 4),
                      "n_md": int(t[m].sum()), "n_px": int(m.sum()), "n_scenes": int(len(np.unique(scenes[m]))),
                      "n_patches": int(len(np.unique(P["pid"][m]))),
                      "scenes": sorted(np.unique(scenes[m]).tolist()) if g in ("no_overlap", "no_overlap_or_edge") else None}
            # per-scene oracle thresholds are not used anywhere; threshold = the model's val threshold
        a, b = "overlap_gt5pct_or_same_acq", "no_overlap_or_edge"
        if a in samples and b in samples:
            d = samples[a] - samples[b]
            res["diff_overlap_minus_nooverlap"] = {"point": round(res[a]["f1"] - res[b]["f1"], 4),
                                                   "ci95": [round(float(np.nanpercentile(d, 2.5)), 4),
                                                            round(float(np.nanpercentile(d, 97.5)), 4)]}
        sp[key] = res
        print(key, json.dumps({g: (v.get("f1"), v.get("ci95"), v.get("n_md")) for g, v in res.items()
                               if isinstance(v, dict)}), flush=True)
    rep["same_place"] = sp
    C.dump(C.OUT / "recheck.json", rep)
    print("done", flush=True)


if __name__ == "__main__":
    main()
