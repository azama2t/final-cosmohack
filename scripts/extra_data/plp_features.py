"""Plastic Litter Project (PLP 2019 / 2021 / 2022, Univ. Aegean, Lesvos, tile 35SMD): labelled pixels + 'win' features.

Sources (data/extra/plp/, not in git):
  PLP2019  Zenodo 3752719 (CC-BY-4.0): ACOLITE L2W netCDF (rhos_*), per-pixel % cover of bags/bottles/reeds/sea.
  PLP2021, PLP2022  marinedebrisdetector mirror PLP.zip (MIT repo; targets from Papageorgiou et al. 2022):
           12-band S2 GeoTIFF subsets (DN, reflectance = DN/1e4) + target points (id, date, type, radius/width).

Label semantics (evidence level B, ARTIFICIAL targets, kept separate from natural accumulations):
  2021: HDPE target (circle r=14 m) and wood target (r=14 m) -> y=1 for HDPE, y=2 for wood (natural material target)
  2022: PVC square 5x5 m (width field = 5) and 2 HDPE circles r=3.5 m (sub-pixel: 38 m2 < 100 m2 pixel)
  2019: fractional plastic cover per 10 m pixel (bags+bottles, %), reeds separate.
  frac = share of the 10 m pixel covered by the target (supersampled 10x10) - 2019: (bags+bottles)/100.
  Background: water pixels >= 5 px from any target -> y=0 with level 'U' (unverified, NOT a checked D).

  .venv/Scripts/python.exe scripts/extra_data/plp_features.py   -> data/extra/features_plp.npz + data/extra/plp/labels_plp.csv
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
from macroplastic.features.pixel import compute_features, feature_names  # noqa: E402

import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from rasterio import features as rfeat  # noqa: E402
from rasterio.transform import from_origin  # noqa: E402
from scipy import ndimage  # noqa: E402

PLP = ROOT / "data" / "extra" / "plp"
S2_12 = ["B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B8A", "B9", "B11", "B12"]
BG_DIST_PX = 5
BG_PER_SCENE = 400
RNG = np.random.default_rng(0)


def target_geoms(g):
    out = []
    for _, r in g.iterrows():
        if pd.notna(r.get("radius")) and r["radius"] > 0:
            out.append(r.geometry.buffer(float(r["radius"])))
        else:
            out.append(r.geometry.buffer(float(r["width"]) / 2.0, cap_style=3))  # square of side = width
    return out


def frac_raster(geom, shape, transform, k=10):
    """Share of each pixel covered by geom (supersampled k x k)."""
    t2 = transform * transform.scale(1.0 / k, 1.0 / k)
    hi = rfeat.rasterize([(geom, 1)], out_shape=(shape[0] * k, shape[1] * k), transform=t2, fill=0, dtype="uint8")
    return hi.reshape(shape[0], k, shape[1], k).mean((1, 3))


def water_like(img):
    """Crude water mask for background sampling: NDWI(B3,B8) > 0 and finite."""
    b3, b8 = img[S2_12.index("B3")], img[S2_12.index("B8")]
    with np.errstate(invalid="ignore", divide="ignore"):
        ndwi = (b3 - b8) / (b3 + b8)
    return np.isfinite(ndwi) & (ndwi > 0)


def rows_from_scene(img, names, labels, scene, date, year, transform, crs, feats_names):
    F = compute_features(img, names, "win")
    H, W = img.shape[1:]
    recs = []
    any_t = np.zeros((H, W), bool)
    for lab in labels:
        fr = lab["frac"]
        any_t |= fr > 0
        for yy, xx in zip(*np.nonzero(fr > 0)):
            recs.append(dict(r=yy, c=xx, y=lab["y"], frac=float(fr[yy, xx]), level="B", kind=lab["kind"],
                             material=lab["material"], target_id=lab["id"], note=lab.get("note", "")))
    far = ndimage.distance_transform_edt(~any_t) >= BG_DIST_PX
    cand = np.argwhere(far & water_like(img) & np.isfinite(F).all(0))
    if len(cand):
        pick = cand[RNG.choice(len(cand), min(BG_PER_SCENE, len(cand)), replace=False)]
        for yy, xx in pick:
            recs.append(dict(r=yy, c=xx, y=0, frac=0.0, level="U", kind="background_unverified", material="water?",
                             target_id=-1, note=""))
    X = np.stack([F[:, d["r"], d["c"]] for d in recs]).astype(np.float32) if recs else np.zeros((0, F.shape[0]), np.float32)
    for d in recs:
        x, y_ = rasterio.transform.xy(transform, d["r"], d["c"])
        d.update(scene=scene, date=date, year=year, x=x, y_utm=y_, crs=str(crs))
    return X, recs


def plp_2021_2022():
    allX, allR = [], []
    for year in (2021, 2022):
        g = gpd.read_file(PLP / "PLP" / f"PLP{year}" / f"PLP{year}_targets.shp")
        g = g.drop_duplicates(subset=["id", "date", "type"])  # 2022-06-26 is listed twice
        g["geom_t"] = target_geoms(g)
        for tif in sorted((PLP / "PLP" / f"PLP{year}" / "Sentinel-2").glob("*.tif")):
            date = tif.name[:8]
            d = g[g.date == date]
            with rasterio.open(tif) as src:
                img = src.read().astype(np.float32) * 1e-4
                tr, crs = src.transform, src.crs
                names = list(src.descriptions) if all(src.descriptions) else S2_12
            labels = []
            for _, r in d.iterrows():
                fr = frac_raster(r["geom_t"], img.shape[1:], tr)
                mat = str(r["type"])
                note = str(r.get("note", r.get("notes", "")) or "")
                labels.append(dict(frac=fr, y=2 if mat.lower() == "wood" else 1, kind="plp_target", material=mat,
                                   id=int(r["id"]), note="" if note == "nan" else note))
            X, recs = rows_from_scene(img, names, labels, tif.stem, date, year, tr, crs, feature_names("win"))
            print(f"PLP{year} {date}: targets {len(d)}, px pos {sum(1 for q in recs if q['y'] > 0)}, bg {sum(1 for q in recs if q['y'] == 0)}")
            allX.append(X)
            allR += recs
    return allX, allR


def plp_2019():
    allX, allR = [], []
    root = PLP / "PLP2019" / "PLP2019_dataset"
    wl = {"B1": 443, "B2": 492, "B3": 560, "B4": 665, "B5": 704, "B6": 740, "B7": 783, "B8": 833, "B8A": 865,
          "B11": 1614, "B12": 2202}
    for nc in sorted((root / "S2_satellite_images_nc").glob("*.nc")):
        date = nc.name.split("_")[1]
        shp = list((root / "Vector_Points" / date).glob("*.shp"))
        if not shp:
            print(f"PLP2019 {date}: no target vectors -> skipped")
            continue
        with rasterio.open(nc) as ds:
            tags = ds.tags()
            subs = [sd for sd in ds.subdatasets if ":rhos_" in sd]
        # S2A/S2B centre wavelengths differ (443/442, 560/559, ...): sort rhos_* by wavelength -> MARIDA order B1..B12
        subs = sorted(subs, key=lambda sd: int(sd.rsplit("_", 1)[1]))
        assert len(subs) == 11, subs
        x0, x1 = [float(v) for v in tags["NC_GLOBAL#xrange"].strip("{}").split(",")]
        y0, y1 = [float(v) for v in tags["NC_GLOBAL#yrange"].strip("{}").split(",")]
        bands = []
        for sd in subs:
            with rasterio.open(sd) as s:
                a = s.read(1).astype(np.float32)
                nd = s.nodata
            if nd is not None:
                a[a == nd] = np.nan
            a[~np.isfinite(a) | (a > 10)] = np.nan
            bands.append(np.flipud(a))  # ACOLITE nc rows run south->north (lat[0,0] = south edge): flip
        img = np.stack(bands)
        H, W = img.shape[1:]
        assert abs((x1 - x0) / W - 10) < 1e-6 and abs((y1 - y0) / H - 10) < 1e-6, (W, H, x0, x1, y0, y1)
        tr = from_origin(x0, y1, 10, 10)
        labels = []
        if shp:
            g = gpd.read_file(shp[0])
            g.columns = [c if c == "geometry" else c.upper() for c in g.columns]
            for _, r in g.iterrows():
                fr = np.zeros((H, W), np.float32)
                row, col = rasterio.transform.rowcol(tr, r.geometry.x, r.geometry.y)
                if not (0 <= row < H and 0 <= col < W):
                    print(f"  PLP2019 {date}: point {r.get('PIXEL_NAME', '')} outside the nc AOI -> skipped")
                    continue
                plast = (float(r.get("CP_BAGS", 0) or 0) + float(r.get("CP_BOTTLES", 0) or 0)) / 100.0
                reeds = float(r.get("CP_REEDS", 0) or 0) / 100.0
                fr[row, col] = plast if plast > 0 else 1e-6  # keep the labelled pixel even with 0 % plastic
                labels.append(dict(frac=fr, y=1 if plast > 0 else 0, kind="plp2019_pixel", material="bags+bottles",
                                   id=int(r.get("POINTID", -1)),
                                   note=f"{r.get('PIXEL_NAME', '')}; plastic {plast:.2f}; reeds {reeds:.2f}; sea {float(r.get('CP_SEA', 0)) / 100:.2f}"))
        X, recs = rows_from_scene(img, list(wl), labels, nc.stem, date, 2019, tr, "EPSG:32635", feature_names("win"))
        for q in recs:  # 2019 labelled pixels: frac is the plastic share (0 for sea-only labelled pixels)
            if q["kind"] == "plp2019_pixel" and q["y"] == 0:
                q["frac"] = 0.0
                q["level"] = "D"
                q["kind"] = "plp2019_pixel_no_plastic"
        print(f"PLP2019 {date}: labelled px {len(labels)}, plastic px {sum(1 for q in recs if q['y'] == 1)}, bg {sum(1 for q in recs if q['level'] == 'U')}")
        allX.append(X)
        allR += recs
    return allX, allR


def main():
    X1, R1 = plp_2021_2022()
    X2, R2 = plp_2019()
    X = np.concatenate(X1 + X2).astype(np.float32)
    R = pd.DataFrame(R1 + R2)
    scenes = sorted(R.scene.unique())
    R["group"] = R.scene.map({s: i for i, s in enumerate(scenes)})
    out = ROOT / "data" / "extra" / "features_plp.npz"
    np.savez_compressed(out, X=X, y=R.y.values.astype(np.int8), frac=R.frac.values.astype(np.float32),
                        level=R.level.values.astype("U1"), kind=R.kind.values.astype("U32"),
                        material=R.material.values.astype("U16"), group=R.group.values.astype(np.int32),
                        scenes=np.array(scenes), date=R.date.values.astype("U8"),
                        xy=R[["x", "y_utm"]].values.astype(np.float64), names=np.array(feature_names("win")),
                        readme=np.array("PLP artificial targets (level B, ARTIFICIAL) + unverified background (U). "
                                        "y: 1 plastic target px (frac>0), 2 wood target px, 0 background/no-plastic. "
                                        "frac: pixel share covered by target (2019: bags+bottles share). "
                                        "features: src/macroplastic/features/pixel.py level win; reflectance: "
                                        "2021/22 DN/1e4 of the mirror GeoTIFF, 2019 ACOLITE rhos. crs EPSG:32635."))
    R.drop(columns=[]).to_csv(PLP / "labels_plp.csv", index=False)
    print(f"-> {out}: {len(R)} px, y1={int((R.y == 1).sum())}, y2={int((R.y == 2).sum())}, U={int((R.level == 'U').sum())}, "
          f"D={int((R.level == 'D').sum())}, scenes={len(scenes)}, F={X.shape[1]}")


if __name__ == "__main__":
    main()
