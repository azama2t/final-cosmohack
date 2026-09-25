#!/usr/bin/env python
"""score.py -- local metric of our predicted masks against organiser masks (their val / a held-out part of train).

    .venv\\Scripts\\python.exe scripts\\tools\\score.py --pred out\\sub_val --gt <their val masks> --target 3
    .venv\\Scripts\\python.exe scripts\\tools\\score.py --pred out\\sub_val --gt <masks> --target 3 --ignore 255 --zero-as ignore

Files are matched by stem (pred .png/.tif vs gt .png/.tif, any mix; a trailing _mask/_label/_gt on either side
is ignored). Metrics, pooled over the pixels of ALL matched files (as "F1 over all pixels of all test images"):
  * target class (--target, value in GT; --pred-target if our code differs, default = same):
    F1, IoU, precision, recall, TP/FP/FN;
  * every class present in GT or prediction: F1 / IoU (one-vs-rest) and macro-F1 / mIoU over GT classes;
  * --ignore values (in GT) are removed from all counts; --zero-as ignore also removes GT 0 (unlabelled);
  * per-file F1 of the target (top files by FN / FP) -> where the errors are.
Also reports missing / extra files and shape mismatches (a submission-format check).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
EXTS = (".png", ".tif", ".tiff", ".npy")
SUF = re.compile(r"([_\-.](mask|masks|label|labels|lbl|gt|cl|pred|prediction))$", re.I)


def _key(p: Path) -> str:
    return SUF.sub("", p.stem).lower()


def _index(d: str) -> dict:
    p = Path(d)
    files = [p] if p.is_file() else [f for f in p.rglob("*") if f.suffix.lower() in EXTS and "_prob" not in f.stem]
    out = {}
    for f in sorted(files):
        out.setdefault(_key(f), f)
    return out


def read_mask(p: Path) -> np.ndarray:
    if p.suffix.lower() == ".npy":
        a = np.load(p)
        return a if a.ndim == 2 else a[0]
    if p.suffix.lower() == ".png":
        from PIL import Image

        a = np.array(Image.open(p))
        return a if a.ndim == 2 else a[..., 0]
    import rasterio

    with rasterio.open(p) as ds:
        return ds.read(1)


def prf(tp, fp, fn):
    return {"f1": round(2 * tp / max(1, 2 * tp + fp + fn), 4) if tp else 0.0,
            "iou": round(tp / max(1, tp + fp + fn), 4) if tp else 0.0,
            "precision": round(tp / max(1, tp + fp), 4), "recall": round(tp / max(1, tp + fn), 4),
            "tp": int(tp), "fp": int(fp), "fn": int(fn)}


def score(pred_dir: str, gt_dir: str, target: int, pred_target: int | None = None, ignore=(), zero_as="negative",
          top: int = 5) -> dict:
    P, G = _index(pred_dir), _index(gt_dir)
    common = sorted(set(P) & set(G))
    missing = sorted(set(G) - set(P))
    extra = sorted(set(P) - set(G))
    pt = target if pred_target is None else pred_target
    ign = set(int(v) for v in ignore) | ({0} if zero_as == "ignore" else set())
    conf = {}  # (gt, pred) -> count
    per_file, bad_shape = [], []
    for k in common:
        g = read_mask(G[k]).astype(np.int64)
        p = read_mask(P[k]).astype(np.int64)
        if g.shape != p.shape:
            bad_shape.append({"file": k, "gt": list(g.shape), "pred": list(p.shape)})
            continue
        v = ~np.isin(g, list(ign)) if ign else np.ones(g.shape, bool)
        gv, pv = g[v], p[v]
        pair = gv * 100000 + pv
        u, c = np.unique(pair, return_counts=True)
        for uu, cc in zip(u.tolist(), c.tolist()):
            key = (uu // 100000, uu % 100000)
            conf[key] = conf.get(key, 0) + cc
        tp = int(((gv == target) & (pv == pt)).sum())
        fp = int(((gv != target) & (pv == pt)).sum())
        fn = int(((gv == target) & (pv != pt)).sum())
        per_file.append({"file": k, **prf(tp, fp, fn)})
    tp = sum(c for (g, p), c in conf.items() if g == target and p == pt)
    fp = sum(c for (g, p), c in conf.items() if g != target and p == pt)
    fn = sum(c for (g, p), c in conf.items() if g == target and p != pt)
    res = {"pred": str(pred_dir), "gt": str(gt_dir), "target": target, "pred_target": pt,
           "ignored_gt_values": sorted(ign), "n_files": len(common), "n_px": int(sum(conf.values())),
           "missing_pred": missing[:50], "n_missing_pred": len(missing), "extra_pred": extra[:50],
           "n_extra_pred": len(extra), "shape_mismatch": bad_shape[:20], "target_metrics": prf(tp, fp, fn)}
    gcls = sorted({g for g, _ in conf})
    pcls = sorted({p for _, p in conf})
    per_cls = {}
    for c in sorted(set(gcls) | set(pcls)):
        t = sum(n for (g, p), n in conf.items() if g == c and p == c)
        f_p = sum(n for (g, p), n in conf.items() if g != c and p == c)
        f_n = sum(n for (g, p), n in conf.items() if g == c and p != c)
        per_cls[int(c)] = {**prf(t, f_p, f_n), "gt_px": int(t + f_n), "pred_px": int(t + f_p)}
    res["per_class"] = per_cls
    res["macro_f1_gt_classes"] = round(float(np.mean([per_cls[c]["f1"] for c in gcls])), 4) if gcls else None
    res["miou_gt_classes"] = round(float(np.mean([per_cls[c]["iou"] for c in gcls])), 4) if gcls else None
    res["worst_files_fn"] = sorted(per_file, key=lambda r: -r["fn"])[:top]
    res["worst_files_fp"] = sorted(per_file, key=lambda r: -r["fp"])[:top]
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", required=True, help="folder (or file) with our masks")
    ap.add_argument("--gt", required=True, help="folder (or file) with organiser masks")
    ap.add_argument("--target", type=int, required=True, help="debris value in the GT masks")
    ap.add_argument("--pred-target", type=int, default=None, help="debris value in our masks (default = --target)")
    ap.add_argument("--ignore", type=int, nargs="*", default=[], help="GT values excluded from all counts (e.g. 255)")
    ap.add_argument("--zero-as", choices=["negative", "ignore"], default="negative",
                    help="GT 0: negative (metric over ALL pixels, default) or ignore (unlabelled)")
    ap.add_argument("--json", default=None, help="write the full result here")
    a = ap.parse_args(argv)
    for st in (sys.stdout, sys.stderr):
        try:
            st.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    r = score(a.pred, a.gt, a.target, a.pred_target, a.ignore, a.zero_as)
    t = r["target_metrics"]
    print(f"[score] files {r['n_files']} (missing pred {r['n_missing_pred']}, extra {r['n_extra_pred']}, shape "
          f"mismatch {len(r['shape_mismatch'])}), px {r['n_px']}, ignored GT {r['ignored_gt_values']}")
    print(f"[score] target {r['target']}: F1 {t['f1']:.4f}  IoU {t['iou']:.4f}  P {t['precision']:.4f}  "
          f"R {t['recall']:.4f}  (TP {t['tp']}, FP {t['fp']}, FN {t['fn']})")
    print(f"[score] macro-F1 over GT classes {r['macro_f1_gt_classes']}, mIoU {r['miou_gt_classes']}; per class: "
          + ", ".join(f"{c}: F1 {v['f1']}" for c, v in r["per_class"].items()))
    if r["n_missing_pred"]:
        print(f"[score] ВНИМАНИЕ: нет предсказаний для {r['n_missing_pred']} файлов GT, например {r['missing_pred'][:5]}")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0 if r["n_files"] else 2


if __name__ == "__main__":
    sys.exit(main())
