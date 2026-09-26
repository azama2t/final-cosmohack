"""DebrisScan baseline (orbtl-ai/DebrisScan, Apache-2.0; Winans et al. 2023 EfficientDet-D0, 2 cm GSD) on our Winans splits.

Runs ONLY in the separate environment .venv-baseline (tensorflow-cpu, requirements-baseline.txt):
  .venv-baseline\\Scripts\\python.exe scripts/photo_count/baseline_debrisscan.py --splits val test

Pipeline as in DebrisScan (app/configs/api_config.py, app/geoprocessor/utils/object_detection.py): image padded with
0 (no-data) to a multiple of CHIP_SIZE 512x512, cut into 512x512 chips, SavedModel signature serving_default on uint8
[1,512,512,3]; boxes (normalised ymin,xmin,ymax,xmax) shifted back to the 640x640 chip, kept if score >= 0.05
(the DebrisScan default confidence threshold 0.30 and our val-selected one are applied at scoring time).
Split lists: data/extra/fml/../count_ds/_l109/winans_split.json written by the main .venv (no torch here).
Output: out/photo_count/debrisscan_<split>.json  {file: {"boxes": [[x1,y1,x2,y2],...], "scores": [...], "classes": [...]}}
NOTE: DebrisScan was trained on the authors' Winans split, which includes part of our spatial test chips -> in its favour.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MODEL = ROOT / "data" / "extra" / "debrisscan" / "efficientdet-d0" / "1"
SPLIT = ROOT / "data" / "extra" / "count_ds" / "_l109" / "winans_split.json"
OUT = ROOT / "out" / "photo_count"
CHIP = 512


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--splits", nargs="+", default=["val", "test"])
    ap.add_argument("--min-score", type=float, default=0.05)
    a = ap.parse_args()
    import tensorflow as tf
    from PIL import Image
    print("tf", tf.__version__, flush=True)
    model = tf.saved_model.load(str(MODEL))
    fn = model.signatures["serving_default"]
    sp = json.loads(SPLIT.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    for split in a.splits:
        res = {}
        t0 = time.time()
        for path in sp[split]:
            im = np.asarray(Image.open(path).convert("RGB"), dtype=np.uint8)
            H, W = im.shape[:2]
            Hp, Wp = -(-H // CHIP) * CHIP, -(-W // CHIP) * CHIP
            pad = np.zeros((Hp, Wp, 3), np.uint8)
            pad[:H, :W] = im
            boxes, scores, classes = [], [], []
            for y0 in range(0, Hp, CHIP):
                for x0 in range(0, Wp, CHIP):
                    chip = pad[y0:y0 + CHIP, x0:x0 + CHIP]
                    if not chip.any():
                        continue
                    o = fn(tf.constant(chip[None]))
                    b = o["detection_boxes"].numpy()[0]
                    s = o["detection_scores"].numpy()[0]
                    c = o["detection_classes"].numpy()[0]
                    k = s >= a.min_score
                    for (ymin, xmin, ymax, xmax), sc, cl in zip(b[k], s[k], c[k]):
                        bx = [x0 + xmin * CHIP, y0 + ymin * CHIP, x0 + xmax * CHIP, y0 + ymax * CHIP]
                        bx = [min(max(bx[0], 0), W), min(max(bx[1], 0), H), min(max(bx[2], 0), W), min(max(bx[3], 0), H)]
                        if bx[2] - bx[0] > 1 and bx[3] - bx[1] > 1:
                            boxes.append([float(v) for v in bx]); scores.append(float(sc)); classes.append(int(cl))
            res[Path(path).name] = {"boxes": boxes, "scores": scores, "classes": classes}
        (OUT / f"debrisscan_{split}.json").write_text(json.dumps(res), encoding="utf-8")
        print(f"{split}: {len(res)} chips, {time.time() - t0:.1f} s", flush=True)


if __name__ == "__main__":
    sys.exit(main() or 0)
