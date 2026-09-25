"""Boat rule: brightness of small detections relative to the local water (boats vs floating material).

Usage: PYTHONPATH=src python scripts/experiments/l43_boats_eval.py [--marida] [--live] [--jobs 8]
  --marida : per connected component of MARIDA class 1 (Marine Debris) and 5 (Ship): peak vis = mean(B2,B3,B4) and
             peak B8 over the component / median of the 31 px window over water pixels (class 7, or unlabelled with
             NDWI > 0; other labelled classes excluded) -> reports/tmp_l43/marida_ratios.csv
  --live   : per small component (length <= 8 px) of every live scene / model, same ratios, peak over the object and
             its 2 px ring -> reports/tmp_l43/live_small.csv
"""
from __future__ import annotations
import argparse, csv, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import numpy as np
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(Path(__file__).parent))
OUT = ROOT / "reports" / "tmp_l43"
HALF = 15  # 31 px window


def excess(b2, b3, b4, b8, w, z):
    """Band excess over the local water median at the pixel of z with the largest vis + B8 excess."""
    md = [float(np.median(b[w])) for b in (b2, b3, b4, b8)]
    d = [b - m for b, m in zip((b2, b3, b4, b8), md)]
    sc = np.where(z, (d[0] + d[1] + d[2]) / 3 + d[3], -np.inf)
    i = np.unravel_index(np.argmax(sc), sc.shape)
    d2, d3, d4, d8 = (float(x[i]) for x in d)
    dv = (d2 + d3 + d4) / 3
    return dict(d2=round(d2, 4), d3=round(d3, 4), d4=round(d4, 4), d8=round(d8, 4),
                nir_share=round(d8 / max(dv + d8, 1e-4), 3) if dv + d8 > 0 else -1,
                blue_red=round(d2 / d4, 3) if d4 > 1e-4 else -1)


def isolation(b8, w, z):
    """Spot = pixels >= half the peak B8 excess, 8-connected with the peak (in z); ring = 2..4 px around the spot.
    ring_hi = share of ring pixels with B8 excess >= 25 % of the peak excess (a filament through the point -> high)."""
    m8 = float(np.median(b8[w]))
    e = b8 - m8
    sc = np.where(z, e, -np.inf)
    i = np.unravel_index(np.argmax(sc), sc.shape)
    pk = float(e[i])
    if pk <= 0:
        return dict(spot_px=0, ring_hi=1.0)
    lab, _ = ndimage.label(e >= 0.5 * pk, structure=np.ones((3, 3), bool))
    spot = lab == lab[i]
    ring = ndimage.binary_dilation(spot, np.ones((3, 3), bool), iterations=4) &         ~ndimage.binary_dilation(spot, np.ones((3, 3), bool), iterations=1)
    return dict(spot_px=int(spot.sum()), ring_hi=round(float(np.mean(e[ring] >= 0.25 * pk)) if ring.any() else 1.0, 3))


def swir(b8, b11, w, z):
    """B11 excess over the local water median at the pixel of z with the largest B8 excess; s11 = d11 / d8."""
    e8 = b8 - float(np.median(b8[w]))
    e11 = b11 - float(np.median(b11[w]))
    i = np.unravel_index(np.argmax(np.where(z, e8, -np.inf)), e8.shape)
    d8, d11 = float(e8[i]), float(e11[i])
    return dict(d11=round(d11, 4), s11=round(d11 / d8, 3) if d8 > 1e-4 else -9)


def marida_one(p: Path):
    from macroplastic.data.marida import load_patch
    img, cl, conf, _ = load_patch(p)
    if not np.isin(cl, (1, 5)).any():
        return []
    img = np.nan_to_num(img)
    b2, b3, b4, b8 = img[1], img[2], img[3], img[7]
    vis = (b2 + b3 + b4) / 3.0
    ndwi = (b3 - b8) / np.maximum(b3 + b8, 1e-6)
    water = (cl == 7) | ((cl == 0) & (ndwi > 0))
    rows = []
    H, W = cl.shape
    for c in (1, 5):
        lab, n = ndimage.label(cl == c, structure=np.ones((3, 3), bool))
        for k, sl in enumerate(ndimage.find_objects(lab), 1):
            m = lab == k
            ys, xs = np.nonzero(m)
            cy, cx = int(ys.mean()), int(xs.mean())
            r0, r1, c0, c1 = max(cy - HALF, 0), min(cy + HALF + 1, H), max(cx - HALF, 0), min(cx + HALF + 1, W)
            w = water[r0:r1, c0:c1] & ~m[r0:r1, c0:c1]
            if w.sum() < 30:
                continue
            mv, m8 = float(np.median(vis[r0:r1, c0:c1][w])), float(np.median(b8[r0:r1, c0:c1][w]))
            ex = excess(b2[r0:r1, c0:c1], b3[r0:r1, c0:c1], b4[r0:r1, c0:c1], b8[r0:r1, c0:c1], w,
                        m[r0:r1, c0:c1])
            ex.update(isolation(b8[r0:r1, c0:c1], w, m[r0:r1, c0:c1]))
            ex.update(swir(b8[r0:r1, c0:c1], img[9][r0:r1, c0:c1], w, m[r0:r1, c0:c1]))
            rows.append(dict(**ex, src=p.stem, cls="debris" if c == 1 else "ship", n_px=int(m.sum()),
                             len_px=int(max(np.ptp(ys), np.ptp(xs)) + 1), conf=int(np.median(conf[m])),
                             vmax=round(float(vis[m].max()), 4), b8max=round(float(b8[m].max()), 4),
                             medv=round(mv, 4), med8=round(m8, 4),
                             rv=round(float(vis[m].max()) / max(mv, 1e-4), 2),
                             r8=round(float(b8[m].max()) / max(m8, 1e-4), 2)))
    return rows


