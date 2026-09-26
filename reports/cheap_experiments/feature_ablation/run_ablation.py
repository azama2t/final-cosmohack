"""§64 / L159 cheap experiment 01: feature ablation of the current weights/lgbm feature set.

Uses the EXACT training path of scripts/train_lgbm_mados.py (data=combined: MARIDA train + MADOS
train/val, same sampling/hyperparameters as weights/lgbm/meta.json config), evaluated on the
OFFICIAL MARIDA val split only (MARIDA test is never read). Only the feature subset changes.

Safety:
- weights/lgbm and any production path is never written to.
- MARIDA test split is never loaded (scripts/train_lgbm.py.ALLOWED_SPLITS = train, val only).
- All outputs go to reports/cheap_experiments/feature_ablation/.
- CPU-only (CUDA_VISIBLE_DEVICES=-1), n_jobs capped at 4, this process runs at BelowNormal priority.

Run: .venv/Scripts/python.exe reports/cheap_experiments/feature_ablation/run_ablation.py
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import time
from pathlib import Path

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["OMP_NUM_THREADS"] = "4"

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

OUT = Path(__file__).resolve().parent
N_JOBS = 4
SEEDS = [0, 1, 2]
MIN_FREE_GB = 20
PROD_THR = 0.63

# ---- low priority for this process (best-effort; do not fail the run if psutil is missing) ----
try:
    import psutil

    p = psutil.Process()
    p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS") else 10)
except Exception as e:  # noqa: BLE001
    print(f"[warn] could not lower process priority: {e}")


def free_gb() -> float:
    return shutil.disk_usage(str(ROOT)[:3] + os.sep).free / 2**30


def check_disk(tag: str):
    g = free_gb()
    print(f"[disk] {tag}: {g:.1f} GB free", flush=True)
    if g < MIN_FREE_GB:
        raise SystemExit(f"STOP: free disk {g:.1f} GB < {MIN_FREE_GB} GB ({tag})")


check_disk("start")

import numpy as np  # noqa: E402
import yaml  # noqa: E402

import train_lgbm as TL  # noqa: E402
import train_lgbm_mados as TLM  # noqa: E402
from macroplastic.features.pixel import feature_names, BANDS11, INDICES, WIN_KEYS, WIN_SIZES, CONTRAST  # noqa: E402

# cap n_jobs project-wide for this script (both MARIDA and MADOS cache builders)
TL.n_threads = lambda: N_JOBS
TLM.MAX_THREADS = N_JOBS

ALL_NAMES = feature_names("win")
assert len(ALL_NAMES) == 48, f"unexpected feature count {len(ALL_NAMES)}: repo feature set changed?"


def group_names(idx: str) -> list[str]:
    """All feature names that depend on spectral index `idx` (its own column + derived window/contrast)."""
    out = [idx] if idx in ALL_NAMES else []
    for k in WIN_KEYS:
        if k == idx:
            for w in WIN_SIZES:
                out += [f"{k}_mean{w}", f"{k}_std{w}"]
    for k, w in CONTRAST:
        if k == idx:
            out.append(f"{k}_dmed{w}")
    return out


WINDOW_DERIVED = []
for k in WIN_KEYS:
    for w in WIN_SIZES:
        WINDOW_DERIVED += [f"{k}_mean{w}", f"{k}_std{w}"]
WINDOW_DERIVED += [f"{k}_dmed{w}" for k, w in CONTRAST]

VARIANTS = {
    "A_RAW_ONLY": list(BANDS11),
    "B_RAW_PLUS_INDICES": list(BANDS11) + list(INDICES),
    "C_RAW_PLUS_WINDOWS": list(BANDS11) + WINDOW_DERIVED,
    "D_FULL": list(ALL_NAMES),
}
for idx in INDICES:
    grp = group_names(idx)
    VARIANTS[f"E_MINUS_{idx}"] = [n for n in ALL_NAMES if n not in grp]

# sanity: log group sizes removed
REMOVED_SIZES = {f"E_MINUS_{idx}": len(group_names(idx)) for idx in INDICES}
print("[variants] removed-group sizes:", REMOVED_SIZES, flush=True)

RESULTS_CSV = OUT / "results.csv"
RESULTS_JSON = OUT / "results.json"
RUN_CMDS = OUT / "run_commands.txt"
FIELDS = ["variant", "seed", "n_features", "removed_features", "threshold", "precision", "recall", "f1", "iou",
          "tp", "fp", "fn", "precision_thr063", "recall_thr063", "f1_thr063", "iou_thr063",
          "tp_thr063", "fp_thr063", "fn_thr063", "train_px", "train_md_px"]


def md_metrics_at(true_md, prob, thr):
    return TL.md_metrics(true_md, prob >= thr)


def main():
    t_start = time.time()
    RUN_CMDS.write_text(
        ".venv/Scripts/python.exe reports/cheap_experiments/feature_ablation/run_ablation.py\n"
        f"n_jobs={N_JOBS} seeds={SEEDS} data=combined (MARIDA train + MADOS train/val, same as weights/lgbm)\n"
        "config base: configs/lgbm.yaml + overrides matching weights/lgbm/meta.json.config\n",
        encoding="utf-8",
    )

    cfg = yaml.safe_load((ROOT / "configs" / "lgbm.yaml").read_text(encoding="utf-8"))
    # match weights/lgbm/meta.json config exactly (production training recipe)
    cfg.update({
        "mados_splits": ["train", "val"],
        "mados_extra": False,
        "mados_weight": 1.0,
        "exclude_marida_train_overlap": False,
        "exclude_same_place_val": True,
    })

    print("[cache] loading MARIDA val (should be cached already)...", flush=True)
    Xva, yva, cva, _ = TL.load_split("val")
    check_disk("after val cache")
    true_md = yva == 1

    print("[cache] building MARIDA train features (one-time, no S2 redownload, local MARIDA patches)...", flush=True)
    Xtr, ytr, ctr, _ = TL.load_split("train")
    check_disk("after marida train cache")

    print("[cache] building MADOS train/val features (one-time, no S2 redownload, local MADOS patches)...",
          flush=True)
    TLM.load_mados("train")
    check_disk("after mados train cache")
    TLM.load_mados("val")
    check_disk("after mados val cache")

    rows = []
    t_last_check = time.time()
    for variant, fnames in VARIANTS.items():
        fidx = [ALL_NAMES.index(n) for n in fnames]
        removed = [n for n in ALL_NAMES if n not in fnames]
        for seed in SEEDS:
            if time.time() - t_last_check > 600:
                check_disk("periodic (10 min)")
                t_last_check = time.time()
            np.random.seed(seed)
            sel = TL.sample_train(ytr, cfg, seed)
            Xm_parts = [Xtr[sel]]
            ym_parts = [ytr[sel]]
            cm_parts = [ctr[sel]]
            sw_parts = [np.ones(len(sel), np.float32)]
            src_info = {"marida": {"n_px": int(len(sel)), "n_md": int(np.sum(ytr[sel] == 1))}}
            Xmd, ymd, cmd, minfo = TLM.mados_pixels(cfg, seed)
            Xm_parts.append(Xmd)
            ym_parts.append(ymd)
            cm_parts.append(cmd)
            sw_parts.append(np.full(len(ymd), float(cfg["mados_weight"]), np.float32))
            src_info["mados"] = {"n_px": int(len(ymd)), "n_md": int(np.sum(ymd == 1)), **minfo}

            X = np.concatenate(Xm_parts)
            y = np.concatenate(ym_parts)
            c = np.concatenate(cm_parts)
            sw = np.concatenate(sw_parts)

            t0 = time.time()
            booster = TLM.train_weighted(X, y, c, sw, cfg, seed, fidx)
            t_train = time.time() - t0

            pv = TL.prob_md(booster, Xva[:, fidx], "binary", [0, 1], nthreads=N_JOBS)
            pv[np.isnan(Xva[:, :11]).any(1)] = 0.0
            thr, met, _curve = TL.best_threshold(true_md, pv, cfg["threshold_grid"])
            met_fixed = md_metrics_at(true_md, pv, PROD_THR)

            row = {
                "variant": variant, "seed": seed, "n_features": len(fnames),
                "removed_features": ";".join(removed) if removed else "",
                "threshold": round(thr, 3),
                "precision": round(met["precision"], 4), "recall": round(met["recall"], 4),
                "f1": round(met["f1"], 4), "iou": round(met["iou"], 4),
                "tp": met["tp"], "fp": met["fp"], "fn": met["fn"],
                "precision_thr063": round(met_fixed["precision"], 4), "recall_thr063": round(met_fixed["recall"], 4),
                "f1_thr063": round(met_fixed["f1"], 4), "iou_thr063": round(met_fixed["iou"], 4),
                "tp_thr063": met_fixed["tp"], "fp_thr063": met_fixed["fp"], "fn_thr063": met_fixed["fn"],
                "train_px": int(len(y)), "train_md_px": int(np.sum(y == 1)),
            }
            rows.append(row)
            print(f"[{variant} s{seed}] nfeat={len(fnames)} F1={met['f1']:.4f} thr={thr:.2f} "
                  f"F1@0.63={met_fixed['f1']:.4f} train={t_train:.1f}s", flush=True)

    with open(RESULTS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    RESULTS_JSON.write_text(json.dumps({
        "meta": {
            "data": "combined (MARIDA train + MADOS train/val)", "seeds": SEEDS, "n_jobs": N_JOBS,
            "hyperparams": cfg["lgbm"], "sampling": cfg["sampling"], "pos_weight": cfg["pos_weight"],
            "threshold_grid": cfg["threshold_grid"], "fixed_threshold": PROD_THR,
            "n_marida_val_px": int(len(yva)), "n_marida_val_md": int(true_md.sum()),
            "elapsed_s": round(time.time() - t_start, 1),
        },
        "variants": {v: fn for v, fn in VARIANTS.items()},
        "rows": rows,
    }, indent=1), encoding="utf-8")

    # ---- terminal summary ----
    by_variant = {}
    for r in rows:
        by_variant.setdefault(r["variant"], []).append(r)
    print("\n=== summary (mean over seeds, best-threshold-on-val) ===")
    print(f"{'variant':22s} {'nfeat':>5s} {'F1':>7s} {'P':>7s} {'R':>7s} {'IoU':>7s} {'F1@0.63':>8s}")
    full_f1 = np.mean([r["f1"] for r in by_variant["D_FULL"]])
    best_variant, best_f1 = None, -1
    for v, rs in by_variant.items():
        f1s = [r["f1"] for r in rs]
        m_f1, sd_f1 = float(np.mean(f1s)), float(np.std(f1s))
        m_p = float(np.mean([r["precision"] for r in rs]))
        m_r = float(np.mean([r["recall"] for r in rs]))
        m_iou = float(np.mean([r["iou"] for r in rs]))
        m_f1_063 = float(np.mean([r["f1_thr063"] for r in rs]))
        print(f"{v:22s} {rs[0]['n_features']:5d} {m_f1:.4f}\xb1{sd_f1:.3f} {m_p:6.4f} {m_r:6.4f} {m_iou:6.4f} "
              f"{m_f1_063:7.4f}")
        if m_f1 > best_f1:
            best_f1, best_variant = m_f1, v
    print(f"\nbest by MARIDA val F1: {best_variant} ({best_f1:.4f} vs FULL {full_f1:.4f}, "
          f"delta={best_f1 - full_f1:+.4f})")
    print(f"REPORT.md -> {OUT / 'REPORT.md'}")
    print(f"elapsed: {time.time() - t_start:.0f}s")


if __name__ == "__main__":
    main()
