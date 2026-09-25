r"""Water-median harmonization of the main detector on MARIDA **val** only (test split is never read).

Question: live Sen2Cor L2A scenes get `harmonize="water_median"` (per-band offset that moves the scene's open-water
median to the MARIDA water median, weights/lgbm/meta.json water_ref). What does that offset do on MARIDA itself,
where the model is in-domain? If it costs F1 on val, harmonization is not "free".

Variants (weights/lgbm, threshold 0.63 from meta.json, CPU, lib_lightgbm via load_predictor):
  none       load_predictor(harmonize=None)
  per_patch  load_predictor(harmonize="water_median"), offset from the 256x256 patch itself; water mask = the spectral
             mask of LGBMPredictor.water_offset (water_mask=None: NDWI>0.1, B2<0.2, B11<0.05) - labels are NOT used.
             <2000 water px -> no offset (patch predicted as-is); counted.
  per_scene  one offset per scene (scene = patch name without trailing _N, as in scripts/final_test.py) from all val
             patches of that scene concatenated (same water_offset code, same spectral mask), then arr + offset with
             the harmonize=None predictor.
Metric (as scripts/final_test.py / scripts/case/detector_compare.py): Marine Debris (1) vs labelled background 2-15,
pooled over patches; unlabelled (0) excluded and reported separately; NaN pixels -> not MD (prob 0).
95 % CI: bootstrap over the 12 val scenes, 2000 reps, numpy default_rng(0) (same loop as final_test.py); paired dF1
uses the same scene draws. Threshold scan 0.30..0.95 step 0.01 per variant (on val, informative).
Sanity: variant none must reproduce reports/lgbm_final_test.json val_md (TP 984, FP 74, FN 91, F1 0.9226, CI).

    $env:CUDA_VISIBLE_DEVICES=""; .venv\Scripts\python.exe scripts\case\marida_harmonize_val.py
Outputs: reports/case_pairs/harmonize_val.json (the .md is written by hand from it)
"""
from __future__ import annotations

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
sys.path.insert(0, str(ROOT / "src"))
import l16_common as C  # noqa: E402  (split_names refuses the test split)

MODEL_DIR = ROOT / "weights" / "lgbm"
OUT = ROOT / "reports" / "case_pairs" / "harmonize_val.json"
BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B11", "B12"]
CLASSES = {0: "Unlabelled", 1: "Marine Debris", 2: "Dense Sargassum", 3: "Sparse Sargassum",
           4: "Natural Organic Material", 5: "Ship", 6: "Clouds", 7: "Marine Water", 8: "Sediment-Laden Water",
           9: "Foam", 10: "Turbid Water", 11: "Shallow Water", 12: "Waves", 13: "Cloud Shadows", 14: "Wakes",
           15: "Mixed Water"}
VARIANTS = ["none", "per_patch", "per_scene"]
GRID = np.round(np.arange(0.30, 0.95 + 1e-9, 0.01), 2)
N_BOOT = 2000
THREADS = min(12, C.THREADS)
_W: dict = {}


def scene_of(name: str) -> str:
    return name.rsplit("_", 1)[0]


# ------------------------------------------------------------------------------------------ workers
def _init(model_dir):
    from macroplastic.models.lgbm_predict import load_predictor

    _W["none"] = load_predictor(Path(model_dir), harmonize=None, num_threads=1)
    _W["wm"] = load_predictor(Path(model_dir), harmonize="water_median", num_threads=1)


def _scene_offset(args):
    """One offset from all patches of a scene (concatenated along x), spectral water mask, no labels."""
    scene, names = args
    from macroplastic.features.pixel import select_bands

    imgs = [select_bands(C.read_patch(n)[0], BANDS) for n in names]
    big = np.concatenate(imgs, axis=2)
    b = big
    with np.errstate(invalid="ignore", divide="ignore"):
        ndwi = (b[2] - b[7]) / (b[2] + b[7])
    n_water = int((np.isfinite(b).all(0) & (ndwi > 0.1) & (b[1] < 0.2) & (b[9] < 0.05)).sum())
    off = _W["none"].water_offset(big, BANDS, None)
    return scene, (None if off is None else off.tolist()), n_water


