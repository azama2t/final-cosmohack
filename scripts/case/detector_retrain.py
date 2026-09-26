r"""Detector v2 experiments (§11 direction 4, task L92): LightGBM retrained on MARIDA train + MADOS + new B (positive
accumulations) + verified D (negatives: foam / glint / cloud / ship), compared with the current model weights/lgbm
(rollback) and baselines (FDI threshold, RandomForest) on ONE check frozen BEFORE the experiments.

The check is frozen in configs/detector_v2_eval.yaml (sub-command `freeze`, refuses to overwrite):
  primary   MARIDA val (official split; MARIDA test is never read by this script), pooled labelled pixels, MD vs rest,
            threshold chosen on val (project convention, as weights/lgbm/meta.json), scene bootstrap 95 % CI, seeds 0-2;
  new data  3-fold cross-fitting by acquisition (key <S2 tile>_<YYYYMMDD>, fold = sha256("detector_v2:"+key) % 3):
            every new B/D object is scored by a model trained WITHOUT its fold (MARIDA + MADOS always in training);
            D false-alarm rate per object (Wilson CI) on inputs as in the service (no harmonisation) and with the
            old water_median harmonisation; B recall per object (when B exists);
  rule      written in the yaml (project rule: dF1 MD val >= max(0.01, 2 x seed std)) + a secondary domain rule.
Level C (candidates) never becomes a positive label: rows with level C / class accumulation / unclear are dropped.

    $env:CUDA_VISIBLE_DEVICES=""; $env:PYTHONPATH="src"
    .venv\Scripts\python.exe scripts\case\detector_retrain.py freeze                  # once, before any experiment
    .venv\Scripts\python.exe scripts\case\detector_retrain.py prep-pairs              # D from reports/case_pairs/visual_labels.csv
    .venv\Scripts\python.exe scripts\case\detector_retrain.py prep-extra              # data/extra/features_*.npz (L89/L90)
    .venv\Scripts\python.exe scripts\case\detector_retrain.py baselines               # weights/lgbm, FDI threshold, RF
    .venv\Scripts\python.exe scripts\case\detector_retrain.py train --exp r0 --seeds 0 1 2
    .venv\Scripts\python.exe scripts\case\detector_retrain.py train --exp d_pairs_w10 --seeds 0 1 2 --pairs-d 10
    .venv\Scripts\python.exe scripts\case\detector_retrain.py report                  # reports/detector_v2/experiments.{json,md}

CPU only (LightGBM), <= 8 threads. Candidate weights -> weights_exp/detector_v2/<exp>_s<seed>/ (never weights/).
Visual labels of the pair crops were made by an AI agent from image chips (one annotator, not field truth): every
row that comes from them carries source="pairs_visual (AI-agent labels)".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

THREADS = 8
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["OMP_NUM_THREADS"] = str(THREADS)
os.environ["L13_THREADS"] = str(THREADS)

import numpy as np  # noqa: E402
import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "scripts" / "case"))

import train_lgbm as TL  # noqa: E402

TL.n_threads = lambda: THREADS
from macroplastic.features.pixel import compute_features_blocked, feature_names  # noqa: E402

EVAL_YAML = ROOT / "configs" / "detector_v2_eval.yaml"
REF_DIR = ROOT / "weights" / "lgbm"
RF_PATH = ROOT / "data" / "case" / "detector_preds" / "rf_seed5.joblib"
VIS_LABELS = ROOT / "reports" / "case_pairs" / "visual_labels.csv"
QDIR = ROOT / "data" / "pairs" / "quality"
BANDS_CACHE = ROOT / "out" / "l78_bands"
EXTRA = ROOT / "data" / "extra"
WORK = ROOT / "out" / "detector_v2"
RUNS = WORK / "runs"
WEXP = ROOT / "weights_exp" / "detector_v2"
REP = ROOT / "reports" / "detector_v2"
LOG = ROOT / "docs" / "LOG.md"
SALT = "detector_v2:"
N_FOLDS = 3
N_BOOT = 2000
GRID = [0.02, 0.98, 0.01]
WIN = feature_names("win")
D_CLASSES = {"foam_whitecap": "foam", "cloud": "cloud", "glint": "glint", "ship_wake": "ship", "seam_artifact": "artifact"}
EXCLUDED = {"unclear": "not verified", "accumulation": "level C candidate - never a positive label"}
TRAIN_CONF = ("high", "medium")
MODES = ("none", "water_median")
WATER_SAMPLE = 5000  # random valid-water pixels per pair crop (descriptive flag rate only)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fold_of(key: str) -> int:
    return int(hashlib.sha256((SALT + key).encode()).hexdigest()[:8], 16) % N_FOLDS


def log_line(direction: str, what: str, result: str, path: str):
    line = f"{datetime.now():%d.%m %H:%M} | {direction} | {what} | {result} | {path}\n"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)


def wilson(k, n, z=1.96):
    if n == 0:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(float(max(0.0, c - h)), 4), round(float(min(1.0, c + h)), 4)]


def mcnemar_exact(b, c):
    """Two-sided exact McNemar p (b = ref flags only, c = cand flags only)."""
    from scipy.stats import binomtest
    n = b + c
    return 1.0 if n == 0 else float(binomtest(min(b, c), n, 0.5).pvalue)


# ============================================================================ MARIDA val (primary check)
_VAL = None


def load_val():
    global _VAL
    if _VAL is None:
        from macroplastic.data.marida import list_patches
        X, y, conf, pid = TL.load_split("val")
        scenes = [p.parent.name for p in list_patches("val")]
        names = sorted(set(scenes))
        lut = np.array([names.index(s) for s in scenes])
        _VAL = dict(X=X, y=y, conf=conf, sid=lut[pid], n_sc=len(names))
    return _VAL


def val_metrics(score, thr=None):
    """Pooled labelled-pixel metrics on MARIDA val; thr=None -> chosen on val (grid), scene bootstrap CI of F1."""
    v = load_val()
    true_md = v["y"] == 1
    score = np.asarray(score, np.float32).copy()
    score[np.isnan(v["X"][:, :11]).any(1)] = 0.0
    if thr is None:
        thr, met, _ = TL.best_threshold(true_md, score, GRID)
    else:
        met = TL.md_metrics(true_md, score >= thr)
    pred = score >= thr
    sid, n_sc = v["sid"], v["n_sc"]
    tp = np.bincount(sid, weights=(true_md & pred), minlength=n_sc)
    fp = np.bincount(sid, weights=(~true_md & pred), minlength=n_sc)
    fn = np.bincount(sid, weights=(true_md & ~pred), minlength=n_sc)
    rng = np.random.default_rng(0)
    idx = rng.integers(0, n_sc, size=(N_BOOT, n_sc))
    T, F, N = tp[idx].sum(1), fp[idx].sum(1), fn[idx].sum(1)
    f1 = np.where(2 * T + F + N > 0, 2 * T / np.maximum(2 * T + F + N, 1), np.nan)
    lo, hi = np.nanpercentile(f1, [2.5, 97.5])
    fp_cls = {str(k): int(np.sum(pred & (v["y"] == k))) for k in range(2, 16) if np.any(v["y"] == k)}
    return {"threshold": round(float(thr), 3), "f1": round(met["f1"], 4), "ci95": [round(float(lo), 4), round(float(hi), 4)],
            "precision": round(met["precision"], 4), "recall": round(met["recall"], 4), "iou": round(met["iou"], 4),
            "tp": met["tp"], "fp": met["fp"], "fn": met["fn"], "fp_by_class": fp_cls}


# ============================================================================ new data (pairs D, extra B/D)
def pair_acq(scene: str) -> str:
    m = json.loads((QDIR / scene / "meta.json").read_text(encoding="utf-8"))
    return f"{m['tile']}_{m['scene_datetime'][:10].replace('-', '')}"


def pairs_table():
    import pandas as pd
    d = pd.read_csv(VIS_LABELS)
    d["acq"] = d["scene"].map(pair_acq)
    d["fold"] = d["acq"].map(fold_of)
    d["level"] = np.where(d["class"].isin(list(D_CLASSES)), "D", np.where(d["class"] == "accumulation", "C", "excluded"))
    return d


def cmd_freeze(a):
    if EVAL_YAML.exists():
        raise SystemExit(f"{EVAL_YAML} exists - the check is frozen; do not overwrite (write a new version file instead)")
    from macroplastic.data.marida import list_patches, resolve_root
    v = load_val()
    split_file = resolve_root() / "splits" / "val_X.txt"
    d = pairs_table()
    comp = {}
    for f in range(N_FOLDS):
        g = d[d.fold == f]
        comp[f"fold{f}"] = {"acquisitions": sorted(g.acq.unique().tolist()), "scenes": sorted(g.scene.unique().tolist()),
                            "objects_by_class": {k: int(n) for k, n in g["class"].value_counts().items()}}
    cfg = {
        "version": 1,
        "frozen_at": datetime.now().isoformat(timespec="seconds"),
        "frozen_by": "L92 (worker-ml), before any detector_v2 experiment",
        "test_policy": "MARIDA test is NOT read (TL.load_split asserts train/val; opened once for the final number, not for selection)",
        "primary": {
            "name": "MARIDA val (official split)",
            "split_file": str(split_file.relative_to(ROOT)).replace("\\", "/"), "split_file_sha256": sha256(split_file),
            "feature_cache": "out/l3_cache/val_win.npz", "feature_cache_sha256": sha256(ROOT / "out/l3_cache/val_win.npz"),
            "n_patches": len(list_patches("val")), "n_scenes": int(v["n_sc"]), "n_labelled_px": int(len(v["y"])),
            "n_md_px": int(np.sum(v["y"] == 1)),
            "metric": "F1 of Marine Debris vs all other labelled pixels (pooled), pixels with NaN band -> not MD",
            "threshold": f"chosen on val per model, grid {GRID} (project convention, as weights/lgbm/meta.json); "
                         "the same threshold is used on new data",
            "ci": f"scene bootstrap {N_BOOT} reps, numpy default_rng(0) (as scripts/baselines.py)",
            "seeds": [0, 1, 2],
        },
        "new_data": {
            "grouping_key": "acquisition = <S2 tile>_<YYYYMMDD> (pairs: from data/pairs/quality/<scene>/meta.json; "
                            "extra: registry tile+date)",
            "fold_rule": f"fold = int(sha256('{SALT}' + key)[:8], 16) % {N_FOLDS}",
            "protocol": "3-fold cross-fitting: objects of fold k are scored by a model trained on MARIDA train + MADOS "
                        "+ new data of the other folds (never its own acquisition); the final candidate is trained on "
                        "all folds and is judged on MARIDA val only",
            "pairs_visual": {
                "file": "reports/case_pairs/visual_labels.csv", "sha256": sha256(VIS_LABELS),
                "labelled_by": "AI agent, image chips, one annotator (L78b) - verified D, NOT field truth",
                "class_to_D": D_CLASSES, "excluded": EXCLUDED,
                "train_confidence": list(TRAIN_CONF), "eval_confidence": "all (high/medium/low reported separately)",
                "pixels": "all pixels of the object bbox (bbox of a 1-8 px component; extra pixels are adjacent water, also not debris)",
                "band_cache": "out/l78_bands/<scene>.npz (S2 L2A crops, scripts/case/pairs_detector_review.py)",
                "composition": comp,
            },
            "extra": "data/extra/features_*.npz + registry*.csv (L89/L90): same fold rule on tile_date; the composition is "
                     "computed deterministically by the rule and written to out/detector_v2/extra_folds.json",
            "metrics": {
                "D_false_alarm": "share of D objects with max P(MD) over the object's pixels >= the model's val threshold; "
                                 "Wilson 95 % CI; inputs 'none' (as the service now) = primary, 'water_median' = secondary",
                "B_recall": "share of B objects with max P >= threshold (and pixel recall); Wilson 95 % CI",
            },
            "descriptive_only": f"flag rate on {WATER_SAMPLE} random valid-water pixels per pair crop (22 S2 crops, unlabelled; "
                                "not used for decisions)",
        },
        "reference": {"weights": "weights/lgbm", "model_sha256": sha256(REF_DIR / "model.txt"),
                      "threshold": json.loads((REF_DIR / "meta.json").read_text(encoding="utf-8"))["threshold"],
                      "note": "rollback; trained on MARIDA train + MADOS (config in its meta.json) - r0 re-trains the same "
                              "config with seeds 0-2 to measure seed std"},
        "baselines": {
            "fdi_threshold": "single FDI threshold + direction tuned on val (scripts/baselines.py sweep)",
            "rf": f"RandomForest MARIDA params (data/case/detector_preds/rf_seed5.joblib, sha256 {sha256(RF_PATH)[:16]}...), "
                  "trained on MARIDA train, 19 features; P(MD) >= t tuned on val, and argmax",
        },
        "acceptance": {
            "primary_project_rule": "ACCEPT if mean_seeds F1_val(cand) - F1_val(reference) >= max(0.01, 2*std_seeds(cand), "
                                    "2*std_seeds(r0)) AND D false-alarm (none) not higher than reference by > 0.05 AND "
                                    "B recall (if B exists) not lower than reference by > 0.05",
            "secondary_domain_rule": "DOMAIN CANDIDATE (orchestrator decides) if F1_val(cand) >= F1_val(reference) - 0.01 for "
                                     "the seed mean AND the D false-alarm rate (none) is lower than reference with exact "
                                     "McNemar p < 0.05 on the same D objects for every seed AND B recall non-inferior (-0.05)",
            "otherwise": "REJECT (the rollback weights/lgbm stays)",
        },
    }
    EVAL_YAML.write_text("# FROZEN before detector_v2 experiments (L92). Do not edit; a new check = a new file.\n"
                         + yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    h = sha256(EVAL_YAML)
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "eval_yaml.sha256").write_text(h + "  configs/detector_v2_eval.yaml\n", encoding="utf-8")
    print(f"frozen {EVAL_YAML} sha256 {h}")
    log_line("4 детектор", "L92: проверка detector_v2 зафиксирована до экспериментов (MARIDA val + 3-fold по снимкам для новых B/D)",
             f"sha256 {h[:16]}", "configs/detector_v2_eval.yaml")


def eval_cfg():
    if not EVAL_YAML.exists():
        raise SystemExit("run `freeze` first")
    return yaml.safe_load(EVAL_YAML.read_text(encoding="utf-8"))


def _pair_bands(scene):
    import pairs_detector_review as R
    return R.load_bands(QDIR / scene)


def cmd_prep_pairs(a):
    """Features (win, 48) of the labelled objects' pixels, per input mode, + random valid-water sample per crop."""
    import rasterio
    from macroplastic.live import stac
    from macroplastic.models.lgbm_predict import load_predictor
    d = pairs_table()
    href = load_predictor(REF_DIR, harmonize="water_median")
    rng = np.random.default_rng(92)
    rows = {m: [] for m in MODES}
    wrows = {m: [] for m in MODES}
    meta_rows, wmeta = [], []
    scenes = sorted(p.parent.name for p in QDIR.glob("*/prob.tif") if (BANDS_CACHE / f"{p.parent.name}.npz").is_file())
    t0 = time.time()
    for sc in scenes:
        bands, scl = _pair_bands(sc)
        with rasterio.open(QDIR / sc / "quality.tif") as ds:
            qa = ds.read(1)
        H, W = qa.shape
        g = d[d.scene == sc]
        sel = np.zeros((H, W), np.int32) - 1  # object row index per pixel
        for i, r in g.iterrows():
            y0, x0, y1, x1 = [int(v) for v in str(r["bbox"]).split()]
            sel[y0:y1, x0:x1] = i
        wy, wx = np.nonzero(qa == 1)
        k = min(WATER_SAMPLE, len(wy))
        pick = rng.choice(len(wy), size=k, replace=False) if k else np.array([], int)
        wmask = np.zeros((H, W), bool)
        wmask[wy[pick], wx[pick]] = True
        for mode in MODES:
            if mode == "none":
                arr, names = bands, stac.BANDS
            else:
                arr, names = href._harmonized(bands, stac.BANDS, (scl == 6))
            obj_feats, w_feats = [], []
            for (y0, y1, x0, x1), f in compute_features_blocked(arr, names, "win"):
                s = sel[y0:y1, x0:x1]
                m = s >= 0
                if m.any():
                    obj_feats.append((f[:, m].T.copy(), s[m].copy()))
                wm = wmask[y0:y1, x0:x1]
                if wm.any():
                    w_feats.append(f[:, wm].T.copy())
            for X, oi in obj_feats:
                rows[mode].append((X, oi))
            if w_feats:
                wrows[mode].append(np.concatenate(w_feats))
        wmeta.append((sc, pair_acq(sc), int(k)))
        print(f"[prep-pairs] {sc}: {len(g)} objects, water sample {k} ({time.time() - t0:.0f}s)", flush=True)
    out = {}
    for mode in MODES:
        X = np.concatenate([r[0] for r in rows[mode]])
        oi = np.concatenate([r[1] for r in rows[mode]])
        out[f"X_{mode}"] = X
        out[f"obj_{mode}"] = oi
        out[f"W_{mode}"] = np.concatenate(wrows[mode])
    assert np.array_equal(out["obj_none"], out["obj_water_median"])
    wscene = np.concatenate([np.full(n, i, np.int32) for i, (_, _, n) in enumerate(wmeta)])
    WORK.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(WORK / "pairs_feats.npz", X_none=out["X_none"], X_water_median=out["X_water_median"],
                        obj=out["obj_none"], W_none=out["W_none"], W_water_median=out["W_water_median"],
                        wscene=wscene, wscenes=np.array([m[0] for m in wmeta]), names=np.array(WIN))
    d.to_csv(WORK / "pairs_objects.csv", index=True, index_label="row")
    n_obj = len(np.unique(out["obj_none"]))
    print(f"[prep-pairs] {len(out['obj_none'])} object px ({n_obj} objects), water sample {len(wscene)} px -> {WORK / 'pairs_feats.npz'}")


