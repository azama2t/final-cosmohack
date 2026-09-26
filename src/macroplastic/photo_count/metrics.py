"""Detection and counting metrics without pycocotools (not installed in .venv).

ap_at_iou: single-class AP at a fixed IoU with greedy score-ordered matching (VOC/COCO style);
interpolation "coco101" (COCO 101-point, what pycocotools reports as AP50) or "voc" (all-point).
"""
import numpy as np


def iou_matrix(a, b):
    """a: (N,4), b: (M,4) xyxy -> (N,M) IoU."""
    a = np.asarray(a, dtype=float).reshape(-1, 4)
    b = np.asarray(b, dtype=float).reshape(-1, 4)
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    return inter / np.maximum(area_a[:, None] + area_b[None, :] - inter, 1e-9)


def match_image(pred_boxes, pred_scores, gt_boxes, iou_thr=0.5):
    """Greedy matching in descending score order. Returns (scores, is_tp) for predictions."""
    pred_boxes = np.asarray(pred_boxes, dtype=float).reshape(-1, 4)
    pred_scores = np.asarray(pred_scores, dtype=float).reshape(-1)
    order = np.argsort(-pred_scores, kind="stable")
    ious = iou_matrix(pred_boxes[order], gt_boxes)
    used = np.zeros(ious.shape[1], dtype=bool)
    tp = np.zeros(len(order), dtype=bool)
    for k in range(len(order)):
        if ious.shape[1] == 0:
            break
        cand = np.where(~used, ious[k], -1.0)
        j = int(np.argmax(cand))
        if cand[j] >= iou_thr:
            used[j] = True
            tp[k] = True
    return pred_scores[order], tp


def match_all(preds, gts, iou_thr=0.5):
    """Per-image (sorted scores, is_tp, n_gt) — compute once, reuse for AP and bootstrap."""
    return [(*match_image(p[0], p[1], g, iou_thr), len(g)) for p, g in zip(preds, gts)]


def ap_from_matched(matched, idx=None, interp="coco101"):
    idx = range(len(matched)) if idx is None else idx
    sel = [matched[i] for i in idx]
    n_gt = sum(m[2] for m in sel)
    if n_gt == 0:
        return float("nan")
    s = np.concatenate([m[0] for m in sel]) if sel else np.zeros(0)
    tp = np.concatenate([m[1] for m in sel]) if sel else np.zeros(0, dtype=bool)
    o = np.argsort(-s, kind="stable")
    tp = tp[o]
    ctp = np.cumsum(tp)
    cfp = np.cumsum(~tp)
    rec = ctp / n_gt
    prec = ctp / np.maximum(ctp + cfp, 1e-9)
    prec = np.maximum.accumulate(prec[::-1])[::-1] if len(prec) else prec  # precision envelope
    if interp == "voc":
        mrec = np.concatenate([[0.0], rec, [1.0]])
        mpre = np.concatenate([[prec[0] if len(prec) else 0.0], prec, [0.0]])
        ch = np.where(mrec[1:] != mrec[:-1])[0]
        return float(np.sum((mrec[ch + 1] - mrec[ch]) * mpre[ch + 1]))
    rs = np.linspace(0, 1, 101)
    pos = np.searchsorted(rec, rs, side="left")
    q = np.where(pos < len(prec), prec[np.minimum(pos, max(len(prec) - 1, 0))] if len(prec) else 0.0, 0.0)
    return float(np.mean(q))


def ap_at_iou(preds, gts, iou_thr=0.5, interp="coco101", idx=None):
    """preds: list of (boxes, scores) per image; gts: list of gt boxes per image; idx: subset of images."""
    return ap_from_matched(match_all(preds, gts, iou_thr), idx, interp)


def counts_at(preds, thr):
    return np.array([int(np.sum(np.asarray(p[1]) >= thr)) for p in preds])


def count_metrics(pred_counts, true_counts):
    pc = np.asarray(pred_counts, dtype=float)
    tc = np.asarray(true_counts, dtype=float)
    err = pc - tc
    return {"mae": float(np.mean(np.abs(err))),
            "bias": float(np.mean(err)),
            "exact": float(np.mean(err == 0)),
            "within1": float(np.mean(np.abs(err) <= 1)),
            "total_pred": int(pc.sum()), "total_true": int(tc.sum())}


def prf_at(preds, gts, thr, iou_thr=0.5, idx=None):
    idx = range(len(gts)) if idx is None else idx
    tp = fp = n_gt = 0
    for i in idx:
        s, m = match_image(preds[i][0], preds[i][1], gts[i], iou_thr)
        keep = s >= thr
        tp += int(m[keep].sum())
        fp += int((~m[keep]).sum())
        n_gt += len(gts[i])
    p = tp / max(tp + fp, 1)
    r = tp / max(n_gt, 1)
    return {"precision": p, "recall": r, "f1": 2 * p * r / max(p + r, 1e-9), "tp": tp, "fp": fp, "n_gt": n_gt}


def bootstrap_ci(stat_fn, n, b=1000, seed=0, alpha=0.05):
    """Percentile bootstrap over photos: stat_fn(idx_array) -> float."""
    rng = np.random.default_rng(seed)
    vals = [stat_fn(rng.integers(0, n, n)) for _ in range(b)]
    vals = np.asarray([v for v in vals if np.isfinite(v)])
    return [float(np.quantile(vals, alpha / 2)), float(np.quantile(vals, 1 - alpha / 2))]
