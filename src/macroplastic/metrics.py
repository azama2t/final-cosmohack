"""Segmentation metrics pooled over all pixels of all patches (micro), ignore label 0.

    from macroplastic.metrics import ConfusionAccumulator, binary_scores
    acc = ConfusionAccumulator(n_classes=16)          # classes 0..15, 0 = unlabeled (ignored)
    for pred, gt in pairs: acc.update(pred, gt)       # (H,W) int arrays
    res = acc.compute()     # {'per_class': {k: {precision, recall, f1, iou, support}}, 'miou', 'md': {...}}

    binary_scores(prob >= t, gt)   # F1/IoU of Marine Debris (class 1) vs all other labeled pixels

Conventions:
* gt == ignore_index (0) pixels are excluded everywhere (MARIDA labels are partial).
* Empty denominator (0/0) -> NaN. mIoU = nanmean of IoU over classes != ignore_index that occur
  in gt or pred (so absent classes do not count). F1 = 2TP/(2TP+FP+FN), IoU = TP/(TP+FP+FN).
* Binary Marine Debris: positive gt = class 1; negative gt = any other labeled class (2..15);
  positive prediction = pred == 1 (multiclass) or True (bool mask).
"""
from __future__ import annotations

from typing import Iterable

import numpy as np

from . import CLASS_NAMES, IGNORE_INDEX, MARINE_DEBRIS


def _div(num: float, den: float) -> float:
    return float(num) / float(den) if den else float("nan")


def confusion_matrix(pred: np.ndarray, gt: np.ndarray, n_classes: int = 16,
                     ignore_index: int | None = IGNORE_INDEX) -> np.ndarray:
    """(n_classes, n_classes) int64 matrix, rows = gt, cols = pred. Pixels with gt == ignore_index
    or gt/pred outside [0, n_classes) are dropped."""
    p = np.asarray(pred).ravel().astype(np.int64)
    g = np.asarray(gt).ravel().astype(np.int64)
    if p.shape != g.shape:
        raise ValueError(f"shape mismatch pred {np.shape(pred)} vs gt {np.shape(gt)}")
    valid = (g >= 0) & (g < n_classes) & (p >= 0) & (p < n_classes)
    if ignore_index is not None:
        valid &= g != ignore_index
    return np.bincount(g[valid] * n_classes + p[valid], minlength=n_classes * n_classes).reshape(n_classes, n_classes)


def scores_from_confusion(cm: np.ndarray, ignore_index: int | None = IGNORE_INDEX,
                          class_names: dict[int, str] | None = None) -> dict:
    """Per-class precision/recall/F1/IoU, mIoU, macro-F1, overall accuracy from a confusion matrix."""
    cm = np.asarray(cm, dtype=np.int64)
    n = cm.shape[0]
    names = class_names if class_names is not None else CLASS_NAMES
    per = {}
    ious, f1s = [], []
    for k in range(n):
        if ignore_index is not None and k == ignore_index:
            continue
        tp = int(cm[k, k])
        fp = int(cm[:, k].sum() - tp)
        fn = int(cm[k, :].sum() - tp)
        r = {"name": names.get(k, str(k)), "tp": tp, "fp": fp, "fn": fn, "support": tp + fn,
             "precision": _div(tp, tp + fp), "recall": _div(tp, tp + fn),
             "f1": _div(2 * tp, 2 * tp + fp + fn), "iou": _div(tp, tp + fp + fn)}
        per[k] = r
        if tp + fp + fn > 0:
            ious.append(r["iou"])
            f1s.append(r["f1"])
    total = int(cm.sum())
    return {
        "per_class": per,
        "miou": float(np.mean(ious)) if ious else float("nan"),
        "macro_f1": float(np.mean(f1s)) if f1s else float("nan"),
        "accuracy": _div(int(np.trace(cm)), total),
        "n_pixels": total,
    }