def load_pairs():
    import pandas as pd
    z = np.load(WORK / "pairs_feats.npz", allow_pickle=False)
    d = pd.read_csv(WORK / "pairs_objects.csv", index_col="row")
    return z, d


# ---------------------------------------------------------------------------- extra data (L89 / L90)
def load_extra():
    """Normalised new B/D pixels from data/extra/features_*.npz. Expected keys (flexible): X (N,48) + names, labels as
    y (1 = B positive, 0 = D negative) or level ('B'/'D'), acquisition key per row as acq / tile_date / scene, object id
    as obj (optional). Rows of level C or unknown are dropped. Returns dict or None."""
    out_file = WORK / "extra_norm.npz"
    if not out_file.is_file():
        return None
    z = np.load(out_file, allow_pickle=False)
    return {k: z[k] for k in z.files}


def cmd_prep_extra(a):
    files = sorted(EXTRA.glob("features_*.npz")) + sorted(WORK.glob("features_*.npz"))
    if a.only:
        files = [f for f in files if any(k in f.name for k in a.only)]
    if not files:
        print("no data/extra/features_*.npz or out/detector_v2/features_*.npz yet")
        return
    Xs, Xws, hws, ys, acqs, objs, srcs = [], [], [], [], [], [], []
    report = {}
    for f in files:
        z = np.load(f, allow_pickle=True)
        keys = set(z.files)
        names = [str(n) for n in z["names"]] if "names" in keys else (WIN if z["X"].shape[1] == len(WIN) else None)
        if names is None or not set(WIN) <= set(names):
            report[f.name] = f"skipped: features {None if names is None else len(names)} != win 48"
            continue
        X = z["X"][:, [names.index(n) for n in WIN]].astype(np.float32)
        if "level" in keys:
            lev = np.array([str(s).upper() for s in z["level"]])
        elif "y" in keys:
            lev = np.where(z["y"].astype(int) == 1, "B", "D")
        elif "label" in keys:
            lev = np.where(z["label"].astype(int) == 1, "B", "D")
        else:
            report[f.name] = "skipped: no level/y/label"
            continue
        ak = next((k for k in ("acq", "tile_date", "scene", "scene_id") if k in keys), None)
        if ak is None:
            report[f.name] = "skipped: no acquisition key"
            continue
        # frozen key format <tile>_<YYYYMMDD> (L90 writes 34VEM_2022-07-21)
        acq = np.array([re.sub(r"(\d{4})-(\d{2})-(\d{2})", lambda m_: "".join(m_.groups()), str(s)) for s in z[ak]])
        obj = np.array([f"{f.stem}:{a_}:{s}" for a_, s in zip(acq, z["obj"])]) if "obj" in keys else np.array(
            [f"{f.stem}:{s}" for s in acq])  # no object id -> one object per acquisition (coarse); obj unique per acq
        keep = np.isin(lev, ["B", "D"])
        if "X_wm" in keys:
            Xw = z["X_wm"][:, [names.index(n) for n in WIN]].astype(np.float32)
            hw = np.ones(len(X), bool)
        else:
            Xw = np.full_like(X, np.nan)
            hw = np.zeros(len(X), bool)
        Xws.append(Xw[keep]); hws.append(hw[keep])
        Xs.append(X[keep]); ys.append((lev[keep] == "B").astype(np.uint8)); acqs.append(acq[keep])
        objs.append(obj[keep]); srcs.append(np.full(int(keep.sum()), f.stem))
        report[f.name] = {"rows": int(len(lev)), "B": int(np.sum(lev == "B")), "D": int(np.sum(lev == "D")),
                          "dropped_C_or_unknown": int(np.sum(~keep)), "acq_key": ak, "sha256": sha256(f)}
    if not Xs:
        print(json.dumps(report, indent=1))
        return
    acq = np.concatenate(acqs)
    folds = np.array([fold_of(k) for k in acq], np.int8)
    np.savez_compressed(WORK / "extra_norm.npz", X=np.concatenate(Xs), X_wm=np.concatenate(Xws), has_wm=np.concatenate(hws),
                        y=np.concatenate(ys), acq=acq,
                        obj=np.concatenate(objs), src=np.concatenate(srcs), fold=folds)
    comp = {}
    for k in sorted(set(acq)):
        comp[k] = int(fold_of(k))
    (WORK / "extra_folds.json").write_text(json.dumps({"rule": eval_cfg()["new_data"]["fold_rule"], "files": report,
                                                       "acq_fold": comp}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=1, ensure_ascii=False))


