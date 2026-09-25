"""L41: before/after crops of the objects newly marked by the "vessel at the end" wake rule.

Usage: PYTHONPATH=src python scripts/experiments/l41_wake2_figs.py [--marks reports/tmp_l41/wake2_marks.csv]
Writes reports/figures/artifacts_wake2_<region>_<date>_<model>.png: RGB crop 800 x 800 m (x5), left - before
(object outline red: counted as debris), right - after (outline yellow: wake, not counted), bright cluster at the
end (the vessel) magenta.
"""
from __future__ import annotations
import argparse, csv, sys
from collections import defaultdict
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(Path(__file__).parent))
from l41_prep import prepare  # noqa: E402
from l41_wake2_eval import rgb  # noqa: E402
from macroplastic.grid.artifacts import classify, end_bright_clusters  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--marks", default=str(ROOT / "reports/tmp_l41/wake2_marks.csv"))
    ap.add_argument("--out", default=str(ROOT / "reports/figures"))
    a = ap.parse_args()
    by = defaultdict(list)
    for r in csv.DictReader(open(a.marks, encoding="utf-8")):
        by[(r["region"], r["date"], r["model"])].append(int(r["label"]))
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except OSError:
        font = None
    for (reg, date, m), labs in by.items():
        d = prepare(reg, date, m)
        b, lab = d["bands"], d["labels"]
        art, f = classify(lab, d["n"], b, d["water"], d["raw"])
        cl = end_bright_clusters(b["B2"], b["B3"], b["B4"], b["B8"], d["water"], lab > 0)
        objs = ndimage.find_objects(lab)
        sl = [objs[k - 1] for k in labs]
        cy = (min(s[0].start for s in sl) + max(s[0].stop for s in sl)) // 2
        cx = (min(s[1].start for s in sl) + max(s[1].stop for s in sl)) // 2
        ext = max(max(x.stop for x in (s[0] for s in sl)) - min(x.start for x in (s[0] for s in sl)),
                  max(x.stop for x in (s[1] for s in sl)) - min(x.start for x in (s[1] for s in sl)))
        h = max(40, ext // 2 + 10); H, W = lab.shape
        r0, c0 = min(max(cy - h, 0), H - 2 * h), min(max(cx - h, 0), W - 2 * h)
        base = rgb(b, r0, r0 + 2 * h, c0, c0 + 2 * h)
        sub = lab[r0:r0 + 2 * h, c0:c0 + 2 * h]
        before, after = base.copy(), base.copy()
        for k in np.unique(sub[sub > 0]):
            mm = sub == k
            e = mm & ~ndimage.binary_erosion(mm)
            before[e] = (255, 60, 50)
            a_ = art[k - 1]
            after[e] = {None: (255, 60, 50), "wake": (255, 220, 0), "seam": (255, 255, 255), "ship": (255, 0, 255)}[a_]
        after[cl[r0:r0 + 2 * h, c0:c0 + 2 * h] > 0] = (255, 0, 255)
        S = 2 * h * max(2, 400 // h)
        im = Image.new("RGB", (2 * S + 10, S + 62), (15, 20, 30))
        im.paste(Image.fromarray(before).resize((S, S), Image.NEAREST), (0, 62))
        im.paste(Image.fromarray(after).resize((S, S), Image.NEAREST), (S + 10, 62))
        dr = ImageDraw.Draw(im)
        info = "; ".join(f"#{k}: {int(f['n_px'][k - 1])} px, {int(f['length_px'][k - 1] * 10)} м" for k in labs)
        dr.text((6, 4), f"{reg} {date} {m}, {2 * h * 10} x {2 * h * 10} м. {info}", fill=(255, 255, 255), font=font)
        dr.text((6, 22), "До (слева): красный контур = находка, входит в индекс и зоны",
                fill=(255, 255, 255), font=font)
        dr.text((6, 40), "После (L41): жёлтый = кильватер, пурпурный = яркая точка-судно у торца (≤ 50 м); красный = находка",
                fill=(255, 255, 255), font=font)
        out = Path(a.out) / f"artifacts_wake2_{reg}_{date}_{m}.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        im.save(out, optimize=True)
        print(out)


if __name__ == "__main__":
    main()
