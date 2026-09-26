"""Count floating litter items on photos (camera at the water) with the model in weights_exp/photo_count/model_card.json.

  python scripts/photo_count/predict.py photo1.jpg [photo2.jpg ...] [--threshold 0.9] [--frame-area-m2 35] [--out result.json]
  [--draw out_dir]  saves copies with boxes

Output per photo: count (items per frame), boxes, threshold, model version; items/km^2 only with --frame-area-m2
("on the frame area, not satellite"). Default device: CPU unless MACROPLASTIC_PHOTO_DEVICE=cuda (GPU work goes via the queue).
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--frame-area-m2", type=float, default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--draw", default=None)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows console: "км²"
    os.environ.setdefault("MACROPLASTIC_PHOTO_DEVICE", "cpu")
    from PIL import Image, ImageDraw, ImageOps
    from macroplastic.photo_count import SURVEY_NOTE
    from macroplastic.photo_count.model import Counter, density_per_km2
    c = Counter.get()
    res = []
    for p in a.images:
        im = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
        boxes, scores, thr = c.count(im, a.threshold)
        d = density_per_km2(len(boxes), a.frame_area_m2)
        r = {"image": str(p), "count": int(len(boxes)), "unit": "штук на кадр", "threshold": thr,
             "model_version": c.card.get("version"),
             "boxes": [[round(float(v), 1) for v in b] + [round(float(s), 4)] for b, s in zip(boxes, scores)],
             "items_per_km2_on_frame": d, "note": SURVEY_NOTE}
        res.append(r)
        print(f"{p}: {r['count']} предм. (порог {thr})" + (f", {d:,.0f} шт./км² на площади кадра, не спутник" if d else ""))
        if a.draw:
            Path(a.draw).mkdir(parents=True, exist_ok=True)
            dr = ImageDraw.Draw(im)
            for b, s in zip(boxes, scores):
                dr.rectangle([float(v) for v in b], outline=(255, 197, 61), width=3)
                dr.text((float(b[0]), max(float(b[1]) - 12, 0)), f"{s:.2f}", fill=(255, 197, 61))
            im.save(Path(a.draw) / (Path(p).stem + "_count.jpg"), quality=90)
    if a.out:
        Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