# ============================================================================ models
class LGBMModel:
    def __init__(self, booster, thr=None, name=""):
        self.b, self.thr, self.name = booster, thr, name
        self.fnames = booster.feature_name()
        self.fidx = [WIN.index(n) for n in self.fnames]

    def score(self, X):
        return self.b.predict(X[:, self.fidx], num_threads=THREADS).astype(np.float32)


def base_cfg():
    return json.loads((REF_DIR / "meta.json").read_text(encoding="utf-8"))["config"]


def training_parts(cfg, seed, pairs_w=0.0, pairs_modes=("none",), extra_b_w=0.0, extra_d_w=0.0, exclude_fold=None,
                   extra_srcs=None, extra_modes=("none",), extra_cap=0, src_weights=None):
    import train_lgbm_mados as TM
    TM.MAX_THREADS = THREADS
    Xtr, ytr, ctr, _ = TL.load_split("train")
    sel = TL.sample_train(ytr, cfg, seed)
    parts = [(Xtr[sel], ytr[sel], ctr[sel], np.ones(len(sel), np.float32))]
    info = {"marida": {"n_px": int(len(sel)), "n_md": int(np.sum(ytr[sel] == 1))}}
    Xm, ym, cm, minfo = TM.mados_pixels(cfg, seed)
    parts.append((Xm, ym, cm, np.full(len(ym), float(cfg.get("mados_weight", 1.0)), np.float32)))
    info["mados"] = {"n_px": int(len(ym)), "n_md": int(np.sum(ym == 1)), "n_scenes": minfo["n_scenes"]}
    if pairs_w > 0:
        z, d = load_pairs()
        obj = z["obj"]
        ok_rows = d[(d.level == "D") & d.confidence.isin(TRAIN_CONF)]
        if exclude_fold is not None:
            ok_rows = ok_rows[ok_rows.fold != exclude_fold]
        m = np.isin(obj, ok_rows.index.values)
        for mode in pairs_modes:
            X = z[f"X_{mode}"][m]
            parts.append((X, np.full(len(X), 20, np.uint8), np.ones(len(X), np.uint8), np.full(len(X), pairs_w, np.float32)))
        info["pairs_D"] = {"n_objects": int(len(ok_rows)), "n_px": int(m.sum()) * len(pairs_modes), "weight": pairs_w,
                           "modes": list(pairs_modes), "source": "pairs_visual (AI-agent labels)",
                           "by_class": {k: int(v) for k, v in ok_rows["class"].value_counts().items()}}
    ex = load_extra() if (extra_b_w > 0 or extra_d_w > 0) else None
    if ex is not None:
        keep = np.ones(len(ex["y"]), bool) if exclude_fold is None else ex["fold"] != exclude_fold
        if extra_srcs:
            keep &= np.isin(ex["src"], list(extra_srcs))
        for lab, w, code in ((1, extra_b_w, 1), (0, extra_d_w, 20)):
            if w <= 0:
                continue
            m = keep & (ex["y"] == lab)
            if extra_cap:  # at most extra_cap px per (source, label), like MARIDA cap_per_class (seeded)
                rng = np.random.default_rng(seed + 92)
                for sv in np.unique(ex["src"][m]):
                    ii = np.flatnonzero(m & (ex["src"] == sv))
                    if len(ii) > extra_cap:
                        m[np.setdiff1d(ii, rng.choice(ii, extra_cap, replace=False))] = False
            sw_src = np.array([float((src_weights or {}).get(str(v), 1.0)) for v in ex["src"]], np.float32)
            for mode in extra_modes:
                mm = m if mode == "none" else (m & ex["has_wm"])
                Xe = ex["X"][mm] if mode == "none" else ex["X_wm"][mm]
                parts.append((Xe, np.full(int(mm.sum()), code, np.uint8), np.ones(int(mm.sum()), np.uint8),
                              w * sw_src[mm]))
            info[f"extra_{'B' if lab else 'D'}"] = {"n_px": int(m.sum()), "weight": w, "modes": list(extra_modes),
                                                    "srcs": sorted(set(ex["src"][m].tolist())),
                                                    "n_acq": int(len(set(ex["acq"][m]))), "n_obj": int(len(set(ex["obj"][m])))}
    X = np.concatenate([p[0] for p in parts]); y = np.concatenate([p[1] for p in parts])
    c = np.concatenate([p[2] for p in parts]); sw = np.concatenate([p[3] for p in parts])
    return X, y, c, sw, info


