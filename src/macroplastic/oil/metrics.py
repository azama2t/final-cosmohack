"""Пиксельные метрики класса «нефть» и бутстреп по сценам (как scripts/baselines.py: 2000 повторов, rng 0)."""
from __future__ import annotations

import numpy as np


def binary_metrics(tp: float, fp: float, fn: float) -> dict:
    """P/R/F1/IoU из счётчиков; пустой знаменатель -> 0.0 (не NaN), чтобы бутстреп не ломался."""
    tp, fp, fn = float(tp), float(fp), float(fn)
    p = tp / (tp + fp) if tp + fp > 0 else 0.0
    r = tp / (tp + fn) if tp + fn > 0 else 0.0
    f1 = 2 * p * r / (p + r) if p + r > 0 else 0.0
    iou = tp / (tp + fp + fn) if tp + fp + fn > 0 else 0.0
    return {"precision": p, "recall": r, "f1": f1, "iou": iou, "tp": int(tp), "fp": int(fp), "fn": int(fn)}


def scene_counts(true: np.ndarray, pred: np.ndarray, scene: np.ndarray) -> dict:
    """{scene: (tp, fp, fn, n_true)} по меченым пикселям (true/pred — bool, scene — ключ сцены на пиксель)."""
    true = np.asarray(true, bool)
    pred = np.asarray(pred, bool)
    scene = np.asarray(scene)
    out = {}
    for s in np.unique(scene):
        m = scene == s
        t, p = true[m], pred[m]
        out[str(s)] = (int(np.sum(t & p)), int(np.sum(~t & p)), int(np.sum(t & ~p)), int(t.sum()))
    return out


def pooled_from_counts(counts: dict) -> dict:
    a = np.array(list(counts.values()), np.int64).reshape(-1, 4)
    return binary_metrics(a[:, 0].sum(), a[:, 1].sum(), a[:, 2].sum())


def bootstrap_ci(counts: dict, reps: int = 2000, seed: int = 0, alpha: float = 0.05) -> dict:
    """ДИ 95 % для P/R/F1/IoU: повторная выборка СЦЕН с возвращением, метрики по сумме счётчиков."""
    a = np.array(list(counts.values()), np.int64).reshape(-1, 4)
    n = len(a)
    if n == 0:
        return {k: [0.0, 0.0] for k in ("precision", "recall", "f1", "iou")}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, n, size=(reps, n))
    s = a[idx].sum(1).astype(np.float64)  # (reps, 4)
    tp, fp, fn = s[:, 0], s[:, 1], s[:, 2]
    with np.errstate(invalid="ignore", divide="ignore"):
        p = np.where(tp + fp > 0, tp / (tp + fp), 0.0)
        r = np.where(tp + fn > 0, tp / (tp + fn), 0.0)
        f1 = np.where(p + r > 0, 2 * p * r / (p + r), 0.0)
        iou = np.where(tp + fp + fn > 0, tp / (tp + fp + fn), 0.0)
    q = [100 * alpha / 2, 100 * (1 - alpha / 2)]
    return {k: [float(v) for v in np.percentile(x, q)] for k, x in
            (("precision", p), ("recall", r), ("f1", f1), ("iou", iou))}


def best_threshold(true: np.ndarray, score: np.ndarray, grid) -> tuple[float, dict]:
    """Порог по максимуму пулового F1 (выбор только на val)."""
    true = np.asarray(true, bool)
    best = None
    for t in grid:
        pr = score >= t
        m = binary_metrics(np.sum(true & pr), np.sum(~true & pr), np.sum(true & ~pr))
        if best is None or m["f1"] > best[1]["f1"]:
            best = (float(t), m)
    return best
