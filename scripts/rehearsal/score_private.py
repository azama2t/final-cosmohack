"""L30 rehearsal: "private leaderboard" scorer for the organizer-style dataset (make_org_dataset.py).

Checks the submission format exactly as the README promises (one PNG per test image, same stem, uint8,
240x240, codes 0..3), then computes the organizer metric: F1 of class 3 (marine debris) over ALL pixels of
all test images pooled. Also reports P/R/IoU and, for diagnostics only, F1 over labelled pixels (gt != 0).

Usage:
    .venv\\Scripts\\python.exe scripts\\rehearsal\\score_private.py --sub out\\rehearsal\\sub_v1 [--sub-zip x.zip]
        [--gt out\\rehearsal\\hidden_test_masks] [--images out\\rehearsal\\org_raw\\test\\images] [--json out.json]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def _load_sub(sub: str) -> dict[str, np.ndarray]:
    p = Path(sub)
    out: dict[str, np.ndarray] = {}
    if p.is_file() and p.suffix.lower() == ".zip":
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if n.endswith("/"):
                    continue
                out[Path(n).name] = np.array(Image.open(io.BytesIO(z.read(n))))
    else:
        for f in sorted(p.iterdir()):
            if f.is_file():
                out[f.name] = np.array(Image.open(f)) if f.suffix.lower() == ".png" else None
    return out


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 0.0
    iou = tp / (tp + fp + fn) if tp + fp + fn else 0.0
    return {"f1": round(f1, 4), "precision": round(p, 4), "recall": round(r, 4), "iou": round(iou, 4),
            "tp": tp, "fp": fp, "fn": fn}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sub", required=True, help="submission folder or zip with <stem>.png")
    ap.add_argument("--gt", default=str(ROOT / "out" / "rehearsal" / "hidden_test_masks"))
    ap.add_argument("--images", default=str(ROOT / "out" / "rehearsal" / "org_raw" / "test" / "images"))
    ap.add_argument("--target", type=int, default=3)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    gt_files = {f.stem: f for f in Path(a.gt).glob("*.png")}
    img_stems = {f.stem for f in Path(a.images).glob("*.tif")}
    sub = _load_sub(a.sub)
    errors: list[str] = []
    if img_stems and img_stems != set(gt_files):
        errors.append(f"internal: test images {len(img_stems)} != hidden masks {len(gt_files)}")
    exp_names = {f"{s}.png" for s in gt_files}
    missing = sorted(exp_names - set(sub))
    extra = sorted(set(sub) - exp_names)
    if missing:
        errors.append(f"missing {len(missing)} files, e.g. {missing[:3]}")
    if extra:
        errors.append(f"unexpected {len(extra)} files, e.g. {extra[:3]}")

    tp = fp = fn = 0
    ltp = lfp = lfn = 0
    bad = 0
    for stem, gf in sorted(gt_files.items()):
        pr = sub.get(f"{stem}.png")
        gt = np.array(Image.open(gf))
        if pr is None:
            pr = np.zeros_like(gt)  # missing file scores as all-background
        elif pr.shape != gt.shape or pr.dtype != np.uint8 or not np.isin(np.unique(pr), [0, 1, 2, 3]).all():
            bad += 1
            if bad <= 3:
                errors.append(f"{stem}.png: shape {pr.shape} dtype {pr.dtype} values {np.unique(pr)[:8].tolist()}")
            if pr.shape != gt.shape:
                pr = np.zeros_like(gt)
        g, p = gt == a.target, pr == a.target
        tp += int((g & p).sum()); fp += int((~g & p).sum()); fn += int((g & ~p).sum())
        lab = gt != 0
        ltp += int((g & p & lab).sum()); lfp += int((~g & p & lab).sum()); lfn += int((g & ~p & lab).sum())
    res = {"valid_format": not errors, "errors": errors, "n_expected": len(gt_files), "n_submitted": len(sub),
           "private_all_px": _prf(tp, fp, fn), "diag_labelled_px_only": _prf(ltp, lfp, lfn)}
    print(json.dumps(res, indent=1, ensure_ascii=False))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