def fit(cfg, seed, **kw):
    import train_lgbm_mados as TM
    TM.MAX_THREADS = THREADS
    X, y, c, sw, info = training_parts(cfg, seed, **kw)
    fidx = [WIN.index(n) for n in feature_names(cfg["features"])]
    b = TM.train_weighted(X, y, c, sw, cfg, seed, fidx)
    water_ref = [round(float(v), 5) for v in np.nanmedian(X[y == 7, :11], 0)]
    return b, info, water_ref


# ============================================================================ new-data evaluation
def eval_pairs(score_fn_by_fold, thr_by_fold, obj_d):
    """score_fn_by_fold(fold) -> callable(X) scores. Returns D false-alarm metrics per mode, OOF by fold."""
    z, d = load_pairs()
    obj = z["obj"]
    out = {}
    for mode in MODES:
        X = z[f"X_{mode}"]
        flags = {}
        pmax = {}
        for f in range(N_FOLDS):
            rows = d[(d.fold == f)].index.values
            m = np.isin(obj, rows)
            if not m.any():
                continue
            s = score_fn_by_fold(f)(X[m])
            oo = obj[m]
            for r in np.unique(oo):
                pm = float(s[oo == r].max())
                pmax[int(r)] = pm
                flags[int(r)] = bool(pm >= thr_by_fold(f))
        res = {}
        for name, sel in (("D_all", d.level == "D"), ("D_train_conf", (d.level == "D") & d.confidence.isin(TRAIN_CONF)),
                          ("excluded_unclear", d["class"] == "unclear"), ("C_accumulation", d.level == "C")):
            ids = [int(i) for i in d[sel].index]
            k = sum(flags[i] for i in ids)
            res[name] = {"n": len(ids), "flagged": int(k), "rate": round(k / len(ids), 4) if ids else None, "ci95": wilson(k, len(ids))}
        by = {}
        for cl in D_CLASSES:
            ids = [int(i) for i in d[d["class"] == cl].index]
            k = sum(flags[i] for i in ids)
            by[D_CLASSES[cl]] = f"{k}/{len(ids)}"
        res["D_by_class"] = by
        by_conf = {}
        for cf in ("high", "medium", "low"):
            ids = [int(i) for i in d[(d.level == "D") & (d.confidence == cf)].index]
            by_conf[cf] = f"{sum(flags[i] for i in ids)}/{len(ids)}"
        res["D_by_confidence"] = by_conf
        res["flags"] = {str(i): int(flags[i]) for i in sorted(flags)}
        res["pmax"] = {str(i): round(pmax[i], 4) for i in sorted(pmax)}
        out[mode] = res
    return out