def _patch(args):
    name, scene_off = args
    from macroplastic.features.pixel import select_bands

    img, cl, _conf, _, _ = C.read_patch(name)
    img = select_bands(img, BANDS)
    probs = {"none": _W["none"].predict_proba(img, BANDS)}
    probs["per_patch"] = _W["wm"].predict_proba(img, BANDS)   # water_mask=None -> spectral mask
    pp_off = _W["wm"].last_offset
    if scene_off is None:
        probs["per_scene"] = probs["none"]
    else:
        probs["per_scene"] = _W["none"].predict_proba(img + np.asarray(scene_off, np.float32)[:, None, None], BANDS)
    lab = cl > 0
    unl = cl == 0
    return {"name": name, "cl": cl[lab],
            "p": {v: probs[v][lab].astype(np.float32) for v in VARIANTS},
            "unl_n": int(unl.sum()),
            "unl_ge": {v: [int((probs[v][unl] >= t).sum()) for t in GRID] for v in VARIANTS},
            "pp_offset": None if pp_off is None else [round(float(x), 5) for x in pp_off],
            "nan_px": int((~np.isfinite(img).all(0)).sum())}


# ------------------------------------------------------------------------------------------ metrics
def ci(v):
    return [round(float(np.percentile(v, 2.5)), 4), round(float(np.percentile(v, 97.5)), 4)]


def f1_of(t, p, n):
    return 2 * t / max(1, 2 * t + p + n)


