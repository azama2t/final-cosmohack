"""Wake rule: effect of the "vessel at the end" wake rule on all live scenes (reports/artifacts.md).

Usage: PYTHONPATH=src python scripts/experiments/l41_wake2_eval.py [--before reports/tmp_l41/artifacts_before.py]
Writes reports/tmp_l41/wake2_marks.csv (one row per newly marked object) and wake2_sheet_<k>.png contact sheets
(RGB crops 64x64 px, object outline yellow, matched bright cluster magenta).
"""
from __future__ import annotations
import argparse, csv, importlib.util, sys
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(Path(__file__).parent))
from l41_prep import prepare  # noqa: E402
from macroplastic.grid import artifacts as A  # noqa: E402


def rgb(b, r0, r1, c0, c1):
    v = np.stack([b[k][r0:r1, c0:c1] for k in ("B4", "B3", "B2")], -1).astype(np.float32)
    v = np.nan_to_num(v)
    lo, hi = np.percentile(v, 1), np.percentile(v, 99.5)
    return np.clip((v - lo) / max(hi - lo, 1e-4) * 255, 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", default=str(ROOT / "reports/tmp_l41/artifacts_before.py"))
    ap.add_argument("--live", default=str(ROOT / "data/live"))
    ap.add_argument("--out", default=str(ROOT / "reports/tmp_l41"))
    a = ap.parse_args()
    spec = importlib.util.spec_from_file_location("art_before", a.before)
    Bf = importlib.util.module_from_spec(spec); spec.loader.exec_module(Bf)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    rows, tiles = [], []
    tot = {}
    for sdir in sorted(Path(a.live).glob("*/*")):
        for pf in sorted(sdir.glob("prob_*.tif")):
            m = pf.stem[5:]
            region, date = sdir.parent.name, sdir.name
            d = prepare(region, date, m, a.live)
            lab, n, b = d["labels"], d["n"], d["bands"]
            new, f = A.classify(lab, n, b, d["water"], d["raw"])
            old, _ = Bf.classify(lab, n, b, d["water"], d["raw"])
            t = tot.setdefault(m, {"objects": 0, "new_wake": 0, "new_wake_px": 0, "changed_other": 0, "end_ship_any": 0})
            t["objects"] += n
            t["end_ship_any"] += int(np.sum(f.get("end_ship", 0)))
            objs = ndimage.find_objects(lab)
            for k in range(n):
                if new[k] == old[k]:
                    continue
                if old[k] is None and new[k] == "wake" and f["end_ship"][k]:
                    t["new_wake"] += 1; t["new_wake_px"] += int(f["n_px"][k])
                    rows.append(dict(region=region, date=date, model=m, label=k + 1, n_px=int(f["n_px"][k]),
                                     length_m=round(float(f["length_px"][k]) * 10), elong=round(float(f["elong"][k]), 1),
                                     dev=round(float(f["dev"][k]), 3), ship_b8=round(float(f["end_ship_b8"][k]), 4),
                                     row=round(float(f["cy"][k])), col=round(float(f["cx"][k]))))
                    sl = objs[k]; cy, cx = (sl[0].start + sl[0].stop) // 2, (sl[1].start + sl[1].stop) // 2
                    H, W = lab.shape; h = 32
                    r0, c0 = min(max(cy - h, 0), H - 2 * h), min(max(cx - h, 0), W - 2 * h)
                    im = rgb(b, r0, r0 + 2 * h, c0, c0 + 2 * h)
                    mm = lab[r0:r0 + 2 * h, c0:c0 + 2 * h] == k + 1
                    e = mm & ~ndimage.binary_erosion(mm)
                    raw = im.copy(); im[e] = (255, 220, 0)
                    tiles.append((f"{region} {date} {m} #{k + 1} {int(f['n_px'][k])}px", raw, im))
                else:
                    t["changed_other"] += 1
            print(region, date, m, n, "new wake:", sum(1 for r in rows if (r["region"], r["date"], r["model"]) == (region, date, m)), flush=True)
    with open(out / "wake2_marks.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["region"]); w.writeheader(); w.writerows(rows)
    print("TOTALS", tot)
    # contact sheets: 6 pairs per row, 5 rows per sheet
    S = 3 * 64; per = 12
    for i in range(0, len(tiles), per):
        chunk = tiles[i:i + per]
        cols = 4; rws = (len(chunk) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (2 * S + 20), rws * (S + 20)), (15, 20, 30))
        dr = ImageDraw.Draw(sheet)
        for j, (name, raw, im) in enumerate(chunk):
            x, y = (j % cols) * (2 * S + 20), (j // cols) * (S + 20)
            sheet.paste(Image.fromarray(raw).resize((S, S), Image.NEAREST), (x, y + 16))
            sheet.paste(Image.fromarray(im).resize((S, S), Image.NEAREST), (x + S + 4, y + 16))
            dr.text((x + 2, y + 2), name, fill=(230, 230, 230))
        sheet.save(out / f"wake2_sheet_{i // per}.png")


if __name__ == "__main__":
    main()