def water_flag_rate(score_fn, thr):
    z, _ = load_pairs()
    res = {}
    for mode in MODES:
        s = score_fn(z[f"W_{mode}"]) >= thr
        per = {}
        for i, sc in enumerate(z["wscenes"]):
            m = z["wscene"] == i
            per[str(sc)] = round(float(s[m].mean()), 6) if m.any() else None
        res[mode] = {"n_px": int(len(s)), "flag_rate": round(float(s.mean()), 6), "per_crop": per}
    return res


def eval_extra(score_fn_by_fold, thr_by_fold):
    """OOF B recall / D false alarm on the new extra data, per input mode and per source file."""
    ex = load_extra()
    if ex is None:
        return None
    out = {}
    for mode in MODES:
        Xall = ex["X"] if mode == "none" else ex["X_wm"]
        rowok = np.ones(len(ex["y"]), bool) if mode == "none" else ex["has_wm"]
        if not rowok.any():
            continue
        flags, lab, src = {}, {}, {}
        pix = {}
        for f in range(N_FOLDS):
            m = (ex["fold"] == f) & rowok
            if not m.any():
                continue
            s = score_fn_by_fold(f)(Xall[m])
            t = thr_by_fold(f)
            ob, yy, ss = ex["obj"][m], ex["y"][m], ex["src"][m]
            order = np.argsort(ob, kind="stable")
            u, first = np.unique(ob[order], return_index=True)
            mx = np.maximum.reduceat(s[order], first)
            for o, v, j in zip(u, mx, first):
                flags[str(o)] = bool(v >= t)
                lab[str(o)] = int(yy[order][j])
                src[str(o)] = str(ss[order][j])
            for sv in np.unique(ss):
                for yv in (0, 1):
                    mm = (ss == sv) & (yy == yv)
                    if mm.any():
                        k = (str(sv), yv)
                        a, b = pix.get(k, (0, 0))
                        pix[k] = (a + int(np.sum(s[mm] >= t)), b + int(mm.sum()))
        res = {}
        for yv, nm in ((1, "B_recall"), (0, "D_false_alarm")):
            ids = [o for o in flags if lab[o] == yv]
            k = sum(flags[o] for o in ids)
            res[nm] = {"n_objects": len(ids), "flagged": int(k), "rate": round(k / len(ids), 4) if ids else None,
                       "ci95": wilson(k, len(ids))}
        by = {}
        for sv in sorted(set(src.values())):
            for yv, nm in ((1, "B_recall"), (0, "D_false_alarm")):
                ids = [o for o in flags if lab[o] == yv and src[o] == sv]
                if ids:
                    k = sum(flags[o] for o in ids)
                    pa, pb = pix.get((sv, yv), (0, 0))
                    by[f"{sv}:{nm}"] = {"n_objects": len(ids), "flagged": int(k), "rate": round(k / len(ids), 4),
                                        "ci95": wilson(k, len(ids)), "pixel_rate": round(pa / pb, 4) if pb else None}
        res["by_src"] = by
        res["flags"] = {o: int(v) for o, v in sorted(flags.items())}
        out[mode] = res
    return out


# ============================================================================ commands
def _save_run(name, rec):
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{name}.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")


def cmd_baselines(a):
    import lightgbm as lgb
    import baselines as BL
    eval_cfg()
    v = load_val()
    t0 = time.time()
    # reference weights/lgbm (rollback)
    ref_meta = json.loads((REF_DIR / "meta.json").read_text(encoding="utf-8"))
    ref = LGBMModel(lgb.Booster(model_file=str(REF_DIR / "model.txt")), float(ref_meta["threshold"]), "reference")
    sv = ref.score(v["X"])
    rec = {"exp": "reference", "kind": "reference weights/lgbm", "seed": ref_meta.get("seed"),
           "val_own_thr": val_metrics(sv, ref.thr), "val": val_metrics(sv)}
    rec["pairs"] = eval_pairs(lambda f: ref.score, lambda f: ref.thr, None)
    rec["water_flag"] = water_flag_rate(ref.score, ref.thr)
    rec["extra"] = eval_extra(lambda f: ref.score, lambda f: ref.thr)
    _save_run("reference", rec)
    print(f"[reference] val F1 {rec['val_own_thr']['f1']} (thr {ref.thr}) | D FA none {rec['pairs']['none']['D_all']} "
          f"| wm {rec['pairs']['water_median']['D_all']}", flush=True)
    # FDI single threshold (tuned on val)
    fi = WIN.index("FDI")
    t, direc, _ = BL.sweep(v["X"][:, fi], v["y"] == 1)

    def fdi_score(X):
        x = X[:, fi]
        with np.errstate(invalid="ignore"):
            r = (x >= t) if direc == "ge" else (x <= t)
        return r.astype(np.float32)
    rec = {"exp": "fdi_threshold", "kind": f"FDI {'>=' if direc == 'ge' else '<='} {t:.5f} (tuned on val)",
           "val": val_metrics(fdi_score(v["X"]), 0.5)}
    rec["pairs"] = eval_pairs(lambda f: fdi_score, lambda f: 0.5, None)
    rec["water_flag"] = water_flag_rate(fdi_score, 0.5)
    rec["extra"] = eval_extra(lambda f: fdi_score, lambda f: 0.5)
    _save_run("fdi_threshold", rec)
    print(f"[fdi] val F1 {rec['val']['f1']} | D FA none {rec['pairs']['none']['D_all']}", flush=True)
    # RandomForest (MARIDA params, seed 5, MARIDA train, 19 features)
    import joblib
    rf = joblib.load(RF_PATH)
    rf.n_jobs = THREADS
    ci = list(rf.classes_).index(1)

    def rf_score(X):
        Z = np.nan_to_num(X[:, :19], nan=0.0)
        return rf.predict_proba(Z)[:, ci].astype(np.float32)

    def rf_arg(X):
        Z = np.nan_to_num(X[:, :19], nan=0.0)
        return (rf.classes_[rf.predict_proba(Z).argmax(1)] == 1).astype(np.float32)
    s = rf_score(v["X"])
    vm = val_metrics(s)
    rec = {"exp": "rf_prob", "kind": "RandomForest MARIDA params seed 5, P(MD) >= t (t on val)", "val": vm}
    rec["pairs"] = eval_pairs(lambda f: rf_score, lambda f: vm["threshold"], None)
    rec["water_flag"] = water_flag_rate(rf_score, vm["threshold"])
    rec["extra"] = eval_extra(lambda f: rf_score, lambda f: vm["threshold"])
    _save_run("rf_prob", rec)
    print(f"[rf_prob] val F1 {vm['f1']} | D FA none {rec['pairs']['none']['D_all']}", flush=True)
    rec = {"exp": "rf_argmax", "kind": "RandomForest MARIDA params seed 5, argmax", "val": val_metrics(rf_arg(v["X"]), 0.5)}
    rec["pairs"] = eval_pairs(lambda f: rf_arg, lambda f: 0.5, None)
    rec["water_flag"] = water_flag_rate(rf_arg, 0.5)
    rec["extra"] = eval_extra(lambda f: rf_arg, lambda f: 0.5)
    _save_run("rf_argmax", rec)
    print(f"[rf_argmax] val F1 {rec['val']['f1']} | D FA none {rec['pairs']['none']['D_all']} ({time.time() - t0:.0f}s)", flush=True)


