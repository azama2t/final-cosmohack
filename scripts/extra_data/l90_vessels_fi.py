"""L90: Finnish-coast vessel boxes (Zenodo 15019034, CC-BY-4.0) -> S2 L2A chips -> pixel features (level win).

Labels: data/extra/finland_vessels/<TILE>.gpkg, one layer per acquisition date (YYYYMMDD), polygons 'boat' in the
tile UTM CRS (axis-aligned boxes drawn by the dataset authors on Sentinel-2 imagery).
Pixels: the same tile+date from Planetary Computer (dates >= 2022-01-25) or Earth Search original processing (earlier),
read with the live reader (src/macroplastic/live/stac.py, same reflectance conversion as the live service).

Chips: 10080 m cells (1008 px) with the most boxes per tile-date (--cells-per-layer), read with a 360 m halo
(>= MAX_HALO); features computed on the halo chip (same function as training/inference), only core pixels kept.
Layers run in parallel processes (--workers); each layer is cached in out/l90_vessels/<tile>_<date>.npz (resume).

Output data/extra/features_vessels_fi.npz:
  X (N,F) float32 features in feature_names('win') order; names (F,)
  y (N,) MARIDA code: 5 Ship (pixel inside an annotated box), 7 Marine Water (ring 3..15 px around boxes, SCL=6, unverified)
  kind (N,) 'ship_box' | 'water_ring'; b8_contrast (N,) = B8_dmed15 (hull brighter than local water)
  box_id (N,) global box id (-1 for water); chip (N,) chip index into chips_meta (json: tile, date, item, source, bounds)
Registry rows (one per box used) -> data/extra/registry_vessels_fi.csv

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/extra_data/l90_vessels_fi.py --cells-per-layer 3 --workers 6
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

os.environ["CUDA_VISIBLE_DEVICES"] = ""
# hung COG range reads stalled all workers for 20+ min (26.09 01:38-01:59, 0 CPU) -> hard HTTP timeouts, then retry()
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "60")
os.environ.setdefault("GDAL_HTTP_CONNECTTIMEOUT", "20")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

SRC = ROOT / "data" / "extra" / "finland_vessels"
OUT = ROOT / "data" / "extra"
CACHE = ROOT / "out" / "l90_vessels"
CELL = 10080.0
HALO = 360.0
RES = 10.0


def retry(fn, *args, tries=5, **kw):
    """Network calls (STAC search, product XML, COG windows) fail transiently (WinError 10054) -> retry with backoff."""
    for k in range(tries):
        try:
            return fn(*args, **kw)
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(10 * (k + 1))


def run_layer(tile: str, lay: str, cells_per_layer: int, max_box_m2: float, water_step: int):
    import warnings
    warnings.filterwarnings("ignore")
    import geopandas as gpd
    from pyproj import Transformer
    from rasterio import features as rfeat
    from rasterio.transform import from_origin
    from scipy import ndimage

    import fetch_live
    from macroplastic.features.pixel import compute_features, feature_names
    from macroplastic.live import stac

    date = f"{lay[:4]}-{lay[4:6]}-{lay[6:]}"
    cache = CACHE / f"{tile}_{lay}_c{cells_per_layer}.npz"
    if cache.is_file():
        return str(cache), "cached"
    names = feature_names("win")
    g = gpd.read_file(SRC / f"{tile}.gpkg", layer=lay)
    epsg = g.crs.to_epsg()
    n_all = len(g)
    g = g[g.geometry.area <= max_box_m2].reset_index(drop=True)
    c = g.geometry.centroid
    g["gx"], g["gy"] = (c.x // CELL).astype(int), (c.y // CELL).astype(int)
    counts = g.groupby(["gx", "gy"]).size().sort_values(ascending=False)
    cells = list(counts.index[:cells_per_layer])
    to_ll = Transformer.from_crs(epsg, 4326, always_xy=True)
    lon, lat = to_ll.transform(float(c.x.median()), float(c.y.median()))
    item = retry(fetch_live.pick_item, date, tile, lon, lat)
    if stac.item_epsg(item) != epsg:
        return None, f"CRS mismatch {stac.item_epsg(item)} vs {epsg}"
    Xs, ys, kinds, contr, boxloc, chiploc, chips, rows = [], [], [], [], [], [], [], []
    for (cx, cy) in cells:
        w0, s0 = cx * CELL, cy * CELL
        bounds = [w0 - HALO, s0 - HALO, w0 + CELL + HALO, s0 + CELL + HALO]
        crop = retry(stac.read_crop, item, epsg, bounds)
        bands, scl = crop["bands"], crop["scl"]
        H, W = bands.shape[1:]
        tr = from_origin(bounds[0], bounds[3], RES, RES)
        sub = g[(g["gx"] == cx) & (g["gy"] == cy)].reset_index(drop=True)
        lab = rfeat.rasterize([(geom, i + 1) for i, geom in enumerate(sub.geometry)], out_shape=(H, W),
                              transform=tr, fill=0, all_touched=True, dtype="int32")
        F = compute_features(bands, stac.BANDS, level="win")
        h = int(HALO / RES)
        core = np.zeros((H, W), bool)
        core[h:H - h, h:W - h] = True
        valid = np.isfinite(F).all(0) & core
        inbox = (lab > 0) & valid
        dist = ndimage.distance_transform_edt(lab == 0)
        ring = (dist >= 3) & (dist <= 15) & (scl == 6) & valid
        ring_idx = np.flatnonzero(ring)[::water_step]
        b8c = F[names.index("B8_dmed15")]
        ci = len(chips)
        for sel, code, kind in ((np.flatnonzero(inbox), 5, "ship_box"), (ring_idx, 7, "water_ring")):
            if len(sel) == 0:
                continue
            Xs.append(F.reshape(len(names), -1)[:, sel].T.astype(np.float32))
            ys.append(np.full(len(sel), code, np.int16))
            kinds.append(np.array([kind] * len(sel)))
            contr.append(b8c.ravel()[sel].astype(np.float32))
            boxloc.append((lab.ravel()[sel] - 1).astype(np.int32))
            chiploc.append(np.full(len(sel), ci, np.int32))
        labv = np.where(inbox, lab, 0)
        n_bright = 0
        for i, geom in enumerate(sub.geometry):
            m = labv == i + 1
            mx = float(np.nanmax(b8c[m])) if m.any() else float("nan")
            bright = bool(np.isfinite(mx) and mx > 0.01)
            n_bright += bright
            lo, la = to_ll.transform(geom.centroid.x, geom.centroid.y)
            rows.append(dict(source="finland_vessels_zenodo15019034", scene=item.id, tile=tile, date=date,
                             geometry=f"POINT({lo:.5f} {la:.5f})", box_m2=round(geom.area), klass="судно", level="D",
                             n_px=int(m.sum()), b8_contrast_max=round(mx, 4), visible_hull=int(bright),
                             chip_local=ci, box_local=i, license="CC-BY-4.0"))
        chips.append(dict(tile=tile, date=date, item=item.id, source=stac.source_of(item), epsg=epsg, bounds=bounds,
                          boxes=len(sub), bright_boxes=n_bright, ship_px=int(inbox.sum()), water_px=int(len(ring_idx)),
                          water_frac_scl6=round(float((scl == 6).mean()), 3), read_s=crop["read_s"]))
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, X=np.concatenate(Xs), y=np.concatenate(ys), kind=np.concatenate(kinds),
                        b8_contrast=np.concatenate(contr), box_local=np.concatenate(boxloc),
                        chip_local=np.concatenate(chiploc), chips=json.dumps(chips), rows=json.dumps(rows, ensure_ascii=False),
                        n_boxes_layer=n_all)
    return str(cache), f"{len(chips)} chips, {len(rows)} boxes, bright {sum(r['visible_hull'] for r in rows)}"


def main():
    import pyogrio
    from macroplastic.features.pixel import feature_names
    ap = argparse.ArgumentParser()
    ap.add_argument("--cells-per-layer", type=int, default=3)
    ap.add_argument("--max-box-m2", type=float, default=20000.0, help="skip larger boxes (merged groups / harbours)")
    ap.add_argument("--water-step", type=int, default=3, help="keep every k-th water-ring pixel")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=str(OUT / "features_vessels_fi.npz"))
    ap.add_argument("--merge-only", action="store_true", help="merge the layers already cached (partial portion)")
    a = ap.parse_args()
    t0 = time.time()
    jobs = [(f.stem, lay) for f in sorted(SRC.glob("*.gpkg")) for lay, _ in pyogrio.list_layers(f)]
    if a.merge_only:
        jobs = []
    done = [str(p) for p in sorted(CACHE.glob(f"*_c{a.cells_per_layer}.npz"))] if a.merge_only else []
    with ProcessPoolExecutor(a.workers) as ex:
        futs = {ex.submit(run_layer, t, l, a.cells_per_layer, a.max_box_m2, a.water_step): (t, l) for t, l in jobs}
        for fu in as_completed(futs):
            t, l = futs[fu]
            try:
                path, msg = fu.result()
            except Exception as e:  # network etc. -> layer skipped, reported
                path, msg = None, f"FAILED {e!r}"
            print(f"{t} {l}: {msg} t={time.time() - t0:.0f}s", flush=True)
            if path:
                done.append(path)
    Xs, ys, kinds, contr, boxids, chipids, chips, rows = [], [], [], [], [], [], [], []
    for p in sorted(done):
        d = np.load(p, allow_pickle=False)
        ch, rw = json.loads(str(d["chips"])), json.loads(str(d["rows"]))
        c0, b0 = len(chips), len(rows)
        # box ids: rows are in (chip_local, box_local) order -> map to global ids
        key = {(r["chip_local"], r["box_local"]): b0 + k for k, r in enumerate(rw)}
        bl, cl = d["box_local"], d["chip_local"]
        boxids.append(np.array([key[(int(c), int(b))] if b >= 0 else -1 for c, b in zip(cl, bl)], np.int32))
        chipids.append(cl + c0)
        Xs.append(d["X"]); ys.append(d["y"]); kinds.append(d["kind"]); contr.append(d["b8_contrast"])
        for k, r in enumerate(rw):
            r["box_id"] = b0 + k
            r["chip"] = r.pop("chip_local") + c0
            r.pop("box_local")
        chips += ch
        rows += rw
    X = np.concatenate(Xs)
    y = np.concatenate(ys)
    b8 = np.concatenate(contr)
    box = np.concatenate(boxids)
    chip = np.concatenate(chipids)
    # per-row level for detector_retrain.py prep-extra: D = hull pixel inside an annotated box (B8 brighter than local
    # water by > 0.01); C = other box pixels (water inside the box, not a reliable label); W = unverified water ring
    level = np.where((y == 5) & (b8 > 0.01), "D", np.where(y == 5, "C", "W"))
    klass = np.where(y == 5, "ship", "water_ring")
    acq = np.array([f"{chips[c]['tile']}_{chips[c]['date']}" for c in chip])
    obj = np.array([f"box{b}" if b >= 0 else f"ring_chip{c}" for b, c in zip(box, chip)])
    np.savez_compressed(a.out, X=X, y=y, kind=np.concatenate(kinds), b8_contrast=b8, level=level, klass=klass, acq=acq,
                        obj=obj, box_id=box, chip=chip, names=np.array(feature_names("win")),
                        chips_meta=json.dumps(chips),
                        note=np.array("S2 L2A (PC/ES, live reader), NOT ACOLITE rhorc like MARIDA; water_ring unverified; "
                                      "ship_box = all pixels touching an annotated box (includes water inside the box: "
                                      "use b8_contrast > 0.01 for hull pixels)"))
    with open(OUT / "registry_vessels_fi.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(dict(pixels=int(len(X)), ship_px=int((y == 5).sum()), water_px=int((y == 7).sum()),
                          ship_px_bright=int(((y == 5) & (np.concatenate(contr) > 0.01)).sum()), chips=len(chips),
                          boxes=len(rows), bright_boxes=int(sum(r["visible_hull"] for r in rows)),
                          layers=len(done), elapsed_s=round(time.time() - t0))), flush=True)


if __name__ == "__main__":
    main()
