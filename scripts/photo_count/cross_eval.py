"""Transfer check of the FML photo counter on other open datasets (no training on them; class-agnostic:
every labelled litter box = one item). Datasets come from the §17 search (L112/L113/L116), docs/COUNT_DATASETS.md.

  python scripts/photo_count/cross_eval.py infer --weights <pth> --tag <tag> --sets maharjan tud_gv ...   (GPU, via queue)
  python scripts/photo_count/cross_eval.py score --tag <tag> --thr <FML val threshold>                     (CPU)

Threshold is NOT re-tuned on these sets (it is the one chosen on the FML val split) — a transfer test.
Maharjan 2022 tiles are 256x256 px = 2x2 m (GSD 0.82 cm, drone at 30 m) -> items per m^2 on the tile scale.
Outputs: out/photo_count/xpreds_<tag>_<set>.npz, reports/photo_count/cross_<tag>.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from macroplastic.photo_count import metrics as M  # noqa: E402

CDS = ROOT / "data" / "extra" / "count_ds"
CACHE = ROOT / "out" / "photo_count"
REP = ROOT / "reports" / "photo_count"


def _yolo_dir(img_dir, lab_dir, exts=(".jpg", ".png", ".jpeg"), keep_cls=None):
    from PIL import Image
    items = []
    for f in sorted(p for p in Path(img_dir).iterdir() if p.suffix.lower() in exts):
        lab = Path(lab_dir) / (f.stem + ".txt")
        if not lab.exists():
            continue
        w, h = Image.open(f).size
        rows = []
        for line in lab.read_text().splitlines():
            p = line.split()
            if len(p) < 5:
                continue
            if keep_cls is not None and int(float(p[0])) not in keep_cls:
                continue
            cx, cy, bw, bh = map(float, p[1:5])
            rows.append([(cx - bw / 2) * w, (cy - bh / 2) * h, (cx + bw / 2) * w, (cy + bh / 2) * h])
        items.append((f, np.asarray(rows, float).reshape(-1, 4)))
    return items


def _coco(json_path, img_dir):
    d = json.loads(Path(json_path).read_text(encoding="utf-8"))
    by = {im["id"]: [] for im in d["images"]}
    cats = {c["id"]: c["name"] for c in d["categories"]}
    for a in d["annotations"]:
        x, y, w, h = a["bbox"]
        by[a["image_id"]].append([x, y, x + w, y + h])
    items = [(Path(img_dir) / im["file_name"], np.asarray(by[im["id"]], float).reshape(-1, 4))
             for im in d["images"] if (Path(img_dir) / im["file_name"]).exists()]
    return items, cats


def dataset(name):
    """-> (items [(path, gt_boxes xyxy)], info dict)"""
    if name == "rf100vl_test":  # only the images present locally (L116 sample: 100 of the test split)
        base = CDS / "rf100vl_floating_waste" / "floating-waste" / "test"
        items, cats = _coco(base / "_annotations.coco.json", base)
        return items, {"source": "RF100-VL floating-waste (Roboflow; HF LibreYOLO/rf100-vl), MIT",
                       "split": "test (images present locally)", "classes": cats,
                       "survey": "камера у воды / с берега, кадры видео"}
    if name == "maharjan":
        items = []
        for sub in ("train_data_v5_Laos", "train_data_v5_talathai"):
            b = CDS / "maharjan_river_uav" / "Nisha-main" / "Datagithub" / sub
            items += _yolo_dir(b / "images", b / "labels")
        return items, {"source": "Maharjan et al. 2022 (Remote Sens. 14, 3049), github Nisha484/Nisha",
                       "split": "all tiles", "survey": "дрон 30 м, надир; плитка 256x256 px = 2x2 м",
                       "tile_area_m2": 4.0}
    if name == "tud_gv":
        b = CDS / "tud_gv_od"
        return _yolo_dir(b / "images", b / "labels_txt"), {
            "source": "TUD-GV object detection (Zenodo 13730228), CC BY 4.0", "split": "images present locally",
            "survey": "камера 2.7/4.0 м над каналом (наклонная)"}
    if name == "floating_rubbish_val":
        b = CDS / "floating_rubbish_hf" / "datasets" / "val"
        return _yolo_dir(b / "images", b / "labels"), {
            "source": "HF gonzz2026 floating rubbish on the surface of the water, CC BY 4.0 (declared)",
            "split": "val", "survey": "фото с судна/берега, 11 классов мусора"}
    raise KeyError(name)


def readable(items):
    """Drop images that do not decode fully (partial downloads while the disk was full, 26.09 05:05)."""
    from PIL import Image
    ok, bad = [], []
    for f, g in items:
        try:
            with Image.open(f) as im:
                im.load()
            ok.append((f, g))
        except Exception:  # noqa: BLE001
            bad.append(f.name)
    return ok, bad


def dataset_ok(name):
    items, info = dataset(name)
    items, bad = readable(items)
    info = {**info, "n_unreadable_skipped": len(bad)}
    return items, info


def fname(tag, name):
    return CACHE / f"xpreds_{tag}_{name}.npz"


def infer(a):
    import torch
    from PIL import Image
    from macroplastic.photo_count.model import load_model, predict_images
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model = load_model(a.weights, dev)
    CACHE.mkdir(parents=True, exist_ok=True)
    for name in a.sets:
        items, _ = dataset_ok(name)
        t0 = time.time()
        preds = []
        for k in range(0, len(items), 4):
            ims = [Image.open(f).convert("RGB") for f, _ in items[k:k + 4]]
            preds += predict_images(model, ims, dev, batch=4)
        np.savez_compressed(fname(a.tag, name), files=np.array([str(f.name) for f, _ in items]),
                            boxes=np.array([p[0] for p in preds], dtype=object),
                            scores=np.array([p[1] for p in preds], dtype=object), allow_pickle=True)
        print(f"{name}: {len(items)} images {time.time() - t0:.1f} s", flush=True)


def score(a):
    out = {"tag": a.tag, "threshold": a.thr, "rule": "threshold from FML val, not re-tuned; class-agnostic; IoU 0.5",
           "sets": {}}
    for name in a.sets:
        p = fname(a.tag, name)
        if not p.exists():
            continue
        items, info = dataset_ok(name)
        z = np.load(p, allow_pickle=True)
        preds = [(b.reshape(-1, 4), s) for b, s in zip(z["boxes"], z["scores"])]
        gts = [g for _, g in items]
        assert len(preds) == len(gts)
        n = len(gts)
        matched = M.match_all(preds, gts)
        tc = np.array([len(g) for g in gts])
        pc = M.counts_at(preds, a.thr)
        err = np.abs(pc - tc)
        r = {**info, "n_images": n, "n_boxes": int(tc.sum()), "ap50": M.ap_from_matched(matched),
             "ap50_ci95": M.bootstrap_ci(lambda i: M.ap_from_matched(matched, i), n, a.boot),
             **{f"count_{k}": v for k, v in M.count_metrics(pc, tc).items()},
             "count_mae_ci95": M.bootstrap_ci(lambda i: float(err[i].mean()), n, a.boot),
             "count_exact_ci95": M.bootstrap_ci(lambda i: float((err[i] == 0).mean()), n, a.boot),
             **M.prf_at(preds, gts, a.thr),
             "baseline_count_mae_median": float(np.mean(np.abs(np.median(tc) - tc)))}
        if info.get("tile_area_m2"):
            A = info["tile_area_m2"]
            r["density"] = {"unit": "шт./м² на площади плитки (дрон, не спутник)", "tile_area_m2": A,
                            "true_mean_per_m2": float(tc.mean() / A), "pred_mean_per_m2": float(pc.mean() / A),
                            "mae_per_m2": float(err.mean() / A),
                            "pred_mean_ci95": M.bootstrap_ci(lambda i: float(pc[i].mean() / A), n, a.boot),
                            "true_mean_ci95": M.bootstrap_ci(lambda i: float(tc[i].mean() / A), n, a.boot)}
        out["sets"][name] = r
        print(f"{name}: n={n} boxes={tc.sum()} AP50={r['ap50']:.3f} MAE={r['count_mae']:.2f} "
              f"exact={r['count_exact']:.2f} P={r['precision']:.2f} R={r['recall']:.2f} "
              f"(median-baseline MAE {r['baseline_count_mae_median']:.2f})")
    REP.mkdir(parents=True, exist_ok=True)
    (REP / f"cross_{a.tag}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("infer")
    