def cmd_train(a):
    eval_cfg()
    cfg = base_cfg()
    for kv in a.set:
        k, v = kv.split("=", 1)
        TL.set_key(cfg, k, v)
    kw = dict(pairs_w=float(a.pairs_d), pairs_modes=tuple(a.pairs_modes), extra_b_w=float(a.extra_b), extra_d_w=float(a.extra_d),
              extra_srcs=tuple(a.extra_src) if a.extra_src else None, extra_modes=tuple(a.extra_modes),
              extra_cap=int(a.extra_cap), src_weights={k: float(v) for k, v in (x.split("=", 1) for x in a.src_weight)})
    uses_new = kw["pairs_w"] > 0 or kw["extra_b_w"] > 0 or kw["extra_d_w"] > 0
    v = load_val()
    for seed in a.seeds:
        t0 = time.time()
        cfg_s = dict(cfg, seed=seed)
        b, info, water_ref = fit(cfg_s, seed, **kw)
        full = LGBMModel(b)
        vm = val_metrics(full.score(v["X"]))
        full.thr = vm["threshold"]
        # cross-fitted models for new data (only if new data is in training; otherwise the full model is out-of-sample)
        fold_models = {}
        if uses_new:
            for f in range(N_FOLDS):
                bf, _, _ = fit(cfg_s, seed, exclude_fold=f, **kw)
                mf = LGBMModel(bf)
                mf.thr = val_metrics(mf.score(v["X"]))["threshold"]
                fold_models[f] = mf
        get = (lambda f: fold_models[f]) if uses_new else (lambda f: full)
        rec = {"exp": a.exp, "seed": seed, "kind": "lightgbm retrain", "set": a.set, "train_kw": kw, "train_sources": info,
               "val": vm, "fold_thresholds": {str(f): m.thr for f, m in fold_models.items()} or None}
        rec["pairs"] = eval_pairs(lambda f: get(f).score, lambda f: get(f).thr, None)
        rec["water_flag"] = water_flag_rate(full.score, full.thr)
        rec["extra"] = eval_extra(lambda f: get(f).score, lambda f: get(f).thr)
        name = f"{a.exp}_s{seed}"
        od = WEXP / name
        od.mkdir(parents=True, exist_ok=True)
        b.save_model(str(od / "model.txt"))
        fn = b.feature_name()
        meta = {"model": "lgbm", "task": "binary", "feature_level": cfg["features"], "features": fn,
                "channels": ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"],
                "input": "reflectance 0..1 (MARIDA/MADOS ACOLITE rhorc + new data, see train_sources)",
                "classes": [0, 1], "md_class": 1, "water_ref": water_ref, "threshold": vm["threshold"],
                "postprocess_min_px": 0, "seed": seed, "config": cfg_s, "train_sources": info, "train_kw": kw,
                "val_md": vm, "note": "detector_v2 candidate (L92); threshold chosen on MARIDA val; MARIDA test never read; "
                                      "NOT in weights/ - the orchestrator decides"}
        (od / "meta.json").write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
        rec["weights"] = str(od.relative_to(ROOT)).replace("\\", "/")
        rec["seconds"] = round(time.time() - t0, 1)
        _save_run(name, rec)
        print(f"[{name}] val F1 {vm['f1']} {vm['ci95']} thr {vm['threshold']} | D FA none "
              f"{rec['pairs']['none']['D_all']['flagged']}/{rec['pairs']['none']['D_all']['n']} wm "
              f"{rec['pairs']['water_median']['D_all']['flagged']}/{rec['pairs']['water_median']['D_all']['n']} | "
              f"water flag none {rec['water_flag']['none']['flag_rate']} | {rec['seconds']}s", flush=True)


# ============================================================================ report
def _load_runs():
    out = {}
    for p in sorted(RUNS.glob("*.json")):
        r = json.loads(p.read_text(encoding="utf-8"))
        out.setdefault(r["exp"], []).append(r)
    return out


