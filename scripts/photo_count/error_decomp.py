"""Decompose the counting error on held-out frames into missed / false / duplicate detections (hypothesis ГХ-1).

Idea after Chagneux et al. 2023 (Computo, CC BY: count precision/recall for litter counting); formulas written here:
  per frame, greedy one-to-one matching (IoU >= 0.5, score order) at the model threshold:
    TP  = matched detections;  missed (FN) = true items without a match;
    duplicate = unmatched detection with IoU >= 0.5 to an ALREADY matched true item (the same object counted twice);
    false = other unmatched detections (background, foam, glint, sand, ...).
  count error = (TP + duplicate + false) - (TP + missed) = duplicate + false - missed.
  CountPR = TP / (TP + duplicate + false), CountRe = TP / (TP + missed).

  python scripts/photo_count/error_decomp.py --tag grouped --split g:test --thr 0.8
Output: reports/photo_count/errors_<tag>.json
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "photo_count"))
from macroplastic.photo_count import metrics as M  # noqa: E402
import eval as E  # noqa: E402


def decompose(pb, ps, g, thr):
    k = ps >= thr
    pb, ps = pb[k], ps[k]
    order = np.argsort(-ps, kind="stable")
    pb = pb[order]
    ious = M.iou_matrix(pb, g)
    used = np.zeros(len(g), bool)
    tp = dup = fal = 0
    for i in range(len(pb)):
        if len(g) == 0:
            fal += 1
            continue
        free = np.where(~used, ious[i], -1.0)
        j = int(np.argmax(free))
        if free[j] >= 0.5:
            used[j] = True
            tp += 1
        elif (ious[i] >= 0.5).any():
            dup += 1
        else:
            fal += 1
    return tp, int((~used).sum()), dup, fal


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--split", required=True)
    ap.add_argument("--thr", type=float, required=True)
    a = ap.parse_args()
    _, preds = E.load_preds(E.fname(a.tag, a.split))
    _, gts = E.split_items(a.split)
    rows = np.array([decompose(p[0], p[1], g, a.thr) for p, g in zip(preds, gts)])
    tp, fn, dup, fal = rows.sum(0)
    n = len(rows)
    err = rows[:, 2] + rows[:, 3] - rows[:, 1]
    out = {"tag": a.tag, "split": a.split, "threshold": a.thr, "n_frames": n, "n_true": int(tp + fn),
           "tp": int(tp), "missed": int(fn), "duplicate": int(dup), "false": int(fal),
           "count_precision": float(tp / max(tp + dup + fal, 1)), "count_recall": float(tp / max(tp + fn, 1)),
           "per_frame_mean": {"missed": float(rows[:, 1].mean()), "duplicate": float(rows[:, 2].mean()),
                              "false": float(rows[:, 3].mean()), "net_error": float(err.mean()),
                              "abs_error": float(np.abs(err).mean())},
           "share_of_abs_error": {}}
    tot = rows[:, 1].sum() + rows[:, 2].sum() + rows[:, 3].sum()
    for name, col in (("missed", 1), ("duplicate", 2), ("false", 3)):
        out["share_of_abs_error"][name] = float(rows[:, col].sum() / max(tot, 1))
    p = ROOT / "reports" / "photo_count" / f"errors_{a.tag}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
