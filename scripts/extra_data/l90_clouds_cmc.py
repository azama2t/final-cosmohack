"""L90: Sentinel-2 Cloud Mask Catalogue (Francis et al., Zenodo 4172871, CC-BY-4.0) -> clouds / shadows over OPEN WATER
as verified negatives (level D) in S2 L2A radiometry (Planetary Computer, live reader) -> pixel features (level win).

Why: the catalogue has hand-drawn CLEAR/CLOUD/SHADOW masks (1022x1022 px at 20 m) for 513 L1C subscenes; 120 are tagged
open_water. We use only masks + footprints (9 MB), not the 15 GB L1C subscenes: pixels are re-read from the L2A product
of the SAME acquisition (same tile+date), so clouds/shadows are the annotated ones.

Geometry (measured, not assumed): footprint shapefile = 1152 px box at 20 m; the mask is its central 1022 px
(offset 65 px). Checked per scene by correlating the cloud mask with bright B2 (20 m) over offsets 65 +- 6 px;
a scene is kept only if the GLOBAL correlation peak over +-40 px lies within 6 px of the centred
offset and the refined r >= 0.3 (r = 0.4..0.5 occurs with correct alignment when the B2 median split is a weak cloud proxy,
e.g. 36TVP: global peak exactly at the centre, r 0.46); others are rejected and listed.

Output data/extra/features_clouds_cmc.npz:
  X (N,F) float32 (feature_names('win')), y (N,) MARIDA codes 6 Clouds, 13 Cloud Shadows, 7 Marine Water (annotated CLEAR
  and NDWI>0), edge (N,) bool (<= 3 px from a class boundary at 10 m: annotation uncertainty), scene (N,) index,
  scenes_meta json (scene, tile, date, item, align offset, r, counts)
Registry rows (one per scene x class) -> data/extra/registry_clouds_cmc.csv

  CUDA_VISIBLE_DEVICES="" .venv/Scripts/python.exe scripts/extra_data/l90_clouds_cmc.py --workers 4
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
SRC = ROOT / "data" / "extra" / "cloudmask_catalogue"
OUT = ROOT / "data" / "extra"
CACHE = ROOT / "out" / "l90_clouds"
MPX = 1022
OFF0 = 65
WIN = 504          # 20 m px -> 1008 px at 10 m (10.08 km)
HALO = 360.0
CAP = dict(cloud=15000, shadow=8000, clear=8000)


def retry(fn, *args, tries=5, **kw):
    """Network calls (STAC search, product XML, COG windows) fail transiently (WinError 10054) -> retry with backoff."""
    for k in range(tries):
        try:
            return fn(*args, **kw)
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(10 * (k + 1))


def run_scene(scene: str, seed: int = 0, verify: bool = False):
    """verify=True: only re-check the alignment of an already cached scene; a failing cache is deleted."""
    import warnings
    warnings.filterwarnings("ignore")
    import geopandas as gpd
    from scipy import ndimage

    from macroplastic.features.pixel import compute_features, feature_names
    from macroplastic.live import stac

    cache = CACHE / f"{scene}.npz"
    if cache.is_file() and not verify:
        return str(cache), "cached"
    if verify and not cache.is_file():
        return None, "not cached"

    def fail(msg):
        if verify and cache.is_file():
            cache.unlink()
            msg = "cache DELETED: " + msg
        return None, msg
    tile = scene.split("_T")[1][:5]
    d = scene.split("_")[2][:8]
    date = f"{d[:4]}-{d[4:6]}-{d[6:]}"
    g = gpd.read_file(next((SRC / "shapefiles" / scene).glob("*.shp")))
    w, s, e, n = g.total_bounds
    epsg = g.crs.to_epsg()
    ll = g.to_crs(4326).geometry.centroid.iloc[0]
    its = retry(stac.search, "planetary-computer", ll.x, ll.y, f"{date}/{date}", tile=tile)
    if not its:
        return None, "no PC L2A item"
    item = its[0]
    if stac.item_epsg(item) != epsg:
        return None, f"CRS mismatch {stac.item_epsg(item)} vs {epsg}"
    mask = np.load(SRC / "masks" / f"{scene}.npy")  # (1022,1022,3) bool CLEAR, CLOUD, SHADOW
    # --- alignment check at 20 m
    M = 40 * 20  # wide margin: the global correlation peak must fall at the expected (centred) offset
    b2 = retry(stac._read_asset, item.assets["B02"].href, epsg, [w - M, s - M, e + M, n + M], res=20.0).astype(np.float32)
    valid = b2 > 0
    if valid.mean() < 0.5:
        return None, f"L2A nodata over footprint ({valid.mean():.2f} valid)"
    br = (b2 > np.percentile(b2[valid], 50)).astype(np.float32)
    mc = mask[..., 1].astype(np.float32)
    if mc.std() == 0:
        return None, "mask has one class only"

    def ds(a, k=4):
        H_, W_ = a.shape
        return a[:H_ // k * k, :W_ // k * k].reshape(H_ // k, k, W_ // k, k).mean((1, 3))

    A, B = ds(br), ds(mc)
    coarse = (-2.0, 0, 0)
    for oy in range(0, A.shape[0] - B.shape[0] + 1):
        for ox in range(0, A.shape[1] - B.shape[1] + 1):
            rr = float(np.corrcoef(A[oy:oy + B.shape[0], ox:ox + B.shape[1]].ravel(), B.ravel())[0, 1])
            if rr > coarse[0]:
                coarse = (rr, oy * 4 - 40 - OFF0, ox * 4 - 40 - OFF0)
    if abs(coarse[1]) > 6 or abs(coarse[2]) > 6:
        return fail(f"alignment: global peak r={coarse[0]:.2f} at offset ({coarse[1]},{coarse[2]}) px from the centred "
                      f"position, not at it -> mask does not match this L2A item, rejected")
    best = (-2.0, 0, 0)
    for dy in range(-6, 7):
        for dx in range(-6, 7):
            oy, ox = 40 + OFF0 + dy, 40 + OFF0 + dx
            sub = br[oy:oy + MPX, ox:ox + MPX]
            if sub.shape != mc.shape:
                continue
            r = float(np.corrcoef(sub.ravel(), mc.ravel())[0, 1])
            if r > best[0]:
                best = (r, dy, dx)
    r, dy, dx = best
    if not np.isfinite(r) or r < 0.3:
        return fail(f"alignment r={r:.2f} < 0.3 (mask vs bright B2), rejected")
    if verify:
        return str(cache), f"verified: global peak ({coarse[1]},{coarse[2]}) r={coarse[0]:.2f}; fine ({dy},{dx}) r={r:.2f}"
    # mask origin (UTM) at 20 m
    mx0 = w + (OFF0 + dx) * 20.0
    my0 = n - (OFF0 + dy) * 20.0
    # choose a WIN x WIN window with most cloud/clear boundary pixels (mixed scene part)
    cls = np.argmax(mask, -1)  # 0 clear 1 cloud 2 shadow
    bnd = (ndimage.maximum_filter(cls, 3) != ndimage.minimum_filter(cls, 3)).astype(np.float32)
    integ = ndimage.uniform_filter(bnd, WIN, mode="constant")
    h = WIN // 2
    integ[:h + 18, :] = -1; integ[-h - 18:, :] = -1; integ[:, :h + 18] = -1; integ[:, -h - 18:] = -1
    cy, cx = np.unravel_index(np.argmax(integ), integ.shape)
    r0, c0 = cy - h, cx - h
    bounds = [mx0 + c0 * 20 - HALO, my0 - (r0 + WIN) * 20 - HALO, mx0 + (c0 + WIN) * 20 + HALO, my0 - r0 * 20 + HALO]
    crop = retry(stac.read_crop, item, epsg, bounds)
    bands = crop["bands"]
    H, W = bands.shape[1:]
    hp = int(HALO / 10)
    lab20 = np.full((WIN + 36, WIN + 36), -1, np.int8)
    lab20[:] = -1
    rs, cs = r0 - 18, c0 - 18
    lab20 = cls[rs:rs + WIN + 36, cs:cs + WIN + 36].astype(np.int8)
    lab = np.repeat(np.repeat(lab20, 2, 0), 2, 1)[:H, :W]
    F = compute_features(bands, stac.BANDS, level="win")
    names = feature_names("win")
    core = np.zeros((H, W), bool)
    core[hp:H - hp, hp:W - hp] = True
    ok = np.isfinite(F).all(0) & core
    bnd10 = ndimage.maximum_filter(lab, 7) != ndimage.minimum_filter(lab, 7)
    ndwi = F[names.index("NDWI")]
    rng = np.random.default_rng(seed)
    Xs, ys, edges, rows, counts = [], [], [], [], {}
    for kname, code, m in (("cloud", 6, (lab == 1) & ok), ("shadow", 13, (lab == 2) & ok),
                           ("clear", 7, (lab == 0) & ok & (ndwi > 0))):
        idx = np.flatnonzero(m)
        counts[kname] = int(len(idx))
        if len(idx) == 0:
            continue
        if len(idx) > CAP[kname]:
            idx = rng.choice(idx, CAP[kname], replace=False)
        Xs.append(F.reshape(len(names), -1)[:, idx].T.astype(np.float32))
        ys.append(np.full(len(idx), code, np.int16))
        edges.append(bnd10.ravel()[idx])
        lo, la = g.to_crs(4326).geometry.centroid.iloc[0].coords[0]
        rows.append(dict(source="cloud_mask_catalogue_zenodo4172871", scene=item.id, tile=tile, date=date,
                         geometry=f"BBOX_UTM{epsg}({bounds[0] + HALO:.0f} {bounds[1] + HALO:.0f} {bounds[2] - HALO:.0f} "
                                  f"{bounds[3] - HALO:.0f})",
                         klass={"cloud": "облако", "shadow": "тень облака", "clear": "чистая вода (фон)"}[kname],
                         level="D" if kname != "clear" else "фон", n_px=int(len(idx)), n_px_available=counts[kname],
                         l1c_scene=scene, align_r=round(r, 3), license="CC-BY-4.0"))
    CACHE.mkdir(parents=True, exist_ok=True)
    meta = dict(scene=scene, tile=tile, date=date, item=item.id, epsg=epsg, bounds=bounds, align_dy=dy, align_dx=dx,
                align_r=round(r, 3), counts=counts, read_s=crop["read_s"])
    np.savez_compressed(cache, X=np.concatenate(Xs), y=np.concatenate(ys), edge=np.concatenate(edges),
                        meta=json.dumps(meta), rows=json.dumps(rows, ensure_ascii=False))
    return str(cache), f"r={r:.2f} off=({dy},{dx}) counts={counts}"


def main():
    import pandas as pd
    from macroplastic.features.pixel import feature_names
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--verify-cached", action="store_true", help="re-check alignment of cached scenes only, then merge")
    ap.add_argument("--merge-only", action="store_true", help="merge scenes already cached (partial portion)")
    ap.add_argument("--out", default=str(OUT / "features_clouds_cmc.npz"))
    a = ap.parse_args()
    t = pd.read_csv(SRC / "classification_tags.csv")
    sel = t[(t.open_water == 1) & (t["snow/ice"] == 0) & (t.clear_percent.between(10, 90))]
    scenes = sel.scene.tolist()[: a.limit or None]
    t0 = time.time()
    done, failed = [], []
    if a.merge_only:
        done = [str(CACHE / f"{sc}.npz") for sc in scenes if (CACHE / f"{sc}.npz").is_file()]
        scenes = []
    with ProcessPoolExecutor(a.workers) as ex:
        futs = {ex.submit(run_scene, sc, 0, a.verify_cached): sc for sc in scenes
                if not a.verify_cached or (CACHE / f"{sc}.npz").is_file()}
        for fu in as_completed(futs):
            sc = futs[fu]
            try:
                p, msg = fu.result()
            except Exception as e:
                p, msg = None, f"FAILED {e!r}"
            print(f"{sc}: {msg} t={time.time() - t0:.0f}s", flush=True)
            (done if p else failed).append(p or (sc, msg))
    Xs, ys, edges, sidx, metas, rows = [], [], [], [], [], []
    for k, p in enumerate(sorted(done)):
        d = np.load(p)
        Xs.append(d["X"]); ys.append(d["y"]); edges.append(d["edge"]); sidx.append(np.full(len(d["y"]), k, np.int32))
        metas.append(json.loads(str(d["meta"])))
        rows += json.loads(str(d["rows"]))
    y = np.concatenate(ys)
    edge = np.concatenate(edges)
    sc = np.concatenate(sidx)
    # per-row level for detector_retrain.py prep-extra: D = annotated cloud / shadow / clear water away from mask
    # boundaries; C = within 3 px of a boundary (20 m masks upsampled x2 -> uncertain)
    level = np.where(edge, "C", "D")
    klass = np.select([y == 6, y == 13], ["cloud", "cloud_shadow"], "clear_water")
    acq = np.array([f"{metas[k]['tile']}_{metas[k]['date']}" for k in sc])
    obj = np.array([f"{metas[k]['tile']}_{metas[k]['date']}:{c}" for k, c in zip(sc, klass)])
    np.savez_compressed(a.out, X=np.concatenate(Xs), y=y, edge=edge, scene=sc, level=level, klass=klass, acq=acq, obj=obj,
                        names=np.array(feature_names("win")), scenes_meta=json.dumps(metas),
                        rejected=json.dumps(failed, ensure_ascii=False),
                        note=np.array("S2 L2A (Planetary Computer, live reader) of the annotated L1C acquisitions; "
                                      "edge=True pixels are near a mask boundary (20 m masks upsampled x2)"))
    with open(OUT / "registry_clouds_cmc.csv", "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    print(json.dumps(dict(scenes=len(done), rejected=len(failed), cloud=int((y == 6).sum()), shadow=int((y == 13).sum()),
                          clear=int((y == 7).sum()), elapsed_s=round(time.time() - t0))), flush=True)


if __name__ == "__main__":
    main()