def decide(exp_runs, ref, r0_std):
    f1 = np.array([r["val"]["f1"] for r in exp_runs])
    mean, sd = float(f1.mean()), (float(f1.std(ddof=1)) if len(f1) > 1 else 0.0)
    ref_f1 = ref["val_own_thr"]["f1"]
    need = max(0.01, 2 * sd, 2 * (r0_std or 0.0))
    dF1 = mean - ref_f1
    ref_fa = ref["pairs"]["none"]["D_all"]
    fa = [r["pairs"]["none"]["D_all"]["rate"] for r in exp_runs]
    fa_ok = all(x is not None and ref_fa["rate"] is not None and x - ref_fa["rate"] <= 0.05 for x in fa)
    b_ok, b_note = True, "no B yet"
    if ref.get("extra") and ref["extra"].get("none"):
        # every B source separately (natural Cózar vs artificial PLP are never pooled for the decision)
        notes = []
        for k, v in ref["extra"]["none"]["by_src"].items():
            if not k.endswith(":B_recall") or not v["n_objects"]:
                continue
            cb = [((r.get("extra") or {}).get("none") or {}).get("by_src", {}).get(k, {}).get("rate") for r in exp_runs]
            ok = all(x is not None and x >= v["rate"] - 0.05 for x in cb)
            b_ok &= ok
            notes.append(f"{k.split(':')[0]}: ref {v['rate']} vs {cb} {'ok' if ok else 'WORSE'}")
        b_note = "; ".join(notes) or "no B"
    pvals = []
    for r in exp_runs:
        rf, cf = ref["pairs"]["none"]["flags"], r["pairs"]["none"]["flags"]
        ids = [i for i in rf if i in cf]
        b = sum(1 for i in ids if rf[i] and not cf[i])
        c = sum(1 for i in ids if cf[i] and not rf[i])
        pvals.append((b, c, mcnemar_exact(b, c)))
    if dF1 >= need and fa_ok and b_ok:
        dec = "ACCEPT (primary rule)"
    elif dF1 >= -0.01 and b_ok and all(p[2] < 0.05 and p[0] > p[1] for p in pvals):
        dec = "DOMAIN CANDIDATE (secondary rule, orchestrator decides)"
    else:
        dec = "REJECT"
    return {"f1_mean": round(mean, 4), "f1_std": round(sd, 4), "dF1_vs_ref": round(dF1, 4), "need": round(need, 4),
            "D_fa_none": fa, "D_fa_ok": fa_ok, "B": b_note, "mcnemar_ref_only_cand_only_p": pvals, "decision": dec}


def cmd_report(a):
    cfg = eval_cfg()
    runs = _load_runs()
    ref = runs["reference"][0]
    # McNemar on D objects only (flags include excluded/C rows -> restrict)
    z, d = load_pairs()
    d_ids = {str(i) for i in d[d.level == "D"].index}
    for rs in runs.values():
        for r in rs:
            for mode in MODES:
                r["pairs"][mode]["flags"] = {k: v for k, v in r["pairs"][mode]["flags"].items() if k in d_ids}
    r0 = runs.get("r0", [])
    r0_std = float(np.std([r["val"]["f1"] for r in r0], ddof=1)) if len(r0) > 1 else None
    table = []
    for exp, rs in runs.items():
        rs = sorted(rs, key=lambda r: r.get("seed") or 0)
        row = {"exp": exp, "kind": rs[0].get("kind"), "n_seeds": len(rs), "train_kw": rs[0].get("train_kw"),
               "train_sources": rs[0].get("train_sources"),
               "val_per_seed": [{k: r["val"][k] for k in ("f1", "ci95", "precision", "recall", "threshold", "fp", "fn")} for r in rs],
               "pairs_D_none": [r["pairs"]["none"]["D_all"] for r in rs],
               "pairs_D_wm": [r["pairs"]["water_median"]["D_all"] for r in rs],
               "pairs_D_by_class_none": rs[0]["pairs"]["none"]["D_by_class"],
               "pairs_D_by_class_wm": rs[0]["pairs"]["water_median"]["D_by_class"],
               "water_flag_none": [r["water_flag"]["none"]["flag_rate"] for r in rs],
               "water_flag_wm": [r["water_flag"]["water_median"]["flag_rate"] for r in rs],
               "extra": [r.get("extra") and {m: {k: v for k, v in r["extra"][m].items() if k != "flags"} for m in r["extra"]}
                         for r in rs]}
        if exp == "reference":
            row["val_per_seed"] = [{k: ref["val_own_thr"][k] for k in ("f1", "ci95", "precision", "recall", "threshold", "fp", "fn")}]
            row["decision"] = {"decision": "rollback (reference)"}
        elif rs[0].get("kind") == "lightgbm retrain":
            row["decision"] = decide(rs, ref, r0_std)
        else:
            row["decision"] = {"decision": "baseline (not a candidate)"}
        table.append(row)
    REP.mkdir(parents=True, exist_ok=True)
    out = {"generated": datetime.now().isoformat(timespec="seconds"), "eval_yaml": "configs/detector_v2_eval.yaml",
           "eval_yaml_sha256": sha256(EVAL_YAML), "acceptance": cfg["acceptance"], "r0_seed_std": r0_std,
           "experiments": table}
    (REP / "experiments.json").write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    write_md(out)
    print(f"-> {REP / 'experiments.md'}")


def _fa(x):
    if not x or x.get("n") in (None, 0):
        return "—"
    return f"{x['flagged']}/{x['n']} = {x['rate']:.2f} [{x['ci95'][0]:.2f}–{x['ci95'][1]:.2f}]"


def _srcs(r, key):
    short = {"features_cozar2024": "Cózar L98", "features_cozar2024_l2a": "Cózar L92", "features_vessels_fi": "суда",
             "features_clouds_cmc": "облака", "features_folines_norm": "FO линии", "features_forefined_norm": "FO Refined",
             "features_plpplastic_norm": "PLP", "features_plpwood_norm": "PLP дерево"}
    return ", ".join(short.get(x, x) for x in ((r.get("train_sources") or {}).get(key) or {}).get("srcs", []))