def main():
    t0 = time.time()
    meta = json.loads((MODEL_DIR / "meta.json").read_text(encoding="utf-8"))
    thr = float(meta["threshold"])
    names = C.split_names("val")
    scenes_of = np.array([scene_of(n) for n in names])
    scenes = np.unique(scenes_of)
    groups = [(s, [n for n in names if scene_of(n) == s]) for s in scenes]

    with ProcessPoolExecutor(max_workers=THREADS, initializer=_init, initargs=(str(MODEL_DIR),)) as ex:
        so = {s: (off, nw) for s, off, nw in ex.map(_scene_offset, groups)}
        t_off = time.time() - t0
        print(f"[scene offsets] {t_off:.0f}s", flush=True)
        res = list(ex.map(_patch, [(n, so[scene_of(n)][0]) for n in names], chunksize=4))
    t_pred = time.time() - t0 - t_off
    print(f"[predict] {len(res)} patches {t_pred:.0f}s", flush=True)

    y = np.concatenate([r["cl"] for r in res])
    md = y == 1
    pid_scene = np.concatenate([np.full(len(r["cl"]), scene_of(r["name"])) for r in res])
    P = {v: np.concatenate([r["p"][v] for r in res]) for v in VARIANTS}
    n_unl = sum(r["unl_n"] for r in res)
    i_thr = int(np.where(GRID == thr)[0][0])

    out_var, per_scene_counts = {}, {}
    for v in VARIANTS:
        pred = P[v] >= thr
        tp, fp, fn, _ = C.confusion(md, pred)
        sc = C.scores(tp, fp, fn)
        per = {s: C.confusion(md[pid_scene == s], pred[pid_scene == s])[:3] for s in scenes}
        per_scene_counts[v] = per
        rng = np.random.default_rng(0)
        f1s = []
        for _ in range(N_BOOT):
            pick = rng.choice(scenes, len(scenes), replace=True)
            t = sum(per[s][0] for s in pick); p = sum(per[s][1] for s in pick); n = sum(per[s][2] for s in pick)
            f1s.append(f1_of(t, p, n))
        cls_n = np.bincount(y, minlength=16)
        cls_p = np.bincount(y[pred], minlength=16)
        unl_pred = sum(r["unl_ge"][v][i_thr] for r in res)
        # threshold scan (pooled labelled pixels)
        scan = []
        for t in GRID:
            pr = P[v] >= t
            a, b, c, _ = C.confusion(md, pr)
            scan.append(f1_of(a, b, c))
        k = int(np.argmax(scan))
        out_var[v] = {
            "threshold": thr, "tp": tp, "fp": fp, "fn": fn,
            "precision_md": sc["precision"], "recall_md": sc["recall"], "f1_md": sc["f1"], "iou_md": sc["iou"],
            "ci95_f1": ci(f1s),
            "unlabelled_px": n_unl, "unlabelled_pred_md": unl_pred, "unlabelled_pred_md_rate": unl_pred / max(1, n_unl),
            "fp_by_class": {CLASSES[c]: {"n": int(cls_n[c]), "pred_md": int(cls_p[c]),
                                         "rate": float(cls_p[c] / cls_n[c]) if cls_n[c] else None}
                            for c in range(2, 16)},
            "per_scene": {s: {"tp": int(per[s][0]), "fp": int(per[s][1]), "fn": int(per[s][2]),
                              "f1": (round(f1_of(*per[s]), 4) if (per[s][0] + per[s][1] + per[s][2]) else None)}
                          for s in scenes},
            "threshold_scan": {"grid": [float(t) for t in GRID], "f1": [round(float(f), 4) for f in scan],
                               "best_threshold": float(GRID[k]), "best_f1": round(float(scan[k]), 4),
                               "f1_at_default": round(float(scan[i_thr]), 4),
                               "unlabelled_pred_md_at_best": int(sum(r["unl_ge"][v][k] for r in res))},
        }

    # paired bootstrap dF1 (same scene draws as the per-variant CIs: default_rng(0))
    paired = {}
    for v in ("per_patch", "per_scene"):
        rng = np.random.default_rng(0)
        a_, b_ = per_scene_counts[v], per_scene_counts["none"]
        d = []
        for _ in range(N_BOOT):
            pick = rng.choice(scenes, len(scenes), replace=True)
            fa = f1_of(*(sum(a_[s][i] for s in pick) for i in range(3)))
            fb = f1_of(*(sum(b_[s][i] for s in pick) for i in range(3)))
            d.append(fa - fb)
        d = np.array(d)
        sc_d = [(f1_of(*a_[s]) - f1_of(*b_[s])) for s in scenes if sum(b_[s]) + sum(a_[s]) > 0]
        paired[f"{v}_minus_none"] = {
            "delta_f1": round(out_var[v]["f1_md"] - out_var["none"]["f1_md"], 4), "ci95": ci(d),
            "share_reps_delta_le_0": round(float((d <= 0).mean()), 4),
            "scenes_better": int(sum(x > 1e-12 for x in sc_d)), "scenes_worse": int(sum(x < -1e-12 for x in sc_d)),
            "scenes_equal": int(sum(abs(x) <= 1e-12 for x in sc_d))}

    # offsets
    pp = [r["pp_offset"] for r in res]
    n_pp = sum(o is not None for o in pp)
    pp_arr = np.array([o for o in pp if o is not None])
    offsets = {
        "per_patch": {"n_patches": len(res), "n_with_offset": n_pp, "n_without_offset_lt2000_water_px": len(res) - n_pp,
                      "by_scene_n_with_offset": {s: int(sum(r["pp_offset"] is not None for r in res
                                                            if scene_of(r["name"]) == s)) for s in scenes},
                      "median_offset_per_band": dict(zip(BANDS, np.round(np.median(pp_arr, 0), 5).tolist())) if n_pp else None,
                      "median_abs_offset_per_band": dict(zip(BANDS, np.round(np.median(np.abs(pp_arr), 0), 5).tolist())) if n_pp else None},
        "per_scene": {s: {"n_patches": int((scenes_of == s).sum()), "n_water_px": so[s][1],
                          "offset": (None if so[s][0] is None else dict(zip(BANDS, np.round(so[s][0], 5).tolist())))}
                      for s in scenes},
        "water_ref": dict(zip(BANDS, meta["water_ref"])),
    }

    # sanity vs frozen final test val numbers
    ft = json.loads((ROOT / "reports" / "lgbm_final_test.json").read_text(encoding="utf-8"))["val_md"]
    a = out_var["none"]
    sanity = {k: {"here": a[k], "lgbm_final_test": ft[k], "equal": abs(a[k] - ft[k]) < 1e-12}
              for k in ("tp", "fp", "fn", "f1_md", "iou_md", "precision_md", "recall_md")}
    sanity["ci95_f1"] = {"here": a["ci95_f1"], "lgbm_final_test": ft["ci95"], "equal": a["ci95_f1"] == ft["ci95"]}
    sanity["all_equal"] = all(v["equal"] for v in sanity.values() if isinstance(v, dict))
    print("[sanity] none == reports/lgbm_final_test.json val_md:", sanity["all_equal"], flush=True)

    rep = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "script": "scripts/case/marida_harmonize_val.py",
        "source": {"model": "weights/lgbm (model.txt, meta.json)", "threshold_from": "weights/lgbm/meta.json (0.63, chosen on val)",
                   "predictor": "macroplastic.models.lgbm_predict.load_predictor(harmonize=None | 'water_median'), CPU lib_lightgbm, num_threads=1 per worker",
                   "data": "MARIDA val: data/MARIDA/splits/val_X.txt, data/MARIDA/patches/*, labels *_cl.tif",
                   "test_split_used": False},
        "protocol": {
            "variants": {"none": "harmonize=None",
                         "per_patch": "harmonize='water_median', offset from the 256x256 patch, water_mask=None (spectral: NDWI(B3,B8)>0.1, B2<0.2, B11<0.05), no labels; <2000 water px -> no offset",
                         "per_scene": "one offset per scene from all val patches of the scene (concatenated), same water_offset code and spectral mask; arr + offset through harmonize=None"},
            "scene_id": "patch name without trailing _N (as scripts/final_test.py)",
            "metric": "Marine Debris (1) vs labelled background (2-15), pooled over patches; unlabelled (0) excluded, reported separately; NaN px -> not MD",
            "ci": f"bootstrap over {len(scenes)} val scenes, {N_BOOT} reps, numpy default_rng(0), same loop as scripts/final_test.py; paired dF1 on the same draws",
            "threshold_scan": "0.30..0.95 step 0.01, pooled labelled val pixels (selected on val = optimistic for that variant)"},
        "n_patches": len(res), "n_scenes": int(len(scenes)), "md_px": int(md.sum()), "labelled_px": int(len(y)),
        "nan_px_total": int(sum(r["nan_px"] for r in res)),
        "sanity_none_vs_lgbm_final_test_val": sanity,
        "variants": out_var, "paired_bootstrap": paired, "offsets": offsets,
        "runtime_s": {"scene_offsets": round(t_off, 1), "predict": round(t_pred, 1), "total": round(time.time() - t0, 1)},
        "threads": THREADS,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    for v in VARIANTS:
        d = out_var[v]
        print(f"{v:10s} TP {d['tp']} FP {d['fp']} FN {d['fn']} P {d['precision_md']:.4f} R {d['recall_md']:.4f} "
              f"F1 {d['f1_md']:.4f} IoU {d['iou_md']:.4f} CI {d['ci95_f1']} best thr {d['threshold_scan']['best_threshold']} "
              f"F1 {d['threshold_scan']['best_f1']} unlab {d['unlabelled_pred_md_rate']:.5f}", flush=True)
    print(json.dumps(paired), flush=True)
    print(f"per-patch offsets: {n_pp}/{len(res)}", flush=True)
    print(f"[done] {time.time() - t0:.0f}s -> {OUT}", flush=True)
    if not sanity["all_equal"]:
        sys.exit("SANITY FAILED: variant none does not reproduce the frozen val numbers")


if __name__ == "__main__":
    main()
