"""Boat rule: effect of the boat rule and the wake-axis extension on all live scenes (classify level, before = HEAD copy).

Usage: PYTHONPATH=src python scripts/experiments/l43_eval.py
Writes reports/tmp_l43/l43_marks.csv (one row per object whose mark changed) and l43_sheet_{wake,ship}_<i>.png.
"""
from __future__ import annotations
import csv, importlib.util, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(Path(__file__).parent))
OUT = ROOT / "reports" / "tmp_l43"


def rgb(b, r0, r1, c0, c1):
    v = np.stack([b[k][r0:r1, c0:c1] for k in ("B4", "B3", "B2")], -1).astype(np.float32)
    v = np.nan_to_num(v)
    lo, hi = np.percentile(v, 1), np.percentile(v, 99.5)
    return np.clip((v - lo) / max(hi - lo, 1e-4) * 255, 0, 255).astype(np.uint8)


def one(args):
    region, date, m = args
    from l41_prep import prepare
    from macroplastic.grid import artifacts as A
    spec = importlib.util.spec_from_file_location("art_before", OUT / "artifacts_before.py")
    Bf = importlib.util.module_from_spec(spec); spec.loader.exec_module(Bf)
    d = prepare(region, date, m)
    lab, n, b = d["labels"], d["n"], d["bands"]
    new, f = A.classify(lab, n, b, d["water"], d["raw"])
    old, _ = Bf.classify(lab, n, b, d["water"], d["raw"])
    rows, tiles = [], []
    objs = ndimage.find_objects(lab)
    tot = dict(objects=n, px=int((lab > 0).sum()), marked_before=sum(a is not None for a in old),
               marked_after=sum(a is not None for a in new),
               px_before=int(sum(f["n_px"][k] for k in range(n) if old[k])),
               px_after=int(sum(f["n_px"][k] for k in range(n) if new[k])))
    for k in range(n):
        if new[k] == old[k]:
            continue
        why = "boat" if f["boat"][k] else ("end_ship" if f["end_ship"][k] else ("trace" if f["trace_px"][k] * 10 >= 1000 else "other"))
        rows.append(dict(region=region, date=date, model=m, label=k + 1, old=old[k] or "", new=new[k] or "", why=why,
                         n_px=int(f["n_px"][k]), length_m=round(float(f["length_px"][k]) * 10),
                         dev=round(float(f["dev"][k]), 3), s11=round(float(f["boat_s11"][k]), 3), trace_m=round(float(f["trace_px"][k]) * 10),
                         row=round(float(f["cy"][k])), col=round(float(f["cx"][k]))))
        sl = objs[k]; cy, cx = (sl[0].start + sl[0].stop) // 2, (sl[1].start + sl[1].stop) // 2
        H, W = lab.shape; h = 32
        r0, c0 = min(max(cy - h, 0), H - 2 * h), min(max(cx - h, 0), W - 2 * h)
        im = rgb(b, r0, r0 + 2 * h, c0, c0 + 2 * h)
        mm = lab[r0:r0 + 2 * h, c0:c0 + 2 * h] == k + 1
        e = mm & ~ndimage.binary_erosion(mm)
        raw = im.copy(); im[e] = (255, 220, 0)
        tiles.append((new[k] or "none", f"{region} {date} {m} #{k + 1} {int(f['n_px'][k])}px {old[k]}->{new[k]}", raw, im))
    return (region, date, m), tot, rows, tiles


def main():
    jobs = [(s.parent.name, s.name, p.stem[5:]) for s in sorted((ROOT / "data/live").glob("*/*"))
            for p in sorted(s.glob("prob_*.tif"))]
    rows, tiles, tot = [], [], {}
    with ProcessPoolExecutor(14) as ex:
        for key, t, rr, tt in ex.map(one, jobs):
            rows += rr; tiles += tt
            a = tot.setdefault(key[2], {})
            for k, v in t.items():
                a[k] = a.get(k, 0) + v
            print(*key, t["marked_before"], "->", t["marked_after"], flush=True)
    with open(OUT / "l43_marks.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["region"]); w.writeheader(); w.writerows(rows)
    print("TOTALS", tot)
    by = {}
    for r in rows:
        kk = (r["model"], r["old"], r["new"], r["why"])
        by.setdefault(kk, [0, 0]); by[kk][0] += 1; by[kk][1] += r["n_px"]
    for kk, v in sorted(by.items()):
        print("CHANGE", kk, v)
    S = 3 * 64
    for kind in ("wake", "ship", "seam", "none"):
        tl = [t for t in tiles if t[0] == kind]
        for i in range(0, len(tl), 24):
            chunk = tl[i:i + 24]
            cols = 4; rws = (len(chunk) + cols - 1) // cols
            sheet = Image.new("RGB", (cols * (2 * S + 20), rws * (S + 20)), (15, 20, 30))
            dr = ImageDraw.Draw(sheet)
            for j, (_, name, raw, im) in enumerate(chunk):
                x, y = (j % cols) * (2 * S + 20), (j // cols) * (S + 20)
                sheet.paste(Image.fromarray(raw).resize((S, S), Image.NEAREST), (x, y + 16))
                sheet.paste(Image.fromarray(im).resize((S, S), Image.NEAREST), (x + S + 4, y + 16))
                dr.text((x + 2, y + 2), name, fill=(230, 230, 230))
            sheet.save(OUT / f"l43_sheet_{kind}_{i // 24}.png")


if __name__ == "__main__":
    main()
