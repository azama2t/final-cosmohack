"""Measuring counter on frames of KNOWN area (INBOX §23 п.4, §24 п.2–3): items per frame -> items/km^2 on the frame scale.

  python scripts/photo_count/eval_area.py --tag winans --dataset winans     (after eval.py infer --splits w:val w:test)
  python scripts/photo_count/eval_area.py --tag maharjan --dataset maharjan (after eval.py infer --splits m:val m:test)

On VAL only: threshold (min count MAE) and detection probability by object size p(size) = recall per size bin,
precision per size bin -> corrected count per frame = sum over detections of precision(bin)/recall(bin).
On TEST once: per-frame MAE of the count and of the density (items/km^2), raw vs corrected, vs median baseline;
aggregate density over the test area; for georeferenced frames (Winans) with DEDUPLICATION of overlapping chips
(unique items on the ground / union area), otherwise "single frames". CI95 — bootstrap over frames (and over VAL frames
for the p(size) uncertainty of the corrected density).
Output: reports/photo_count/area_<tag>.json
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

BINS_CM = [0, 30, 60, 120, 1e9]
BIN_NAMES = ["< 30 см", "30–60 см", "60–120 см", "≥ 120 см"]


def size_bin(boxes, gsd_m):
    b = np.asarray(boxes, float).reshape(-1, 4)
    side_cm = np.maximum(b[:, 2] - b[:, 0], b[:, 3] - b[:, 1]) * gsd_m * 100
    return np.clip(np.digitize(side_cm, BINS_CM) - 1, 0, len(BIN_NAMES) - 1)


def pd_table(preds, gts, thr, gsd, idx=None):
    """recall and precision per size bin (IoU 0.5) on the given frames."""
    idx = range(len(gts)) if idx is None else idx
    nb = len(BIN_NAMES)
    gt_n, gt_hit, det_n, det_tp = np.zeros(nb), np.zeros(nb), np.zeros(nb), np.zeros(nb)
    for i in idx:
        pb, ps = preds[i]
        keep = ps >= thr
        pb = pb[keep]
        g = gts[i]
        ious = M.iou_matrix(pb, g)
        # greedy one-to-one by IoU
        used_g, used_p = set(), set()
        pairs = sorted(((ious[p, q], p, q) for p in range(ious.shape[0]) for q in range(ious.shape[1]) if ious[p, q] >= 0.5),
                       reverse=True)
        for _, p, q in pairs:
            if p in used_p or q in used_g:
                continue
            used_p.add(p); used_g.add(q)
        gb = size_bin(g, gsd)
        db = size_bin(pb, gsd)
        for q in range(len(g)):
            gt_n[gb[q]] += 1
            gt_hit[gb[q]] += q in used_g
        for p in range(len(pb)):
            det_n[db[p]] += 1
            det_tp[db[p]] += p in used_p
    rec = np.where(gt_n > 0, gt_hit / np.maximum(gt_n, 1), np.nan)
    prec = np.where(det_n > 0, det_tp / np.maximum(det_n, 1), np.nan)
    return {"gt_n": gt_n, "recall": rec, "det_n": det_n, "precision": prec}


def corr_factors(t):
    """precision/recall per bin; bins without data -> overall ratio; clipped to [0.2, 5]."""
    rec, prec = t["recall"], t["precision"]
    overall = (np.nansum(t["det_n"] * np.nan_to_num(prec)) / max(np.nansum(t["det_n"]), 1)) / \
              max(np.nansum(t["gt_n"] * np.nan_to_num(rec)) / max(np.nansum(t["gt_n"]), 1), 1e-6)
    f = np.where(np.isfinite(rec) & np.isfinite(prec) & (rec > 0), prec / np.maximum(rec, 1e-6), overall)
    return np.clip(f, 0.2, 5.0)


def corrected_counts(preds, thr, gsd, f):
    out = []
    for pb, ps in preds:
        pb = pb[ps >= thr]
        out.append(float(f[size_bin(pb, gsd)].sum()) if len(pb) else 0.0)
    return np.array(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--dataset", required=True, choices=["winans", "maharjan"])
    ap.add_argument("--boot", type=int, default=1000)
    a = ap.parse_args()
    pre = {"winans": "w", "maharjan": "m"}[a.dataset]
    gsd = {"winans": 0.02, "maharjan": 2.0 / 256}[a.dataset]
    vf, vp = E.load_preds(E.fname(a.tag, f"{pre}:val"))
    _, vg = E.split_items(f"{pre}:val")
    tf, tp = E.load_preds(E.fname(a.tag, f"{pre}:test"))
    tfiles, tg = E.split_items(f"{pre}:test")
    trf, trg = E.split_items(f"{pre}:train")
    from PIL import Image
    with Image.open(tfiles[0]) as im:
        W_, H_ = im.size
    area_m2 = W_ * H_ * gsd * gsd
    km2 = area_m2 / 1e6
    # ---------------- VAL: threshold + p(size)
    tcv = np.array([len(g) for g in vg])
    grid = np.round(np.arange(0.05, 0.96, 0.05), 2)
    thr = float(min(grid, key=lambda t: (M.count_metrics(M.counts_at(vp, t), tcv)["mae"],
                                         -M.count_metrics(M.counts_at(vp, t), tcv)["exact"])))  # same rule as eval.py
    tab = pd_table(vp, vg, thr, gsd)
    f = corr_factors(tab)
    rng = np.random.default_rng(0)
    f_boot = []
    for _ in range(200):  # p(size) uncertainty from VAL frames
        idx = rng.integers(0, len(vg), len(vg))
        f_boot.append(corr_factors(pd_table(vp, vg, thr, gsd, idx)))
    f_boot = np.array(f_boot)
    # ---------------- TEST once
    tc = np.array([len(g) for g in tg], float)
    pc = M.counts_at(tp, thr).astype(float)
    cc = corrected_counts(tp, thr, gsd, f)
    med = float(np.median([len(g) for g in trg]))
    n = len(tg)

    def per_frame(pred):
        e = pred - tc
        return {"count_mae": float(np.abs(e).mean()), "count_bias": float(e.mean()),
                "count_mae_ci95": M.bootstrap_ci(lambda i: float(np.abs(e[i]).mean()), n, a.boot),
                "density_mae_km2": float(np.abs(e).mean() / km2),
                "density_mae_km2_ci95": [x / km2 for x in M.bootstrap_ci(lambda i: float(np.abs(e[i]).mean()), n, a.boot)],
                "density_bias_km2": float(e.mean() / km2)}
    res = {"tag": a.tag, "dataset": a.dataset, "gsd_m": gsd, "frame_px": [W_, H_], "frame_area_m2": area_m2,
           "rule": "threshold and p(size) from VAL only; TEST scored once; IoU 0.5; all classes = one item",
           "threshold": thr, "n_test_frames": n, "n_test_items": int(tc.sum()),
           "val_detection_probability": {"bins": BIN_NAMES, "gt_n": tab["gt_n"].tolist(),
                                         "recall": [None if not np.isfinite(x) else float(x) for x in tab["recall"]],
                                         "precision": [None if not np.isfinite(x) else float(x) for x in tab["precision"]],
                                         "factor": f.tolist(),
                                         "factor_ci95": np.quantile(f_boot, [0.025, 0.975], axis=0).T.tolist()},
           "test_ap50": M.ap_at_iou(tp, tg),
           "test_raw": per_frame(pc), "test_corrected": per_frame(cc),
           "test_baseline_median": {"median_train_count": med, **per_frame(np.full(n, med))},
           "true_mean_density_km2": float(tc.mean() / km2)}
    # aggregate density over the test area
    agg = {"true_km2": float(tc.sum() / (n * km2)), "raw_km2": float(pc.sum() / (n * km2)),
           "corrected_km2": float(cc.sum() / (n * km2)), "mode": "одиночные кадры (серий перекрытия нет или не георефер.)"}
    cb = np.array([corrected_counts(tp, thr, gsd, fb).sum() / (n * km2) for fb in f_boot])
    agg["corrected_km2_ci95_p_uncert"] = [float(np.quantile(cb, 0.025)), float(np.quantile(cb, 0.975))]
    agg["true_km2_ci95_frames"] = M.bootstrap_ci(lambda i: float(tc[i].mean() / km2), n, a.boot)
    if a.dataset == "winans":
        from macroplastic.photo_count import winans as Wn
        ch = Wn.load_all()
        names = [Path(x).name for x in tfiles]
        fps = [ch[x]["fp"] for x in names]
        U = Wn.union_area(fps) / 1e6
        gt_geo = [Wn.geo_boxes(ch[x], g) for x, g in zip(names, tg)]
        pr_geo = [Wn.geo_boxes(ch[x], p[0][p[1] >= thr]) for x, p in zip(names, tp)]
        ug, up = Wn.dedup_count(gt_geo), Wn.dedup_count(pr_geo)
        ratio = float(cc.sum() / max(pc.sum(), 1e-9))
        agg.update({"mode": "дедупликация перекрывающихся чипов (гео-IoU ≥ 0,3) / площадь объединения",
                    "sum_frame_area_km2": n * km2, "union_area_km2": U,
                    "unique_true_items": ug, "unique_pred_items": up,
                    "true_km2_dedup": ug / U, "raw_km2_dedup": up / U, "corrected_km2_dedup": up * ratio / U,
                    # same relative p(size) uncertainty, rescaled from "sum of frames" to "deduplicated / union area"
                    "corrected_km2_dedup_ci95_p_uncert": [x * (up / U) / max(agg["raw_km2"], 1e-9)
                                                          for x in agg["corrected_km2_ci95_p_uncert"]],
                    "inflation_without_dedup_true": float(tc.sum() / (n * km2)) / (ug / U)})
    res["test_aggregate"] = agg
    out = ROOT / "reports" / "photo_count" / f"area_{a.tag}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    r, c, b = res["test_raw"], res["test_corrected"], res["test_baseline_median"]
    print(f"{a.dataset}: thr {thr} | test {n} frames {int(tc.sum())} items | AP50 {res['test_ap50']:.3f}")
    print(f"  per frame MAE: raw {r['count_mae']:.2f} {r['count_mae_ci95']}, corrected {c['count_mae']:.2f}, median {b['count_mae']:.2f}")
    print(f"  density MAE km^-2: raw {r['density_mae_km2']:.0f}, corrected {c['density_mae_km2']:.0f}, median {b['density_mae_km2']:.0f}")
    print(f"  aggregate km^-2: {json.dumps(agg, ensure_ascii=False)}")
    print(f"  p(size) val: {res['val_detection_probability']}")


if __name__ == "__main__":
    main()
