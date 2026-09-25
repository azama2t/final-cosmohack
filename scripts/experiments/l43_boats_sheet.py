"""L43: contact sheets of small detections selected by a pandas query over reports/tmp_l43/live_small.csv.

Usage: PYTHONPATH=src python scripts/experiments/l43_boats_sheet.py "<query>" <out.png> [max]
Each tile: RGB crop 41x41 px (410 m) around the object, stretched per tile; right half = same with object outline.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from PIL import Image, ImageDraw
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(Path(__file__).parent))
from l41_prep import prepare  # noqa: E402

H = 20


def crop_rgb(b, r0, r1, c0, c1):
    v = np.stack([b[k][r0:r1, c0:c1] for k in ("B4", "B3", "B2")], -1).astype(np.float32)
    v = np.nan_to_num(v)
    lo, hi = np.percentile(v, 1), np.percentile(v, 99.5)
    return np.clip((v - lo) / max(hi - lo, 1e-4) * 255, 0, 255).astype(np.uint8)


def main():
    q, out = sys.argv[1], Path(sys.argv[2])
    mx = int(sys.argv[3]) if len(sys.argv) > 3 else 48
    d = pd.read_csv(ROOT / "reports/tmp_l43/live_small.csv").query(q)
    if len(d) > mx:
        d = d.sample(mx, random_state=0)
    d = d.sort_values(["region", "date", "model"])
    tiles = []
    cache = {}
    for _, r in d.iterrows():
        key = (r.region, r.date, r.model)
        if key not in cache:
            cache.clear()
            cache[key] = prepare(r.region, r.date, r.model)
        p = cache[key]
        lab, b = p["labels"], p["bands"]
        Hh, Ww = lab.shape
        r0, c0 = min(max(r.cy - H, 0), Hh - 2 * H - 1), min(max(r.cx - H, 0), Ww - 2 * H - 1)
        im = crop_rgb(b, r0, r0 + 2 * H + 1, c0, c0 + 2 * H + 1)
        mm = lab[r0:r0 + 2 * H + 1, c0:c0 + 2 * H + 1] == r.k
        e = mm & ~ndimage.binary_erosion(mm)
        o = im.copy(); o[e] = (255, 230, 0)
        tiles.append((f"{r.region[:9]} {r.date[2:]} {r.model} #{r.k} rv{r.rv:.1f} r8{r.r8:.0f} {r.art if isinstance(r.art, str) else ''}", im, o))
    S = 4 * (2 * H + 1); cols = 4
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (2 * S + 16), max(rows, 1) * (S + 18)), (15, 20, 30))
    dr = ImageDraw.Draw(sheet)
    for j, (name, im, o) in enumerate(tiles):
        x, y = (j % cols) * (2 * S + 16), (j // cols) * (S + 18)
        sheet.paste(Image.fromarray(im).resize((S, S), Image.NEAREST), (x, y + 16))
        sheet.paste(Image.fromarray(o).resize((S, S), Image.NEAREST), (x + S + 2, y + 16))
        dr.text((x + 2, y + 2), name, fill=(230, 230, 230))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(out, len(tiles))


if __name__ == "__main__":
    main()
