"""L35: why does weights/lgbm "as is" score private F1 0.058 on the rehearsal org dataset (MADOS test)?

For every org TEST chip (out/rehearsal/private_key.json) computes P(debris) three ways:
  native : mados.load_patch (float rhorc, MARIDA band order)            -- what the model was trained on
  quant  : native -> org packing (NaN->0, x1e4, rint, clip 0..65535) -> /1e4, NO nodata masking
  chain  : the org GeoTIFF read through the real adapter.yaml (data/ingest/org_l32) = what predict_org sees
and compares arrays (max|dP|, max|dX| per band, negatives, NaN), then scores against the hidden masks
(org codes 0/1/2/3, 3 = debris): pooled over ALL pixels (organiser metric, 0 = negative) and over labelled
pixels only (MARIDA-style). Per-scene and per-GT-class breakdown of FP. Thresholds: meta (0.63) + grid.

Usage: .venv\\Scripts\\python.exe scripts\\experiments\\l35_asis_check.py [--weights weights\\lgbm] [--out reports\\l35_asis_check.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
if not os.environ.get("CUDA_VISIBLE_DEVICES"):
    os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from macroplastic.data import mados  # noqa: E402
from macroplastic.features.pixel import BANDS11  # noqa: E402
from macroplastic.ingest.formats import test_cfg  # noqa: E402
from macroplastic.models.lgbm_predict import load_predictor  # noqa: E402
from macroplastic.organizer_adapter import list_samples, load_config  # noqa: E402
from macroplastic.organizer_adapter.adapter import load_image  # noqa: E402

THR_GRID = [0.5, 0.63, 0.8, 0.9, 0.95, 0.97, 0.98, 0.99, 0.995, 0.998, 0.999]


def prf(tp, fp, fn):
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 0.0
    return {"f1": round(f1, 4), "p": round(tp / (tp + fp), 4) if tp + fp else 0.0,
            "r": round(tp / (tp + fn), 4) if tp + fn else 0.0, "tp": int(tp), "fp": int(fp), "fn": int(fn)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=str(ROOT / "weights" / "lgbm"))
    ap.add_argument("--config", default=str(ROOT / "data" / "ingest" / "org_l32" / "adapter.yaml"))
    ap.add_argument("--key", default=str(ROOT / "out" / "rehearsal" / "private_key.json"))
    ap.add_argument("--gt", default=str(ROOT / "out" / "rehearsal" / "hidden_test_masks"))
    ap.add_argument("--variants", default="native,quant,chain")
    ap.add_argument("--out", default=str(ROOT / "reports" / "l35_asis_check.json"))
    a = ap.parse_args()

    pred = load_predictor(a.weights, device="cpu")
    thr0 = float(pred.threshold)
    grid = sorted(set(THR_GRID + [thr0]))
    key = json.loads(Path(a.key).read_text(encoding="utf-8"))
    items = [it for it in key["items"] if it["split"] == "test"]
    cfg = test_cfg(load_config(a.config), None)
    samples = {Path(s.image_paths[0]).stem: s for s in list_samples(cfg)}
    variants = a.variants.split(",")

    # accumulators: variant -> scope -> thr -> [tp, fp, fn]
    acc = {v: {sc: {t: np.zeros(3, np.int64) for t in grid} for sc in ("all", "lab")} for v in variants}
    fp_by_cls = {v: np.zeros(4, np.int64) for v in variants}  # at thr0, FP by org GT code 0/1/2
    per_scene = defaultdict(lambda: {v: np.zeros(3, np.int64) for v in variants})  # at thr0 (all px)
    per_scene_px = defaultdict(lambda: np.zeros(4, np.int64))
    cmp = {"max_dP_native_chain": 0.0, "max_dP_quant_chain": 0.0, "n_px_dP_gt_0.1_native_chain": 0,
           "max_dX_native_chain": np.zeros(11), "max_dX_quant_chain": np.zeros(11),
           "neg_px_native": np.zeros(11, np.int64), "nan_px_native": np.zeros(11, np.int64),
           "nan_px_chain": np.zeros(11, np.int64), "px_any_nan_chain": 0, "px_any_nan_native": 0,
           "debris_px_neg_any_band": 0, "n_px": 0}
    prob_hist = {v: np.zeros(20, np.int64) for v in variants}  # P histogram over unlabelled px

    for k, it in enumerate(items):
        img, _, _, info = mados.load_patch(it["mados"], extra=True)
        gt = np.array(Image.open(Path(a.gt) / f"{it['id']}.png"))
        g = gt == 3
        lab = gt != 0
        scene = it["mados"].rsplit("_", 1)[0]
        per_scene_px[scene] += np.bincount(gt.ravel(), minlength=4)[:4]
        arrs = {}
        if "native" in variants:
            arrs["native"] = img
        if "quant" in variants:
            dn = np.clip(np.rint(np.nan_to_num(img, nan=0.0) * 10000.0), 0, 65535)
            arrs["quant"] = (dn / 10000.0).astype(np.float32)
        if "chain" in variants:
            x, names, _ = load_image(cfg, samples[it["id"]])
            assert list(names) == list(BANDS11), names
            arrs["chain"] = x
        probs = {v: pred.predict_proba(arrs[v], list(BANDS11)) for v in variants}
        # array comparisons
        cmp["n_px"] += g.size
        cmp["neg_px_native"] += (img < 0).reshape(11, -1).sum(1)
        cmp["nan_px_native"] += (~np.isfinite(img)).reshape(11, -1).sum(1)
        cmp["px_any_nan_native"] += int((~np.isfinite(img)).any(0).sum())
        cmp["debris_px_neg_any_band"] += int(((img < 0).any(0) & g).sum())
        if "chain" in arrs:
            c = arrs["chain"]
            cmp["nan_px_chain"] += (~np.isfinite(c)).reshape(11, -1).sum(1)
            cmp["px_any_nan_chain"] += int((~np.isfinite(c)).any(0).sum())
            ok = np.isfinite(c) & np.isfinite(img)
            d = np.where(ok, np.abs(c - img), 0).reshape(11, -1).max(1)
            cmp["max_dX_native_chain"] = np.maximum(cmp["max_dX_native_chain"], d)
            if "native" in probs:
                dp = np.abs(probs["chain"] - probs["native"])
                cmp["max_dP_native_chain"] = max(cmp["max_dP_native_chain"], float(dp.max()))
                cmp["n_px_dP_gt_0.1_native_chain"] += int((dp > 0.1).sum())
            if "quant" in arrs:
                q = arrs["quant"]
                ok = np.isfinite(c)
                d = np.where(ok, np.abs(c - q), 0).reshape(11, -1).max(1)
                cmp["max_dX_quant_chain"] = np.maximum(cmp["max_dX_quant_chain"], d)
                cmp["max_dP_quant_chain"] = max(cmp["max_dP_quant_chain"],
                                                float(np.abs(probs["chain"] - probs["quant"]).max()))
        for v, p in probs.items():
            for t in grid:
                m = p >= t
                acc[v]["all"][t] += [(g & m).sum(), (~g & m).sum(), (g & ~m).sum()]
                acc[v]["lab"][t] += [(g & m & lab).sum(), (~g & m & lab).sum(), (g & ~m & lab).sum()]
            m = p >= thr0
            fp_by_cls[v] += np.bincount(gt[m & ~g].ravel(), minlength=4)[:4]
            per_scene[scene][v] += [(g & m).sum(), (~g & m).sum(), (g & ~m).sum()]
            prob_hist[v] += np.histogram(p[~lab], bins=20, range=(0, 1))[0]
        if k % 20 == 0:
            print(f"{k + 1}/{len(items)} {it['id']} {it['mados']}", flush=True)

    res = {"weights": a.weights, "threshold_meta": thr0, "n_chips": len(items), "variants": variants,
           "gt_px_0123": np.bincount(np.zeros(1, int), minlength=1).tolist()}
    tot = sum(per_scene_px.values())
    res["gt_px_0123"] = tot.tolist()
    res["metrics"] = {v: {sc: {str(t): prf(*acc[v][sc][t]) for t in grid} for sc in ("all", "lab")}
                      for v in variants}
    res["best"] = {v: {sc: max(((t, prf(*acc[v][sc][t])) for t in grid), key=lambda z: z[1]["f1"])
                       for sc in ("all", "lab")} for v in variants}
    res["fp_by_gt_class_at_meta_thr"] = {v: dict(zip(["0_unlabelled", "1_water", "2_other", "3"],
                                                     fp_by_cls[v].tolist())) for v in variants}
    res["per_scene_at_meta_thr"] = {s: {"gt_px_0123": per_scene_px[s].tolist(),
                                        **{v: prf(*per_scene[s][v]) for v in variants}} for s in sorted(per_scene)}
    res["prob_hist_unlabelled_px"] = {v: h.tolist() for v, h in prob_hist.items()}
    res["compare"] = {k: (v.round(6).tolist() if isinstance(v, np.ndarray) else v) for k, v in cmp.items()}
    res["bands"] = list(BANDS11)
    Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    for v in variants:
        m, l = res["metrics"][v]["all"][str(thr0)], res["metrics"][v]["lab"][str(thr0)]
        print(f"{v:7s} thr {thr0}: all-px F1 {m['f1']} (tp {m['tp']} fp {m['fp']} fn {m['fn']}) | labelled F1 {l['f1']} "
              f"(fp {l['fp']}) | best all {res['best'][v]['all'][0]}:{res['best'][v]['all'][1]['f1']} "
              f"best lab {res['best'][v]['lab'][0]}:{res['best'][v]['lab'][1]['f1']}")
    print(json.dumps(res["compare"]))
    print(json.dumps(res["fp_by_gt_class_at_meta_thr"]))
    for s, d in res["per_scene_at_meta_thr"].items():
        print(s, d["gt_px_0123"], {v: (d[v]["tp"], d[v]["fp"], d[v]["fn"]) for v in variants})
    return 0


if __name__ == "__main__":
    sys.exit(main())