def live_one(args):
    region, date, model = args
    from l41_prep import prepare
    from macroplastic.grid import artifacts as A
    d = prepare(region, date, model)
    lab, n, b, water, raw = d["labels"], d["n"], d["bands"], d["water"], d["raw"]
    if b is None or n == 0:
        return []
    A.BOAT_REL_VIS = 1e9  # baseline: without the boat rule
    art, f = A.classify(lab, n, b, water, raw)
    b2, b3, b4, b8 = (np.nan_to_num(b[k].astype(np.float32)) for k in ("B2", "B3", "B4", "B8"))
    vis = (b2 + b3 + b4) / 3.0
    H, W = lab.shape
    objs = ndimage.find_objects(lab)
    rows = []
    fl = ndimage.binary_dilation(raw | (lab > 0), iterations=2)
    for k in range(n):
        if f["length_px"][k] > 8 or objs[k] is None:
            continue
        sl = objs[k]
        cy, cx = (sl[0].start + sl[0].stop) // 2, (sl[1].start + sl[1].stop) // 2
        r0, r1, c0, c1 = max(cy - HALF, 0), min(cy + HALF + 1, H), max(cx - HALF, 0), min(cx + HALF + 1, W)
        m = lab[r0:r1, c0:c1] == k + 1
        z = ndimage.binary_dilation(m, np.ones((3, 3), bool), iterations=2)
        w = water[r0:r1, c0:c1] & ~fl[r0:r1, c0:c1]
        if w.sum() < 30:
            continue
        v, e = vis[r0:r1, c0:c1], b8[r0:r1, c0:c1]
        mv, m8 = float(np.median(v[w])), float(np.median(e[w]))
        ex = excess(b2[r0:r1, c0:c1], b3[r0:r1, c0:c1], b4[r0:r1, c0:c1], e, w, z)
        ex.update(isolation(e, w, z))
        ex.update(swir(e, np.nan_to_num(b["B11"].astype(np.float32))[r0:r1, c0:c1], w, z))
        rows.append(dict(**ex, region=region, date=date, model=model, k=k + 1, art=art[k] or "", n_px=int(f["n_px"][k]),
                         len_px=int(f["length_px"][k]), cy=cy, cx=cx,
                         vmax_obj=round(float(v[m].max()), 4), vmax=round(float(v[z].max()), 4),
                         b8max=round(float(e[z].max()), 4), medv=round(mv, 4), med8=round(m8, 4),
                         rv=round(float(v[z].max()) / max(mv, 1e-4), 2), r8=round(float(e[z].max()) / max(m8, 1e-4), 2),
                         rv_obj=round(float(v[m].max()) / max(mv, 1e-4), 2),
                         r8_obj=round(float(e[m].max()) / max(m8, 1e-4), 2)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--marida", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--jobs", type=int, default=8)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.marida:
        ps = sorted(p for p in (ROOT / "data/MARIDA/patches").glob("*/*.tif") if not p.stem.endswith(("_cl", "_conf")))
        with ProcessPoolExecutor(a.jobs) as ex:
            rows = [r for rr in ex.map(marida_one, ps, chunksize=8) for r in rr]
        with open(OUT / "marida_ratios.csv", "w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
        print("marida components", len(rows))
    if a.live:
        jobs = [(s.parent.name, s.name, p.stem[5:]) for s in sorted((ROOT / "data/live").glob("*/*"))
                for p in sorted(s.glob("prob_*.tif"))]
        with ProcessPoolExecutor(a.jobs) as ex:
            rows = [r for rr in ex.map(live_one, jobs) for r in rr]
        with open(OUT / "live_small.csv", "w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
        print("live small objects", len(rows))


if __name__ == "__main__":
    main()
