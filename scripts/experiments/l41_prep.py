"""Wake rule helper: rebuild labels/bands/water of one scene exactly as scripts/build_service_data.py does before classify."""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src")); sys.path.insert(0, str(ROOT / "scripts"))
import build_service_data as B  # noqa: E402


def prepare(region, date, model, live=ROOT / "data" / "live"):
    sdir = Path(live) / region / date
    water, s_transform, s_crs, _ = B.read_band(sdir / "water_mask.tif")
    water = water == 1
    shape = water.shape
    water &= ~B.edge_zone(sdir, shape, B.EDGE_PX)
    cloud_px = np.zeros(shape, bool)
    if (sdir / "scl.tif").exists():
        scl, *_ = B.read_band(sdir / "scl.tif")
        if scl.shape == shape:
            water &= ~np.isin(scl, (4, 5))
            cloud_px |= np.isin(scl, B.CLOUD_SCL)
    bands = None
    with rasterio.open(sdir / "bands.tif") as ds:
        names = list(ds.descriptions)
        want = ("B2", "B3", "B4", "B8", "B11")
        if ds.shape == shape and all(b in names for b in want):
            bands = {b: ds.read(names.index(b) + 1) for b in want}
    if bands is not None:
        spec = B.spectral_cloud(bands["B2"], bands["B11"], water)
        water &= ~spec
        cloud_px |= spec
    prob, *_ = B.read_band(sdir / f"prob_{model}.tif")
    prob = prob.astype(np.uint8)
    pj = B.read_json(sdir / f"prob_{model}.json") if (sdir / f"prob_{model}.json").exists() else {}
    thr = float(pj.get("threshold", 0.5))
    labels, n, raw = B.clean_mask(prob, water, thr, min_px=2, buffer_px=1)
    near = B.near_cloud_components(labels, n, cloud_px, B.CLOUD_BUFFER_PX)
    shadow = (B.shadow_components(labels, n, bands["B2"], bands["B3"], bands["B4"], bands["B8"], water)
              if bands is not None else np.zeros(n, bool))
    labels, n = B.drop_components(labels, n, near | shadow)
    return dict(labels=labels, n=n, raw=raw, bands=bands, water=water, prob=prob, transform=s_transform,
                crs=s_crs, thr=thr, scl=scl if (sdir / "scl.tif").exists() else None)