def binary_from_counts(tp: int, fp: int, fn: int, tn: int = 0) -> dict:
    """Precision/recall/F1/IoU from binary counts (0/0 -> NaN)."""
    return {"tp": int(tp), "fp": int(fp), "fn": int(fn), "tn": int(tn),
            "precision": _div(tp, tp + fp), "recall": _div(tp, tp + fn),
            "f1": _div(2 * tp, 2 * tp + fp + fn), "iou": _div(tp, tp + fp + fn)}


def _binary_counts(pred, gt, positive: int, ignore_index: int | None) -> tuple[int, int, int, int]:
    p = np.asarray(pred)
    g = np.asarray(gt)
    if p.shape != g.shape:
        raise ValueError(f"shape mismatch pred {p.shape} vs gt {g.shape}")
    pp = p.astype(bool) if p.dtype == bool else (p == positive)
    valid = np.ones(g.shape, bool) if ignore_index is None else (g != ignore_index)
    gp = (g == positive) & valid
    gn = valid & ~gp
    tp = int(np.count_nonzero(pp & gp))
    fp = int(np.count_nonzero(pp & gn))
    fn = int(np.count_nonzero(~pp & gp))
    tn = int(np.count_nonzero(~pp & gn))
    return tp, fp, fn, tn


def binary_scores(pred, gt, positive: int = MARINE_DEBRIS, ignore_index: int | None = IGNORE_INDEX) -> dict:
    """Binary P/R/F1/IoU of class `positive` (default 1 Marine Debris) for one array or a list of
    arrays (pooled). `pred` is a bool mask or a class map (== positive)."""
    if isinstance(pred, (list, tuple)):
        tot = np.zeros(4, np.int64)
        if len(pred) != len(gt):
            raise ValueError("pred and gt lists differ in length")
        for p, g in zip(pred, gt):
            tot += _binary_counts(p, g, positive, ignore_index)
        return binary_from_counts(*tot)
    return binary_from_counts(*_binary_counts(pred, gt, positive, ignore_index))


class ConfusionAccumulator:
    """Accumulates a multiclass confusion matrix and binary Marine Debris counts over many patches.

    update(pred, gt[, md_pred]): pred is an (H,W) class map; md_pred (optional bool (H,W)) is the
    binary MD decision (e.g. prob >= t); if omitted, md_pred = (pred == 1).
    """

    def __init__(self, n_classes: int = 16, ignore_index: int | None = IGNORE_INDEX, positive: int = MARINE_DEBRIS):
        self.n_classes = n_classes
        self.ignore_index = ignore_index
        self.positive = positive
        self.cm = np.zeros((n_classes, n_classes), np.int64)
        self.md = np.zeros(4, np.int64)  # tp, fp, fn, tn

    def update(self, pred: np.ndarray, gt: np.ndarray, md_pred: np.ndarray | None = None) -> None:
        self.cm += confusion_matrix(pred, gt, self.n_classes, self.ignore_index)
        mp = (np.asarray(pred) == self.positive) if md_pred is None else np.asarray(md_pred).astype(bool)
        self.md += _binary_counts(mp, gt, self.positive, self.ignore_index)

    def update_many(self, pairs: Iterable[tuple[np.ndarray, np.ndarray]]) -> None:
        for p, g in pairs:
            self.update(p, g)

    def compute(self) -> dict:
        res = scores_from_confusion(self.cm, self.ignore_index)
        res["md"] = binary_from_counts(*self.md)
        res["confusion"] = self.cm.tolist()
        return res


def summary_line(res: dict) -> str:
    """One-line text: 'MD F1 0.812 IoU 0.684 P .. R .. | mIoU 0.71'."""
    md = res["md"]
    return (f"MD F1 {md['f1']:.4f} IoU {md['iou']:.4f} P {md['precision']:.4f} R {md['recall']:.4f} "
            f"(tp {md['tp']} fp {md['fp']} fn {md['fn']}) | mIoU {res['miou']:.4f}")
