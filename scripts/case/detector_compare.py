r"""L63: main detector (weights/lgbm) vs baselines on ONE check sample (MARIDA test) + error analysis by background class.

All settings are chosen on MARIDA val only (or fixed by the method), then applied once to MARIDA test:
  lgbm          weights/lgbm, P(MD) >= 0.63 (threshold from weights/lgbm/meta.json, chosen on val)
  rf_argmax     RandomForest with the MARIDA-paper parameters (as scripts/baselines.py, seed 5), trained on MARIDA
                train (cache out/l3_cache/train_win.npz, 11 bands + 8 indices), MD = argmax (no threshold)
  rf_prob       same RF, MD = P(MD) >= t, t chosen on val (grid 0.02..0.98)
  fdi_ndvi_box  FDI in [lo, hi) AND NDVI in [lo, hi) (4 thresholds tuned on val by F1 MD, as baselines.py)
  fdi_interval  lo <= FDI < hi (2 thresholds tuned on val)
  fdi_threshold single FDI threshold + direction tuned on val (the literal "threshold on FDI")
  ndvi_threshold single NDVI threshold + direction tuned on val
Metrics (same as scripts/final_test.py): Marine Debris vs the rest of the LABELLED pixels (cl > 0); pixels with
NaN in any band are predicted "not MD"; unlabelled pixels (cl == 0) are reported separately (share flagged as MD),
never mixed into P/R/F1/IoU. 95 % CI: bootstrap over test scenes (2000 reps, default_rng(0), same loop as final_test).
The lgbm test numbers must equal reports/lgbm_final_test.json (reproducibility check; that file is not touched).

    $env:CUDA_VISIBLE_DEVICES=""; .venv\Scripts\python.exe scripts\case\detector_compare.py
Outputs: reports/case_detector/{compare.md, metrics.json, per_patch.csv, fn_by_scene.csv, fp_by_class.csv,
         fp_by_class.png, examples/*.png}; data/case/detector_preds/{test,val}_preds.npz, rf_seed5.joblib
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ.setdefault("OMP_NUM_THREADS", "1")
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "experiments"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
import l16_common as C  # noqa: E402
import baselines as BL  # noqa: E402

THREADS = min(12, C.THREADS)
MODEL_DIR = ROOT / "weights" / "lgbm"
PRED_DIR = ROOT / "data" / "case" / "detector_preds"
REP = ROOT / "reports" / "case_detector"
EX = REP / "examples"
BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
CLASSES = {0: "Unlabelled", 1: "Marine Debris", 2: "Dense Sargassum", 3: "Sparse Sargassum",
           4: "Natural Organic Material", 5: "Ship", 6: "Clouds", 7: "Marine Water", 8: "Sediment-Laden Water",
           9: "Foam", 10: "Turbid Water", 11: "Shallow Water", 12: "Waves", 13: "Cloud Shadows", 14: "Wakes",
           15: "Mixed Water"}
MODELS = ["lgbm", "rf_argmax", "rf_prob", "fdi_ndvi_box", "fdi_interval", "fdi_threshold", "ndvi_threshold"]
MAIN4 = ["lgbm", "rf_argmax", "fdi_ndvi_box", "fdi_interval"]
LABEL = {"lgbm": "LightGBM + windows (main)", "rf_argmax": "RandomForest (MARIDA params), argmax",
         "rf_prob": "RandomForest, P(MD) >= t", "fdi_ndvi_box": "FDI x NDVI box (4 thr)",
         "fdi_interval": "FDI interval (2 thr)", "fdi_threshold": "FDI single threshold",
         "ndvi_threshold": "NDVI single threshold"}
N_BOOT = 2000

_W = {}


# ----------------------------------------------------------------------------------------------- settings on val
def tune_rules():
    """Rule thresholds tuned on MARIDA val (cached features, all labelled pixels) - exactly baselines.py code."""
    Xva, yva, _, _, names = BL.load("val")
    md = yva == 1
    fdi, ndvi = Xva[:, names.index("FDI")], Xva[:, names.index("NDVI")]
    t_f, d_f, f1_f = BL.sweep(fdi, md)
    t_n, d_n, f1_n = BL.sweep(ndvi, md)
    b_int = BL.tune_interval(fdi, md)
    b_box = BL.tune_box2(fdi, ndvi, md)
    rules = {"fdi_threshold": {"FDI": [t_f, d_f]}, "ndvi_threshold": {"NDVI": [t_n, d_n]},
             "fdi_interval": {"FDI": list(b_int[0])}, "fdi_ndvi_box": {"FDI": list(b_box[0]), "NDVI": list(b_box[1])}}
    val_f1 = {"fdi_threshold": f1_f, "ndvi_threshold": f1_n,
              "fdi_interval": BL.scores(BL.in_box([fdi], b_int), yva)["f1_md"],
              "fdi_ndvi_box": BL.scores(BL.in_box([fdi, ndvi], b_box), yva)["f1_md"]}
    return rules, val_f1


def train_rf():
    """RF exactly as scripts/baselines.py rf_baseline (seed 5 = paper); weights were not saved there -> retrain."""
    import joblib
    from sklearn.ensemble import RandomForestClassifier

    path = PRED_DIR / "rf_seed5.joblib"
    Xva, yva, _, _, _ = BL.load("val")
    if path.exists():
        rf = joblib.load(path)
        fit_s = None
    else:
        Xtr, ytr, ctr, _, _ = BL.load("train")
        y = ytr.copy()
        y[np.isin(y, (12, 13, 14, 15))] = 7
        w = 1.0 / ctr.astype(np.float64)
        t0 = time.time()
        rf = RandomForestClassifier(n_estimators=125, criterion="gini", max_depth=20, min_samples_leaf=1,
                                    min_impurity_decrease=0, oob_score=False, class_weight="balanced_subsample",
                                    random_state=5, n_jobs=THREADS)
        rf.fit(Xtr, y, sample_weight=w)
        fit_s = round(time.time() - t0, 1)
        rf.n_jobs = 1
        joblib.dump(rf, path, compress=3)
    rf.n_jobs = THREADS
    p_md = rf.predict_proba(Xva)[:, list(rf.classes_).index(1)]
    grid = np.round(np.arange(0.02, 0.981, 0.01), 2)
    md = yva == 1
    f1s = [(float(t), 2 * np.sum(md & (p_md >= t)) / max(1, np.sum(md) + np.sum(p_md >= t))) for t in grid]
    t_best, f1_best = max(f1s, key=lambda z: z[1])
    arg = np.asarray(rf.classes_)[rf.predict_proba(Xva).argmax(1)] == 1
    f1_arg = BL.scores(arg, yva)["f1_md"]
    rf.n_jobs = 1
    return path, float(t_best), {"rf_prob": float(f1_best), "rf_argmax": float(f1_arg)}, fit_s


# ----------------------------------------------------------------------------------------------- per-patch worker
def _init(model_dir, rf_path, rules, rf_thr, lgbm_thr):
    import joblib
    import lightgbm as lgb
    from macroplastic.features.pixel import feature_names

    _W["B"] = lgb.Booster(model_file=str(Path(model_dir) / "model.txt"))
    meta = json.loads((Path(model_dir) / "meta.json").read_text(encoding="utf-8"))
    allf = feature_names(meta.get("feature_level", "win"))
    _W["FIDX"] = [allf.index(n) for n in meta["features"]]
    _W["I19"] = [allf.index(n) for n in BL.BANDS + BL.INDICES]
    _W["FDI"], _W["NDVI"] = allf.index("FDI"), allf.index("NDVI")
    _W["RF"] = joblib.load(rf_path)
    _W["RF"].n_jobs = 1
    _W["rules"], _W["rf_thr"], _W["thr"] = rules, rf_thr, lgbm_thr


def _patch(name):
    from macroplastic.features.pixel import compute_features

    img, cl, _conf, _, _ = C.read_patch(name)
    f = compute_features(img, BANDS, "win")
    _, h, w = f.shape
    bad = np.isnan(img).any(0)
    # main model: identical to scripts/final_test.py
    pr = _W["B"].predict(f[_W["FIDX"]].reshape(len(_W["FIDX"]), -1).T, num_threads=1).reshape(h, w).astype(np.float32)
    pr[bad] = 0.0
    # RF on 11 bands + 8 indices
    X = f[_W["I19"]].reshape(19, -1).T
    ok = np.isfinite(X).all(1) & ~bad.ravel()
    rf = _W["RF"]
    prob = np.zeros(h * w, np.float32)
    argm = np.zeros(h * w, bool)
    if ok.any():
        P = rf.predict_proba(X[ok])
        prob[ok] = P[:, list(rf.classes_).index(1)]
        argm[ok] = np.asarray(rf.classes_)[P.argmax(1)] == 1
    fdi, ndvi = f[_W["FDI"]], f[_W["NDVI"]]
    r = _W["rules"]
    masks = {
        "lgbm": pr >= _W["thr"],
        "rf_argmax": argm.reshape(h, w),
        "rf_prob": prob.reshape(h, w) >= _W["rf_thr"],
        "fdi_ndvi_box": BL.in_box([fdi.ravel(), ndvi.ravel()], [tuple(r["fdi_ndvi_box"]["FDI"]),
                                                                tuple(r["fdi_ndvi_box"]["NDVI"])]).reshape(h, w),
        "fdi_interval": BL.in_box([fdi.ravel()], [tuple(r["fdi_interval"]["FDI"])]).reshape(h, w),
        "fdi_threshold": BL.apply_rule(fdi, *r["fdi_threshold"]["FDI"]),
        "ndvi_threshold": BL.apply_rule(ndvi, *r["ndvi_threshold"]["NDVI"]),
    }
    for k in masks:
        masks[k] = np.asarray(masks[k], bool) & ~bad
    counts = {k: np.bincount(cl[m], minlength=16)[:16] for k, m in masks.items()}   # pred-MD pixels per class
    ncl = np.bincount(cl[~bad], minlength=16)[:16]
    return {"name": name, "cl": cl, "bad": bad, "lgbm_prob": pr, "rf_prob": prob.reshape(h, w),
            "masks": masks, "counts": counts, "ncl": ncl}


# ----------------------------------------------------------------------------------------------- metrics
def evaluate(res, names):
    """Pooled labelled-pixel metrics + scene bootstrap CI (same loop and seed as final_test.py)."""
    scene = np.array([n.rsplit("_", 1)[0] for n in names])
    scenes = np.unique(scene)
    out = {}
    for mname in MODELS:
        per_patch = []
        for r in res:
            lab = r["cl"] > 0
            y = r["cl"][lab] == 1
            p = r["masks"][mname][lab]
            per_patch.append(C.confusion(y, p)[:3])
        per_patch = np.array(per_patch)
        tp, fp, fn = (int(v) for v in per_patch.sum(0))
        sc = C.scores(tp, fp, fn)
        per = {s: per_patch[scene == s].sum(0) for s in scenes}
        rng = np.random.default_rng(0)
        f1s, ps, rs, ious = [], [], [], []
        for _ in range(N_BOOT):
            pick = rng.choice(scenes, len(scenes), replace=True)
            t = sum(per[s][0] for s in pick); p = sum(per[s][1] for s in pick); n = sum(per[s][2] for s in pick)
            f1s.append(2 * t / max(1, 2 * t + p + n))
            ps.append(t / max(1, t + p)); rs.append(t / max(1, t + n)); ious.append(t / max(1, t + p + n))
        ci = lambda v: [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]  # noqa: E731
        n_unlab = int(sum(r["ncl"][0] for r in res))
        pred_unlab = int(sum(r["counts"][mname][0] for r in res))
        cls_n = np.sum([r["ncl"] for r in res], 0)
        cls_p = np.sum([r["counts"][mname] for r in res], 0)
        out[mname] = {"precision_md": sc["precision"], "recall_md": sc["recall"], "f1_md": sc["f1"], "iou_md": sc["iou"],
                      "tp": tp, "fp": fp, "fn": fn, "md_px": tp + fn,
                      "ci95_f1": ci(f1s), "ci95_precision": ci(ps), "ci95_recall": ci(rs), "ci95_iou": ci(ious),
                      "unlabelled_px": n_unlab, "unlabelled_pred_md": pred_unlab,
                      "unlabelled_pred_md_rate": pred_unlab / max(1, n_unlab),
                      "per_class": {CLASSES[c]: {"n": int(cls_n[c]), "pred_md": int(cls_p[c]),
                                                 "rate": float(cls_p[c] / cls_n[c]) if cls_n[c] else None}
                                    for c in range(16)},
                      "n_patches": len(names), "n_scenes": int(len(scenes))}
    return out


def save_preds(res, split, meta):
    names = np.array([r["name"] for r in res])
    d = {"names": names, "lgbm_prob": np.stack([r["lgbm_prob"] for r in res]).astype(np.float16),
         "rf_prob_md": np.stack([r["rf_prob"] for r in res]).astype(np.float16),
         "nan_mask": np.packbits(np.stack([r["bad"] for r in res]), axis=-1)}
    for m in MODELS:
        d[f"mask_{m}"] = np.packbits(np.stack([r["masks"][m] for r in res]), axis=-1)
    np.savez_compressed(PRED_DIR / f"{split}_preds.npz", **d)
    (PRED_DIR / f"{split}_preds_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


# ----------------------------------------------------------------------------------------------- figures
COLORS = {"lgbm": "#2a78d6", "rf_argmax": "#eb6834", "fdi_ndvi_box": "#1baf7a", "fdi_interval": "#eda100"}


def fig_fp_by_class(test_m, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    cls = [c for c in range(2, 16) if test_m["lgbm"]["per_class"][CLASSES[c]]["n"] > 0] + [0]
    labels = [f"{CLASSES[c]} (n={test_m['lgbm']['per_class'][CLASSES[c]]['n']:,})".replace(",", " ") for c in cls]
    y = np.arange(len(cls))
    hgt = 0.2
    fig, ax = plt.subplots(figsize=(10, 7.5))
    for i, m in enumerate(MAIN4):
        v = [100 * (test_m[m]["per_class"][CLASSES[c]]["rate"] or 0) for c in cls]
        ax.barh(y + (i - 1.5) * hgt, np.maximum(v, 1e-3), height=hgt * 0.9, color=COLORS[m], label=LABEL[m])
    ax.set_xscale("log")
    ax.set_xlim(1e-3, 100)
    ax.set_yticks(y, labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("Share of class pixels flagged as Marine Debris, % (log scale; 0 drawn at 0.001)", fontsize=9)
    ax.set_title("MARIDA test: false detections by background class", fontsize=11, loc="left")
    ax.grid(axis="x", color="#dddddd", lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.legend(fontsize=8, frameon=False, loc="upper center", bbox_to_anchor=(0.4, -0.08), ncol=2)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _rgb(img):
    rgb = np.stack([img[3], img[2], img[1]], -1)
    rgb = np.nan_to_num(rgb)
    lo, hi = np.percentile(rgb, 1), np.percentile(rgb, 99.5)
    return np.clip((rgb - lo) / max(hi - lo, 1e-6), 0, 1) ** 0.8


def _overlay(base, cl, mask):
    o = base * 0.45
    md = cl == 1
    lab = cl > 0
    o[mask & md] = (0.1, 0.85, 0.2)          # TP green
    o[mask & ~md & lab] = (1.0, 0.25, 0.1)   # FP on labelled background red
    o[mask & ~lab] = (1.0, 0.1, 1.0)         # flagged on unlabelled magenta
    o[~mask & md] = (0.2, 0.5, 1.0)          # FN blue
    return o


def fig_example(res_by_name, name, title, center, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    img, _, _, _, _ = C.read_patch(name)
    r = res_by_name[name]
    cl = r["cl"]
    cy, cx = center
    hw = 32
    y0, x0 = int(np.clip(cy - hw, 0, cl.shape[0] - 2 * hw)), int(np.clip(cx - hw, 0, cl.shape[1] - 2 * hw))
    sl = (slice(y0, y0 + 2 * hw), slice(x0, x0 + 2 * hw))
    base = _rgb(img)[sl]
    lab = np.zeros(cl[sl].shape + (3,))
    pal = {1: (1, 0.1, 0.1), 2: (0.1, 0.6, 0.1), 3: (0.5, 0.9, 0.4), 4: (0.6, 0.4, 0.1), 5: (1, 1, 0.1),
           6: (0.95, 0.95, 0.95), 7: (0.05, 0.1, 0.5), 8: (0.7, 0.55, 0.3), 9: (0.6, 1, 1), 10: (0.8, 0.7, 0.4),
           11: (0.2, 0.7, 0.7), 12: (0.4, 0.6, 1), 13: (0.3, 0.3, 0.3), 14: (1, 0.6, 0.9), 15: (0.2, 0.3, 0.8)}
    for c, col in pal.items():
        lab[cl[sl] == c] = col
    present = [CLASSES[c] for c in np.unique(cl[sl]) if c > 0]
    panels = [("RGB (B4,B3,B2)", base), ("MARIDA label (black = unlabelled)", lab)]
    for m in ("lgbm", "rf_argmax", "fdi_ndvi_box"):
        panels.append((LABEL[m], _overlay(base.copy(), cl[sl], r["masks"][m][sl])))
    fig, axs = plt.subplots(1, 5, figsize=(16, 3.9))
    for ax, (t, a) in zip(axs, panels):
        ax.imshow(a, interpolation="nearest")
        ax.set_title(t, fontsize=8)
        ax.set_xticks([]); ax.set_yticks([])
    fig.suptitle(f"{title} - {name} [{y0}:{y0 + 2 * hw}, {x0}:{x0 + 2 * hw}] 64x64 px = 640 m; classes in crop: "
                 f"{', '.join(present)}\npredictions: green TP, red FP on labelled background, "
                 f"magenta flagged on unlabelled, blue missed MD", fontsize=8.5, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.86))
    fig.savefig(path, dpi=110)
    plt.close(fig)


def pick_examples(res):
    """Deterministic example choice from the saved test predictions (main model first)."""
    ex = []
    used = set()

    def center_of(mask):
        ys, xs = np.nonzero(mask)
        return int(np.median(ys)), int(np.median(xs))

    def add(kind, name, mask, why):
        if name is None or (name, kind) in used:
            return
        used.add((name, kind))
        ex.append({"kind": kind, "name": name, "center": center_of(mask), "why": why})

    # successes: most TP with lgbm, at most 2 FP
    succ = sorted(((int((r["masks"]["lgbm"] & (r["cl"] == 1)).sum()), r["name"]) for r in res
                   if int((r["masks"]["lgbm"] & (r["cl"] > 1)).sum()) <= 2), reverse=True)
    for k, (tp, n) in enumerate(succ[:2]):
        r = next(x for x in res if x["name"] == n)
        add(f"success_{k + 1}", n, r["masks"]["lgbm"] & (r["cl"] == 1), f"lgbm TP {tp}, FP <= 2")
    # false positives by background class: worst patch for lgbm; if lgbm has none on that class, worst for RF / box
    for cname, cls in (("sargassum", (2, 3)), ("foam", (9,)), ("ship", (5,)), ("wakes", (14,)),
                       ("turbid_sediment", (8, 10)), ("natural_organic", (4,))):
        for m in ("lgbm", "rf_argmax", "fdi_ndvi_box"):
            best = max(res, key=lambda r: sum(int(r["counts"][m][c]) for c in cls))
            v = sum(int(best["counts"][m][c]) for c in cls)
            if v > 0:
                add(f"fp_{cname}_{m}", best["name"], best["masks"][m] & np.isin(best["cl"], cls),
                    f"{m}: {v} px of {'/'.join(CLASSES[c] for c in cls)} flagged as MD")
                break
    # misses: most FN for lgbm
    worst = max(res, key=lambda r: int((~r["masks"]["lgbm"] & (r["cl"] == 1)).sum()))
    fnw = int((~worst["masks"]["lgbm"] & (worst["cl"] == 1)).sum())
    if fnw:
        add("miss_lgbm", worst["name"], ~worst["masks"]["lgbm"] & (worst["cl"] == 1), f"lgbm FN {fnw}")
    # unlabelled: most lgbm detections on unlabelled pixels
    un = max(res, key=lambda r: int(r["counts"]["lgbm"][0]))
    if int(un["counts"]["lgbm"][0]):
        add("unlabelled_lgbm", un["name"], un["masks"]["lgbm"] & (un["cl"] == 0),
            f"lgbm flagged {int(un['counts']['lgbm'][0])} unlabelled px")
    return ex[:10]


# ----------------------------------------------------------------------------------------------- main
def run_split(split, init_args):
    names = [ln.strip() for ln in (C.MARIDA / "splits" / f"{split}_X.txt").read_text().splitlines() if ln.strip()]
    with ProcessPoolExecutor(max_workers=THREADS, initializer=_init, initargs=init_args) as ex:
        res = list(ex.map(_patch, names, chunksize=4))
    return names, res


def main():
    t0 = time.time()
    PRED_DIR.mkdir(parents=True, exist_ok=True)
    EX.mkdir(parents=True, exist_ok=True)
    meta = json.loads((MODEL_DIR / "meta.json").read_text(encoding="utf-8"))
    lgbm_thr = float(meta["threshold"])
    rules, rules_val_f1 = tune_rules()
    print("[val] rules", rules, rules_val_f1, flush=True)
    rf_path, rf_thr, rf_val_f1, rf_fit_s = train_rf()
    print(f"[val] rf thr {rf_thr} val F1 {rf_val_f1} fit {rf_fit_s}s", flush=True)
    init_args = (str(MODEL_DIR), str(rf_path), rules, rf_thr, lgbm_thr)

    settings = {
        "lgbm": {"setting": f"P(MD) >= {lgbm_thr}", "source": "weights/lgbm/meta.json (threshold chosen on val)"},
        "rf_argmax": {"setting": "argmax of 11 classes (12-15 merged into Marine Water)", "source": "fixed by method (MARIDA paper), trained on MARIDA train, seed 5"},
        "rf_prob": {"setting": f"P(MD) >= {rf_thr}", "source": "threshold grid 0.02..0.98 on val (F1 MD)"},
        "fdi_ndvi_box": {"setting": f"FDI in {np.round(rules['fdi_ndvi_box']['FDI'], 5).tolist()}, NDVI in {np.round(rules['fdi_ndvi_box']['NDVI'], 5).tolist()}", "source": "4 thresholds tuned on val (F1 MD)"},
        "fdi_interval": {"setting": f"FDI in {np.round(rules['fdi_interval']['FDI'], 5).tolist()}", "source": "2 thresholds tuned on val (F1 MD)"},
        "fdi_threshold": {"setting": f"FDI {'>=' if rules['fdi_threshold']['FDI'][1] == 'ge' else '<='} {rules['fdi_threshold']['FDI'][0]:.5f}", "source": "threshold + direction tuned on val (F1 MD)"},
        "ndvi_threshold": {"setting": f"NDVI {'>=' if rules['ndvi_threshold']['NDVI'][1] == 'ge' else '<='} {rules['ndvi_threshold']['NDVI'][0]:.5f}", "source": "threshold + direction tuned on val (F1 MD)"},
    }
    for k, v in rules_val_f1.items():
        settings[k]["val_f1_tuning_cache"] = round(float(v), 4)
    for k, v in rf_val_f1.items():
        settings[k]["val_f1_tuning_cache"] = round(float(v), 4)

    out = {"when": datetime.now().isoformat(timespec="seconds"), "settings": settings,
           "metric": "MD vs other labelled pixels (cl>0), pooled over patches; NaN pixels -> not MD; unlabelled separate",
           "ci": f"scene bootstrap, {N_BOOT} reps, numpy default_rng(0), same loop as scripts/final_test.py"}
    all_res = {}
    for split in ("val", "test"):
        ts = time.time()
        names, res = run_split(split, init_args)
        out[f"{split}"] = evaluate(res, names)
        save_preds(res, split, {"split": split, "settings": settings, "models": MODELS,
                                "note": "masks: np.unpackbits(axis=-1)[..., :256]; probs float16; NaN pixels -> 0"})
        if split == "test":
            all_res[split] = (names, res)
        print(f"[{split}] {len(names)} patches {time.time() - ts:.0f}s", flush=True)
        for m in MODELS:
            d = out[split][m]
            print(f"  {m:15s} P {d['precision_md']:.4f} R {d['recall_md']:.4f} F1 {d['f1_md']:.4f} IoU {d['iou_md']:.4f} "
                  f"CI {d['ci95_f1']} unlab {d['unlabelled_pred_md_rate']:.5f}", flush=True)

    # reproducibility vs the frozen final test
    ft = json.loads((ROOT / "reports" / "lgbm_final_test.json").read_text(encoding="utf-8"))
    chk = {}
    for split, key in (("test", "test_md"), ("val", "val_md")):
        a, b = out[split]["lgbm"], ft[key]
        chk[split] = {k: {"here": a[k], "final_test": b[k], "equal": abs(a[k] - b[k]) < 1e-12}
                      for k in ("tp", "fp", "fn", "f1_md", "iou_md", "precision_md", "recall_md")}
        chk[split]["ci95_f1"] = {"here": out[split]["lgbm"]["ci95_f1"], "final_test": b["ci95"],
                                 "equal": out[split]["lgbm"]["ci95_f1"] == b["ci95"]}
    chk["all_equal"] = all(v["equal"] for s in ("test", "val") for v in chk[s].values())
    out["reproducibility_vs_lgbm_final_test"] = chk
    print("[check] identical to reports/lgbm_final_test.json:", chk["all_equal"], flush=True)

    names, res = all_res["test"]
    # per-patch table
    with open(REP / "per_patch.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["patch", "scene", "n_labelled", "n_md", "n_unlabelled"] +
                   [f"{m}_{k}" for m in MAIN4 for k in ("tp", "fp", "fn", "unlab")])
        for r in res:
            cl, lab = r["cl"], r["cl"] > 0
            row = [r["name"], r["name"].rsplit("_", 1)[0], int(r["ncl"][1:].sum()), int(r["ncl"][1]), int(r["ncl"][0])]
            for m in MAIN4:
                tp, fp, fn, _ = C.confusion(cl[lab] == 1, r["masks"][m][lab])
                row += [tp, fp, fn, int(r["counts"][m][0])]
            w.writerow(row)
    # FN by scene
    scene = np.array([n.rsplit("_", 1)[0] for n in names])
    fn_scene = []
    for s in np.unique(scene):
        rr = [r for r, sc in zip(res, scene) if sc == s]
        row = {"scene": s, "n_patches": len(rr), "n_md": int(sum(r["ncl"][1] for r in rr))}
        for m in MAIN4:
            row[f"{m}_fn"] = int(sum(((r["cl"] == 1) & ~r["masks"][m]).sum() for r in rr))
            row[f"{m}_fp"] = int(sum(((r["cl"] > 1) & r["masks"][m]).sum() for r in rr))
        fn_scene.append(row)
    with open(REP / "fn_by_scene.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fn_scene[0]))
        w.writeheader(); w.writerows(fn_scene)
    out["fn_fp_by_scene_test"] = fn_scene
    # FP by class
    with open(REP / "fp_by_class.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["class_id", "class", "n_px_test"] + [f"{m}_pred_md" for m in MODELS] + [f"{m}_rate" for m in MODELS])
        for c in range(16):
            pc = {m: out["test"][m]["per_class"][CLASSES[c]] for m in MODELS}
            w.writerow([c, CLASSES[c], pc["lgbm"]["n"]] + [pc[m]["pred_md"] for m in MODELS] +
                       [("" if pc[m]["rate"] is None else f"{pc[m]['rate']:.6f}") for m in MODELS])
    fig_fp_by_class(out["test"], REP / "fp_by_class.png")
    # examples
    rbn = {r["name"]: r for r in res}
    exs = pick_examples(res)
    for i, e in enumerate(exs, 1):
        fn = f"{i:02d}_{e['kind']}.png"
        fig_example(rbn, e["name"], f"{e['kind']}: {e['why']}", e["center"], EX / fn)
        e["file"] = f"reports/case_detector/examples/{fn}"
    out["examples"] = exs
    out["seconds"] = round(time.time() - t0, 1)
    out["rf_fit_seconds"] = rf_fit_s
    (REP / "metrics.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    print(f"[done] {time.time() - t0:.0f}s -> {REP}", flush=True)


if __name__ == "__main__":
    main()