def write_md(out):
    L = ["# Детектор v2 — эксперименты (L92, §11 п.4)", "",
         f"Проверка зафиксирована ДО экспериментов: `configs/detector_v2_eval.yaml` (sha256 `{out['eval_yaml_sha256'][:16]}…`). "
         "Тест MARIDA не читался. Порог каждой модели выбран на MARIDA val (как у `weights/lgbm`), CI — бутстреп по сценам val.",
         "Новые данные (все — S2 L2A, как в сервисе): D пар — визуальная разметка ИИ-агентом 97 объектов на 22 снимках пар "
         "(пена/облако/блик/судно/шов; не полевая истина); B Cózar L92 — 234 случайные нити Cózar 2024 (1 на снимок, L2A той же съёмки); "
         "B Cózar L98 — 146 окон с 15 снимков; B/D FloatingObjects (L89: линии и проверенные точки Refined; исключены tangshan, toledo, "
         "tunisia, mandaluyong); D суда Финляндии и облака CMC (L90); PLP (L89) — искусственные малые мишени, отдельный тест. "
         "Оценка на новых данных — 3-fold по снимкам (тайл+дата): каждый объект оценивает модель, не видевшая его снимка. "
         "C (accumulation), unclear, U (непроверенный фон), W/C судов в обучение не входят.", "",
         "## Сводка", "",
         "Колонки D/B: доля объектов с хотя бы одним пикселем P ≥ порога модели (порог с MARIDA val), 95 % CI Уилсона, seed 0; "
         "«как в сервисе» = L2A без гармонизации; «гарм.» = water_median. Cózar — естественные нити плавучего материала (B, L2A той же съёмки); "
         "PLP — искусственные мишени (B, другая радиометрия).", "",
         "| эксперимент | данные | F1 MD val (seed) | 95 % CI (seed 0) | ΔF1 к откату | D пар, как в сервисе | D пар, гарм. | B Cózar, как в сервисе | B Cózar, гарм. | B FO линии | B FO Refined | D FO Refined | B PLP пластик (малые мишени) | D суда Финляндии, как в сервисе | D облака CMC, доля пикселей | вода пар, доля флагов | решение |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    rows = {}
    for r in out["experiments"]:
        f1s = [v["f1"] for v in r["val_per_seed"]]
        f1 = f"{np.mean(f1s):.4f}" + (f" ± {np.std(f1s, ddof=1):.4f} (n={len(f1s)})" if len(f1s) > 1 else "")
        ci = r["val_per_seed"][0]["ci95"]
        dd = r["decision"]
        dF = f"{dd['dF1_vs_ref']:+.4f} (нужно ≥ {dd['need']:.4f})" if "dF1_vs_ref" in dd else "—"
        data = r["kind"] if not r.get("train_kw") else (
            "MARIDA+MADOS" + (f" + D пар ×{r['train_kw']['pairs_w']:g} ({'/'.join(r['train_kw']['pairs_modes'])})" if r['train_kw']['pairs_w'] else "")
            + (f" + B ×{r['train_kw']['extra_b_w']:g} ({_srcs(r, 'extra_B')})" if r['train_kw']['extra_b_w'] else "")
            + (f" + D ×{r['train_kw']['extra_d_w']:g} ({_srcs(r, 'extra_D')})" if r['train_kw']['extra_d_w'] else "")
            + (f"; вес ист. {r['train_kw'].get('src_weights')}" if r['train_kw'].get('src_weights') else "")
            + (f"; ≤ {r['train_kw']['extra_cap']} px/ист." if r['train_kw'].get('extra_cap') else ""))
        wf = f"{np.mean(r['water_flag_none']):.5f}"
        ex0 = r["extra"][0] or {}

        def _d(src, m="none"):
            x = ((ex0.get(m) or {}).get("by_src") or {}).get(f"{src}:D_false_alarm")
            return _fa({"n": x["n_objects"], "flagged": x["flagged"], "rate": x["rate"], "ci95": x["ci95"]}) if x and x["n_objects"] else "—"

        def _px(src, m="none"):
            x = ((ex0.get(m) or {}).get("by_src") or {}).get(f"{src}:D_false_alarm")
            return f"{x['pixel_rate']:.4f}" if x and x.get("pixel_rate") is not None else "—"

        def _b(m, src):
            x = ((ex0.get(m) or {}).get("by_src") or {}).get(f"{src}:B_recall")
            return _fa({"n": x["n_objects"], "flagged": x["flagged"], "rate": x["rate"], "ci95": x["ci95"]}) if x and x["n_objects"] else "—"
        rows[r["exp"]] = (f"| {r['exp']} | {data} | {f1} | {ci[0]:.3f}–{ci[1]:.3f} | {dF} | {_fa(r['pairs_D_none'][0])} | "
                 f"{_fa(r['pairs_D_wm'][0])} | {_b('none', 'features_cozar2024_l2a')} | {_b('water_median', 'features_cozar2024_l2a')} | "
                 f"{_b('none', 'features_folines_norm')} | {_b('none', 'features_forefined_norm')} | {_d('features_forefined_norm')} | "
                 f"{_b('none', 'features_plpplastic_norm')} | {_d('features_vessels_fi')} | {_px('features_clouds_cmc')} | {wf} | {dd['decision']} |")
        
    ub, un = REP / "unet_baseline.json", REP / "unet_newdata.json"
    if ub.is_file() and un.is_file():
        vb = json.loads(ub.read_text(encoding="utf-8"))["val"]
        nd = json.loads(un.read_text(encoding="utf-8"))
        for k, lab in (("argmax", "argmax"), ("prob", f"P ≥ {nd['threshold_prob']}")):
            v = vb[f"unet_{k}"]
            pr, cz = nd["pairs"][k], nd["cozar_l2a"][k]
            rows[f"unet_marida_{k}"] = (f"| unet_marida_{k} | официальный U-Net MARIDA (L99), {lab}; только данные со снимками | {v['f1_md']:.4f} | "
                     f"{v['ci95_f1'][0]:.3f}–{v['ci95_f1'][1]:.3f} | — | {_fa(pr['D_all'])} | — | "
                     f"{_fa({'n': cz['n'], 'flagged': cz['flagged'], 'rate': cz['rate'], 'ci95': cz['ci95']})} | — | — | — | — | — | — | — | "
                     f"{pr['water_flag_rate_all_valid_px']:.5f} (все px воды) | baseline (not a candidate) |")
    first = ["reference", "r0", "rf_prob", "rf_argmax", "fdi_threshold", "unet_marida_argmax", "unet_marida_prob"]
    L += [rows[k] for k in first if k in rows] + [rows[k] for k in sorted(rows) if k not in first]
    L += ["", "D по классам (вход как в сервисе / с гармонизацией), первый seed:", ""]
    for r in out["experiments"]:
        L.append(f"- {r['exp']}: {r['pairs_D_by_class_none']} / {r['pairs_D_by_class_wm']}")
    L += ["", "## Правило принятия (записано до экспериментов)", ""]
    for k, v in out["acceptance"].items():
        L.append(f"- **{k}**: {v}")
    L += ["", f"Разброс F1 по seed у воспроизведения отката r0: {out['r0_seed_std']}.", "",
          "Полные числа (по seed, McNemar, B/D новых наборов) — `reports/detector_v2/experiments.json`; "
          "прогоны — `out/detector_v2/runs/*.json`; веса кандидатов — `weights_exp/detector_v2/` (не в `weights/`)."]
    (REP / "experiments.md").write_text("\n".join(L) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("freeze")
    sub.add_parser("prep-pairs")
    pe = sub.add_parser("prep-extra")
    pe.add_argument("--only", nargs="*", default=[], help="substrings of feature file names to include")
    sub.add_parser("baselines")
    t = sub.add_parser("train")
    t.add_argument("--exp", required=True)
    t.add_argument("--seeds", type=int, nargs="+", default=[0, 1, 2])
    t.add_argument("--pairs-d", type=float, default=0.0, help="weight of pair D pixels (0 = not used)")
    t.add_argument("--pairs-modes", nargs="+", default=["none"], choices=list(MODES))
    t.add_argument("--extra-b", type=float, default=0.0)
    t.add_argument("--extra-d", type=float, default=0.0)
    t.add_argument("--extra-src", nargs="*", default=[], help="source file stems to use (default all)")
    t.add_argument("--extra-modes", nargs="+", default=["none"], choices=list(MODES))
    t.add_argument("--extra-cap", type=int, default=0, help="max px per (source, label) in training (0 = all)")
    t.add_argument("--src-weight", nargs="*", default=[], help="per-source multiplier, e.g. features_folines_norm=0.3")
    t.add_argument("--set", nargs="*", default=[])
    sub.add_parser("report")
    a = ap.parse_args()
    {"freeze": cmd_freeze, "prep-pairs": cmd_prep_pairs, "prep-extra": cmd_prep_extra, "baselines": cmd_baselines,
     "train": cmd_train, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
