"""Labelled pixels + 'win' features for FloatingObjects / RefinedFloatingObjects crops (fetched by fo_fetch.py).

  .venv/Scripts/python.exe scripts/extra_data/fo_features.py  -> data/extra/features_floatingobjects.npz

Per crop (data/extra/floatingobjects/crops/<region>/<cells>.tif, uint16 DN/1e4 + SCL):
  fo_line      y=1 level B  pixels touched by the hand-drawn FloatingObjects lines (all_touched, as in
                            marinedebrisdetector); lines are centre lines -> label noise at the edges.
  refined_pos  y=1 level B  pixel containing a refined point of type 1 (marine debris)
  refined_neg  y=0 level D  pixel containing a refined point of type 0 (annotated negative)
  background   y=0 level U  random SCL-water (6) pixels >= 10 px from any label (UNVERIFIED, not D)
Features: src/macroplastic/features/pixel.py compute_features(level='win') on the whole crop (block-wise with halo),
same code as training/inference. Also stored: scl, crop name, UTM x/y, epsg.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
from macroplastic.features.pixel import compute_features_blocked, feature_names  # noqa: E402

import geopandas as gpd  # noqa: E402
import rasterio  # noqa: E402
from rasterio import features as rfeat  # noqa: E402
from scipy import ndimage  # noqa: E402

FO = ROOT / "data" / "extra" / "floatingobjects"
BANDS = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]
BG_DIST = 10
BG_MULT, BG_MIN, BG_MAX = 3, 300, 4000
RNG = np.random.default_rng(0)


def labels_for(region: str, crs):
    lines = pts = None
    p = FO / "shapefiles" / f"{region}.shp"
    if p.is_file():
        g = gpd.read_file(p)
        lines = g[g.geometry.notna()].to_crs(crs)
    p = FO / "refined" / f"{region}.shp"
    if p.is_file():
        g = gpd.read_file(p)
        pts = g[g.geometry.notna()].to_crs(crs)
    return lines, pts


def process_crop(tif: Path, region: str):
    with rasterio.open(tif) as s:
        a = s.read()
        tr, crs = s.transform, s.crs
    H, W = a.shape[1:]
    img = a[:12].astype(np.float32) / 10000.0
    img[:, a[0] == 0] = np.nan
    img[a[:12] == 0] = np.nan
    scl = a[12].astype(np.uint8)
    lines, pts = labels_for(region, crs)
    lab = np.zeros((H, W), np.int8)  # 0 none, 1 line, 2 refined pos, 3 refined neg
    if lines is not None and len(lines):
        m = rfeat.rasterize(((g, 1) for g in lines.geometry), out_shape=(H, W), transform=tr, fill=0,
                            all_touched=True, dtype="uint8")
        lab[m > 0] = 1
    if pts is not None and len(pts):
        for geom, t in zip(pts.geometry, pts["type"].astype(int)):
            r, c = rasterio.transform.rowcol(tr, geom.x, geom.y)
            if 0 <= r < H and 0 <= c < W:
                lab[r, c] = 2 if t == 1 else 3
    anyl = lab > 0
    far = ndimage.distance_transform_edt(~anyl) >= BG_DIST if anyl.any() else np.ones((H, W), bool)
    cand = np.argwhere(far & (scl == 6) & np.isfinite(img).all(0))
    npos = int(np.isin(lab, (1, 2)).sum())
    nbg = int(np.clip(BG_MULT * npos, BG_MIN, BG_MAX))
    bgsel = cand[RNG.choice(len(cand), min(nbg, len(cand)), replace=False)] if len(cand) else np.zeros((0, 2), int)
    sel = np.zeros((H, W), np.int8)
    sel[anyl] = lab[anyl]
    sel[bgsel[:, 0], bgsel[:, 1]] = np.where(sel[bgsel[:, 0], bgsel[:, 1]] == 0, 4, sel[bgsel[:, 0], bgsel[:, 1]])
    names = feature_names("win")
    Xs, rr, cc, kk = [], [], [], []
    for (y0, y1, x0, x1), f in compute_features_blocked(img, BANDS, "win", block=512):
        s_ = sel[y0:y1, x0:x1]
        yy, xx = np.nonzero(s_)
        if not len(yy):
            continue
        Xs.append(f[:, yy, xx].T.copy())
        rr.append(yy + y0)
        cc.append(xx + x0)
        kk.append(s_[yy, xx])
    if not Xs:
        return None
    X = np.concatenate(Xs).astype(np.float32)
    r, c, k = np.concatenate(rr), np.concatenate(cc), np.concatenate(kk)
    xs, ys = rasterio.transform.xy(tr, r, c)
    return dict(X=X, k=k, scl=scl[r, c], x=np.asarray(xs), y=np.asarray(ys), epsg=crs.to_epsg(), names=names)


def _job(args):
    tif, region = args
    import zlib
    rng_seed = zlib.crc32(f"{region}/{tif.name}".encode())  # reproducible (hash() is salted per process)
    global RNG
    RNG = np.random.default_rng(rng_seed)
    return process_crop(tif, region)


def main():
    items = json.loads((FO / "items.json").read_text(encoding="utf-8"))
    kind_of = {1: "fo_line", 2: "refined_pos", 3: "refined_neg", 4: "background_unverified"}
    lvl_of = {1: "B", 2: "B", 3: "D", 4: "U"}
    y_of = {1: 1, 2: 1, 3: 0, 4: 0}
    parts = []
    regions = sorted(p.name for p in (FO / "crops").iterdir() if p.is_dir())
    jobs = [(tif, region) for region in regions for tif in sorted((FO / "crops" / region).glob("*.tif"))
            if not tif.name.endswith(".part.tif")]
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=int(os.environ.get("L89_WORKERS", "8"))) as ex:
        for (tif, region), d in zip(jobs, ex.map(_job, jobs)):
            if d is None:
                continue
            d.update(region=region, crop=tif.stem)
            n = {kind_of[v]: int((d["k"] == v).sum()) for v in (1, 2, 3, 4)}
            print(f"{region} {tif.stem}: {n}", flush=True)
            parts.append(d)
    if not parts:
        print("no crops")
        return
    X = np.concatenate([p["X"] for p in parts])
    k = np.concatenate([p["k"] for p in parts])
    group = np.concatenate([np.full(len(p["k"]), regions.index(p["region"]), np.int32) for p in parts])
    crops = sorted({f"{p['region']}/{p['crop']}" for p in parts})
    crop_idx = np.concatenate([np.full(len(p["k"]), crops.index(f"{p['region']}/{p['crop']}"), np.int32) for p in parts])
    out = ROOT / "data" / "extra" / "features_floatingobjects.npz"
    np.savez_compressed(
        out, X=X, y=np.vectorize(y_of.get)(k).astype(np.int8), level=np.vectorize(lvl_of.get)(k).astype("U1"),
        kind=np.vectorize(kind_of.get)(k).astype("U24"), group=group, scenes=np.array(regions),
        crop=crop_idx, crops=np.array(crops), scl=np.concatenate([p["scl"] for p in parts]),
        xy=np.stack([np.concatenate([p["x"] for p in parts]), np.concatenate([p["y"] for p in parts])], 1),
        epsg=np.concatenate([np.full(len(p["k"]), p["epsg"], np.int32) for p in parts]),
        tile=np.array([items[r]["tile"] for r in regions]), item_id=np.array([items[r]["item_id"] for r in regions]),
        names=np.array(feature_names("win")),
        readme=np.array("FloatingObjects lines (B, natural accumulations, noisy centre lines) + Refined points "
                        "(B pos / D neg) + unverified SCL-water background (U). y: 1 pos, 0 neg/background. "
                        "group -> scenes (region = one S2 acquisition; split by group!). Pixels: Earth Search L2A "
                        "(same reader as live scenes), not the dataset's own L1C/L2A GeoTIFFs. "
                        "durban_20190424 excluded (same acquisition as MARIDA train S2_24-4-19_36JUN)."))
    print(f"-> {out}: {len(k)} px; " + ", ".join(f"{kind_of[v]}={int((k == v).sum())}" for v in (1, 2, 3, 4)) +
          f"; regions={len(regions)}, crops={len(crops)}")


if __name__ == "__main__":
    main()
