"""Photo counter evaluation on FML.

Step 1 (GPU, via scripts/gpu_queue.py) — raw detections (score >= 0.05) cached per split:
  python scripts/photo_count/eval.py infer --weights data/extra/fml/published/fasterrcnn_fml.pth --tag published --splits val test
Step 2 (CPU) — threshold chosen on VAL only (min count MAE), then the held-out TEST is scored once:
  python scripts/photo_count/eval.py score --tag published
Outputs: out/photo_count/preds_<tag>_<split>.npz, reports/photo_count/eval_<tag>.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.photo_count import data as D  # noqa: E402
from macroplastic.photo_count import metrics as M  # noqa: E402

CACHE = ROOT / "out" / "photo_count"
REP = ROOT / "reports" / "photo_count"


def split_items(split, root=None):
    """'val'/'test'/'train' = official FML split; 'g:<name>' = grouped-by-set split (data/extra/fml/split_grouped.json)."""
    if split.startswith("g:"):
        sys.path.insert(0, str(ROOT / "scripts" / "photo_count"))
        import train as T
        items = T.items_for("grouped", split[2:])
        return [f for f, _ in items], [D.yolo_boxes(l) for _, l in items]
    root = Path(root) if root else D.FML
    return D.load_split(split, root)


def fname(tag, split):
    return CACHE / f"preds_{tag}_{split.replace(':', '_')}.npz"


def save_preds(path, files, preds):
    np.savez_compressed(path, files=np.array([f.name for f in files]),
                        boxes=np.array([p[0] for p in preds], dtype=object),
                        scores=np.array([p[1] for p in preds], dtype=object), allow_pickle=True)


def load_preds(path):
    z = np.load(path, allow_pickle=True)
    return list(z["files"]), [(b.reshape(-1, 4), s) for b, s in zip(z["boxes"], z["scores"])]


def infer(a):
    import torch
    from PIL import Image
    from macroplastic.photo_count.model import load_model, predict_images
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = load_model(a.weights, dev)
    CACHE.mkdir(parents=True, exist_ok=True)
    for split in a.splits:
        files, _ = split_items(split, a.root)
        t0 = time.time()
        preds = []
        for k in range(0, len(files), a.batch):
            ims = [Image.open(f).convert("RGB") for f in files[k:k + a.batch]]
            preds += predict_images(model, ims, dev, batch=a.batch)
        dt = time.time() - t0
        save_preds(fname(a.tag, split), files, preds)
        print(f"{split}: {len(files)} images, {dt:.1f} s ({len(files) / max(dt, 1e-9):.1f} img/s) on {dev}", flush=True)


def summarize(preds, gts, thr, boot=1000):
    n = len(gts)
    matched = M.match_all(preds, gts, 0.5)
    tc = np.array([len(g) for g in gts])
    pc = M.counts_at(preds, thr)
    ap = M.ap_from_matched(matched)
    cm = M.count_metrics(pc, tc)
    out = {"n_images": n, "n_boxes": int(tc.sum()), "ap50": ap, "ap50_voc": M.ap_from_matched(matched, interp="voc"),
           "threshold": thr, **{f"count_{k}": v for k, v in cm.items()}, **M.prf_at(preds, gts, thr)}
    if boot:
        err = np.abs(pc - tc)
        out["ci95"] = {
            "ap50": M.bootstrap_ci(lambda i: M.ap_from_matched(matched, i), n, boot),
            "count_mae": M.bootstrap_ci(lambda i: float(err[i].mean()), n, boot),
            "count_exact": M.bootstrap_ci(lambda i: float((err[i] == 0).mean()), n, boot),
            "count_bias": M.bootstrap_ci(lambda i: float((pc - tc)[i].mean()), n, boot),
        }
    return out


def score(a):
    root = Path(a.root) if a.root else D.FML
    res = {"tag": a.tag, "splits": [a.val_split, a.test_split], "dataset": str(root.relative_to(ROOT)) if root.is_relative_to(ROOT) else str(root),
           "iou": 0.5, "rule": "threshold = argmin count MAE on VAL (grid 0.05..0.95 step 0.05); TEST scored once"}
    # ---- VAL: choose the threshold
    vf, vp = load_preds(fname(a.tag, a.val_split))
    _, vg = split_items(a.val_split, a.root)
    assert len(vf) == len(vg)
    tc = np.array([len(g) for g in vg])
    grid = np.round(np.arange(0.05, 0.96, 0.05), 2)
    rows = []
    for t in grid:
        cm = M.count_metrics(M.counts_at(vp, t), tc)
        f = M.prf_at(vp, vg, t)
        rows.append({"thr": float(t), "mae": cm["mae"], "exact": cm["exact"], "bias": cm["bias"], "f1": f["f1"]})
    best = min(rows, key=lambda r: (r["mae"], -r["exact"]))
    thr = best["thr"]
    res["val_grid"] = rows
    res["val"] = summarize(vp, vg, thr, boot=0)
    res["threshold_selected_on_val"] = thr
    res["threshold_f1_on_val"] = max(rows, key=lambda r: r["f1"])["thr"]
    print(f"VAL: thr={thr} mae={best['mae']:.3f} exact={best['exact']:.3f} ap50={res['val']['ap50']:.3f}", flush=True)
    # ---- TEST once
    out = REP / f"eval_{a.tag}.json"
    if a.test_split and out.exists() and not a.force:
        print(f"{out} exists — test already scored once; use --force only for a re-render", file=sys.stderr)
        return 3
    if a.test_split:
        tf, tp = load_preds(fname(a.tag, a.test_split))
        _, tg = split_items(a.test_split, a.root)
        assert len(tf) == len(tg)
        res["test"] = summarize(tp, tg, thr, boot=a.boot)
        res["test_at_author_thr_0.5"] = {k: v for k, v in summarize(tp, tg, 0.5, boot=0).items()
                                         if k.startswith("count_") or k in ("precision", "recall", "f1")}
        if a.test_split.startswith("g:"):  # per acquisition set (set17 = other days: site/season transfer)
            sets = json.loads((ROOT / "data" / "extra" / "fml" / "single_sets_index.json").read_text())
            of = {x: k for k, v in sets.items() for x in v}
            names = [Path(f).stem for f in tf]
            per = {}
            for sname in sorted({of[x] for x in names}, key=lambda k: int(k[3:])):
                idx = [i for i, x in enumerate(names) if of[x] == sname]
                pp, gg = [tp[i] for i in idx], [tg[i] for i in idx]
                cm = M.count_metrics(M.counts_at(pp, thr), [len(g) for g in gg])
                per[sname] = {"n_images": len(idx), "ap50": M.ap_at_iou(pp, gg), "count_mae": cm["mae"],
                              "count_exact": cm["exact"], "count_bias": cm["bias"]}
            res["test_per_set"] = per
        tcount = np.array([len(g) for g in tg])
        res["test_baselines"] = {
            "count_median_train": M.count_metrics(np.full(len(tg), a.median_count), tcount)["mae"]
            if a.median_count is not None else None,
        }
        t = res["test"]
        print(f"TEST: n={t['n_images']} ap50={t['ap50']:.3f} {t['ci95']['ap50']} mae={t['count_mae']:.3f} "
              f"{t['ci95']['count_mae']} exact={t['count_exact']:.3f} {t['ci95']['count_exact']}", flush=True)
    REP.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {out}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("infer")
    i.add_argument("--weights", required=True)
    i.add_argument("--tag", required=True)
    i.add_argument("--splits", nargs="+", default=["val", "test"])
    i.add_argument("--batch", type=int, default=4)
    i.add_argument("--root", default=None)
    s = sub.add_parser("score")
    s.add_argument("--tag", required=True)
    s.add_argument("--val-split", default="val")
    s.add_argument("--test-split", default="test")
    s.add_argument("--boot", type=int, default=1000)
    s.add_argument("--median-count", type=float, default=None)
    s.add_argument("--root", default=None)
    s.add_argument("--force", action="store_true")
    a = ap.parse_args()
    return infer(a) if a.cmd == "infer" else score(a)


if __name__ == "__main__":
    sys.exit(main() or 0)
